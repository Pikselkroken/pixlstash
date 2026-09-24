# PixlStash Integration Architecture

> Cross-cutting reference for the **boundary** between the FastAPI backend (`pixlstash/`) and the Vue 3 SPA (`frontend/`). Read alongside [backend_architecture.md](backend_architecture.md) and [frontend_architecture.md](frontend_architecture.md).
>
> Anything in this document is a contract — changing one side without updating the other will break the app.

---

## Table of Contents

1. [Single-Origin Model](#1-single-origin-model)
2. [API Surface & URL Prefix](#2-api-surface--url-prefix)
3. [API Client (`apiClient.js`)](#3-api-client-apiclientjs)
4. [Authentication & Session](#4-authentication--session)
5. [Share Tokens (Public Read-Only Access)](#5-share-tokens-public-read-only-access)
6. [CORS Policy](#6-cors-policy)
7. [WebSocket Channels](#7-websocket-channels)
8. [Real-Time Event Contract](#8-real-time-event-contract)
9. [Image & Thumbnail Serving](#9-image--thumbnail-serving)
10. [File Uploads (Import)](#10-file-uploads-import)
11. [Long-Running Operations](#11-long-running-operations)
12. [Configuration Sync](#12-configuration-sync)
13. [Error Handling Contract](#13-error-handling-contract)
14. [Build & Deployment Coupling](#14-build--deployment-coupling)
15. [Host vs Container Paths](#15-host-vs-container-paths)
16. [Versioning](#16-versioning)
17. [Integration Pitfalls](#17-integration-pitfalls)
18. [Integration Diagrams](#18-integration-diagrams)
19. [Duplicates Queue API (v1.9)](#19-duplicates-queue-api-v19)
20. [Folder-Structure Read API (v1.11, Phase 2)](#20-folder-structure-read-api-v111-phase-2)
21. [About your library (v1.11)](#21-about-your-library-v111)
22. [Folder-Structure Commit API (v1.11, Phase 3)](#22-folder-structure-commit-api-v111-phase-3)
23. [Layout & Move API (v1.11, Phase 4b)](#23-layout--move-api-v111-phase-4b)
24. [Move Reconciliation API (v1.11, Phase 5)](#24-move-reconciliation-api-v111-phase-5)
25. [Text in Pictures (#1197)](#25-text-in-pictures-1197)

---

## 1. Single-Origin Model

PixlStash is designed to be served from **one origin**: the FastAPI server hosts both the API and the bundled SPA. The frontend assumes this in many places:

- `deriveBackendUrl()` in [apiClient.js](../frontend/src/utils/apiClient.js) builds the API base URL from `window.location` — no hard-coded backend host.
- WebSocket URLs are derived from the same origin (`http:` → `ws:`, `https:` → `wss:`).
- Image `<img src>` URLs are same-origin relative or absolute to the page origin.
- Cookie-based auth depends on the SPA and API being same-origin.

**Override**: `VITE_BACKEND_URL` (build-time env var) can point the SPA at a different backend — used during local Vite development against a remote server.

---

## 2. API Surface & URL Prefix

- All REST endpoints live under **`/api/v1/`** (constant `API_V1_PREFIX` in [server.py](../pixlstash/server.py)).
- The `apiClient` request interceptor automatically prepends `/api/v1` to any relative URL that does not already start with it — frontend code can call `apiClient.get('/pictures')` and have it routed to `/api/v1/pictures`.
- WebSocket endpoints are **also under `/api/v1/`**: `/api/v1/ws/updates` and `/api/v1/ws/comfyui`.
- Auth endpoints follow the same rule: `POST /api/v1/login`, `POST /api/v1/logout`, `GET /api/v1/check-session`.
- Static assets are served at `/assets/*` (Vite bundle output) and the SPA shell at `/` (serves `frontend/dist/index.html`).

**Contract rule**: every new backend router must be mounted with `prefix=API_V1_PREFIX`. Every new frontend call must use a relative URL (the client adds the prefix).

---

### 2.1 The `/dedup` contract (v1.9)

The duplicate queue was built by two lanes at once, so the agreement is written
down rather than inferred from either side. The client half lives in
[`api/dedup.js`](../frontend/src/api/dedup.js); this is the integration-side
copy, reconciled against `routes/dedup.py` as shipped (2026-07-29).

| Route | Purpose | Response |
|---|---|---|
| `GET /dedup/policy` | tier defaults, bounds and closed vocabularies | `{ defaults, bounds }` |
| `GET /dedup/groups` | one page of the queue, confidence descending | `{ groups, total, offset, limit, next_cursor, policy, scope, scan }` |
| `GET /dedup/stacks/{stack_id}/members` | one page of an existing stack's members, for the deck expansion | `{ stack_id, member_count, leader_picture_id, leader_thumbnail_version, stackable, blocked_by_sets, offset, limit, next_offset, members }` |
| `POST /dedup/counts` | the sidebar badge, the per-tier split, and N scoped counts | `{ unresolved_groups, by_tier, scopes, policy, scan }` |
| `POST /dedup/scan` | queue a scan for one scope | `ScanProgressModel` |
| `POST /dedup/verdicts/stack` | the "same picture" verdict | `VerdictResponse` |
| `POST /dedup/verdicts/keep-separate` | the "different pictures" verdict | `VerdictResponse` |
| `POST /dedup/verdicts/reopen` | un-resolve a group (clearing a stacked verdict also dissolves its stack) | `{ signature, previous_verdict, reopened_at, group_returned_to_queue, batch_id, unstacked_picture_ids }` |
| `POST /dedup/auto-stack` | bulk-stack the exact tier, `dry_run` first | `{ batch_id, dry_run, groups, pictures, scope, dry_run_summary, results, failures }` |

Shapes and rules the frontend depends on:

- **A group is `{ signature, tier, confidence, member_count, cover_picture_id,
  why, created_at, candidates, stacks }`.** `signature` is a hash of the sorted member
  content hashes and is the id every verdict route takes, **in the request
  body**, never in a path. `tier` is `exact | near | embedding`; the exact tier
  is rendered as a different kind of claim, never as "100% similar".
- **A candidate is `{ picture_id, width, height, megapixels, size_bytes, format,
  is_raw, score, tag_count, created_at, imported_at, stack_id,
  reference_folder_id, file_path, smart_score, sharpness, cover_score, why }`.**
  `file_path` is populated **only** for a reference-folder picture and is null
  for a managed one, which is exactly the design's "paths only where they
  matter" rule enforced server-side rather than trusted to the client.
  `smart_score` ([1, 5] scale) and `sharpness` (typical 0-0.5) are the cover
  ranking's top signals, **null-safe**: null means not computed yet or failed —
  render a dash, never a zero. `cover_score` is the **deprecated** legacy
  composite; do not build new UI on it.
- **A group carries `stacks`, and it is the thing the row renders (2026-08-01).**
  `{ "<stack id>": { stack_id, member_count, leader_picture_id,
  leader_thumbnail_version, matched_picture_ids, stackable, blocked_by_sets } }`,
  one entry per existing stack the group touches, `{}` when none is. A stack
  verdict moves whole **stacks**, so the smallest thing the queue may offer to
  move is a unit: a loose picture (`stack_id: null`), or a **deck**, every
  candidate sharing a `stack_id`, drawn as one tile.
  - **`member_count` is the STACK's live member count, not the group's.** It is
    routinely larger than the number of that stack's members in `candidates`
    (measured: 36 of 116 stack-touching groups name only ONE member of a stack),
    so a group's true picture total can exceed `candidates.length`. Sizing a deck
    from `candidates` draws a 4-deep stack as one picture and then silently moves
    four.
  - **`leader_picture_id` is the deck's face**, and it is frequently *not* in
    `matched_picture_ids`. A cover choice on a deck resolves to the leader, so
    showing a matched member while meaning the leader is the mismatch the deck
    exists to remove. `leader_thumbnail_version` is its `?v=` token, same
    contract as a candidate's, so the face renders without expanding anything.
  - **`stackable` / `blocked_by_sets` are the unit-level rollup**: false when ANY
    member of the deck is frozen, because a stack cannot be partially stacked.
    This already covers a locked sibling **outside** the group, a locked set
    freezes a whole stack.
  - **Count and leader are eager; the members are not.** Shipping every member of
    every stack would put a 40-member stack's worth of tiles behind one row.
    `GET /dedup/stacks/{stack_id}/members` is the expansion's own read: plain
    `offset` paging (a stack's membership is not a live list being decided out
    from under the client), `next_offset` is `null` at the end, members come back
    leader-first with exactly the fields a candidate carries plus `position` and
    `is_leader`, and `why` is always `[]`: evidence belongs to the duplicate
    group, not to a stack the user already made. A stack with no live member is a
    **404**, never an empty stack that looks like it exists. Its envelope's
    `stackable` / `blocked_by_sets` are the same pair, with the same meaning and
    over the same member rows, as a `GET /dedup/mixed-stacks` row: `false` means
    split, unstack and `DELETE /stacks/{stack_id}/members` all answer `423`.
    **The envelope can be `false` while every listed member is `true`.** The
    envelope counts scrapheaped member rows (a scrapheaped picture in a locked
    set still freezes its stack against being broken up); the per-member flag is
    the narrower "may this picture be put in a dedup stack", and a scrapheaped
    locked member freezes no live sibling. Drive the split/unstack affordance off
    the envelope, not off the members.
- **A candidate also carries `stackable` and `blocked_by_sets`.** `stackable:
  false` means a locked picture set freezes it, so it can be neither stacked nor
  metadata-unioned, and `blocked_by_sets` is `[{id, name}]` for the tooltip.
  Render it as excluded-by-the-server (the same treatment as a user exclusion,
  with a lock rather than an X) and act on the `stackable` ones only.
  `cover_picture_id` is already moved onto a stackable member.
- **A group with fewer than two stackable members is withheld entirely** (owner
  call, 2026-07-30). It poses no stackable decision, so it is not served, not
  counted in `total`, not counted by `POST /dedup/counts` (`unresolved_groups`
  and `by_tier`), and not planned into `POST /dedup/auto-stack` or its dry run.
  One rule, every surface, so the badge can never disagree with the list. The
  filter is **SQL inside the group predicate**, not a post-filter on the page:
  dropping rows after the `LIMIT` would shrink pages and desynchronise the
  cursor. Groups that keep two or more stackable members are still served whole,
  frozen members included and marked. Nothing is deleted: the group row survives
  and unlocking the set brings it straight back with no rescan.
- **A fully collapsed group is withheld the same way** (design D1): a group
  whose live members already sit in one and the same stack poses no decision, so
  it is not served, not counted, and, since 2026-08-01, **not planned into
  `POST /dedup/auto-stack` or its dry run either**. Auto-stack used a weaker
  filter that ignored stack units, so it reported far more "stacks to create"
  than the badge showed and would have re-covered stacks the user had already
  curated. The button's count and the run are now the same population.
- **A withheld group's signature stays valid.** A client holding a page from
  before the lock landed can still POST it, and that is the path the partial
  success and the `423` below exist for.
- **A why-pill is `{ text, against }`.** `against: true` is counter-evidence and
  renders as the red x; the client orders counter-evidence first, because a
  collapsed row only has room for two pills and the warning is the half that
  matters.
- **`scan` is `{ status, scanned_pictures, total_pictures, scanned_buckets,
  total_buckets, groups_found, error }`** and rides on the queue, the counts and
  the scan trigger, so any of the three can feed the progress banner. `status`
  is `idle | pending | running | complete | failed`. There is **no percentage
  and no time estimate**: the client derives the percentage from pictures, or
  from buckets when no picture total is known yet, and deliberately shows no
  "N min left" rather than inventing one.
- **Scope is `(scope_type, scope_id)`** with `scope_type` one of
  `global | project | set | character | folder`, published in
  `bounds.scope_types`. The whole vault is `global` and takes no id; every other
  type requires one, and a folder's id is its absolute path. `ScopeRequestModel`
  **forbids extra fields**, so a scope label or glyph is a 422: those are client
  presentation state and live in the URL query instead.
- **The tier gate is two booleans plus a threshold**, not a list of tier names:
  `near_enabled`, then `embedding_enabled` which requires it. `bounds` carries
  `tiers` (strongest first), `always_on_tiers`, `tier_requires`,
  `min_threshold`, `max_threshold` and `max_page_size`, so no bound is stated
  twice. A threshold below the floor is a **400, never a silent clamp**.
- **Counts take a LIST of scopes and always return the global badge**, so a
  context menu labelling three entries refreshes the sidebar in the same
  request and the two can never disagree. `by_tier` deliberately includes tiers
  that are switched off, so the tier menu can show what enabling one would add.
- **`batch_id`** is what makes a bulk auto-stack reverse with one `Ctrl+Z`, so
  it has to reach the client on the real run. `failures` names groups the run
  skipped: one unstackable group never aborts it, so a partial result is
  reported rather than hidden.

**Keep-separate records one operation, exactly like stack (owner override,
2026-07-30).** Until then it deliberately recorded nothing (the #644-era CSO
ruling: no reversible picture facet, and an empty row would still consume a
`Ctrl+Z`); the owner explicitly reversed that ruling. Every verdict — stack and
keep-separate — now records one operation (`dedup.stack` /
`dedup.keep_separate`), a client gesture id groups several into one undo, and
both flow through the standard `ActionReceipt` with nothing dedup-specific. The
keep-separate operation's before/after payloads are empty (no picture facet
changed); its undo reopens the verdict and returns the group to the queue via
the registered post-restore hook, and redo re-decides it. The explicit
**Reopen** ("Clear decision") action remains available as the non-undo way
back — and since the 2026-07-30 clear-decision fix it, too, records a
`dedup.reopen` operation whenever clearing a stacked verdict has to dissolve
the verdict's stack (the response carries the `batch_id`; undoing it restacks
and re-decides). A picture-neutral clear still records nothing.

**Paging is a keyset cursor; `offset` is the deprecated fallback.** The queue is
ordered by confidence descending while a scan is still inserting rows, so an
offset can re-serve a group the client already holds or skip one. `next_cursor`
over `(confidence DESC, signature)` removes that hazard instead of mitigating
it, so it is the **primary path**:

- A first page is always `offset=0`. A cursor is a position inside one ordering,
  and the policy, the threshold or the scope may have changed under it, so
  `useDedupStore.loadFirstPage` never reuses one.
- A response carrying a non-empty `next_cursor` puts that queue on the cursor
  path: `loadMore` sends `cursor` and **never sends `offset` alongside it**,
  because a server free to choose between the two could silently keep the weaker
  one. `next_cursor: null` (or absent) ends the cursor path.
- A cursor outranks the offset arithmetic in both directions. `total` is a live
  count under a running scan, so a served cursor means "more" even when the
  offset says the page was the last, and an **empty page ends the queue whatever
  the cursor says**, or a server that kept minting cursors past the end would
  loop the read-ahead.
- A cursor needs no correction when a verdict removes a row: it names a position
  in the ordering, not a count of rows before it. Only the offset is decremented
  in `removeGroup`.
- **The offset fallback stays seamless and keeps its mitigations.** A server that
  publishes no `next_cursor` is paged exactly as before, and either path can hand
  over to the other mid-queue. `loadMore` dedupes by signature on **both** paths
  and drops a re-seen group (a duplicated row could be resolved twice, and the
  second verdict would 400), and the offset still advances by the page's
  **served** length rather than its kept length.

**The sidebar badge is reconciled from the server after every verdict.** Both
verdict kinds now raise the standard `pictures_changed` event (added 2026-07-30
alongside the keep-separate op-logging; before that a keep-separate raised no
event at all), so a second tab has a refresh signal — but the event names
pictures, not dedup counts. `useDedupStore` therefore keeps the optimistic tick
for immediacy and fires `POST /dedup/counts` behind it (one scope, one COUNT,
not awaited so auto-advance is not held up), and `DuplicateQueue` refreshes the
counts on queue open even when it is already showing the requested scope. **Do
not treat a WebSocket event as the source of truth for a dedup count.**

**The auto-stack consent dialog reads `dry_run_summary`, not the envelope.** The
server derives `{ groups, groups_by_tier, pictures, covers_gaining_tags,
covers_gaining_score, covers_gaining_metadata }` from one read of one group list,
so the dialog's rows cannot disagree with each other across a landing scan. The
design's "covers gaining metadata from copies" row is
`covers_gaining_metadata`, and "Stacks to create" sums `groups_by_tier`. The
top-level `groups` / `pictures` are used only as a fallback for a server that
predates the summary. **`groups_by_tier` counts only what the run would act on**
(exact-only today, zero-filled for the rest), so it is *not* the queue's
remainder: the dialog's "Groups left in the queue to review" row stays on
`POST /dedup/counts` -> `by_tier`, which is the only call that knows it. Those
two rows therefore still come from different calls, deliberately, and could
disagree across a race.

**Punch-list for the backend lane** (fields a designed UI state wants and the
shipped surface does not provide; none is worked around silently):

1. **No thumbnail version on a candidate.** The grid busts its thumbnail cache
   with `?v=<version>`; a dedup candidate carries none, so the queue's
   thumbnails load without one. A thumbnail regenerated mid-triage therefore
   shows stale until a reload. Non-blocking, and a wrong decision is not
   possible from it.
2. ~~**No "covers gaining metadata" count on the auto-stack dry run.**~~
   **Resolved:** `dry_run_summary.covers_gaining_metadata`, and the row is back
   in the dialog.
3. **The queue remainder and the run's counts still come from two calls.**
   `dry_run_summary.groups_by_tier` covers the run, not the queue, so the
   "groups left in the queue" row is fed from `POST /dedup/counts` -> `by_tier`.
   Noted so the two are known to be able to disagree across a race.

### 2.2 The `Keep cover only` contract

Two routes on the **stacks** surface, not the dedup one, because the action is
about stacks however they were made: the queue is not the only way stacks get
created. Design: `docs/design/keep-cover-only.md`; backend: §22.12 of
`docs/backend_architecture.md`.

| Route | Purpose | Response |
|---|---|---|
| `POST /stacks/keep-cover-only/preview` | the confirm dialog's only source of truth | `KeepCoverOnlyPreviewResponse` |
| `POST /stacks/keep-cover-only` | collapse every eligible stack to its cover | `KeepCoverOnlyResponse` |

Both take the same body: `{ stack_ids?: int[], picture_ids?: int[], batch_id?:
string, keep_recipes?: bool, keep_every_ghost?: bool }`.

**`keep_recipes: true` is Keep recipes only (#1315)**, the same routes and the
same rules below with one more condition per copy: it moves only if it could be
made again after the Scrapheap is emptied. The rest stay live and are counted,
one reason each, in `pictures_staying_no_recipe`, `_model_missing`,
`_no_thumbnail` and `_ghost_not_kept` (per stack in `staying_picture_ids`). A
stack with no such copy is a fifth stack bucket,
`stacks_skipped_nothing_reproducible`, and `ghost_retention` says which retention
position the plan assumed. `keep_every_ghost: true` plans under `on`, and the
real call sets the server's `workflow_ghost_retention` to `on` before anything
moves, once the plan has something to move (a failure to save it raises with
nothing moved): the dialog re-previews when that box changes so the figure stays the
button's. The op type is `stack.keep_recipes_only`; the setting change is not
part of its undo. A copy that stays is left out of the metadata union. The
response carries `pictures_staying` (the same stacks the preview counts) and
`ghost_retention`.

**Send `expected_picture_ids` on the real call**, the preview's
`picture_ids_moving`. Keep recipes only is the first mode whose inputs move
while the dialog is open (a background finder writes a thumbnail, an import
covers an instance hash, a model reaches the shelf), so a plan that has **grown**
is a **409** that moves nothing: preview again and re-confirm. A plan that
shrank still runs. At least one id list must be non-empty (400 otherwise); they are
unioned, and **the unit is the stack**, any picture named pulls in its whole
stack, so a partial selection inside a stack collapses the whole stack. Loose
pictures name no stack and are ignored.

**2000 ids per request, counted before de-duplication and shared by the two
lists.** No list may carry more than 2000 entries and the two together may not
exceed 2000 (400 either way). Both halves of that used to be looser: the cap was
applied to the de-duplicated set, so a body of repeats passed, and it was applied
per list, so one request carried 4000.

**`batch_id` must be client-namespaced** (`cli-` plus 4-76 of `A-Z a-z 0-9 _ -`)
or the request is a 400, the same rule the `/dedup/*` routes and the
`X-Operation-Batch-Id` header enforce, from the same helper
(`pixlstash/utils/request_origin.py::require_client_batch_id`). Omit it and the
server mints an `srv-…`. It is the undo handle, so an unvalidated one lets a
caller graft its rows into another batch and reverse more than the user did.

Shapes and rules the frontend depends on:

- **Every figure in the dialog comes from the preview, and only from it.** The
  headline and the button label must render from the *same computed value*, not
  merely the same endpoint. While the preview is in flight or has failed, show
  an en dash and disable the confirm: never a zero, never a stale number.
- **The stack buckets are disjoint and sum to `stacks_selected`:**
  `stacks_eligible + stacks_skipped_locked + stacks_skipped_character_on_copy +
  stacks_skipped_single_member`. Do not derive one by subtracting the others;
  the server counts each directly and refuses to answer if the sum breaks.
  `unknown_stack_ids` is *outside* the arithmetic (those are not stacks).
- **The headline is `pictures_moving`**, computed over the eligible stacks only,
  so it never includes a skipped stack's members. `picture_ids_moving` is the id
  list behind it, for marking cards.
- **`covers_gaining_metadata` is a union, not a sum**, of
  `covers_gaining_tags` and `covers_gaining_score`: a cover can gain both.
- **`bytes_held_by_copies` is held, not freed.** Never render it as freed,
  reclaimed or saved space, and never as a figure block; it is a *sentence*
  about what could later be reclaimed. Nothing is freed until the Scrapheap is
  emptied, and `scrapheap_retention_days: null` means **never** (the default on
  a fresh install), so the retention copy must branch on this value rather than
  hardcode "30 days". `originals_deleted_from_disk` is always `0` and should be
  stated out loud, exactly as the sibling delete-forever dialog states its own
  zero.
- **`reference_folder_pictures_moving` is a SUBSET of `pictures_moving`,** not a
  fourth bucket: those rows move like any other, but their files are
  user-managed and are not touched.
- **`stacks[]` carries one row per selected stack**, eligible and skipped alike,
  each with `stack_id`, `cover_picture_id`, `member_count`,
  `copy_picture_ids` (empty when skipped), `eligible`, `skip_reason`,
  `locked_sets` and `lost_characters`. Rows and headline come from the same
  read, which is the property the auto-stack dialog lacked when it reported "62
  stacks to create" for work that would create 3.
- **`skip_reason` is a closed vocabulary:** `set_locked` (a live member is
  frozen by a locked picture set: the **whole** stack is refused, with
  `locked_sets` naming what to unlock), `character_only_on_copy` (a character
  link sits only on a copy, with `lost_characters` naming it), `single_member`.
  Menu state follows the shipped `Delete` item: disabled with the lock reason
  only when **every** selected stack is locked; otherwise enabled, with the
  dialog reporting the skips.
- **The mutation's response mirrors the preview** field-for-field where they
  overlap (`stacks_collapsed` == `stacks_eligible`, `pictures_moved` ==
  `pictures_moving`, and so on), plus `tags_added`, `scores_lifted` and
  `batch_id`. The skipped lists come back as **rows**, not counts, so the
  receipt's second sentence can name what was skipped.
- **`batch_id` is the undo handle** for `POST /operations/batches/{batch_id}/undo`;
  the whole call is one `stack.keep_cover_only` operation, so one `Ctrl+Z`
  reverses every stack it collapsed. It is `null` when nothing was collapsed.
- **No `confirm_token` and no type-to-confirm.** Those are reserved for
  destroying an on-disk original. Cancel is focused by default and plain `Enter`
  does not accept, deliberately inverting the app's dialog convention because
  users arrive with `Enter` under their finger from the queue's verdict keys.

**The collapse announces two things, and the second one was missing** (fixed
2026-08-02). The copies leave, and the covers' **stack membership** changes: a
cover that led a stack of five now leads nothing live, and a card renders that
number as its stack badge.

- The copies are `removed`, the covers are `updated`. **Never merged**: telling
  the grid a scrapheaped picture was merely updated leaves a 404-clickable card.
- The covers' announcement is **unconditional** (gated only on something having
  moved) and carries `fields: ["stack_count"]`. It used to be gated on
  `tags_added or scores_lifted`, which tests the wrong property: a collapse
  whose metadata union found nothing new said nothing at all about the cover, so
  every view went on drawing a stack of five around a picture that was alone.
- The metadata union keeps its **own** `updated` event, with no `fields`,
  emitted only when it did something. Two events, because narrowing the union's
  announcement to `stack_count` would tell a client sorting by score that the
  change cannot affect its order, which is false.
- **`stack_count` is the field name because it is the derived, listing-only
  value the client re-reads.** The server computes it per stack over LIVE
  members in the `fields=grid` projection (`_enrich_stack_counts`) and
  `GET /pictures/{id}/metadata` does not carry it at all, so the per-card
  metadata refresh the SPA uses for every other `updated` event cannot repair a
  badge. See §8.2 for the branch that consumes it.
- **The undo and the redo announce it back**, from
  `operation_log_service._emit`. The surviving members carry no facet diff and
  are therefore not even in the operation's picture list, so they are resolved
  separately (`lifecycle["stack_siblings"]`, computed in the restore's own
  session after `delete_emptied_stacks`) and get the same
  `fields: ["stack_count"]` announcement. That rule is general, not
  keep-cover-only's: any lifecycle move changes the live count of every stack it
  touched.

**The frontend consumer (shipped).** `api/stacks.js` owns both URLs
(`previewKeepCoverOnly` / `keepCoverOnly`); the copy and the two selection
computations are pure functions in `utils/keepCoverOnly.js`;
`KeepCoverOnlyDialog.vue` renders the consent and `ImageGrid.vue` owns the
preview, the run and the ghosting. Five points where the wiring is load-bearing:

- **One computed, two renderings.** `picturesMoving` is `null` until the preview
  lands and drives both the headline block and the confirm label, so the two
  cannot describe different moments even in principle.
- **The confirm acts on the stacks the preview described**, frozen when the
  dialog opened (`keepCoverOnlyTargetStackIds`), never on the live selection
  re-read at confirm time.
- **The menu's stack count and its locked gate are client-side** and only decide
  what is *offered* (`selectedKeepCoverOnlyStacks` /
  `keepCoverOnlyLockReason`); the preview stays authoritative about what
  actually happens.
- **`stack.keep_cover_only` is registered** in `OP_ICONS` (`mdi-layers-minus`)
  and in `DESTRUCTIVE_RULES`, so the receipt inherits the 8s window. The skipped
  rows become the receipt's second sentence via
  `useOperationStore.noteNextReceipt(opType, note)`: the same pill as the move,
  never a competing notice.
- **The badge is reconciled off the WebSocket, in every tab including the acting
  one, never by a refetch.** `runKeepCoverOnly` deliberately does not call
  `debouncedFetchAllGridImages()`: a refetch rebuilds the grid without the
  scrapheaped copies and takes the ghosted tiles, and with them the one-click
  undo they advertise, off the screen. The `stack_count` announcement above
  drives `ImageGrid.refreshStackFacets`, which patches fields only. That is also
  why the acting tab's own echo is not suppressed for this field: it has no
  optimistic local copy of a count only the server can compute, and an undo has
  no local grid op at all.

### 2.2b One recipe read, and one graph read (v1.12, #1313)

Two routes, and the split is by **question**, not by caller:

| Route | Answers | Costs |
|---|---|---|
| `GET /comfyui/pictures/{id}/recipe` | **What the picture was made with** — prompts, models with strengths, settings, seed, the shelf rows, the resolution lock, `workflow_key`, `topology_hash` (**derived from the graph this answer shows**, not from the picture's stored column, which describes whichever chunk the extraction pass read). Reads the graph that *executed*; for a picture carrying only the editor `workflow` chunk it rebuilds that into one first (`converted_from_editor_graph: true`), and answers for A1111 pictures through their infotext. | one file read; a ComfyUI `/object_info` read when the pre-flight is asked for, **and for an editor-graph picture either way** (from a one-minute cache) |
| `GET /comfyui/pictures/{id}/workflow` | **The graph's bytes**, in the editor's format — what Copy, Download and paste-into-ComfyUI need. | one file read |

`?preflight=false` on the recipe read skips the ComfyUI round-trip. The
lightbox's Recipe tab asks that way, because it re-reads on every filmstrip step
and a round-trip per arrow-key is not affordable; the Remix dialog keeps the
default, because it is about to run the recipe and needs to know whether it can.
`preflight.checked` is then `false`, which already means *the question was not
asked* — never that the recipe passed. **The one exception is a picture whose
only graph is the editor `workflow` chunk**: rebuilding that needs
`/object_info`, so without it there is nothing to report at all rather than
merely nothing to judge, and the flag cannot switch the read off. The map is
reused for a minute, so the filmstrip still steps for the cost of one file
read — and the graph is still not *judged* against it, so `preflight.checked`
stays `false` on a request that asked for no check. A ComfyUI that cannot be
reached at all answers `reason: "comfyui_unreachable"` rather than
`"editor_graph"`: one is a machine to start and the other is a fact about the
file, and reporting the second for the first reads as permanent.

The tab fetches the graph route
separately and **only when the workflow box is opened**: the graph is the one
large thing here.

Field-level rules neither side may drift from:

1. **`seed_text` is what a client prints, never `seed`.** ComfyUI draws seeds up
   to `2**64 - 1`; a JavaScript `Number` loses digits above `2**53`, so
   rendering `seed` shows the wrong seed for about half of real ones. `seed`
   stays a number for the callers that had it.
2. **`model_slots[].model_id` / `.verified` and `inputs` are owner-only.** They
   name rows of the owner's shelf and *other* pictures' ids and content hashes;
   a picture-scoped token is refused those pictures on every other route, so it
   gets the filename and the strength — which are in the graph it can already
   read — and nothing about the library.
3. **`settings` is one dict, from `extract_recipe_extras`.** ComfyUI keys are
   `steps`, `cfg`, `guidance`, `sampler_name`, `scheduler`, `denoise`, `width`,
   `height`; an A1111 picture sends A1111's own names through the same field, so
   a client renders whatever keys arrive rather than branching on `source`.
   **The first node naming a field wins**, which is iteration order, not
   execution order: a graph that samples twice reports one pass's steps beside
   another's CFG, and the block must not be read as "the settings of the pass
   that made this picture".

**`/workflow` carries no recipe fields.** It briefly did, in this branch, before
B5 (#1397) landed the recipe read; serving the same facts twice from two
implementations is how two vocabularies drift permanently apart, so the recipe
half was moved onto `/recipe` and deleted here.

### 2.2c The `/models/workflow-sets` contract (#1438)

One route behind the model shelf's `Workflow set` axis, and the split of work
across the seam is the whole of the contract: **the server resolves the evidence
and groups nothing; the client folds.**

| Route | Answers | Costs |
|---|---|---|
| `GET /models/workflow-sets` | **Which shelf models a kept picture proves ran together** — one entry per *combination* (the exact model ids one or more recipes bound), with its recipe count, its picture count and up to three cover thumbnails as `{picture_id, version}`, plus `no_set`: the ids in none of those combinations, i.e. the models **no kept
picture in this library was made with** (engines excluded - see rule 1). | one pass over `workflow_recipe_asset`, plus the `GROUP BY workflow_structural_hash` and the `ROW_NUMBER()` cover window the shelf's `used by` counts and the workflows grid already run |

Rules neither side may drift from:

1. **Co-occurrence is evidence; its absence is not.** No combination is withheld
   for lacking a pairing, `no_set` is returned rather than dropped, and a member
   the evidence could only reach through a basename several shelf rows answer to
   carries `ambiguous: true` and is still listed. A client may not render a
   missing companion as incompatible, which is why the grid and the *Works with*
   dialog both close with that sentence in as many words.
2. **Membership is not stored and overlaps.** A model appears in every
   combination it has run in; nothing is written, no column on `model` names a
   set, and the answer is derived per request. A client must not cache it as a
   property of a row.
3. **`models` arrives in ONE order and the client reads its head.** Checkpoint,
   then `unknown`, then VAE, text encoder, adapter, engine. The head is the file
   the set is named after, which is how a Flux or Wan set with no `checkpoint`
   row gets a name without the client inventing a second fallback rule.
4. **The GROUPING is the client's, and the payload stays per-combination.** The
   server proposes no grouping at all: `setGroups` (`utils/workflowSets.js`) unions
   the combinations under each head to make the cards, while the combinations
   themselves stay in hand — `worksWith` needs them to answer a pairwise question
   exactly, since a union would report two VAEs as each other's companions on the
   strength of sharing a checkpoint, and each member's own recipe and picture
   counts come from the combinations that name it. **A client must not collapse
   this payload to unions on the way in.**
5. **Scoped to the ACTIVE library**, unlike `POST /models/companions`, which
   counts every recipe the hub holds. A delete warning must keep a file some
   other library needs; this grid is a picture of what the library in front of
   the reader has made, so a recipe with no kept picture here is not a set.
6. **A cover is two facts, not a path.** `{picture_id, version}`, and the client
   builds the URL with `pictureThumbnailUrl` (`api/pictures.js`). An `<img src>`
   never reaches the Axios interceptor, so a path served from here arrives with
   no `/api/v1` prefix and no share token appended, and the browser asks the page
   origin for a route it does not serve — every cover broken. It is also why this
   route spells no URL of its own beside `routes/workflows.py`'s; that one sends a
   path and pays for it with `workflowCoverUrl` on the client to put it right.
7. **Owner-only, and read once.** It sizes the whole vault one card at a time and
   names a picture per cover, so it is on the shelf's owner tier with no
   per-object scope to narrow it to. The client fetches it when something needs
   it and again after a scan, never on a filter tick.

### 2.3 The `/workflows` contract (v1.11)

The Workflows view (implementation plan §F1/§F2, plus the v1.12 card grid and
its writes). Seven GETs and nine mutators; running a workflow is the run route
further down this section. Forgetting ghosts is not here either: it is two
purges beside the retention setting,
`DELETE /server-config/ghost-retention/ghosts` and
`.../model-ghosts?expected=N`, whose counts `GET /server-config/ghost-retention`
returns as `picture_ghosts` and `model_ghosts` (Settings › Privacy,
`PrivacySection`). The client sends the model count it showed as `expected`,
and a `409` means the set changed and nothing was forgotten.

| Route | Purpose | Response |
|---|---|---|
| `GET /api/v1/workflows?include_hidden=&include_one_offs=` | The card grid, in cover-rank order, one card per stack | `{cards: [WorkflowCard], one_offs, hidden}`. The two flags are the Workflows Filters panel's *Show hidden workflows* and an unticked *Hide one-offs* (F7); both default false, so the grid a client asks nothing for is unchanged. **They widen what is LISTED, never what is counted**: `one_offs` and `hidden` are taken over the same sets whatever the flags say, so a client can label the checkbox that is letting them in. **The widening belongs here and not in the client** because the grouping runs over exactly the cards the grid lists — a hidden member let back in makes its stack two again, where a client-side filter would draw it beside a cover still declaring `stack_size: 1`. Each card also carries `ghosts` (picture ghosts this library holds for the card's own variants) and `model_ghosts` (VALUES its variants name that the shelf does not hold - a filename or a `*_sha256` digest, so a model missing under both spellings counts 2 and this is not a count of models), which is what the panel's Ghosts row asks about; both are per CARD, not per topology, because a topology can carry several cards and only one of them may hold the ghost. **Neither is a library total.** A picture ghost whose `structural_hash` is null, or whose variant belongs to no card, is attributed to nobody, so these can sum to less than the `picture_ghosts` figure Settings › Privacy shows; the card fields answer "which cards keep something", never "how many ghosts exist". `hidden` is on the card too. **On this route** it is true only for a card `include_hidden` let in, so a client that did not ask never sees it set — but one that did **must mark those cards**, or the checkbox silently mixes them into the grid they were deliberately kept out of. **On `GET /workflows/{workflow_key}` it is always the card's own state**, with no flag involved: the detail route opens a hidden card by design, which is the only way one can be unhidden. **`name` is never null**: the owner's name, else the workflow FILE that runs it without its extension, else `"<model>: <Type> + <post-processing>"` built here (`Krea 2: Text to Image + FaceDetailer`), else `"Untitled workflow"`. The built one takes the first of `BASE_MODEL_KINDS` — derived from `CHECKPOINT_WIDGETS`, so a VAE or a text encoder can never name a card — and names it the way the model shelf does (`model.display_name`, a hub-to-hub join) rather than by its filename stem, which is what turns `realvisxl` into `Krea 2`. `specials` carries the same post-processing the suffix spells, and **null there is not `[]`**: null means the card's document has not been read for it yet, `[]` means it has and the graph has none. Every slot in `models` and `loras` carries `title`, the shelf's name for that file (null where the shelf has not scanned it), and **a client shows `title` in preference to `name`** — the generated name row was built from it, so a chip reading `realvisxl.safetensors` under a row reading `Krea 2` is one model described twice, the pair that drifted in #1416. The suffix is on the generated name only, never appended to a name the owner chose or to a workflow file's. The character-LoRA half of #1454 is deliberately absent: a card does not know which character LoRA was used, and cannot. `type_label` is `type` as ComfyUI spells it (`txt2img` → `Text to Image`), served rather than mirrored so a card's name row and its type chip cannot say one fact in two vocabularies. `covers` are objects, not strings (#1465): `{url, picture_id, thumbnail_width, thumbnail_height, square_crop_x, square_crop_y, square_crop_side}`. `picture_id` is the picture the cover DRAWS, so a client can open it (#1455) without parsing the id back out of `url`, which is a path shape rather than an interface; it rides on the cover rather than in a parallel `cover_ids` list because two lists paired by position are two lists that can come apart. `url` is **API-relative** — an `<img src>` bypasses the client's interceptor, so a consumer prefixes the API base and appends the share token itself — and the rest is the stored face-weighted SQUARE rectangle within that bitmap, under the names `GET /pictures/thumbnails/batch` already uses, so `utils/squareCrop.js` reads a cover with no mapping layer. The crop fields are null until the picture has been processed, and a consumer must then fall back to plain `object-fit: cover` rather than inventing a framing from a missing number. **They are three independently nullable ints, not one optional block**: `render_thumbnail` does write all three together, but nothing in the schema enforces it, so a consumer decides on `square_crop_x`/`_y` and derives `side = min(width, height)` when only that one is missing — which is what `squareCropParams` does, and what the square-mode grid has always done |
| `GET /api/v1/workflows/{workflow_key}` | One card opened | `{card, notes, hidden, variants: [WorkflowVariant], pins}`. `pins` is `null` when nobody has pinned on the card (the client applies its own default pins) and `[]` when everything is unpinned; the two are different answers. A card's `models` and `loras` each carry `slot_label`, the address `PUT /workflows/{key}/slots` marks |
| `GET /api/v1/workflows/{workflow_key}/pictures?limit=` | Ids for one card's pictures, newest first | `[int]` |
| `GET /api/v1/workflows/recipes/{structural_hash}/graph` | One recipe's stored graph | `{structural_hash, document, runnable}` |

The writes (v1.12 B4), every one of them `OWNER_ONLY` and every one of them
raising a `workflows_changed` event (§8) on the way out:

| Route | Purpose | Body → Response |
|---|---|---|
| `PATCH /api/v1/workflows/{workflow_key}` | Name, notes, hidden | `{name?, notes?, hidden?}` → the opened card. Fields **not sent** stand; an explicit `null` name or notes clears it |
| `PUT /api/v1/workflows/{workflow_key}/slots` | Mark LoRA slots `structural` \| `recipe` | `{marks: {slot_label: mark}}` → `{key, moved: {old: [key, …]}}`. **Re-keys every card of the topology**: `key` is where the card the caller had went (the biggest successor of a split), and a key not in `moved` did not move. **A key in `moved` may list itself**, which is a card that both moved and did not: a variant whose stored document will not parse keeps the key it is on, so if a sibling moved, that card is still open at its own URL and still holds its name, its pins and its saved recipes. A client following the caller's card takes `key`; a client deciding a card is gone must check for its own key in the list rather than read every entry as a departure. A label the topology has no LoRA slot for is a 422 |
| `PUT /api/v1/workflows/{workflow_key}/defaults` | The card's parameter overrides, whole | `{defaults: [{slot_label, input_name, value}]}` → the opened card, the values back as `provenance: "edited"`. Stored as text, so `30` comes back `"30"` and `true` as `"true"` |
| `PUT /api/v1/workflows/{workflow_key}/pins` | The pinned parameters, whole | `{pins: [{slot_label, input_name}] \| null}` → the same. `[]` is everything unpinned, `null` forgets the choice |
| `PUT /api/v1/workflows/{workflow_key}/inputs` | The picture-input setup, whole, **per library** | `{inputs: [{slot_label, input_name, mode, pixel_sha?, picture_id?}]}` → the same, each pin as the `pixel_sha` stored. `mode: "fixed"` must carry a `pixel_sha` or a `picture_id` (422 otherwise); a `picture_id` is stored as that picture's content, and one that is not a kept picture is a 400; 503 when no library is open. **It replaces the whole set, so a client writes back only a set it has read**: every run pre-flight returns it as `RunGroup.picture_inputs` (#1457) |
| `POST /api/v1/workflows/{workflow_key}/unstack` | Take one card out of its stack | — → `{stack_id: null, keys}` |
| `POST /api/v1/workflows/stacks` | Stack cards together | `{keys}` (≥2) → `{stack_id, keys}`. Each key **expands to the stack it is already in**, so stacking two stacks merges them, and `keys[0]` stays the cover |
| `PUT /api/v1/workflows/stacks/{stack_id}/order` | Reorder, `keys[0]` the cover | `{keys}` (≥2) → `{stack_id, keys}`. Ordering an `auto:<core hash>` grouping is what materialises it |
| `POST /api/v1/workflows/stacks/{stack_id}/unstack` | Dissolve a whole stack | — → `{stack_id: null, keys}` |

Running a card (v1.12 B7), also `OWNER_ONLY` — which is **narrower** than the
`PICTURE_SCOPED` run routes in `comfyui.py`, because a card is a whole-library
identity rather than one caller's picture. Neither route raises
`workflows_changed`: a run makes a picture, and the card it ran is unchanged.

| Route | Purpose | Body → Response |
|---|---|---|
| `POST /api/v1/workflows/run/preflight` | What a run would do, doing none of it | The body below → `{ok, runs, groups: [RunGroup]}` |
| `POST /api/v1/workflows/run` | Run it | The same body → `{status, runs, groups, prompts: [{workflow_key, prompt_id}]}`; `status: "refused"` with `prompts: []` when nothing was submittable |

The body, identical on both: **exactly one source** — `picture_ids`,
`saved_recipe_id` or `workflow_key` (400 otherwise) — plus an optional `target`
workflow key that runs *that* card instead, which is how a stack's other member
is chosen. Then `prompt`, `negative`, `loras: [{node_id, field, sha256,
strength_model?, strength_clip?}]` (**one slot is a node AND a field**, so a
stacker's `lora_name_1` and `lora_name_2` are two slots), `values:
[{slot_label, input_name, value}]` addressed the way a card's defaults are,
`count`, `seed_mode: "new" | "keep" | "fixed"` with `seed`,
`destination: {set_id?, project_id?, character_id?}`, `inputs: [{slot_label,
input_name, picture_id}]`, `stack` and `allow_unchecked`.

**`inputs` fills the card's picture inputs (#1457), and is usually empty.**
The server answers each input in order: the body's entry (`picture_id: null`
means "the selection goes here"), a `fixed` pin whose picture is still kept, a
stored `selection` fed from `picture_ids`, and then — over the whole card —
**the one input still open when exactly one is, which the selection fills with
nothing in the body saying so.** A two-input card with a pinned reference
therefore runs on a selection with an empty `inputs`, and so does "Make more
like these". The body needs an entry only for a picture picked for this run, or
to say which input the selection feeds when two or more are open. An entry
naming an input no resolved card has is a 400; one naming a picture that is not
kept, or whose file is gone, is a 404. At most one entry may take the selection
(422). Pictures are uploaded into ComfyUI's input folder **only after every
refusal is decided**, once per distinct picture per request; the pre-flight
uploads nothing.

Each `RunGroup` carries `picture_inputs: [{slot_label, input_name, title, mode,
pixel_sha, picture_id, picture_missing, fill}]`: every picture input of the
card, enumerated from the graph with the stored setup over it. `fill` is how
this run answers it — `request`, `fixed`, `selection`, `graph` (open, and the
file the graph already names is on this ComfyUI, so it runs as authored) or
`null` (unfilled). `picture_missing` is a pin whose picture has gone: an empty
slot to choose again, not a separate refusal.

Seven rules the client must not re-derive:

1. **With several pictures and no `target`, the server groups them by each
   picture's recipe.** A selection spanning three cards is three groups, each
   carrying the pictures that chose it — not one run of the first card over all
   of them.
2. **`reasons` empty is the only thing that means "this would run".** Each
   entry is `{code, …payload}` from a closed set: `comfyui_not_configured`,
   `comfyui_unreachable`, `ui_format`, `missing_nodes: {nodes}`,
   `missing_models: {models: [{file, folder}]}`, `a1111`,
   `picture_input_unfilled: {inputs: [{slot_label, input_name, title}]}`,
   `no_lora_loader`, `pixlstash_nodes`, `no_save_node`, `no_runnable_source`.
   `picture_input_unfilled` replaced `fixed_input_deleted` in #1457 with the
   same payload shape plus each input's `title` (a slot label is a hash); a client that only knows the old code no longer
   recognises the refusal and must fall back to its generic sentence. It names
   the open inputs the graph cannot run on as they stand, and blocks its group,
   not the batch.
   A code and never a sentence: one batch mixes sources, and a panel grouping
   "these four are missing the same model" cannot do it from prose.
   **Two group fields are facts rather than refusals** and must not be read as
   reasons: `substitutions`, and `bypassed_loras: [{file, folder, node_id,
   class_type, field}]`. Both say what this run will do differently from what
   the graph says, on the pre-flight and on the run alike.
3. **A missing model blocks the whole batch**, mixed or not, and so does an
   unreachable ComfyUI. Every group's `runs` goes to zero and nothing is
   submitted — including the groups whose own `reasons` are empty.
   **A missing LoRA is the exception** (#1463). A LoRA is optional, so where
   its loader *can* be taken out of the graph — consumers rewired to its own
   inputs, ComfyUI's own bypass — it is, and then it is no longer missing from
   the graph at all: nothing blocks, `missing_models` does not mention it, and
   it is named in `bypassed_loras` instead. Everything else — a checkpoint, a
   VAE, a text encoder, a ControlNet — still blocks, because the graph cannot
   run without it. **Where the loader cannot be taken out, the LoRA keeps its
   refusal and is still a `missing_models` entry with folder `loras`**: a
   **stacker** whose other slots are filled (taking the node out would drop
   adapters that *are* installed), a loader nothing can be rewired around, and
   a reference this hub can no longer name (`(forgotten model)` — the file may
   be installed and only the name is lost). A LoRA the *request* asked to add
   is also never bypassed: the owner asked for that one by name.
   **`bypassed_loras` is only ever set on a group that is actually being
   submitted**, and is cleared again when a missing model elsewhere zeroes the
   batch — it says "the run goes ahead without this LoRA", which must not
   appear beside a refusal.
   **A bypassed run lands on its own card**, as a substituted one does: taking
   a node out changes the topology, so the pictures it produces carry the
   submitted graph and are filed under a different `workflow_key` than the card
   that was run.
4. **A new run is NOT stacked with the picture it came from** unless the body
   says `stack: true`. This is where it differs from the retired
   `POST /comfyui/run_recipe` (#1410), which stacked by default: that replayed
   one picture's own graph, so the output genuinely was another take of it,
   while this runs a card. With `stack: true` and a selection feeding an input,
   **each selected picture is its own source**: its outputs join its own stack,
   not the first picture's.
5. **`allow_unchecked` is the consent rule and it does something.** Without it
   an uninspectable ComfyUI (`comfyui_unreachable` / `comfyui_not_configured`)
   blocks the batch and nothing is submitted. With it the runs go ahead **and
   the reason is still reported**, because the fact stays true. Consent reaches
   no other code: a missing model is a fact that *was* established, so a
   consented batch missing one is still refused. It does not reach a `loras`
   entry either: a filename slot is resolved against what that ComfyUI lists,
   so with nothing to resolve against the run is a 400 rather than one that
   quietly keeps whatever LoRA the stored graph named. A digest slot needs no
   list and goes through.
6. **The two routes answer identically, including their errors.** A body that
   cannot be interpreted against this card is a `400`/`404`/`422` **on both** —
   two sources named, an unknown `saved_recipe_id`, a malformed key,
   `seed_mode: "fixed"` with no `seed`, `seed_mode: "keep"` on a source that
   keeps none, a LoRA addressed to a slot the graph has not, a picture input
   the card has not, a picture that is not kept, or the total runs over
   `MAX_RUNS_PER_REQUEST`. Everything else — including a LoRA that
   is on the shelf but not on this ComfyUI — is a reason code. A pre-flight
   that 400s where the run returns reasons would not be a dry run.

7. **A selection feeding an input is the run's repeat axis.** Such a group runs
   once per selected picture, times `count`, and `runs` (and the cap) count that
   product: 40 pictures at `count` 5 is 200 runs.

**Edited defaults are overrides applied at run time and never written back into
a graph.** The stored document is content-addressed, so rewriting it would
change the identity of the very card being run.

**`seed_mode: "keep"` needs a source that carries a seed.** Tiers 1 and 2 do;
tier 3 does not, because a stored instance document nulls its seeds by design,
so `keep` there is a 400 rather than `count` identical images at seed 0.

**A failure part way through the submissions answers `status: "partial"`** with
the prompt ids already queued. They are running in ComfyUI whatever the request
returns, and an id nobody was told about is a generation the owner cannot find,
cancel or attribute.

Two rules the writes add to the five below: **a stack left with one member
dissolves** (the row goes, and the card stands on its own), and a
`{stack_id}` is either a minted 32-hex id or `auto:` followed by a 64-hex core
hash — anything else is a 422.

The four file gestures (v1.12 B8), all `OWNER_ONLY`. Each one resolves the
card's graph the same three tiers the run does, so a card the library only
knows from its pictures exports and duplicates like any other:

| Route | Purpose | Response |
|---|---|---|
| `GET /api/v1/workflows/{workflow_key}/export` | The workflow as a file to give away | `{filename, workflow, removed: [string], source: "file" \| "picture" \| "instance"}` |
| `POST /api/v1/workflows/{workflow_key}/duplicate` | A second copy in the user's workflow folder | `201 {name, workflow_key}` |
| `POST /api/v1/workflows/{workflow_key}/insert-lora-loader` | A copy with a LoRA loader spliced in | `201 {name, workflow_key, node_id, class_type}` |
| `DELETE /api/v1/workflows/{workflow_key}` | Send the imported file to the trash | `{deleted, workflow_key}` |
| `GET /api/v1/recipes/{recipe_id}/export` | The saved recipe as a file | `{filename, recipe, shares: [string]}` |
| `GET /api/v1/recipes/used?workflow_key=…` | Every look this workflow's own pictures were made with, a saved recipe's included and flagged `saved`. `workflow_key` repeats for a selection of several and the answer is the union. **The Recipes tab's list, filled without anybody pressing Save** | `[{prompt, loras: [{filename}], pictures, cover_picture_id, saved}]` |

Five rules the client must not re-derive:

1. **The workflow export is scrubbed and the recipe export is not, and that is
   the whole difference between them.** The export blanks the prompt and
   caption targets **and every other widget a person could have written in**,
   nulls seeds, empties every LoRA slot the owner has not marked `structural` —
   **by filename and by digest** — strips `_meta` titles, blanks picture file
   names and anything in a widget named like a key or a password, resets output
   paths (`filename_prefix` names a folder on the owner's disk), drops
   `checkpoint_id` (a row id in this machine's database), and drops any model
   name the model shelf cannot vouch for along with the *folder* of the ones it
   can. A recipe
   *is* the prompt and the LoRA names, so its export withholds nothing and
   says so in `shares` instead — which is the list the export dialog puts in
   front of the owner before they agree to it.
2. **`removed` names categories, never values.** "prompts", "seeds", "LoRA
   slots that are part of the look", "node titles", "picture file names",
   "where the pictures were saved", "the folders your models are filed in",
   "model names this machine does not hold", "values in fields named like a key
   or a password". "node titles" appears only when a title is not ComfyUI's own
   default (the node's class name), so a category in this list always means
   something a person put there. A forgotten model name sitting in
   a LoRA widget reports as the **model** category, not the LoRA one: it is a
   forgotten model name, and calling it "part of the look" would tell the owner
   the opposite of what happened. A response repeating the prompt
   it withheld would be the leak the scrub exists to stop, so a client wanting
   to tell the owner what came out renders these strings and has nothing else
   to render.
3. **A model name the shelf does not hold does not travel, and that is
   stricter than the Ghosts filter on purpose.** Forgetting a model's name
   deletes its hub rows and rewrites no graph, so a picture's embedded
   metadata still names the forgotten LoRA in full and resolving a source from
   that picture would carry it straight back out. The export checks the shelf
   rather than the ghost list, so a name nobody on this machine holds is blank
   in the file — including a name the ghost list can no longer see because
   forgetting it is what removed it from there. **Prose is found by widget
   name across the whole graph**, never by asking which node the sampler
   reads: that question returns nothing for a hires-fix graph with two
   samplers, and names widgets (`text`) that an SDXL encoder (`text_g`,
   `text_l`) does not have. Three further rules catch prose no widget name
   announces: a raw-string primitive (`PrimitiveStringMultiline`, `String
   Literal`) handing its value into an encoder, the reducer's own backstop (a
   newline, or longer than a filename can be), and whitespace — a combo token
   ComfyUI would offer has none, and a filename is tested for first. What is
   left is a single word, on an unknown node, in a widget no rule names.
4. **Duplicate and Insert loader write a file and never change one.** Both
   land in the user's workflow folder under a free name (`… (copy).json`,
   `… (copy) (2).json`), both are **unscrubbed** — they stay on this machine
   and are meant to run — and both emit `workflows_changed` with
   `reason: "imported"`. Insert loader reaches the owner's ComfyUI for
   `object_info` (503 when it cannot) and answers 409, with the sentence, where
   the splice cannot be made honestly: no model source, several models or text
   encoders, a graph that already loads a LoRA PixlStash cannot swap. **It
   chooses no LoRA**: the loader lands at ComfyUI's own widget defaults, the
   way dropping the node in ComfyUI would leave it, so the client tells the
   owner to pick one rather than presenting the copy as ready to run.
5. **Delete is for an imported file only.** A card the library knows from its
   pictures has no file, and `DELETE` answers 409 telling the client to hide it
   instead — the card, its variants and its pictures always stay either way.
   The file goes to the system trash through the watched inbox, the same path
   `DELETE /comfyui/workflows/{name}` takes.

Five things the two sides have agreed and neither may drift from:

1. **The list is one row per topology, never per recipe.** ~192 rows against
   ~617 on the owner's library. The frontend never flattens the variants into
   the list, and the backend never returns them there.
2. **Both hashes are 64-character SHA-256 hex, checked and not trusted**, on
   every route. A malformed one is a 422 naming the parameter.

   A well-formed hash this machine has never filed is a **404 on the two routes
   that resolve a hub object** (`/variants` and `/recipes/{h}/graph`), because
   an empty 200 there would read as "this workflow has no variants" rather than
   "this machine does not have it". It is **200 with an empty list on
   `/pictures`**, and that is not an inconsistency: that route never opens the
   hub. It asks the vault which of *its* pictures carry the hash, and "none"
   is the true and complete answer for a topology this library never used —
   which is the same answer a topology the hub *does* know gets when its
   pictures are all in the Scrapheap. Making it 404 would buy a hub round trip
   per tile strip in order to distinguish two states the caller draws
   identically.
3. **`assets` is a SET and `adapter_slots` is a count, and they answer
   different questions.** A topology row's `assets` are the distinct files its
   variants name *between them*, de-duplicated server-side; `adapter_slots` is
   how many adapters **one run** loads. The frontend builds the row's only
   identifying line from the second, never from the length of the first — the
   owner's largest family is 159 character LoRAs in one slot, and a descriptor
   built from the names claims the graph loads all 159 at once. A **variant**
   carries no `adapter_slots` and needs none: every name on a recipe is a file
   that run actually loaded, so the client counts them there.

4. **An empty `assets` list is a state, not a missing field.** Forgetting a
   model's name is a row delete in the hub, so the graph stays and only the
   ability to say which model it was is gone. `forgotten_models` counts those
   (read off the document's unresolved asset references; a maximum over
   variants on a topology row), so the client says "3 models, names forgotten"
   (or "a, b, names forgotten", with no number beside a union of names) and
   keeps "no model names" for a graph that names none. `ghosts` (this
   library's picture ghosts) and `model_ghosts` (names for models not on the
   shelf) are what the toolbar's Ghosts toggle filters on, client-side.
5. **`runnable` is always `false`, and it is in the payload rather than in a
   comment.** The stored document has its parameters, seeds and prompts nulled
   and names its assets by an opaque reference — which is what lets a workflow
   outlive the pictures it made — so it describes the graph and will not open in
   ComfyUI. What ships as a download is
   `GET /workflows/{key}/export` above, which resolves a runnable graph and
   scrubs it; this route's document is for reading, not for opening.

Every route is `OWNER_ONLY`. That is a decision, not a default: the counts are
read across every non-deleted picture in the vault, so a scoped token holding
them would learn the size of the whole library one workflow at a time. There is
therefore no scoped/narrowed variant of these routes, and adding one means adding
a narrowing parameter and a policy to check it against.

**The cards (v1.12 B3) sit beside the topology list, not on top of it.** The
shipped Workflows shelf reads `GET /workflows` and keeps working until F1b
swaps the route; B9 then moves the grid onto `/workflows` itself. Five things
the two sides have agreed:

1. **The card's shape is `frontend/src/utils/workflowCard.js`, and the backend
   serves that document.** It was merged before this route existed and
   `frontend_architecture.md` promises it needs no mapping layer, so the field
   names are the ones written down there — `key`, `name`, `type`, `imported`,
   `models`, `loras` (each slot `{name, title, icon, base_model,
   base_model_folded, kind, quant, mark?, slot_label?}`), `differs_by`,
   `picture_count`, `rating`, `covers`,
   `stack_size`, `saved_recipe_count`, `defaults` — and `mark` carries B1's own
   `structural` | `recipe` vocabulary rather than a translation of it, which is
   how the solid/dashed meaning would get inverted. The route adds
   `topology_hash`, `variant_count`, `member_keys`, `members`, `stack_id`, `rank`,
   `last_used` and `cover_ids` beside them; a caller that only knows the
   document ignores those and still needs no mapping.

   **`cover_ids` pairs with `covers` by POSITION, and that is the contract.**
   Same order, same length, built from one candidate list. It exists because
   the Workflows grid opens the picture a cover draws (#1455) and the only
   other route to that id is to parse it out of the thumbnail URL — a path
   shape, not an interface. A client pairs the two before it drops an empty
   entry, or the strip opens the wrong picture from the right tile
   (`utils/workflowCard.js:coverPictures`); the server side is asserted by
   `tests/test_workflows_api.py::test_cover_ids_name_the_pictures_the_cover_strip_draws`
   and, where an owner-chosen cover rebuilds the strip, by the chosen-cover
   test beside it.

   **`name` is never null**, and that is part of the contract rather than a
   convenience. `workflow_attr.name` is written only on an explicit rename, so
   most cards have none — and the card's name row is its only identifying text
   while `InfoPopover` puts it straight into an `aria-label`, so a null renders
   an empty row and the label "About null". The server resolves it: the owner's
   name, else the workflow file that runs the card (without its extension),
   else the card described from what it loads and does — `Krea 2: Text to
   Image + FaceDetailer` — else `Untitled workflow`. The described one names
   the base model as the model shelf names it and falls back to the filename
   stem for a model this machine has never scanned; it is deliberately not
   unique, and the ⓘ panel carries what a shared name does not separate.

   **A slot's `name` is DERIVED, and `quant` is what was taken out of it.** The
   server serves `model_utils.derive_model_name(...)` — no folder, no
   extension, no quantization postfix — so a chip reads `t5xxl` rather than
   `t5xxl_fp8_e4m3fn.safetensors`, and the card's own generated `name` row
   (`_display_name`) loses the postfix with it. `null`, never `""`, for a slot
   whose name the recipe did not record; a name that is *nothing but* its
   quant falls back to the file's own string, the way a model shelf row does.
   `quant` is one canonical id (`bf16`, `fp8_e4m3`, `q4_k_m`, `int8`,
   `mixed`), null where nothing records it — the model shelf's own column
   where this machine holds the file, the filename postfix otherwise. **It is
   an id and not a label**: `FP8 E4M3` is display copy and lives in the client
   (`utils/modelShelf.quantBadge`), the way slot kinds already do. A client
   showing the name must show the badge beside it, or two quantization builds
   of one model read identically.

   `GET /pictures/{id}/recipe`'s `model_slots` carry the same `quant` under
   the same rules, so the overlay and the card agree; its `name` stays RAW
   there, because it is the model shelf lookup's own key and what the chip's
   hover text shows.
2. **A card is not a topology and not a variant.** `key` is the topology plus
   the non-LoRA models plus the LoRA slots marked *structural*
   (`services/workflow_identity.py`), so adding a character LoRA keeps the same
   card. In this API and in the code the `workflow_recipe` / `structural_hash`
   tier is a **variant**; a *saved recipe* is the look a person keeps, and B6's
   `saved_recipe` table keys those on the same card key, so
   `saved_recipe_count` is a real count rather than a placeholder.
3. **`rating` and `rank` are different numbers and neither substitutes for the
   other.** `rating` is the plain mean of the stars a card has and is `null`
   when it has none; `rank` is the Bayesian mean the grid is *ordered* by,
   smoothed towards the library's own mean rating, and is meaningless shown on
   its own. A card nobody rated has `rating: null` and a `rank` near the
   library average, which is the point of having both.

   **`last_used` (v1.12 F1a) is the third of these and is nobody else's job.**
   It is when a kept picture was last made by any variant of the card, in the
   same ISO spelling `GET /workflows` already serves, and `null` when the card
   has no kept pictures. The Workflows grid offers *Recently used* beside *Your
   ratings* and *Picture count*, and nothing already on the card carries it:
   `rank` is a rating and `covers` is an order, not a date. A client sorting by
   it puts `null` BELOW every dated card — reading "never" as a date is how a
   workflow with nothing to show would outrank every workflow made before 1970.
4. **The grid draws one card per stack.** `stack_size` ≥ 2 makes a card a
   stack; the card drawn is the cover, `member_keys` names the rest, and the
   cover's `differs_by` is the union over the members. The order is a manual
   assignment, then an unstacking, then the automatic group by `core_hash`; a
   stored member row is filed under the core hash it was written against, so a
   card that has since left its group simply is not found in it and takes
   cover-rank order like a newcomer.

   **Every member carries the stack, not only the cover.** A member opened on
   its own reports the same `stack_size` and `member_keys`, because it also
   carries the `differs_by` it earned against that cover — and `factChips`
   branches on `stack_size`, dropping the "differs by" label at 1 and rendering
   those chips as plain facts about a cover the payload would never name.
   Chips and size are therefore always consistent: a card outside a stack has
   `stack_size: 1` and no chips at all.

   **`members` names the stack without a read per member.** Every stacked
   card, on the grid and on the detail route, carries the whole stack in its
   order, itself included, as `{key, name, sets_apart, differs_by}`. Members
   of one stack usually get the same generated `name`, so `sets_apart` lists
   the models and structural LoRAs a member loads that some other member does
   not (shelf title, plus its quant), minus any its own `name` already says,
   and `differs_by` is its own chips against the cover. Recipe LoRAs are left
   out, since they vary inside one card.

   **`stack_id` (v1.12 F2) is what a client WRITES to the stack by.**
   `PUT /workflows/stacks/{stack_id}/order` and
   `POST /workflows/stacks/{stack_id}/unstack` take either a stored stack's id
   or `auto:<core hash>` for an automatic grouping nobody has ordered yet, and
   **nothing else on the card derives it**: `topology_hash` is not it, a core
   hash being the topology with the recipe LoRAs taken out. Without the field a
   client could draw a stack and not reorder one, which is exactly the state
   F1a shipped in. Ordering an automatic group materialises its row and the
   `auto:` id still answers, so an id read before the write is good after it.

   **It is null in three cases, and a client must not tell them apart by
   guessing.** A card in no stack has none. Nor does one whose group falls
   below two once the dropped cards are taken — it is drawn standing alone, so
   `stack_size: 1` beside a non-null id is a state this field never answers
   with, whichever route serves the card. So does a card in a stack **this
   listing drew only part of** — because `cards` drops hidden cards and
   one-offs *before* grouping (point 4 above), while the order route validates
   against the hub's membership, which still counts them. A client ordering the
   members it was given would be refused with `keys must name every card in the
   stack, and no other`, naming keys it was never told existed. Serving no id
   is the honest answer: the write cannot be formed, so it is not offered. The
   consequence is worth stating plainly — **hiding one member of a stack takes
   the whole stack's reorder away** until it is unhidden, because the hub's
   membership rows are deliberately left standing when a card is hidden.
5. **Nothing is precomputed, and `cards` is not everything.** The grid is three
   vault queries in one session — one `GROUP BY workflow_structural_hash`, one
   `ROW_NUMBER()` window and one `GROUP BY workflow_key` over the saved recipes,
   plus a fourth only when the owner has chosen a cover — joined in memory to
   the hub's card rows, with no aggregate table, so no client may assume a
   figure is stable across a rating or an import. Hidden cards and one-offs are
   excluded and returned as the counts `hidden` and `one_offs`; both still open
   by key on the detail route, because hiding is a decision about the grid
   rather than a deletion.

   **A one-off is all four of**: fewer than three pictures, never rated, never
   imported as a file, and with no saved recipe on it. The fourth clause is
   B6's: saving a look is the plainest statement that somebody means to run a
   workflow again, so a card carrying one is never folded into the count.

   **"Imported" means imported by hand** (#1440): a file a pull from ComfyUI
   wrote (`workflow_pulled_file`) does not count, because a pull
   brings a whole install's experiments in one gesture and exempting them all
   would bury the grid. A file the owner dropped in, or one a pull only
   matched, still takes its card out. The card's wire `imported` keeps meaning
   "has a file"; the narrower test is server-side (`Card.hand_imported`).

6. **A card can have no variant at all, and `variant_count: 0` is how a client
   knows (#1466).** ComfyUI saves in *editor* format unless somebody
   deliberately exports the API one, and an editor-format file names its widget
   values by position: filing it writes a topology and no recipe, so its card
   key is the topology's alone and no `workflow_variant` row carries it. Such a
   card is a stored workflow file and nothing else — no pictures, no cached
   slot list, and a null `core_hash`, so it stacks with nothing.

   **Its `models` and `loras` are recovered from the file, not read off a
   recipe**, and they carry the same fields as any other card's: `name` is the
   graph's own string put through `derive_model_name` (see §2.3's slot note),
   `title`, `icon` and the two `base_model` spellings are
   the model shelf's where the shelf holds the file, and `kind` is the loader's real slot
   (`unet` for a Flux graph, never `checkpoint` by default). `slot_label` is
   null on every one of them, because a slot label is an address inside a
   stored topology and this card has none — so nothing here can be the target
   of `PUT /workflows/{key}/slots`. A LoRA recovered this way is `structural`:
   it is in the file, which is what the mark means.

   **An empty row is "not read", never "has none" — and that is per ROW.**
   The recovery finds loaders by class, over `MODEL_FILENAME_FIELDS` (through
   `model_filename_fields`, which also reads a ComfyUI-MultiGPU wrapper as the
   loader it wraps), and no
   list of classes is every loader there is: a graph can have its LoRAs
   recovered and its base model missed, so a non-empty `models` does not mean
   the card was read either. A client renders any empty row on a
   `variant_count: 0` card as unread; `checkpointUnread` and `lorasUnread` in
   `utils/workflowCard.js` are the shipped reading of it.

   `icon`, `base_model` and `base_model_folded` are served on **every** card's
   slots, not only
   these: they are what the model shelf draws a model with, and a card that has
   to draw itself out of its models rather than its pictures would otherwise
   need a second request per model to do it.

**The file-keyed workflow routes are retired (#1410).** `GET` / `PUT
/api/v1/comfyui/workflows/{workflow_name}/inputs` (#1305), `GET .../parameters`
and `PUT .../pins` (#1306), `POST .../run` (#1307), and `POST
/api/v1/comfyui/run_i2i`, `run_t2i` and `run_recipe` are gone. They addressed a
workflow by **file name**; a card is content-addressed and carries its own
defaults, pins, inputs and slots, so the contract for all of it is the card
routes above plus `POST /api/v1/workflows/run` (§2.2 and §11.2). The hub's
file-keyed `workflow_picture_input` and `workflow_parameter_pins` tables stop
being read; the hub is append-only, so they are not dropped.

**Where a LoRA loader would go (#1376)** is the one ComfyUI-file read that
survives beside the list and the import.
`GET /api/v1/comfyui/workflows/{workflow_name}/lora-insertion` (`OWNER_ONLY`,
it asks the owner's ComfyUI) answers `{workflow, has_lora_loader, plan,
reason}`, `plan` being `{model: {node_id, class_type, output}, clip: … | null,
rewires: [{node_id, class_type, field, type}], pixlstash_loader}` and `null`
with a `reason` when no loader can go in (several models or text encoders, a
second model chain of another kind, a node already loading a LoRA some way of
its own, a CLIP source that reads the model, no model, a node this ComfyUI
lacks or that does not say what it hands on, ComfyUI unreachable).
`has_lora_loader` is `null` for a UI-format file, which may carry a loader
nobody can read. `pixlstash_loader` says the digest loader could be the one
inserted, which leaves the outputs unreplayable by a later replay of the same
recipe. `GET /api/v1/comfyui/pictures/{id}/recipe` carries the same `{plan,
reason}` as `lora_insertion` when its `lora_slots` is empty. A run recomputes
the plan rather than trusting one sent back, and the loader is the run's, never
written into the stored file.

**The list's LoRA slots carry no `value`** (#1310). Each row of
`GET /api/v1/comfyui/workflows` reports `lora_slots` as `{node_id, class_type,
field, by}`, `by` being `filename` for a core loader and `digest` for a
ComfyUI-PixlStash one; a stacker's slots share a `node_id` and differ by
`field`, and an empty list means the graph has no loader. That route is
`ANY_TOKEN` and deliberately readable by share-link tokens, and a slot's value
is a LoRA filename or digest - the owner's model inventory, which `/models/`
and `/adapters/` keep from those tokens. The owner-only card detail read and
the picture-scoped `GET /api/v1/comfyui/pictures/{id}/recipe` carry the values.

`GET /api/v1/comfyui/workflows` carries `has_selection_input` and `runnable`
(a save node, in API format). The selection path offers a runnable workflow
where `has_selection_input` is true, and the toolbar one where it is false.

**The pictures a workflow made (v1.12 B5).** `GET /api/v1/pictures` takes
`workflow_key=<card>` and `workflow_stack=<stored stack id, or the core hash of an automatic grouping>`, which is
how a card or a stack opens onto its own grid without a route of its own: the
server resolves the card to the variants that made its pictures and matches
`picture.workflow_structural_hash` against them, so the client sends the key it
was given and nothing else. Both narrow like every other filter on that route -
given together they intersect, and they combine with tags, scores and the rest.
**A card no picture was made with answers with an empty grid, never the whole
library**; a client that treats "no results" as "filter ignored" would be
reading it backwards. The key itself comes from a card read, or from
`GET /api/v1/comfyui/pictures/{id}/recipe` (§11.2), which reports the card the
picture's own variant is on.

The shipped consumer is F7's *Show all N pictures*: the picture figure in the
Workflow tab's header and in a card's ⓘ sends `workflow_key` and lands on the
library with a removable **Workflow** chip. It sends the KEY and not the stack
even for a stack's cover, because the figure it is on is that card's own
picture count — a card carries its own pictures and never its members' — so a
stack filter would answer with more pictures than the link offered.

---

### 2.3 The `/libraries` contract (v1.11)

Six routes, and the split between them is a locality split rather than a
read/write one.

| Route | Tier | Shape |
|---|---|---|
| `GET /libraries` | `owner_only` | `{ libraries[], can_manage, in_docker, cli_hint }` |
| `GET /libraries/inspect?path=` | `local_owner_only` | one verdict (below) |
| `POST /libraries` | `local_owner_only` | `{ path, name? }` → the library, `201` |
| `PATCH /libraries/{library_uuid}` | `local_owner_only` | `{ name }` → the library |
| `DELETE /libraries/{library_uuid}` | `local_owner_only` | `{ status, library, inert_share_links }` |
| `POST /libraries/active` | `local_owner_only` | `{ uuid }` → the library |

**`can_manage` is the single gate the frontend reads.** The listing is
`owner_only` so the Settings tab renders for any owner; every management verb is
`local_owner_only` because four of the five take or write a host path and the
other two exercise authority over other principals' state. Rather than have each
control guess, `GET /libraries` answers `can_manage` from the same predicate the
authz gate applies, and `LibrariesSection` hides the whole management surface —
the Add button and the per-row `⋯` menu — when it is false. A remote session is
given a visible reason instead of controls that each fail.

**`inspect` returns one of five verdicts**, and the client branches only on
`can_add`:

| `verdict` | `can_add` | Means |
|---|---|---|
| `attached` | `false` | This exact folder is a registered library (`library` names it). `picture_count` is still what is on disk, indexed or not: a desktop first run creates the vault in a folder that may already hold pictures, and the empty library asks this to know whether to offer bringing them in |
| `overlaps` | `false` | A registered library contains it, or it contains one (`library` names it); or it overlaps a watch or reference folder of any registered library (`library` is `null`, `detail` names it) |
| `vault` | `true` | A vault nothing is using — `POST /libraries` attaches it |
| `pictures` | `true` | Pictures, no vault — `POST /libraries` starts a library over them |
| `empty` | `true` | Neither — `POST /libraries` starts a fresh one |

`headline` and `detail` are written server-side and rendered verbatim, so the
sentence naming the library that covers a folder exists once, where the rule
lives; only the button label is the client's. `picture_count_capped` is `true`
when the recursive count stopped at its entry cap, so `picture_count` is a floor
and the copy says "at least".

**The verdict is advisory.** `POST /libraries` re-inspects the path itself and
answers `409` with the same sentence if the folder became covered in between, so
a client cannot skip the rule by not asking. It requires the folder to exist
(`404` otherwise) and creates no directory: the picker makes one with `POST
/filesystem/folders`, which it already used.

Both path-taking routes answer `400` for a relative path or one resolving into a
blocklisted system directory (resolved **then** checked, so a symlink cannot
smuggle one past), and `403` for a path outside `filesystem_roots` when that is
configured. `POST` also accepts an optional `name`; without one the server uses
the folder's own, and **the picker sends one** — library names are unique among
attached libraries, so two folders both called `2024` would otherwise be
unaddable from the dialog.

**`DELETE` removes no file.** The registry clears the attached flag and keeps
the row, so the `inert_share_links` it reports stop working rather than being
revoked, and adding the same folder again revives the row — same uuid, same
tokens live again. The active library is refused (`409`); switch away first.

`PATCH` and `DELETE` take the **uuid only**, and only of an **attached**
library. The registry's `get` also accepts a row id and a name, which is right
for a CLI a person types at; over HTTP the handlers resolve through `by_uuid`,
because a client left open across a detach and attach would name a different
library by row id. `by_uuid` does return detached rows — that is how a uuid stays
meaningful for the tokens stamped with it — so the handlers filter them: a
library already forgotten is a `404`, not a second `200`.

`DELETE` answers `503` while a library switch is in flight. The other three do
not: these routes are hub-only and deliberately keep answering when no vault is
open, which is the state an owner recovers from. Detach is the exception because
it reads which library is active, and mid-swap that is the one thing moving.

---


## 3. API Client ([apiClient.js](../frontend/src/utils/apiClient.js))

Single shared **axios** instance with:

| Setting | Value | Rationale |
|---------|-------|-----------|
| `baseURL` | derived from `window.location` (or `VITE_BACKEND_URL`) | Same-origin assumption |
| `withCredentials: true` | always on | Required so the browser sends the JWT cookie |
| `timeout` | 60 000 ms | Many endpoints are slow (import, plugin runs) |
| Default `Content-Type` | `application/json` | Overridden for `multipart/form-data` uploads |

### Request interceptor

1. Skip absolute URLs to other origins (avoids leaking the share token to third-party hosts such as a local ComfyUI).
2. If a share token is active, inject `?token=<token>` as a query param.
3. On **mutating** requests (`POST`/`PUT`/`PATCH`/`DELETE`) inject the per-tab **`X-Client-Id`** header (the same-origin guard above applies, so it is never leaked off-origin). See §8.1.
4. Prepend `/api/v1` to any relative URL that doesn't already start with it.

### Response interceptor

On `401 Unauthorized`, the client calls `logout()` automatically — **except**:
- The probe endpoint `/users/me/auth` (used to test credentials without side-effects).
- Requests made under a share token (a 401 just means that endpoint is outside the token's scope).

All frontend code **must** route HTTP traffic through this client; bypassing it skips auth, share-token injection, and 401 handling. The only legitimate bypass is direct `<img src>`. Such a URL **must** be built from `API_BASE_URL`, since the `/api/v1` prefix is added by the request interceptor that only Axios requests reach; without it the request lands on the SPA fallback, which answers 200 with HTML rather than an error. It also passes through `appendShareToken()` wherever the resource is reachable under a share token — which is most of them, but not the shelf's own (a model icon is `OWNER_ONLY`, a training-run sample `LOCAL_OWNER_ONLY`), where a share token could never resolve anyway.

---

## 4. Authentication & Session

### Authentication modes

1. **Cookie session** (browser SPA): `POST /api/v1/login` with `{username, password}` returns a JWT in an **HttpOnly cookie**. The browser sends it automatically thanks to `withCredentials: true`.
2. **Bearer token** (programmatic clients): a `UserToken` (long-lived API token) passed as `Authorization: Bearer <token>`.
3. **Share token** (public read-only): a scoped `UserToken` passed as `?token=<token>` query param. See §5.

### Session bootstrap

On app mount, the SPA calls:

- `GET /api/v1/login` — determines whether registration is needed.
- `GET /api/v1/check-session` — validates the current cookie; on `401`, the SPA renders the login screen.
- `GET /api/v1/users/me/config` (or equivalent) — fetches the user's settings (`sessionContext` ref).

### Logout

`POST /api/v1/logout` clears the session cookie; the SPA wipes `isAuthenticated` and `sessionContext`.

### Reactive state

`apiClient.js` exports reactive refs that the rest of the SPA reads:

| Export | Type | Meaning |
|--------|------|---------|
| `isAuthenticated` | `Ref<boolean>` | True after successful login or `check-session` |
| `sessionContext` | `Ref<object \| null>` | Current user/scope/limits |
| `isReadOnly` | `ComputedRef<boolean>` | True when `sessionContext.scope === 'READ'` |

Components must respect `isReadOnly` for any mutating UI (hide edit/delete affordances when true).

---

## 5. Share Tokens (Public Read-Only Access)

- Activated via `activateShareToken(token)` when the SPA detects a `?token=` query param at boot.
- Stored in module-scope (not persisted) — refreshing without the query param exits share mode.
- Injected automatically into:
  - Every same-origin axios request (request interceptor).
  - Every `<img src>` / `<video src>` URL built through `appendShareToken(url)`.
- A share token is a `UserToken` with `scope=READ` and an optional `resource_type`/`resource_id` (picture set, character, project). The backend enforces scope per request; the SPA hides all write affordances when `isReadOnly` is true.
- Backend never logs the token; frontend never sends it cross-origin.

---

## 6. CORS Policy

Configured in [server.py](../pixlstash/server.py) (`CORSMiddleware`):

- `allow_origin_regex` always permits **`localhost`**, **`127.0.0.1`**, and the host's detected **LAN IP**, on any port and over `http` or `https`. This lets the Vite dev server (default `:5173`) and other dev clients talk to the backend without manual configuration.
- Additional explicit origins can be added through the server config `cors_origins` list.
- `allow_credentials=True` — required because the SPA uses cookie auth.
- `allow_methods=["*"]`, `allow_headers=["*"]`.

**Rule**: any new dev environment must satisfy the regex above or be added to `cors_origins`, otherwise cookies will be dropped.

---

## 7. WebSocket Channels

Two endpoints, both under the API prefix:

| Endpoint | Used by | Purpose |
|----------|---------|---------|
| `GET /api/v1/ws/updates` | [App.vue](../frontend/src/App.vue) | Vault-wide events (pictures, tags, characters, plugin progress) |
| `GET /api/v1/ws/comfyui?clientId=…` | [ComfyUiRunner.vue](../frontend/src/components/io/ComfyUiRunner.vue) | ComfyUI workflow execution stream |

### Lifecycle (`/ws/updates`)

1. Frontend opens the socket after auth succeeds.
2. On `open`, the SPA sends a `set_filters` message with the current view filters (selected character, set(s), search query). The backend uses these to scope which events the client receives.
3. The server pushes JSON events as state changes occur.
4. On `close`, the SPA auto-reconnects after 2 s (`updatesReconnectTimer`).

### Filter message format

```json
{
  "type": "set_filters",
  "client_id": "<opaque per-tab uuid>",
  "selected_character": "<id|null>",
  "selected_set": "<id|null>",
  "selected_sets": ["<id>", ...],
  "search_query": "..."
}
```

When filters change in the UI, the SPA re-sends a `set_filters` message.

`client_id` carries the tab's `X-Client-Id` over the socket because browsers cannot set custom headers on a WebSocket handshake. The server stores it on the per-client record (capped at 200 chars, ignored if longer). It is **forward-looking only** — for v1 the frontend matches the echoed `origin_client_id` against its own id locally, so the server does not yet need it to route events. See §8.1.

---

## 8. Real-Time Event Contract

The backend's [EventType](../pixlstash/event_types.py) enum names are **not** sent verbatim. Wire payloads use **snake_case** `type` strings. Both sides must agree on these strings — they are the integration contract.

### Uniform event envelope

`_broadcast_ws_event` ([server.py](../pixlstash/server.py)) stamps **every** picture/mutation event with the same origin-aware envelope so the SPA can decide *who* caused a change and *what* changed, and drive the grid by intent instead of doing a full reload on every event:

| Field | Type | Description |
|---|---|---|
| `type` | string | Wire type. Picture/mutation events: `picture_imported` \| `pictures_changed` \| `tags_changed` \| `descriptions_changed` \| `characters_changed` \| `plugin_progress`. Snapshot/restore events (carry snapshot/restore info rather than `picture_ids`): `snapshot_created` \| `snapshot_deleted` \| `restore_started` \| `restore_completed` \| `restore_failed`. Machine/vault events (carry neither): `vram_oom` \| `external_moves_pending`. Library-object events (carry their own ids rather than `picture_ids`): `workflows_changed`. |
| `event` | string | Backend `EventType.name`; diagnostic only, not part of the behavioural contract. |
| `source` | `"ui"` \| `"external"` | Coarse origin class. `"ui"` = an attributable owner action through the SPA; `"external"` = work that originated outside the UI (watch/reference folders, external API writes, background ML finishers, externally-run ComfyUI). Defaults to `"external"`. |
| `origin_client_id` | `string` \| `null` | The `X-Client-Id` of the originating tab, or `null` for background/external work. **The primary signal** — a tab recognises the echo of its own change by matching this against its own id. |
| `picture_ids` | `number[]` | Affected picture ids. |
| `fields` | `string[]` (optional) | Columns that changed (e.g. `["smart_score"]`); drives the silent-vs-sort-changed decision. Omitted for edits that may affect any view (user edits, imports). Three values are **not** columns and name a routing class instead: `detections` (card content), `pixels` (the picture's own bytes were rewritten — see §8.3) and `stack_count` (the stack's live member count, derived by the listing endpoint and re-read by its own targeted call). See §8.2. |
| `change_kind` | `"added"` \| `"updated"` \| `"removed"` \| `"restored"` (optional) | Set at the emit site where cheap (`removed` on deletes is free; `added` is implicit for `picture_imported`). **Omitted entirely when unset** — the SPA infers `added` for `picture_imported` and falls back to `updated` otherwise. `"restored"` is a scrapheap comeback (undo of a move, or `POST /pictures/scrapheap/restore`): the card returns, but the picture is **not** new to the vault, so the sidebar must not raise its NEW marker for it. The value set is a closed allowlist on **both** ends — `WsBroadcasterMixin.CHANGE_KINDS` and `resolveChangeKind` — and each silently degrades an unknown kind (the backend drops the field, the SPA falls back to `updated`), so the two move together or not at all. |

Per-type payload specifics (all carry the envelope fields above):

| Wire `type` | Trigger | Type-specific fields | Frontend behaviour |
|-------------|---------|---------------|--------------------|
| `pictures_changed` | Picture metadata/score/quality updated | optional `fields: string[]` | Routed through the decision rule (§8.2). When `fields` is present and **none** of the named fields affect the SPA's current sort/filters (e.g. `["smart_score"]` under a date sort), a same-view change is applied silently or ignored. Omit `fields` for changes that may affect any view (user edits) so the SPA always reconciles. |
| `picture_imported` | New picture entered the vault (ComfyUI, watch folder, API) | — | Slick in-place insert for the initiating tab, targeted insert for a foreign owner tab, or the **"New pictures"** pill for external imports (§8.2). |
| `characters_changed` | Character created/updated/deleted or face reassigned | — | Refresh sidebar (character list) |
| `tags_changed` | Tags or tag predictions changed | `picture_ids: number[]` | Bump `wsTagUpdate` so affected grid cards re-render |
| `descriptions_changed` | Picture descriptions/captions changed | `picture_ids: number[]` | Refresh affected descriptions |
| `plugin_progress` | Image plugin run progress | `plugin`, `progress`, `total`, `picture_id` | Update `wsPluginProgress` for the plugin progress UI |
| `vram_oom` | A GPU task ran out of VRAM: emitted before each retry, then once more to close the sequence | `attempt` (the attempt this frame is about, 1-based), `max_attempts`, `gave_up`, `recovered`, `task_type` (diagnostic only) | One keyed notice (`vram-oom`), updated in place by the later frames. Exactly one closing frame: `recovered` (that attempt succeeded) or `gave_up` (the sequence ended without the work). **`gave_up` with `attempt < max_attempts` is an early stop** — the task died of something else, or the app is shutting down — and the SPA promises no later retry for it; only an exhausted sequence says the work will be tried again. The retry frames carry an explicit timeout longer than the backend's pause, or the card would expire between frames and stop coalescing. |
| `external_moves_pending` | A reference-folder scan queued one or more moves the owner made outside PixlStash for reconciliation (v1.11 Phase 5) | — (no count; the queue is reclassified live, so a number on the wire could already be stale) | Debounced (3s) re-fetch of `GET /moves/pending`, so a burst of scans settling around the same time re-fetches once |
| `workflows_changed` | A workflow card changed (v1.12 B4): a workflow file imported or dropped in the watched inbox, a card named/annotated/hidden, its parameter defaults, pins or picture inputs written, its LoRA slots re-marked, a stack written, or a saved recipe written | `keys: string[]` (the card keys touched; may be empty), `reason: "imported" \| "changed" \| "stacks" \| "recipes"` | **Nothing listens yet.** F1b (#1404) put the grid on `/workflows`, so the screen now exists, but it re-reads on its own gestures rather than on this event; live refresh is F2's (#1405). So this row is still the contract a client will be written against rather than behaviour that has shipped. When it is: re-fetch `GET /workflows` (or the one card, when `keys` names it). **Deliberately carries no card**: a card's counts, cover strip, rank and stack are computed per request across the whole vault, so anything on the wire would be re-read anyway. `reason` is a hint about what moved, not a contract — the value set is a closed allowlist on the backend (`WORKFLOW_CHANGE_REASONS`, module level in `ws/broadcaster.py`) that **degrades an unknown value to `changed`** rather than rejecting it, so a client must treat any value as "look again" |
| `snapshot_created` / `snapshot_deleted` | Vault snapshot created or deleted | snapshot info (id, kind, …) | Refresh the snapshots panel |
| `restore_started` / `restore_completed` / `restore_failed` | Vault restore lifecycle | restore info | Drive the restore progress/result UI |

> **`source` migration:** the import emit's legacy value `"user"` is migrated to `"ui"`. During the transition the frontend (`normaliseSource`) accepts **both** — the real signal is the `origin_client_id` match, so accepting the legacy value just over-notifies (safe). Drop the legacy acceptance once both ends have shipped.

**Rules for adding a new event:**
1. Add the enum to `event_types.py`.
2. Use a snake_case wire `type` and document it here.
3. Always include enough context (`picture_ids`, and `change_kind` where cheap) so the SPA can do targeted updates rather than full reloads.
4. For a `pictures_changed` event raised by background work that only touches non-visible/non-sortable columns (embeddings, scores), tag it with `fields` (pass `{"picture_ids": [...], "fields": [...]}` to `notify`) so the SPA can skip the refresh under unaffected sorts. Map the field in `App.vue#pictureChangeFieldAffectsView`.
5. Mutating in-request emits must pass `source`/`origin_client_id` (and `change_kind`) into the event `data` dict — see §8.1.
6. Handle it via `useGridRealtimeSync` (picture events) or the remaining `App.vue` branches (tags/descriptions/characters/plugin).

**Backend filtering**: the server uses the client's last `set_filters` to decide whether to push an event. Events outside the client's current view are dropped server-side to reduce noise. The stream is **owner-only** — scoped/READ tokens may connect but receive nothing.

### 8.1 Client id & origin attribution

Each browser **tab** generates one opaque id (`crypto.randomUUID()`), persisted in `sessionStorage` (survives reload; in-memory fallback in private mode). It is:

- stored in `useWsStore` and mirrored into `apiClient.js` module scope (to dodge Pinia-init timing);
- sent on **every mutating HTTP request** as the `X-Client-Id` header (≤200 chars — an oversized value is **dropped, not truncated**, so a crafted long value can never collide with a legitimate short one);
- sent over the socket via `set_filters.client_id` (§7), because browsers can't set headers on a WS handshake.

The backend's `OriginClientMiddleware` captures the header into `request.state.origin_client_id` (and a contextvar). Mutating handlers thread it into the event `data` dict so `_broadcast_ws_event` echoes it back as `origin_client_id`, letting the originating tab suppress the reload for its own optimistic op.

**Security:** `X-Client-Id` is attacker-controllable and is used **only** for echo-matching — **never** for authorization or scoping. It is length-capped and not logged at INFO. The WS stream stays owner-only. Signed off by the CSO when the origin-aware envelope shipped (PR #468).

### 8.2 Frontend decision rule

The picture-event policy lives in [`useGridRealtimeSync.js`](../frontend/src/composables/useGridRealtimeSync.js) (App.vue keeps only socket lifecycle). For each picture event, in order:

1. **Own-origin echo** (`origin_client_id === myClientId`) → **suppress** (the optimistic local op already applied it). **Exception:** an `updated` event whose `fields` include a *server-computed* sort field (`smart_score`, `character_likeness`) that is also the **active sort** → single-card `refreshSmartScoreForImage`/`refreshGridImage` reconcile, never a reload (the optimistic guess can diverge from server truth).
2. **Foreign owner UI** (`source: "ui"`, different origin) → targeted op: `added` → insert at sorted position + highlight; `updated` → `refreshGridImage`/reposition, gated by `pictureChangeAffectsView(fields)` (ignored when the changed fields don't affect the current view); `removed` → `removeImagesById`.
3. **External** (`source: "external"`) →
   - `added` → the primary-coloured **"New pictures"** pill (never auto-inserts under the user);
   - `removed` → removed **silently** in place (never leave a 404-clickable card);
   - `updated` with `pictureChangeAffectsView(fields) === true` (would reorder the grid) → the sibling **"Sort order changed externally — click to refresh"** pill, instead of reshuffling under the user;
   - `updated` with known fields that are **invisible** to the current sort/filter → **ignored** (e.g. a background `smart_score` recompute under a date sort) to avoid a per-card `/metadata` + thumbnail **refetch storm** for values that aren't even displayed.
4. **Unrecognised shape** (e.g. a bulk sort/filter-defining change) → a rare, **logged** full-reload fallback.

Two field classes are decided **before** the origin dispatch, because for both
of them the origin makes no difference to what has to happen:

- **Card-content fields** (`detections`, `pixels`) → never a pill and never a
  reshuffle. `detections` takes a targeted per-card `refreshGridImage`; `pixels`
  takes `applyRotatedCards` instead, and is the one card op that is **not**
  deferred under an open overlay. See §8.3.
- **Stack facets** (`stack_count`) → **one batched** `refreshStackFacets(ids)`
  read for the whole event, never a pill, never a reshuffle, and never the
  per-card path: `stack_count` is derived per stack by the listing endpoint and
  is absent from `GET /pictures/{id}/metadata`, so `refreshGridImage` cannot
  repair a stack badge. Uniform across origins for the same reason `restored`
  is: the acting tab has no optimistic local copy of a server-computed count,
  and an undo (Ctrl+Z, the toolbar, the lightbox) has no local grid op at all.

  There is no `MAX_TARGETED_UPDATE` escalation here, deliberately: one read is
  not a fetch storm, and the reload it would escalate to is precisely what must
  not happen while a ghost window is open.

Both require **every** named field to be in the class. Mixed fields fall through
to the ordinary dispatch, so a cover that also gained a score still gets the
sort treatment its own (separate) announcement carries.

### 8.3 `pixels` and `orientation`: the picture's own bytes

`fields: ["pixels"]` means the FILE was rewritten, so two things the client
holds are stale at once: the thumbnail URL and its cache token (which come from
`POST /pictures/thumbnails`, never from `GET /pictures/{id}/metadata`) and, for
a turn, the `orientation` every surface builds its display URL's `?v=o<n>` from.
A client told only `updated` re-reads metadata it already has and goes on
painting the picture it was already painting.

**Five producers stamp `pixels`, and only two of them are turns — those two
stamp `orientation` alongside it, which is how a client tells them apart:**

| Producer | Fields | A turn? |
|---|---|---|
| `POST /pictures/rotate` | `["orientation", "pixels"]` | yes |
| the operation-log restore behind undo/redo (`_emit`) | `["orientation", "pixels"]` for an orientation; `["pixels"]` for a location | orientation only |
| `ThumbnailGenerationTask` | `["pixels"]` | no — a regenerated bitmap, up to 64 ids per batch |
| `POST /pictures/layout/move-to-match` and `LayoutMoveTask` | `["file_path", "pixels"]` | no — the path moved |

The grid does not care which it was: the thumbnail is re-read either way, so it
keys its applier on `pixels` and uses `orientation` only to decide whether to
defer. The **lightbox** cares about nothing else — its `<img>` URL is built from
the picture id, the format and the orientation, so a regenerated thumbnail and a
moved file leave it untouched, while a turn moves the `?v=o<n>` AND invalidates
the boxes and the text drawn in the file's own coordinate space. Its
`wsOrientationUpdate` signal therefore fires on `orientation`, not on `pixels`.

**Naming the turn is what removed the guesswork.** An earlier revision of this
feature raised the overlay's signal on `pixels` and inferred the turn by
comparing the orientation before and after a metadata read. That was wrong three
ways: the read can be discarded by the shared request-id counter (leaving the
lightbox stale with no retry, i.e. #1419 again), navigating away and back
rebuilds the record under it, and a NULL orientation — every video, and any row
`MissingOrientationFinder` has not backfilled — read as "turned", so a background
batch re-read the boxes and cleared the viewer's word selection for a change that
turned nothing. Neither `fields` nor a client should have to guess this.

Because the signal is now precise, it is **id-gated** like `ocr_text` and unlike
the score and detection signals: those are ungated because two of their frames
can coalesce into one watcher flush, which cannot happen to a socket-driven ref
(one write per `ws.onmessage`, one macrotask each, Vue's pre-flush queue drained
on the microtask between).

**A TURN's applier runs under an open overlay**, unlike every other deferred op
in §9.1 of the frontend document: `applyRotatedCards` is fields-only — it writes
the shape and bitmap of cards already present and never inserts, removes or
reorders, a turned photo having nowhere to move to — so there is no
restructuring to keep off the frozen filmstrip, and deferring it only queued a
whole-grid refetch for overlay close. The exception is a grid list already
parked for close (`pendingGridImages`): that branch of `closeOverlay` assigns
wholesale and clears the deferral flags with it, so an in-place write made then
is discarded with nothing queued to repair it, and the turn defers after all.

A **non-turn** byte rewrite keeps the ordinary deferral. Its card still needs the
applier rather than a metadata refresh — the thumbnail URL is not on
`/pictures/{id}/metadata` — but it is background work arriving in a steady stream
for the length of an import, not a gesture waiting to land.

**`MAX_TARGETED_UPDATE` does not apply to the applier.** That cap is written for
the per-id `refreshGridImage` loop ("one /metadata + thumbnail fetch each"), and
the applier is one batched `POST /pictures/thumbnails` for the whole set.
Escalating it sent the tab that *issued* a 51–200 picture rotate
(`ROTATE_MAX_IDS` is 200) into a whole-library reload of a change it had already
applied optimistically.

**The overlay must survive that write.** `applyRotatedCards` replaces the
`allGridImages` array, and the lightbox re-seeds its open card from the sequence
frozen at open whenever that prop moves - so without care an undo turns the
picture and the grid's own repaint turns it straight back (#1419). Two things
stop it, both on the frontend: `fetchOverlayMetadata` patches that snapshot's
`orientation`, and the re-seed preserves `orientation` / `pixel_sha` for rows
the patch cannot reach, exactly as the metadata merge excepts the same two
fields from local-wins. See `frontend_architecture.md` §9.1.

**The grid is not the only destination.** `useUpdatesSocket` routes each `pictures_changed` frame to every store that holds a snapshot of a server read, and each destination owns its own decision, the grid's table above is *not* shared. The other subscriber is the **Duplicates queue** (`useDedupStore.applyPictureEvent`), whose rows are groups rather than cards:

- `removed` **with ids** → **surgical**. The named pictures are taken out of every loaded group (candidates, the group's `member_count`, and each deck's depth/`matched_picture_ids`/leader), then any group left spanning fewer than two stack units is removed, the client-side twin of `live_groups_filter`'s HAVING clauses. A full `loadFirstPage` is deliberately *not* used: the queue is windowed and keyset-paged, so rebuilding it throws a triage in progress back to row 1.
- `removed` **with no ids**, and `restored` → **not applied to the list**. A returning group lands at a position in the confidence ordering the client cannot compute (there is no per-signature read), and the queue has never been a live insert surface, a scan's new groups arrive by paging too. The badge, which refreshes on `useSidebarRefresh`'s own `pictures_changed` path, carries the change and the row returns with the next page. The one exception is an **empty window**: "nothing left to review" while the badge says otherwise is a lie, and there is nothing on screen to disturb, so the first page is reloaded.
- **Origin is not consulted.** Unlike the grid, this store never applies a scrapheap move optimistically (no queue action deletes a picture), so its own tab's echo is as new to it as another tab's.
- The **decided page** keeps its thinned rows and only loses their dead tiles, matching the server: the verdict already happened and "clear this decision" is the only route back to it.

**ComfyUI classification:** the **in-app** runner is **UI-initiated but async** — there is no optimistic client-side copy to suppress, because the generation completes server-side after the request returns. `routes/comfyui.py` therefore emits a **single** `picture_imported` with `source: "ui"` and **no origin echo** (`origin_client_id` omitted), so **every** owner tab — including the initiating one — performs a slick in-place insert via `handleForeignUi` rather than the originator suppressing its own echo. It does **not** fire a second `pictures_changed`/`CHANGED_PICTURES` broadcast; the field-scoped `Missing*Finder` events (smart_score/quality) emit their own targeted events later. Externally-run ComfyUI lands via the watch/reference finders, which stay `source: "external"`, origin `null` → the "New pictures" pill.

---

## 9. Image & Thumbnail Serving

Browser-native `<img>` tags **cannot** use the axios interceptor, so the integration relies on:

- **Cookie auth** (sent automatically by the browser on same-origin GETs).
- **Share-token injection** via `appendShareToken(url)` — every component that builds an image URL for direct browser fetch must wrap it.

### Endpoint patterns

| URL | Purpose |
|-----|---------|
| `GET /api/v1/pictures/thumbnails/{id}.webp` | Cached WebP thumbnail. Backend uses an async lock + LRU memory cache + on-disk `.pixlstash/` cache. |
| `POST /api/v1/pictures/thumbnails` | Batch thumbnail metadata (JSON). |
| `GET /api/v1/pictures/{id}.{ext}` | Original file (optionally watermarked). |

### Generated thumbnails: the URL is stable, so the *response* carries the freshness contract

`GET /characters/{id}/thumbnail` and `GET /picture_sets/{id}/thumbnail` serve a **generated** image from a server-side cache file whose bytes change under an unchanged URL (the face crop is rebuilt when a better picture wins **or when the user pins a different one** — `PATCH /characters/{id}` with `thumbnail_picture_id`, cleared with `null` — the set collage when its top members change). Two facts make this a trap:

- Starlette's `FileResponse` sets an `ETag` but **answers no conditional request** — its only conditional logic is `If-Range` (verified against starlette 1.3.1).
- With no `Cache-Control` at all, browsers fall back to **heuristic** caching, so a regenerated thumbnail can stay stale for an unbounded window with no revalidation.

The client used to paper over this with a per-request `?cb=<Date.now()>`, which guaranteed freshness by re-downloading every character thumbnail on every sidebar refresh — against a route whose picture lookup is already expensive (issue #651).

**Contract (both routes, via `pixlstash/utils/http_cache.py`):** the response carries `Cache-Control: private, no-cache` plus a weak `ETag`, and a matching `If-None-Match` is answered with a bodyless `304` that repeats the `ETag` and the policy. So the browser revalidates every time but transfers bytes only when they actually changed. **The frontend must therefore NOT cache-bust these URLs** — `SideBar.fetchCharacterThumbnail` calls `getCharacterThumbnail(id)` with no `cacheBuster`, and re-adding one would restore the download-per-refresh cost the header exists to remove.

Note the contrast with `/pictures/thumbnails/{id}.webp`, which is *content-addressed* (`?v=WxH` changes when the bitmap is regenerated) and may therefore be cached for a while: `private, max-age=3600, must-revalidate`. Stable-URL generated images get `no-cache`; version-tokened ones get a max-age.

### Watermarking

The decision to watermark is made server-side per request based on `User.embed_watermark` and the token's scope. The frontend does **not** need to know whether a given URL will be watermarked, but it must regenerate URLs (cache-bust) when watermark settings change.

---

## 10. File Uploads (Import)

- **Endpoint**: `POST /api/v1/pictures/import` (multipart/form-data).
- **Content**: image files or `.zip` archives (extracted server-side).
- **Deduplication**: server computes `pixel_sha` (SHA-256 of decoded pixels) and skips content it already has, **including content sitting in the Scrapheap**. See below.
- **Async**: the response includes a `task_id`. The frontend polls `GET /api/v1/pictures/import/status?task_id=…` for completion percentage.
- **Real-time**: as pictures are persisted, the backend also broadcasts `picture_imported` over the WebSocket carrying the uniform envelope (§8). The SPA distinguishes its **own** upload (drives a progress dialog) from a **foreign owner tab** (slick insert) and from **external** imports (the "New pictures" pill) via `source`/`origin_client_id`.

**Contract**: the SPA sets `isUploadInProgress` for the duration of its own upload so that incoming `picture_imported` events don't double-count.

### 10.1 Three outcomes, not two: the Scrapheap bucket

A file whose `pixel_sha` matches a **soft-deleted** picture is neither imported nor an ordinary duplicate.
Importing it again would put a second copy of every scrapheaped picture back on disk (a bulk "Keep cover only"
cleanup makes that a predictable way to undo the cleanup and double the bytes); restoring it automatically would
be the opposite surprise, because the user scrapheapped it deliberately. So the server reports it and the SPA
**offers** the restore.

Both status endpoints carry it, and the buckets are disjoint and sum to the total (never derived by subtraction,
so no summary line can overstate what happened):

| Endpoint | Fields |
|---|---|
| `GET /pictures/import/status?task_id=…` | `imported_count`, `duplicate_count`, `scrapheaped_count`, `scrapheaped_picture_ids[]`; per-file `results[].status` is `success` / `duplicate` / `scrapheaped` |
| `GET /pictures/import/staging/{id}/status` | the same three plus `failed_count` and `cancelled_count` |

**Both status endpoints are `OWNER_ONLY`** (corrected 2026-08-01; they were
`ANY_TOKEN`). The table above is the reason: `results[].picture_id`,
`results[].file` (the vault-relative filename) and `scrapheaped_picture_ids[]`
are per-object data about pictures anywhere in the vault, which is precisely
what `ANY_TOKEN` promises a route does not return. Neither task id nor
`staging_id` being unguessable changes that; a capability URL is not an access
policy. Every `POST` sibling that starts an import is already owner only, so no
caller that could have a task to poll loses access.

`scrapheaped_count` is per **file**; `scrapheaped_picture_ids` is per **picture**, so several incoming copies of one
scrapheaped picture name its id once. The SPA feeds those ids straight to the **shipped**
`POST /pictures/scrapheap/restore` (there is deliberately no second restore route, and therefore no new
`AccessPolicy` declaration): `ImageImporter.vue` raises one sticky notice whose action restores them and reports
`restored_count` honestly, since retention can sweep a match away between the import and the click. The restore
broadcasts `CHANGED_PICTURES` with `change_kind: "restored"` (§8), which the grid already consumes.


---

## 11. Long-Running Operations

Two complementary mechanisms; most workflows use both:

1. **Task-id polling** — for client-initiated operations with a clear end state (import, export, bulk score apply, plugin run on many pictures): the endpoint returns `{task_id}`; the SPA polls `…/status?task_id=…` until completion, then fetches the result (e.g. download the ZIP via `/pictures/export/download/{task_id}`).
2. **WebSocket events** — for backend-initiated state changes (watch folder ingest, background quality/tag/embedding work, plugin progress): the SPA refreshes affected views from events without polling.

**Rule of thumb**:
- If the user triggered it and expects a result file → polling.
- If it changes vault state that other clients also need to see → WebSocket event.
- For UX (e.g. plugin progress bar), emit both: polling for the initiator and WS broadcasting for everyone else.

### 11.1 Object detection (Segment) & bbox export

The **Segment** action runs Florence-2 object detection over the selected pictures and stores labelled boxes per picture (see [backend_architecture.md §6/§7](backend_architecture.md)). It follows the WebSocket-event branch of the rule above — it is a backend task, not a downloadable result.

- **Enqueue**: `POST /api/v1/pictures/detect` with body `{ "picture_ids": [int, …], "prompt": "optional phrase" }`. An empty/omitted `prompt` runs dense object detection; a non-empty phrase runs open-vocabulary grounding for that phrase. Scoped tokens have `picture_ids` filtered to their grant (deny-by-default; all-out-of-scope → 403). Returns `{ "status": "queued", "task_id", "picture_ids", "prompt" }`. Progress surfaces in the existing task-manager UI.
- **Completion**: the task fires a `pictures_changed` event (`{picture_ids, change_kind:"updated"}`) over the WebSocket; the SPA refreshes affected views.
- **Read**: `GET /api/v1/pictures/{id}/detections` returns a **bare JSON array** (object-scope enforced before any read):
  ```json
  [ { "id": 1, "picture_id": 42, "frame_index": 0, "detection_index": 0,
      "label": "dog", "bbox": [x1, y1, x2, y2], "score": null,
      "source": "florence2:od" } ]
  ```
  `bbox` is pixel `xyxy` in the **original** picture coordinate space (same convention as faces). `score` is `null` for Florence (it emits no per-box confidence). The overlay (`ImageOverlay.vue`) renders these as a toggleable layer next to the face-bbox layer.
- **Export sidecar** (`GET /api/v1/pictures/export?bbox_mode=…`, FULL exports only): writes a per-image `{stem}.json` into the ZIP. `bbox_mode=none` (default) writes nothing. Two formats:
  - `bbox_mode=coco-json` — a COCO-subset sidecar (pixel `xyxy`), written *alongside* the `.txt` caption. Boxes and `width`/`height` scale to match the exported image when a reduced `resolution` is selected.
    ```json
    {"image":"IMG_0001.jpg","width":1920,"height":1080,
     "schema":"pixlstash.detections/v1","bbox_format":"xyxy_px",
     "objects":[{"label":"dog","bbox":[x1,y1,x2,y2],"score":0.0}]}
    ```
  - `bbox_mode=ideogram-json` — an **Ideogram-4 structured-JSON caption** ([official schema](https://github.com/ideogram-oss/ideogram4/blob/main/docs/prompting.md)): this `{stem}.json` *is* the caption ai-toolkit consumes (set `caption_ext: json` in the dataset config). Boxes are **normalized `[y_min,x_min,y_max,x_max]` on a 0-1000 grid** (resolution-independent, so the `resolution` setting does not affect them). Each detection is a `type:"obj"` element with its label as `desc`; key order (`type, bbox, desc` / top-level order) is preserved because the model was trained on a fixed key order. The picture's caption becomes `high_level_description`; `style_description` is omitted (optional). The `.txt` caption is still written per `caption_mode`, so the user picks which one ai-toolkit reads via `caption_ext`.
    ```json
    {"high_level_description":"a dog on grass",
     "compositional_deconstruction":{"background":"",
       "elements":[{"type":"obj","bbox":[y_min,x_min,y_max,x_max],"desc":"dog"}]}}
    ```

### 11.2 Replaying a picture's recipe (v1.9; the popup is v1.12 F5)

A picture's own recipe can be run again through the shipped ComfyUI engine. It follows the WebSocket branch of the rule above: the dialog submits, closes, and the app-wide `ComfyUiRunner` owns progress; the output arrives as a normal `picture_imported` event and is inserted in place (§8).

**The dialog that owned this pair of round trips was `RemixDialog` ("Generate variants…"), deleted in v1.12 F5 (#1407).** The recipe read below is unchanged and is still what the lightbox's Recipe tab and the Run popup prefill from; what changed is the *submit*. The Run popup sends `POST /api/v1/workflows/run` (§ the one run route, B7) rather than `POST /comfyui/run_recipe`, so the reason codes it renders are that route's, not this one's `preflight`. `run_recipe` itself was then deleted by B9 (#1410), which leaves `POST /api/v1/workflows/run` the only way to replay a recipe.

Two round trips, both scoped to the source picture (`PICTURE_SCOPED` in `ROUTE_POLICIES`):

- **Ask** `GET /api/v1/comfyui/pictures/{id}/recipe`. Answers whether the file carries a *replayable* recipe — the embedded API-format `prompt` chunk, never the UI `workflow` chunk — and pre-flights it against the user's ComfyUI:
  ```json
  {"available": true, "reason": null, "source": "comfyui",
   "summary": "API Workflow · 12 nodes",
   "positive_prompt": "…", "negative_prompt": "…", "seed": 12345,
   "settings": {"steps": 25, "cfg": 7.5, "sampler_name": "euler",
                "scheduler": "normal", "denoise": 1.0},
   "workflow_key": "…", "models": ["…"], "loras": [],
   "node_count": 12,
   "node_classes": ["CheckpointLoaderSimple","CLIPTextEncode","KSampler","SaveImage"],
   "source_is_imported": true, "source_label": "Watched folder",
   "seed_inputs": [{"node_id":"3","class_type":"KSampler","field":"seed","value":1}],
   "preflight": {"ok": true, "checked": true, "missing_node_classes": [],
                 "missing_models": [], "missing_input_images": [],
                 "has_save_image": true, "unchecked_fields": 0,
                 "unchecked_models": 0}}
  ```
  `node_classes` (distinct `class_type`, sorted) and `source_is_imported` / `source_label` exist for the owner's **consent** decision, not for display polish — see the untrusted-graph note below. `node_classes` is read from the file, so unlike everything under `preflight` it is populated even when ComfyUI was unreachable.
  **Three distinct negative answers, and the SPA must not collapse them**, because they send the user to three different places:
  | Response | Meaning | UI |
  |---|---|---|
  | `available:false`, `reason:"no_prompt_chunk"` | Ordinary photo or stripped metadata: nothing was made in ComfyUI here. **The lightbox hides its Recipe tab entirely on this answer** | Recipe mode disabled: "No executable workflow embedded" |
  | `available:false`, `reason:"editor_graph"` | The file carries ComfyUI's editor graph and PixlStash could not rebuild it exactly — a node class this ComfyUI does not have, a widget array it cannot account for, a subgraph. `conversion_problems` is one sentence per reason | Recipe mode disabled, the recipe still shown (prompt, models, seed read off the editor graph), with `conversion_problems` printed under the refusal |
  | `available:false`, `reason:"a1111"`, `source:"a1111"` | A1111 output: its recipe is readable (prompts, settings, LoRAs, seed) but is not a graph ComfyUI can be handed | Recipe mode disabled, the recipe still shown. A client that does not know this value falls through to the row above, which stays true |
  | `available:false`, `reason:"no_seed_input"` | The graph has no seed to change, so a re-run would be byte-identical (and would be deduped on `pixel_sha`, emitting no event — the user would see nothing at all) | Recipe mode disabled, with that reason |
  | `preflight.ok:false` | Checked, and this ComfyUI cannot run it | Recipe mode disabled, naming the missing node types / models / input images |
  | `preflight.checked:false` | ComfyUI was unreachable — **the check did not run; this is NOT a pass** | Recipe mode stays *selectable* but is **refused by default**: the run needs an explicit acknowledgement (below) |

  **`settings`, `negative_prompt` and each `lora_slots[].strengths` are read from the picture's own file**, like `positive_prompt` beside them. `settings` holds whichever of `steps` / `cfg` / `sampler_name` / `scheduler` / `denoise` the graph names — read from any node, since split-sampler graphs spread them over a scheduler, a `KSamplerSelect` and a `CFGGuider` — so **treat every key as optional** and do not read the block as "the settings of the pass that made this picture": a graph that samples twice can report one pass's steps beside another's CFG. `strengths` is always numbers (`{"model": 0.8, "clip": 0.6}`), on both branches, and a key is absent rather than of another type when the graph wires it or the value cannot be rendered. Each settings key is reported **at its own type** — `steps` an int, `cfg` and `denoise` numbers, `sampler_name` and `scheduler` strings — and a value of the wrong type is absent rather than passed through, so `steps` is a number on both branches and never a string to be parsed. `workflow_key` is an opaque digest over a graph this token can already read from `/workflow`; it also encodes which LoRA slots the topology marks structural, a library-wide decision, which is one bit more than the file alone says — see the backend note. What a `workflow_key` *groups* is an owner-only question, answered by the card routes and never by this one.

  On the `a1111` branch the values come from the infotext instead. a value that is wholly a number is reported as one (`steps`, `cfg_scale`), and anything else stays the text A1111 wrote (`size: "512x768"`, `sampler: "Euler a"`). `settings` then carries **A1111's own field names, as an open set** — `steps`, `sampler`, `cfg_scale`, `size` and whatever else that build wrote (`denoising_strength`, `clip_skip`, ADetailer and ControlNet fields) — so render it as a list of name/value pairs rather than reaching for named keys. **One key is PixlStash's own**: a ControlNet field names its model inside its own value, and that name is lifted out into `<field>_model` (`controlnet_0_model`, `…_model_2` for a second) so it can be forgotten like any other model (#1375), which also means the `controlnet_0` beside it no longer repeats the name. Both are still rendered as ordinary name/value pairs; the lifted name is not added to `models`, which stays the checkpoint. `lora_slots[].node_id` and `.class_type` are `null` there: the reduction's ids name no node in any graph, and there is no replay to send one back to.

  `unchecked_fields > 0` means the check was partial (a field ComfyUI does not enumerate, or a `remote` combo it fills lazily) and must not read as a clean bill of health. It is **not** the same state as `checked:false` and must not be gated the same way. `unchecked_models` counts the other gap: a model-shaped value (a model file extension) on a loader PixlStash cannot read at all, so it was never compared; a "no missing models" answer is only as good as that count is small.

- **Run** `POST /api/v1/workflows/run` (§11.1). The per-picture replay routes this step used to name — `POST /api/v1/comfyui/run_recipe` and `POST /api/v1/comfyui/run_i2i` — were retired in #1410; the consent and seed rules below are unchanged and are now that route's. It returns `{status, prompts:[{picture_id, prompt_id}]}`; the SPA passes `prompts` to `ComfyUiRunner` so its ComfyUI-WebSocket progress tracking picks the run up.

**The client never sends a graph.** The run route re-extracts the prompt chunk from the picture's file on every call. That keeps the picture-scoped authz declaration a complete access control for the endpoint, and it means a stale client cannot replay something the file no longer contains.

**But the graph is still untrusted input, and the contract reflects that** (review finding R3, CWE-829). It is authored by whoever made the image file; replaying it executes it on the owner's ComfyUI, bounded only by their installed node packs. The owner is the trust anchor, so the two sides split the job:

- **Server side.** The run **refuses** when `preflight.checked` is false and the body carries no `allow_unchecked: true`: `200` with `{"status": "refused"}`, `runs: 0` and the reason on the group, not a `400` — the batch shape reports per card, so a client must read `status` rather than the HTTP code. The refusal is enforced on the server, not only in the dialog — a UI-only gate is not a gate. **Consent is the literal JSON `true`** (`StrictBool`): `"true"`, `"yes"`, `"on"` and `1` are each a `422`, because a lenient cast would read `"false"` as consent too. The retired `run_recipe` also took the camelCase `allowUnchecked`; `POST /workflows/run` does **not**, so that spelling is a field the model ignores. An accepted override is logged with the node classes that ran.
- **Client side.** The SPA must render `node_classes` before the run button is usable, must send `allow_unchecked` **only** for a run the user explicitly acknowledged (never as a constant, never when `preflight.checked` is true), and must surface `source_is_imported` as information rather than a gate (it is the Source row in the disclosure; it was a banner until 2026-08-06, which fired on the common watched-folder case). The gate is deliberately narrow: an acknowledgement in front of a common state becomes a reflex and stops protecting the rare one. **The v1.12 Run popup goes one step further and never sends `allow_unchecked` at all** — an uninspectable ComfyUI blocks the run, and the backend refuses independently — so `allow_unchecked` now has no caller in this app and remains for programmatic ones.

**A fixed seed takes the full 64-bit range** (the shipped Flux2 Klein template's own `noise_seed` exceeds 2³²; the retired template paths checked 32 bits). The dialog caps its input at `Number.MAX_SAFE_INTEGER` regardless, because above 2⁵³ a JavaScript number cannot carry the value the user typed.

---

## 12. Configuration Sync

- All persistent user settings live on the `User` row (see [backend_architecture.md §6](backend_architecture.md#6-database-models)).
- Frontend fetches them once at boot into `sessionContext` and a local `configSnapshot` ref.
- Updates use `PATCH` against the user-config endpoint with **partial** payloads (only changed fields).
- The SPA applies updates **optimistically** to local refs and reconciles on response. Failed updates revert and surface a toast.
- Hidden tags, sort, columns, theme, watermark settings, smart-score penalised tags, etc. are all part of this object — keep the field names identical on both sides.

---

## 13. Error Handling Contract

| HTTP status | Frontend reaction |
|-------------|------------------|
| `2xx` | Use response data |
| `400`, `422` | Surface the response's `detail` field in a toast; do not log the user out |
| `401` | Auto-logout (see §3); SPA navigates to login. Suppressed for share-token sessions and the auth probe |
| `403` | Toast "permission denied"; component disables the action |
| `404` | Component-local "not found" state |
| `409` | Surface conflict details (used by import-dedup and rename operations) |
| `5xx` | Generic error toast; the user may retry |

Backend rule: errors must use FastAPI's `HTTPException(status_code, detail=...)` with a human-readable `detail`. Never return a `500` for an expected validation failure.

---

## 14. Build & Deployment Coupling

### Build output

[vite.config.js](../frontend/vite.config.js) writes the build to **`../pixlstash/frontend/dist`** — directly into the Python package. This is intentional: `pip install -e .` then ships the built SPA along with the backend.

### Serving order (in `_setup_routes`)

1. `/assets/*` is mounted as `StaticFiles(directory=…/frontend/dist/assets)`.
2. `/` returns `frontend/dist/index.html` (the SPA shell). If the dist directory is missing (e.g. a clean dev checkout), the root returns a small JSON status so the user sees a clear error.
3. All API routers are mounted under `/api/v1/`.
4. Other top-level routes are added explicitly for public sharing.

### Dev workflow

- Backend: `python -m pixlstash.app` (default port `9537`).
- Frontend: `npm run dev` inside [frontend/](../frontend/) (Vite at `:5173`, HMR enabled).
- CORS regex automatically permits `localhost:5173`.
- Cookies cross ports only if both sides agree on credentials (`withCredentials: true` + `allow_credentials=True`).

### Production workflow

- `npm run build` → `pixlstash/frontend/dist/`.
- Run `python -m pixlstash.app`; the SPA is served from the same origin as the API. No proxy needed.

**Pitfall**: forgetting to run `npm run build` before packaging leaves users with the JSON status fallback at `/`.

---

## 15. Host vs Container Paths

When the backend runs in Docker, filesystem paths in the database refer to **container paths**, but the user thinks in **host paths** (e.g. when picking watch folders or reference folders).

- Translation happens entirely backend-side via [utils/path_mapper.py](../pixlstash/utils/path_mapper.py) and [utils/host_path_utils.py](../pixlstash/utils/host_path_utils.py).
- `ImportFolder` / `ReferenceFolder` rows carry both `path` (container) and `host_path` (display).
- API responses include both values for these resources; the SPA must display `host_path` and only send a `host_path` (never a container path) when creating new folders. The backend resolves to a container path.
- The folder picker at `GET /api/v1/filesystem/browse` returns results in container-path space; the SPA presents them with their host equivalents.

The frontend itself should **never** transform paths — always trust the backend's translation.

---

## 16. Versioning

- A single source of truth for the version: the root `pyproject.toml`.
- The backend exposes it via `GET /version` (returns `version`, `install_type`, `docker_variant`).
- The frontend bakes it in at build time via `vite.config.js` (`__APP_VERSION__` reads `pyproject.toml`).
- The SPA can call `/version` at runtime to detect a backend upgrade and prompt the user to reload.

**Rule**: bump the version in `pyproject.toml` *before* building the frontend so the bundle reflects the actual release.

---

## 17. Integration Pitfalls

A focused list — read before changing anything that crosses the boundary.

1. **Don't add new routers without `prefix=API_V1_PREFIX`.** The interceptor expects every API call under `/api/v1`.
2. **Don't bypass `apiClient`.** Hand-rolled `fetch()` calls skip auth, share-token injection, and 401 handling.
3. **Always wrap browser-fetched image URLs in `appendShareToken()`.** Share mode silently breaks without it.
4. **Use snake_case wire `type` strings for events**, not the `EventType` enum name. Mismatched names manifest as a silently dead UI.
5. **Always include `picture_ids` in picture-related events** so the SPA can do targeted refresh — full reloads on every event will not scale.
6. **Optimistic UI must reconcile on failure.** Always revert local state if the PATCH errors out.
7. **Settings field names are a contract.** Renaming a `User` column requires a coordinated frontend change and an Alembic migration.
8. **CORS depends on cookies.** If you ever set `withCredentials: false` on the client or `allow_credentials=False` on the server, the SPA cannot log in.
9. **Image URLs from `<img>` tags use cookie auth.** If you ever switch to header-only tokens for browser sessions, all `<img>` URLs must become blob URLs fetched through `apiClient`.
10. **`frontend/dist/` is part of the Python package.** Add the build step to release automation; never commit a stale `dist/`.
11. **Host vs container paths**: do not let host paths leak into the database, and never display container paths in the UI.
12. **WebSocket reconnect is silent.** If the backend changes the filter schema, old clients will keep sending stale filters until they reload — version the filter message if you change it incompatibly.
13. **Delete-forever is a two-call flow and cannot be short-circuited.** `POST /pictures/scrapheap/delete-preview` returns a single-use `confirm_token` bound to that exact selection; `DELETE /pictures/scrapheap` refuses without it (400 missing, 409 spent/expired/wrong-selection) and destroys nothing on a refusal. A type-to-confirm dialog is a client control and proves nothing to the server — CORS admits any `localhost`/LAN-IP *port* with credentials (§6), so a page on another local port could otherwise drive the one irreversible endpoint. Clear the token after every attempt and re-run the preview to retry; never cache one.
14. **`X-Client-Id` / `origin_client_id` is for echo-matching only — never authorization.** It is attacker-controllable; any access decision based on it is a vulnerability. Every mutating in-request emit must carry `source`/`origin_client_id` in the event `data` dict, or the originating tab will full-reload on its own change.

---

## 18. Integration Diagrams

### 18.1 End-to-end request & event flow

```mermaid
sequenceDiagram
    autonumber
    participant U as User (Browser)
    participant SPA as Vue SPA
    participant AX as apiClient (axios)
    participant WS as WebSocket (/api/v1/ws/updates)
    participant API as FastAPI (/api/v1)
    participant V as Vault / Workers
    participant DB as SQLite

    U->>SPA: open app
    SPA->>AX: GET /check-session
    AX->>API: cookie + Bearer
    API-->>AX: 200 user context
    SPA->>WS: open connection
    SPA->>WS: { type: set_filters, ... }

    U->>SPA: upload images
    SPA->>AX: POST /pictures/import (multipart)
    AX->>API: forward
    API->>V: enqueue import + processing
    API-->>AX: { task_id }
    AX-->>SPA: task_id
    loop until done
        SPA->>AX: GET /pictures/import/status?task_id=…
        AX-->>SPA: { progress }
    end

    par Background pipeline
        V->>DB: write Picture, Quality, Tags, Embeddings
        V-->>API: emit events (snake_case type)
        API-->>WS: filter by client's set_filters
        WS-->>SPA: { type: pictures_changed, picture_ids: [...] }
        SPA->>SPA: refresh grid / sidebar
    end

    U->>SPA: open a picture
    SPA->>SPA: build <img src=/api/v1/pictures/{id}.{ext}>
    Note over SPA,API: Browser sends cookie automatically;<br/>share token appended via appendShareToken()
    API-->>SPA: image bytes (watermarked if applicable)
```

### 18.2 Origin & build coupling

```mermaid
flowchart LR
    subgraph DevTime["Dev mode"]
        Vite["Vite dev server :5173"] -- HMR --> Browser
        Browser -- "REST + WS<br/>(VITE_BACKEND_URL or :9537)" --> Backend9537["FastAPI :9537"]
    end

    subgraph BuildTime["Build"]
        NPM["npm run build"] --> Dist["frontend/dist/"]
        Dist -. "outDir: ../pixlstash/frontend/dist" .-> Packaged["pixlstash/frontend/dist/"]
    end

    subgraph Production["Prod"]
        UserBrowser[Browser] -- "everything same-origin" --> Single["FastAPI :9537<br/>(serves SPA + API + WS)"]
        Single -- "GET /" --> Packaged
        Single -- "GET /assets/*" --> Packaged
        Single -- "/api/v1/*" --> Single
        Single -- "/api/v1/ws/updates" --> Single
    end
```

### 18.3 Auth & share-token routing

```mermaid
flowchart TB
    Req["Frontend code calls<br/>apiClient.get('/pictures')"]
    Interceptor{"Request interceptor"}
    Abs{"Absolute URL?"}
    SameOrig{"Same origin?"}
    Inject["Inject ?token=…<br/>if share active"]
    Prefix["Prepend /api/v1"]
    Send["Send with cookie + Authz header"]
    Resp{"Status?"}
    OK["Resolve data"]
    Logout["logout() unless<br/>share-token or auth probe"]
    Throw["Reject with error"]

    Req --> Interceptor --> Abs
    Abs -- "yes" --> SameOrig
    SameOrig -- "yes" --> Inject
    SameOrig -- "no (external)" --> Send
    Abs -- "no" --> Inject --> Prefix --> Send
    Inject --> Send
    Send --> Resp
    Resp -- "2xx" --> OK
    Resp -- "401" --> Logout --> Throw
    Resp -- "other" --> Throw

    Browser["<img :src=appendShareToken(url)>"] -. "cookie auto-sent;<br/>?token= preserved" .-> Send
```

---

## 19. Duplicates Queue API (v1.9)

The contract behind the sidebar **Duplicates** destination. Every route is
`owner_only`; a share token gets 403 on all of them. Backend design is
`docs/backend_architecture.md` §22.

**Two rules the client must hold to.** The queue is *paged*, never fetched whole
(`GET /dedup/groups` returns `total` so a scrollbar can be sized without a second
request), and a group is addressed by its **`signature`**, never by an id. The
signature is a hash of the group's member content hashes, so it survives a rescan
and a re-import; a numeric id would not.

### `GET /dedup/policy`

No parameters. Renders the tier switches and the threshold slider so 0.90 and the
0.65 floor are never hardcoded twice.

```jsonc
{
  "defaults": { "near_enabled": false, "embedding_enabled": false,
                "threshold": 0.9, "min_group_size": 2, "max_group_size": 24 },
  "bounds": {
    "min_threshold": 0.65, "max_threshold": 0.99999,
    "tiers": ["exact", "near", "embedding"],
    "always_on_tiers": ["exact"],
    "tier_requires": { "exact": null, "near": "exact", "embedding": "near" },
    "scope_types": ["global", "project", "set", "character", "folder"],
    "verdicts": ["stacked", "keep_separate"],
    "max_page_size": 200
  }
}
```

`always_on_tiers` is why the exact switch renders disabled; `tier_requires` is why
enabling *embedding* must first enable *near*. Sending `embedding_enabled=true`
without `near_enabled` is a **400**, and a `threshold` below `min_threshold` is a
**422** — neither is silently corrected.

### `GET /dedup/groups`

Query: `near_enabled`, `embedding_enabled`, `threshold`, `scope_type`,
`scope_id`, `cursor`, `limit` (≤ 200), the deprecated `offset`,
`decided` (default `false`) and the repeatable `verdict`. With `decided=true` the same shape pages the
**resolved** groups instead — each row additionally carrying its live
`verdict` (`stacked` | `keep_separate`) and `decided_at` — so a decision can
be reviewed and cleared via `POST /dedup/verdicts/reopen`. The decided page
deliberately ignores the tier gate and the threshold: a decision made under
yesterday's policy must not be hidden by today's. On the open queue both
fields are `null`.

**The decided page has its own filter: `verdict` (2026-07-30).** The tier gate
is not in force there, so what the Duplicates toolbar's filter menu offers on
that page is the *decision*: `verdict=stacked`, `verdict=keep_separate`, or
neither for both (the param is repeatable, and listing every verdict means the
same as omitting it). `total` and `next_cursor` are computed under the same
filter as the page, so the scrollbar can never be sized for rows that will not
be served. Every decided response also carries `by_verdict` — the per-verdict
count taken **without** the filter, so the menu can say what turning a verdict
back on would add — and `verdicts`, the echo of the filter in force. `by_verdict`
may sum to less than `total`: a resolved group whose live verdict row is missing
still lists (so its "clear decision" way back survives) but belongs to no
verdict. Sending `verdict` **without** `decided` is a **400** — open-queue
groups carry no verdict, so the filter could only silently empty the queue.

**The decided page is ordered by recent activity descending.** A stacked
verdict uses its live `PictureStack.updated_at`, so editing that stack brings
its group back to the top of Decided and the Compare Group sequence. Other
verdicts use `decided_at` (2026-07-30; the open queue keeps its
confidence-descending order). `next_cursor` encodes that same effective
timestamp. Both pages mint **distinct cursor families** that reject each other
with a 400 — never reuse a queue cursor on the decided page or across the flip.

**`decided_at` is display-ready.** It means "when this decision last became
live" (a redo re-stamps it), even though a stacked row may sort by the newer
stack activity described above. Format is
**naive-UTC ISO 8601** with microseconds and **no offset suffix**
(`"2026-07-30T12:28:53.123456"`, no trailing `Z`) — the same convention as
every other timestamp on this API (`created_at`, the operation log's stamps) —
so parse it as UTC. It is `null` on the open queue and `null` for the stale
edge of a resolved group whose verdict is missing or reopened (such rows sort
into the list's tail); the server never invents a stamp for them.

```jsonc
{
  "groups": [{
    "signature": "9f2c…",           // the id every verdict route takes
    "tier": "exact",                 // exact | near | embedding
    "confidence": 1.0,               // 1.0 for exact; else the WEAKEST pairwise link
    "member_count": 2,
    "cover_picture_id": 41,          // a preselection, never a silent decision
    "why": [                         // group evidence, BOTH directions
      { "text": "Identical file hash", "against": false },
      { "text": "Different resolution", "against": true }
    ],
    "created_at": "2026-07-29T09:00:00",
    "candidates": [{
      "picture_id": 41, "width": 6016, "height": 4016, "megapixels": 24.16,
      "size_bytes": 14800000, "format": "jpeg", "is_raw": false,
      "score": 4, "tag_count": 2,
      "created_at": "2026-05-12T14:22:00", "imported_at": "2026-05-13T08:00:00",
      "stack_id": null, "reference_folder_id": null,
      "file_path": null,             // non-null ONLY for reference-folder pictures
      "thumbnail_version": "320x240",// append as ?v= — same token the grid uses
      "smart_score": 4.3,            // [1,5]; null = not computed/failed → dash
      "sharpness": 0.31,             // typical 0–0.5; null = not computed/failed
      "cover_score": 108.64,         // DEPRECATED legacy composite — do not use
      "why": [{ "text": "Best smart score (4.3)", "against": false },
              { "text": "Highest resolution", "against": false }]
    }]
  }],
  "total": 128, "offset": 0, "limit": 20,
  "cursor": null,                    // echo of the cursor this page was read from
  "next_cursor": "MXwxfDQy",         // pass back as ?cursor=; null at end-of-found
  "policy": { … }, "scope": { "scope_type": "global", "scope_id": null, "key": "global" },
  "scan": { "status": "running", "scanned_pictures": 79412, "total_pictures": 112000,
            "scanned_buckets": 210, "total_buckets": 940, "groups_found": 128,
            "error": null }
}
```

**Page with `cursor`, not `offset`.** Send the previous page's `next_cursor`
back verbatim and stop when it is `null`. The queue is a live list: deciding a
verdict removes the group the user just decided, and a tier-2 scan commits new
groups after every bucket, so an `offset` re-read skips exactly as many groups as
changed underneath it — reproducibly, with a single verdict between two pages.
The cursor is **opaque**: never construct, parse or edit one. A cursor the server
did not mint is a **400** (not a silent restart from page 1), `offset` is
deprecated but still works, and sending **both** is a **400**.

`scan` is the banner. `status` is `idle` when the scope has never been scanned —
that is not an error, the queue still shows what an earlier global scan found.
`file_path` is populated **only** for reference-folder pictures, where the user
manages the files; for a managed-library picture the path is an implementation
detail and the API returns `null`.

Render the `why` pills as reasons, not conclusions: `against: false` is the olive
check, `against: true` the red x. A group carrying red pills is the one that needs
Compare.

**`thumbnail_version` must be appended as `?v=`** to every thumbnail URL the queue
builds — `/api/v1/pictures/thumbnails/{picture_id}.webp?v={thumbnail_version}` —
exactly as the batch-thumbnail endpoint does (both come from the same server-side
helper, so they cannot drift). Without it a thumbnail regenerated mid-triage keeps
painting the stale cached bitmap, because the queue's URL never changes. The value
is `"0"` until the picture has been processed.

**Scope validation.** `scope_id` must be an integer for `project` / `set` /
`character`; anything else is a **400** at the boundary on every route that takes a
scope, including `POST /dedup/scan` (which writes nothing on a rejected scope). For
`folder`, `%` and `_` are escaped rather than treated as wildcards, so a folder
scope always means the folder it names.

### `POST /dedup/counts`

Read-only despite the verb (a scope list does not fit a URL). Body:
`{ "policy": {…}, "scopes": [{ "scope_type": "set", "scope_id": "9" }] }`.

```jsonc
{
  "unresolved_groups": 128,                              // the sidebar badge
  "by_tier": { "exact": 96, "near": 30, "embedding": 2 },// INCLUDING disabled tiers
  "scopes": [{ "scope_type": "set", "scope_id": "9", "key": "set:9",
               "unresolved_groups": 4 }],
  "policy": { … }, "scan": { … }
}
```

`by_tier` deliberately reports tiers that are switched off, so a tier switch can
be labelled with what enabling it would add. A non-global scope without a
`scope_id` is a **400**, and the `scopes` list is capped at **200** entries (each
is a separate correlated `COUNT`) — over that is a **422**.

### `POST /dedup/scan`

Body: `{ "policy": {…}, "scope": { "scope_type": "project", "scope_id": "3" } }`.
Returns the scan progress row **immediately** — the queue is opened while the
scan runs. Hashes are cached (computed on import), so a scoped scan only reads and
compares them. Tier 1 lands in milliseconds; tier 2 groups appear as each
candidate bucket finishes, so poll `GET /dedup/groups` and watch
`scan.scanned_buckets` rather than waiting for `status: "complete"`.

### Verdict routes

All three take `{ "signature": "9f2c…", "batch_id": "…" }`; `POST
/dedup/verdicts/stack` additionally takes `cover_picture_id` and
`excluded_picture_ids`. An unknown signature, a cover outside the **resulting
stack**, or excluding down to fewer than two members is a **400**.

**`cover_picture_id` may be a folded stack's leader** (design B2), not only a
group member: a group frequently names one picture of an existing stack, the
queue renders that stack as a single unit whose face is its leader, and picking
the unit must not promote the matched member over the leader the user already
chose. The accepted set is the group's members **plus the full membership of
every stack the verdict folds in**: anything else, including the leader of a
stack this group does not touch, is still a 400.

**A locked-set member is a partial success, not a refusal (2026-07-30).** A frozen
picture can join neither the stack (its set's membership cannot change) nor the
metadata union (its labels cannot change), so a group straddling a locked-set
boundary has no legal *whole-group* stack. `POST /dedup/verdicts/stack` stacks the
members it may and reports the rest in `skipped`, rather than costing the user the
decision about the whole group:

```jsonc
"skipped": [ { "picture_id": 38025, "reason": "set_locked",
               "sets": [ { "id": 91, "name": "Evaluation Set" } ] } ]
```

Skipped ids also appear in `excluded_picture_ids` (the verdict records them as
exclusions so a rescan does not re-ask); `skipped` is what says the exclusion was
the server's rather than the user's. The cover moves onto a member that survived,
and `cover_picture_id` reports where it landed. **Only when fewer than two members
survive is it a 423**, and that detail names the pictures as well as the sets, so
a client can mark the exact thumbnails:
`{ code: "set_locked", action, sets: [{id, name}], picture_ids: [...] }`.

This path is the *stale-client* case. `GET /dedup/groups` already marks every
frozen candidate `stackable: false`, so in the normal flow the user never presses
Stack on one. The manual `POST /stacks` routes are unchanged and still refuse
whole-request with 423: they act on exactly the pictures the user named, so there
is no "rest of the group" to fall back to.

**`batch_id` is namespaced.** Omit it and the server mints its own `srv-…`;
supply one only to make several calls reverse as one undo, and then it must match
`cli-<4–76 chars of A-Z a-z 0-9 _ ->` (≤ 80). Any other shape — including a
`srv-…` id — is a **400**, so a client cannot mint what reads as a server batch
or graft its rows into an existing one.

| Route | Effect |
|---|---|
| `POST /dedup/verdicts/stack` | Stacks the included members behind the cover and applies the metadata union. |
| `POST /dedup/verdicts/keep-separate` | Records that the group is not duplicates. Changes **no** picture row. |
| `POST /dedup/verdicts/reopen` | Returns a decided group to the queue. Clearing a `stacked` verdict whose stack still stands **dissolves that stack** (restoring the recorded pre-verdict stack state, folded stacks included) and records one undoable `dedup.reopen` operation — the response's `batch_id` is its undo handle and `unstacked_picture_ids` names what moved. A picture-neutral clear (keep-separate, or a stack already dissolved by hand) records nothing and returns `batch_id: null`, so clients must gate any receipt/narration on `batch_id`, exactly as for keep-separate. The metadata union is never reverted here. |
| `POST /dedup/mixed-stacks/{stack_id}/split` | Splits the marked member(s) off a mixed stack. Send `picture_ids`, the ids the user marked on the row (they start from `stranded_picture_ids` and are the user's to adjust); omit it and the server uses the stranded set it computes at `threshold`, the same opening marking. Every id must be a **live member of the stack in the path** — a picture in another stack or in none is a `400`, and so is a soft-deleted member, whose `detail` names the Scrapheap rather than claiming the id is not a member. (Widened 2026-08-02, deliberately reversing the subset-of-stranded bound that security finding F7 added the day before; see `docs/design/mixed-stacks-and-stack-units.md` B7.) Records one undoable `dedup.split_stack` operation; `batch_id` is always present. |
| `POST /dedup/mixed-stacks/{stack_id}/unstack` | Dissolves a mixed stack entirely. Records one undoable `dedup.unstack` operation; `batch_id` is always present. |

**A locked picture set refuses the whole stack on every route that detaches a
member**: `POST /dedup/mixed-stacks/{id}/split`, `POST
/dedup/mixed-stacks/{id}/unstack` and `DELETE /stacks/{id}/members`, with the
same `423` and the same `{"code": "pictures_locked", "action", "sets",
"picture_ids"}` detail the picture-level guards use. Stacks are set-membership
atomic, and a locked set freezes a stack's siblings *through* the stack, so
detaching one severs a freeze the lock exists to hold: unguarded, unstack
followed by delete turned a hard `423` into a soft delete. It is the whole stack,
never the frozen member alone, which is the same rule
`docs/design/keep-cover-only.md` states. Mixed-stack rows carry `stackable` /
`blocked_by_sets` so the client can disable the action with a reason instead of
issuing it and reading an error.

### Mixed stacks (design D5/B5)

`GET /dedup/mixed-stacks?threshold=&offset=&limit=&include_kept=` lists live
stacks whose members do not form one connected cluster at *that* threshold,
**pass the queue's own slider value; the list is threshold-relative and the same
stack is mixed at 0.90 and cohesive at 0.65.** Rows are ranked
least-held-together first (stranded members desc, component count desc, weakest
edge asc) and carry `component_count`, `component_sizes`, `components`,
`largest_component_size`, `stranded_picture_ids`, `weakest_edge` (`null` when no
pair is close enough to be an edge at all), `unhashed_picture_ids` (members whose
`perceptual_hash` has not arrived: report as *not yet comparable*, never as a
mistake), `suggested_action` (`split` / `unstack`), `membership_fingerprint`,
`kept`, `leader_picture_id`, `leader_thumbnail_version`, and `stackable` /
`blocked_by_sets` (the same pair `GET /dedup/stacks/{stack_id}/members` reports,
rolled up over the whole stack: `false` means a locked picture set freezes a
member, so split and unstack will both answer `423`).

Each row also carries `member_edges`, one entry per member parallel to
`member_ids`, holding **two numbers that are not interchangeable**:
`strongest_edge` / `closest_picture_id` is thresholded, so it is `null` for a
stranded member by construction and is what the stranded verdict is made on;
`nearest_edge` / `nearest_picture_id` is unconditional, how close that member
really gets to its closest sibling whatever the threshold says. **Bind the
number a user sees to `nearest_edge`**, which is `null` only when there is
nothing comparable to measure against (the member is in `unhashed_picture_ids`,
or no other member is hashed), so a dash means *not comparable*, never *unlike
everything*. The envelope adds
`total`, `kept_total`, `live_stack_count` and `next_offset` (plain offset paging;
this list is tens of rows, not thousands).

`POST` / `DELETE /dedup/mixed-stacks/{stack_id}/keep` set and clear the durable
**Keep** dismissal. It is keyed on stack id **plus** `membership_fingerprint`, so
adding a member later re-raises the stack; `POST` is idempotent
(`created: false` when it was already kept) and `DELETE` clears every
fingerprint (`removed: N`). Keep changes no picture, so it is **not** an undoable
operation and returns no `batch_id`: `DELETE` is the way back, not `Ctrl+Z`.

Both actions return `{stack_id, split_picture_ids, remaining_picture_ids,
stack_dissolved, batch_id}`. **`stack_dissolved` is reported, not inferred**: a
split that would leave a stack of one frees the last member too and drops the
stack row, so a client must read the flag rather than assume the remainder still
exists. All five routes are OWNER_ONLY.

**Undoing a verdict also returns its group to the queue.** `POST
/operations/undo` and `POST /operations/batches/{batch_id}/undo` restore the
pictures (for a stack) *and* reopen the verdict — both kinds, since the
2026-07-30 owner override made keep-separate op-logged (`dedup.keep_separate`)
— so after an undo the group is back in `GET /dedup/groups` and back in the
sidebar count with no extra call; do not follow an undo with a `reopen`. Redo
re-decides it. A client gesture id shared across several verdicts (a bulk
multi-select, or a mixed stack + keep-separate gesture) reverses as one batch
undo, each verdict through its own listed operation. `reopen` remains the
explicit, non-undo way back from either verdict kind.

`stack` and `keep-separate` return the same shape (for keep-separate,
`stack_id` / `cover_picture_id` are null and `metadata_union` is empty).
`batch_id` is **always** populated on both — the client's `cli-…` gesture id
when one was sent, a server-minted `srv-…` otherwise — and is the
`POST /operations/batches/{batch_id}/undo` handle:

```jsonc
{ "signature": "9f2c…", "verdict": "stacked", "stack_id": 77,
  "cover_picture_id": 41, "picture_ids": [41, 42], "excluded_picture_ids": [],
  "batch_id": "a1b2…", "skipped": [],
  "metadata_union": { "tags_added": 3, "scores_lifted": 1,
                      "characters_pending": 0, "membership_changed": true,
                      "best_score": 5 } }
```

`metadata_union` is what the action receipt should say. Stacking **unions** tags,
project membership and set membership onto every member and lifts every member to
the highest score; nothing is overwritten and nothing is deleted.

> **The union is also a visibility change.** Adding an out-of-scope duplicate to a
> *shared* set means every live share token for that set now reaches it. That is
> the shipped stack-atomic membership model (ordinary `POST /stacks` does the
> same), but auto-stack applies it in bulk — worth saying out loud in the consent
> copy. See backend §22.9 accepted risk A1.

### `POST /dedup/auto-stack`

Body: `{ "scope": {…}, "dry_run": true, "batch_id": null, "limit": null }`.
**Defaults to `dry_run: true`**, which returns the counts the consent dialog shows
and writes nothing. Send `dry_run: false` to apply.

Dry run:

```jsonc
{ "batch_id": null, "dry_run": true, "groups": 1204, "pictures": 2611,
  "scope": { … }, "results": [],
  "dry_run_summary": {
    "groups": 1204,
    "groups_by_tier": { "exact": 1204, "near": 0, "embedding": 0 },
    "pictures": 2611,
    "covers_gaining_tags": 310,
    "covers_gaining_score": 88,
    "covers_gaining_metadata": 361   // the dialog's "covers gaining metadata" row
  } }
```

`dry_run_summary` is derived from the **same** snapshot as the top-level counts in
a single read, so the dialog's figures can never disagree with each other. The
union is not executed to produce them and nothing is written; a cover "gains" a
facet when some other member of its group carries something it does not.

**`pictures` is the distinct stack-expanded set the run would move** (design
B4), not the groups' member counts: a group that folds an existing stack in
reparents that stack's whole membership, and two groups can name members of the
same stack. It can therefore exceed the sum of the groups' `member_count`s.
The `covers_gaining_*` rows stay on the groups' own members, because the tag and
score union runs over exactly those.

Applied (including a partially applied run):

```jsonc
{ "batch_id": "a1b2…", "dry_run": false, "groups": 1203, "pictures": 2609,
  "scope": { … },
  "results":  [ { …verdict…, "outcome": "applied" } ],
  "failures": [ { "signature": "…", "outcome": "blocked", "status_code": 423,
                  "error": { "code": "set_locked", … } } ],
  "blocked": 1, "failed": 0 }
```

Only the **exact** tier is eligible; near and embedding groups always go through
the queue no matter how confident they look. Every group in the run shares one
`batch_id`, so N stacks reverse with a single `POST
/operations/batches/{batch_id}/undo`.

**Every group is accounted for under exactly one `outcome`** — `applied`,
`blocked` (a guard refused it, in practice a locked picture set at 423) or
`failed` (it could not be resolved at all). One bad group never aborts the run,
and **the `batch_id` is returned even on a partially applied run**, so work that
did happen always comes back with its undo handle. Show `blocked` groups to the
user: they are still in the queue awaiting an individual decision.

### Not in this API

There is **no deletion route** anywhere in v1.9. A stack is a grouping row plus a
cover pointer; dropping it restores the flat grid exactly. Any UI copy implying
files are removed would be wrong.

## 20. Folder-Structure Read API (v1.11, Phase 2)

The two-minute pass behind the mapping screen (`MapTree`). It reads a folder tree
on disk and proposes **what each level is** — Project, Set, Person, Tag, or just a
folder. Backend design is `docs/backend_architecture.md` §24; the release plan is
`docs/plans/v1.11.0-existing-library.md` §4 Phase 2.

**Three rules the client must hold to.**

1. **It reads. It never writes.** No `Picture`, `Project`, `PictureSet`,
   `Character` or `Tag` row is created, and no file is opened for writing, moved
   or renamed. The result is a proposal the owner edits on the mapping screen and
   Phase 3 commits — or does not.
2. **A proposal without evidence does not exist.** Every `kind` a row carries
   comes with the `evidence` that produced it. A signal that cannot state its
   reason returns nothing rather than a guess, so `kind: null` with
   `evidence: []` is the normal answer for an ordinary folder name and must
   render as *"This one is…"*, never as a low-confidence pick.
3. **Narrowed is not decided.** Where the signals only rule things *out*, the row
   comes back with `kind: null` and two or more `candidates`. The UI offers those
   ("one of these: Project, Set") and the owner picks. Collapsing `candidates`
   to `candidates[0]` would invent a decision the backend deliberately refused to
   make.

### The eight signals

All deterministic, all local, **no LLM** — a folder name is a string and `Mira`
could be a person, a project or a client.

| `signal` | Reads | Proposes | Scope |
|---|---|---|---|
| `cardinality` | how many distinct names a level has, over how many parents | `tag`, or *not* `tag` | one whole level |
| `sidecars` | a caption `.txt`/`.caption` beside every picture (case-insensitive) | `set` | one folder |
| `faces` | one identity across the folder's pictures, **sampled at 20** | `person` | one folder |
| `name_match` | the folder name against entities the vault already has | that entity's kind | one folder |
| `leaf` | pictures and no folders below; a date *with other words* strengthens it | `set` | one folder |
| `container` | the level below mostly reads as Sets, People or date buckets, and this folder holds few pictures itself | `project`; a bare year (`2009`) narrows to `project`/`set` | one folder, read off the level below |
| `capture_day` | EXIF capture dates from the same 20-picture sample | `set` | one folder |
| `batch_numbering` | most direct pictures named `<prefix><digits>` with one prefix (`IMG_0412`) | `set`, only where nothing else spoke | one folder |

Two `signal` values carry **evidence without a proposal**: `date_bucket` on a
folder whose whole name is a date (*"filed by date"* — Lightroom, phones and
Google Photos exports all file by capture day whether or not the pictures belong
together, so the row proposes nothing and the tooltip explains the blank) and
on a level mostly made of such folders (*"3 of 3 folders filed by date"*). So
`kind: null`, `candidates: []` may now arrive with non-empty `evidence`; render
the text, offer no pick.

`faces` is the only expensive one and the only sampled one:
**`sampled_per_folder` pictures per folder, never the whole folder**, which is
what makes this two minutes rather than an hour. The number is in the response
rather than hardcoded in the client, and the evidence string says what it was
(`"one face, 19 of 20"`). The full pass runs later as ordinary background work
and can only *add* people — it never revises a row the owner has accepted.

One more `signal` value, `level_vote`, can appear on a **level** proposal: it
means the level took its rows' answer as its own, and its `text` says the count
(`"31 of 149 folders read as Set"`).

### `POST /api/v1/folder-structure/read`

Body `{"path": "/absolute/path/to/library"}`. Returns `{"task_id": "…"}` and
starts the read in the background. An optional `"match_existing": false`
turns the `name_match` signal off, for a read taken before the library it is
for exists (the Add-library dialog): otherwise the *active* library's People
and Sets would be proposed and their ids handed out as `match`.

`local_owner_only`: it takes a caller-supplied host path (§16.3 host-capability
tier). The blocklist (`validate_reference_folder_path`) runs on the
**realpath**, not on the string the caller sent — deliberately stricter than
`GET /filesystem/browse`, which checks the raw path only: browse lists one
level, this walks a subtree and decodes files out of it, so a symlink to a
restricted directory must not get through. `filesystem_roots` containment is the
same as browse's, and applies only when the owner has configured roots.

| Status | When |
|---|---|
| **400** | not absolute, resolves into a restricted system directory, or unusable as a path |
| **403** | outside the configured `filesystem_roots`, or Docker mode |
| **404** | the resolved path is not a directory |
| **409** | a read is already running — there is one at a time, and the screen only ever shows one |

**Starting a read discards the previous one.** There is a single slot, so once a
new `POST` succeeds the earlier `task_id` returns 404 from both the status and
the cancel route. A client holding a completed result should keep it rather than
expect to re-fetch it.

**No inference engine is not an error.** With no GPU task runner the read still
runs and the other signals still answer; only `faces` stays silent, so no
folder comes back as a Person. The result says which happened
(`face_signal_ran`) — without that field the same tree answers differently
depending on whether models had loaded, and neither the client nor the owner
could tell that from a library with nobody in it.

### `GET /api/v1/folder-structure/read/status?task_id=…`

Polled per §11's task-id branch. `result` is `null` until the read has **settled**
(`completed`, `cancelled` or `failed`); a `failed` read carries `error` and a
`null` result.

```jsonc
{
  "task_id": "0f1c…",
  "status": "running",        // queued | running | completed | failed | cancelled
  "stage": "faces",           // walking | faces | done
  "processed": 149,           // folders whose face sample has been read
  "total": 352,               // folders that will get one; 0 until `walking` ends
  "progress": 42.3,           // percent, 0.0 while total is 0
  "error": null,              // set only when status is "failed"
  "result": null
}
```

`stage` is what the progress bar names — "the bar names what it is buying". There
are only two working stages: `walking` (the tree is being collected, and the
sidecar signal is counted from the same listing) and `faces`. The counters only
mean folders during `faces`; during `walking` `total` is `0` and `processed`
counts folders found so far, which is why the client must render `walking` as an
indeterminate bar rather than 0%.

### The result

```jsonc
{
  "root": {"path": "/home/me/Generations", "name": "Generations",
           "picture_count": 28412},
  "sampled_per_folder": 20,
  "folder_count": 352,
  "picture_count": 28412,
  "truncated": false,           // true = the walk hit max_folders and stopped
  "max_folders": 20000,
  "unreadable_folders": 0,      // folders skipped because they could not be read
  "skipped_folders": {          // folders deliberately not walked
    "hidden": 0,                //   dot-folders: a vault's own caches
    "restricted": 0             //   below the root and on the system blocklist
  },
  "face_signal_ran": true,      // false = no inference engine; nobody is a Person
  "captions": [                 // caption-file conventions found beside the pictures
    {"suffix": ".txt", "kind": "tags", "files": 27_800, "folders": 340,
     "sample": "1girl, solo, red hair, looking at viewer"},
    {"suffix": "_caption.txt", "kind": "description", "files": 612, "folders": 4,
     "sample": "A woman with red hair stands by a window."}
  ],
  "levels": [ /* one per depth, ascending, level 1 = the root itself */ ]
}
```

**`captions`** is every text file that pairs with a picture by name, grouped by
the suffix after the picture's stem (`a.txt`, `a_tags.txt`, `a.jpg.caption`
beside `a.jpg` are the suffixes `.txt`, `_tags.txt`, `.jpg.caption`), most
files first. `kind` is the read's guess from a few sampled files (comma lists
read as `tags`, prose as `description`); `sample` is an excerpt so the owner
can check it without opening a file. Binary files, JSON and markup are never
offered, and only text extensions (`.txt`, `.caption`) are considered. The
owner confirms or corrects each row on the commit (§22, `captions`). Empty
when there are none.

Two fields the screen must not ignore, because both mean *this map is not the
whole library*:

- **`truncated: true`** — the tree was bigger than the walk's bound and the
  levels describe a prefix of it.
- **`unreadable_folders > 0`** — that many folders could not be opened
  (permissions, a broken mount) and are **absent from `levels` entirely**. A
  read that omits a subtree and presents itself as complete is worse than one
  that refuses.
- **`skipped_folders`** — folders deliberately not walked, as opposed to ones
  that failed. `hidden` counts dot-folders (a vault's own caches and sidecar
  directories); `restricted` counts directories on the system blocklist found
  *below* the root, because the walk re-checks the blocklist per directory
  rather than only on the path the caller named. Both are ordinary and neither
  needs to interrupt the screen, but they are counted rather than dropped in
  silence, for the same reason `unreadable_folders` is.

A **level**:

```jsonc
{
  "depth": 3,                   // 1 = the root folder itself
  "folder_count": 149,
  "direct_picture_count": 26734,  // pictures directly in these folders, NOT
                                  // recursive — summing recursive counts would
                                  // count a picture once per ancestor
  "proposal": {
    "kind": null,               // project|set|person|tag|folder|null
    // Names used once each rule Tag OUT and rule nothing in, so this level
    // comes back narrowed, never empty. `candidates: []` here would mean
    // something else entirely — see the shape table below.
    "candidates": ["project", "set", "person"],
    "match": null,
    "evidence": [{"signal": "cardinality",
                  "text": "149 names under 14 parents, used once each, so not labels"}]
  },
  "folders": [ /* every folder at this depth — see below */ ]
}
```

A level's `proposal` is the read *of the level as a whole*, which is what the
level header shows and what the digit keys 1–4/0 assign. It is the only place
`cardinality` can speak, because cardinality is a property of a level and not of
a folder. Level 1 is always the single root folder and never carries a
cardinality reading.

A **folder row**:

```jsonc
{
  "id": "3/57",                 // stable for the life of this read; the handle
                                // every per-row override addresses
  "parent_id": "2/4",           // null at level 1
  "depth": 3,
  "name": "mira",
  "relative_path": "2024 Shoots/mira",   // POSIX separators, relative to root
  "picture_count": 2914,        // recursive, this folder and everything under it
  "direct_picture_count": 118,  // files directly in it — what `faces` sampled from
  "child_count": 3,
  "proposal": {
    "kind": "person",
    "candidates": [],
    "match": {"entity_type": "character", "id": 41, "name": "Mira"},
    "evidence": [
      {"signal": "faces", "text": "one face, 19 of 20",
       "sampled": 20, "matched": 19},
      {"signal": "name_match", "text": "matches the person Mira"}
    ]
  }
}
```

**`relative_path`, never an absolute one.** The rows are for a screen, and the
absolute path is already in `root.path`; joining is the client's job. This keeps
a screenshot of the mapping screen from carrying the owner's home directory.

**`id` is `"<depth>/<walk-index>"` and belongs to one read.** The index is the
folder's position in the whole walk, **not within its level** — so a level's ids
are sparse and out of order, and `id`s must be treated as opaque strings rather
than sorted or indexed on. It is not a database id (nothing here is in the
database yet) and it is not stable across two reads of the same folder. Persist
an override against `relative_path`, never against `id`.

`match` is present only for `name_match`, and it is a **lookup, not an
inference**: `entity_type` is one of `project`, `set`, `character`, `tag`, and
`id` is that row's real primary key. When `match` is non-null the row's `kind` is
that entity's kind, and accepting the row should attach to the existing entity
rather than create a second one with the same name. **`tag` is the exception and
carries `id: null`** — a tag in this vault is a string on a picture, not a row of
its own (`Tag.tag`), so there is no id to hand back and the name *is* the handle.

Two ways `name_match` declines to hand back a `match`, and both are deliberate:

- **Two entities of the same kind share the name** (`PictureSet.name` is not
  unique, and a real vault has duplicates on day one). The `kind` is still known
  and is returned; `match` is `null` and the evidence says
  `"matches 2 existing sets"`. Returning whichever row the query happened to
  order first, under a field this section calls a real primary key, would send
  Phase 3's attach at an arbitrary set.
- **Two *kinds* of entity share the name** (a project *and* a person both called
  `Mira`). That is a narrowing, not a match: `kind: null`, `match: null`,
  `candidates: ["project", "person"]`, with the evidence saying so.

### Evidence

`evidence` is an ordered list, strongest signal first, and every entry carries a
`signal` (the table above) and a display-ready `text`. Entries may carry extra
per-signal numbers — `sampled`/`matched` for `faces`, `pictures`/`with_sidecar`
for `sidecars`, `names`/`parents` for `cardinality` — and the client is free to
ignore them and render `text`. **`text` is the contract; the numbers are a
convenience.** New signals add new `signal` values, so treat an unrecognised one
as "render the text, offer no special affordance" rather than an error.

The three ways a proposal comes back, and all three are legitimate:

| Shape | Means | Screen |
|---|---|---|
| `kind` set, `evidence` non-empty | a signal answered | the row is filled, with its reason under it |
| `kind: null`, `candidates` 2+ | signals ruled things out, nothing in | "one of these: …" |
| `kind: null`, `candidates: []` | nothing proposed; `evidence` may still explain why (`date_bucket`) | "This one is… ▾" |

`kind: "folder"` — **"just a folder"** — is in the enum because the *owner* can
choose it on the mapping screen and Phase 3 will send it back. **No signal ever
proposes it**, because no signal can prove that a string means nothing. A row the
backend had nothing to say about comes back as `kind: null`, not as `"folder"`.

**The other four `kind` values are the layout's facets**, `Facet` in
`pixlstash/utils/library_layout.py` (v1.11 Phase 4a) — `project`, `person`,
`set`, `tag` — and the read sources them from that enum rather than spelling
them again, so the two cannot drift. `folder` is the one addition and is
deliberately not a facet: it is the *absence* of one. A client can treat a
`kind` other than `folder` as a facet name the layout will accept.

### `DELETE /api/v1/folder-structure/read?task_id=…`

Asks a running read to stop. Returns `{"status": "cancelled"}`, or **404** if the
task-id is unknown (including an id evicted by a later read). A read that has
already settled is **not** cancelled and reports what it actually is —
`{"status": "completed"}` — rather than claiming a cancel the client cannot check.

Cancel stays live for the whole two minutes, per the release plan's risk table,
and a cancelled read keeps its partial `result` so the screen can still show what
was found. It takes effect **at the next folder boundary**, so a cancel issued
while a folder's face batch is in flight lands when that batch returns rather
than instantly.

### Not in this API

- **No commit.** Nothing here writes. The accept path is §22, Phase 3.
- **No per-row re-read.** A single read answers the whole tree; there is no
  "re-run faces on this one folder" route.
- **No language reading of folder names.** Explicitly out (release plan §5): no
  LLM ships with PixlStash, and `name_match` is a string comparison against rows
  the vault already has, not a semantic one.

## 21. About your library (v1.11)

### `GET /insights`

Read-only findings over the library. One request, computed live — there is no
cache, no rebuild route and nothing to poll, so the screen's "Look again"
button is this same GET. `owner_only`.

```jsonc
{
  "total_pictures": 12000,
  "folder_pictures": 12000,   // how many sit in a folder read in place; the
  "folders": 200,             // rest are vault-managed and have no folder name
  "findings": [ /* … */ ]
}
```

Each finding:

```jsonc
{
  "id": "unsorted_pile",      // stable key for the CHECK, not for the row
  "state": "todo",            // "todo" | "clear"
  "title": "900 pictures are in _unsorted and nowhere else",
  "evidence": "…the counts the finding was read off…",
  "action": {                 // null when there is nothing to open
    "label": "Sort them",
    "note": "rapid triage",   // what the button opens, shown under it
    "kind": "unassigned_in_folder",
    "path": "/home/me/library/_unsorted",
    "folder_label": "_unsorted"
  }
}
```

**Two contract points the frontend depends on.**

1. **A check that found nothing still returns a row**, with `state: "clear"`,
   its evidence, and `action: null`. The client must render it rather than
   filter it out: a screen where every row is a complaint reads as a nag, and a
   check that vanishes when it passes cannot be trusted when it fires. The
   client picks the glyph from `id`, so a reworded finding keeps its icon.
2. **`action` is passed through untouched.** The client must not re-derive the
   path or the kind — the folder the evidence counted is the folder the tool
   opens on, and rewriting is where the two get to disagree.

`kind` is a closed vocabulary, and each value maps to something that already
exists in the client:

| `kind` | Opens |
|---|---|
| `unassigned_in_folder` | `/character/UNASSIGNED?path=<path>` |
| `unassigned_with_face` | `/character/UNASSIGNED?face=with_face` |
| `duplicates_in_folder` | `/duplicates?scope=folder&scope_id=<path>` (the queue's existing folder scope) |
| `duplicates` | `/duplicates`, unscoped |
| `settings` | the settings dialog on the `tab` pane — a dialog, not a route, so App.vue handles this one |

**None of these is the obvious destination, and the reason is always the same
one: a finding counts what its own button can show.** A number the owner cannot
reach reads as the feature being broken, so where the two disagreed the *check*
was changed to match the destination, not the other way round.

* `unassigned_with_face` carries the face facet because unassigned alone is
  mostly pictures with no face in them. Unassigned means no face here is named;
  `with_face` means there is one. The pair is the counted set exactly — and
  both sides exclude `face.face_index = -1`, the sentinel row the extractor
  writes for a picture it found **no** face in. Reading that row as an unnamed
  face made the finding fire on most of a scanned library and open an empty
  grid.
* `duplicates_in_folder` for a two-folder overlap scopes to the pair's **common
  ancestor**, never to one of the two folders. Tier 1 is
  `GROUP BY pixel_sha, size_bytes HAVING count(*) > 1` with the scope predicate
  applied *inside* the aggregate, so a scope holding one copy of each shared
  file sees count 1 and the queue comes back empty. The queue's folder scope is
  a sub-tree prefix match, so the ancestor holds both copies. The server sends
  the unscoped `duplicates` instead when that ancestor would not narrow
  anything — a filesystem root, a relative path, or a sub-tree holding more
  than `SCOPE_MAX_WIDENING` times the two folders' own pictures. Two unrelated
  trees under one home directory are *siblings*, structurally identical to two
  folders inside a library, so only the size test separates them.

**Counts are in ROWS, not pictures.** Every grid request the app makes carries
`fields=grid`, which the listing route maps to `stack_leaders_only`: a stack of
eight is one row. A finding counting pictures states a number its own button
cannot produce.

### `?path=` and `?face=` — the two facets a finding opens on

A reference folder and an import folder each have a route of their own
(`/ref-folder/:id`, `/import-folder/:id`). The folder an insight points at has
no id of any kind, so it travels as **`?path=<absolute folder>`** on any grid
route (`useViewStore.parseFolderPath`), resolving to the same `{pathPrefix}`
payload the sidebar emits and therefore to the listing API's existing
`file_path_prefix` query param. On a folder route the sidebar owns the payload,
so the route never applies it directly — but it does ride along there, because
it is also how the sidebar's own subfolder selection stays in the URL: a
subfolder click carries an `rfId` and so takes the ref-folder branch, which
pushes `/ref-folder/:id?path=<folder under it>`, and the sidebar restores the
subfolder from the query once the folder listing is in. **There the path is
relative to the folder the id names**, since the id already says where that
folder is and spelling it out only put the owner's folder tree in the address
bar (#1206 item 9); a folder root pushes no query. An absolute `?path=` is
still read, which is what a link shared before that carries and the only shape
available on `/`. **Either shape must name somewhere INSIDE the folder**: a
`?path=` that climbs out — an absolute one under another folder, or either
shape carrying a `.` or `..` segment — selects the folder root instead, the
same refusal an out-of-tree absolute path has always got.

**`?face=with_face|without_face`** is the face facet, additive like
`?stack_state=` — an absent or unrecognised value leaves
`useFilterStore.faceBboxFilter` alone.

Reading both on every grid route rather than only on `/` is what makes
`/character/UNASSIGNED?path=…` and `/character/UNASSIGNED?face=with_face`
expressible at all.

**Two backend changes were needed for this, both small and both in
`Picture.find_unassigned`:**

1. It now accepts `file_path_prefix` and passes it to the shared
   `PredicateFilter`. It did not before, so
   `character_id=UNASSIGNED&file_path_prefix=…` silently answered with every
   unassigned picture in the library.
2. Its `stack_leaders_only` branch now treats a folder scope the way it already
   treated a project scope. The fast path represents a stack by its **global**
   `stack_position == 0` member; under a folder filter that member can be
   outside the scope, and the whole stack fell out of a grid whose own pictures
   were right there. The narrowed collapse reuses
   `PredicateFilter(file_path_prefix=…).file_path_prefix_predicates()` — the
   clauses are compiled once and handed to `Picture.stack_leader_filter`, which
   ranks the in-scope members and takes the best one, because two spellings of
   "in this folder" is how the leader and the members get to disagree.

### Not in this API

There is **no write anywhere on this surface**, no work queued and no file
touched. Any UI copy implying otherwise would be wrong.

---

## 22. Folder-Structure Commit API (v1.11, Phase 3)

The accept path behind the `Preview` screen: takes the mapping the owner
confirmed over a §20 read and writes it. Backend design is
`docs/backend_architecture.md` §25; the release plan is
`docs/plans/v1.11.0-existing-library.md` §4 Phase 3.

**One rule the client must hold to, same as §20's first: it moves, renames and
copies zero files, in either commit mode.** `mode: "reference"` (the default)
registers the scanned root as an ordinary reference folder — the same
mechanism `POST /reference-folders` already ships, indexed in place. `mode:
"local_import"` (v1.11.x, the "Add a library" fix) instead imports the pictures
as ordinary MANAGED ones — no reference folder at all. Either way every
picture found is linked to the accepted projects, people, sets and tags by
writing database rows. Nothing on disk changes except the new thumbnail each
newly-indexed picture gets, exactly as any other import produces.

### `POST /api/v1/folder-structure/commit`

```jsonc
{
  // Exactly ONE of these two identifies the read being committed.
  "task_id": "…",              // the settled read's task_id (§20)
  "read_result": { /* … */ },  // or the read's own result, from §20's status
  "label": "Generations",       // optional; defaults to the folder's own name
  "mode": "reference",          // "reference" (default) | "local_import"
  "captions": [                 // local_import only: one row per §20 caption pattern
    {"suffix": ".txt", "kind": "tags"},
    {"suffix": "_caption.txt", "kind": "description"},
    {"suffix": "_notes.txt", "kind": "ignore"}
  ],
  "assignments": [
    // One entry per folder the owner accepted as something. A folder left
    // "just a folder" or undecided is simply absent — there is nothing here
    // for it to do, and every picture under it is still indexed and
    // searchable (§20's "arrives ungrouped" case).
    {"relative_path": "2024 Shoots", "kind": "project"},
    {"relative_path": "2024 Shoots/mira", "kind": "person", "match_id": 41},
    {"relative_path": "Datasets/mira-lora-v3", "kind": "set"},
    {"relative_path": "final", "kind": "tag"}
  ]
}
```

**`read_result` exists because a read lives in one server process's memory and
processes end.** The desktop's first run reads the library folder while the GPU
runtime downloads and then restarts the backend onto that runtime, so by the
time the owner answers the mapping questions the task that produced the answer
is gone and `task_id` can only be `404 Task not found` — with the answer sitting
in the dialog. Sending the result back is the same information by another route.
Two consequences worth knowing: a supplied result reserves nothing, so it
carries none of the one-commit protection a task-identified read gets (the
caller holding the result owns that), and a body that names both or neither is a
`400`.

`relative_path` is the same handle §20's folder rows carry — POSIX-separated,
relative to the read's root, `""` for the root itself. `kind` is one of
`project`, `person`, `set`, `tag` (never `folder`: a row with nothing to do is
omitted, not sent as `folder`). `match_id` names an existing entity to attach
to, exactly as `name_match`'s `match.id` proposed or as the owner picked from
`candidates`; omitted, a new one is created named after the folder.

**A folder's nearest accepted ancestor of each exclusive kind wins** — a
picture is filed under the *closest* Project, Person or Set above it, not
every one along the path, mirroring `library_layout`'s first-match-wins
segments. Tags are the exception: every accepted Tag ancestor applies, because
a picture can carry more than one label.

Returns `{"task_id": "…"}` and starts the commit in the background.

`local_owner_only` (§16.3): the read already validated the host path once, and
this route is the write that follows from it.

| Status | When |
|---|---|
| **400** | an `assignments` row is malformed or names an unknown `kind` |
| **404** | `task_id` does not name a read this session holds |
| **409** | the named read has not settled yet, a commit is already running (against any read), the named read has **already been committed**, or the read's root path is already a reference folder that has completed a scan (§25 — the reuse-vs-refuse rule) |

### `mode: "local_import"`

For the read's root when it IS the active library's own `image_root`, or a
folder inside it — the "Add a library" flow's "pictures" verdict, where the
folder a fresh vault was just created in already held loose files before the
owner ever pointed PixlStash at it. `label` is ignored in this mode: there is
no reference folder to name.

**Every picture the walk finds becomes an ordinary MANAGED picture** (relative
`file_path`, exactly as anything else imported into this library), not a
reference-folder one. Routing this case through `mode: "reference"` instead
would collide with the rule `POST /reference-folders` already enforces the
other direction — a reference folder may never equal or contain `image_root`
(`409 "Path conflicts with the PixlStash data folder."`) — so the two stay two
modes, never one. Import is **idempotent by `file_path`**: a file already
indexed under this path (an overlapping earlier `local_import`, or an ordinary
import that reached it independently) is reused by id, never re-imported as a
second row — same spirit as `mode: "reference"`'s own "don't redo what already
happened" rule for a resumed commit (§25).

**`captions` says what each caption file beside the pictures is.** A picture
built by the import reads the files at the confirmed suffixes: a `tags` file
becomes its tags (and it is not queued for the tagger), a `description` file
its description, and an `ignore` pattern is never opened however tag-like its
content. **Absent and empty are different answers.** A request with no
`captions` key is a client that never asked (an older one), and the import
probes the known conventions as it always did (`_tags.txt`, `.caption`, a
content-sniffed `.txt`). `[]` is the owner having been asked with nothing to
confirm, and reads no caption file at all. The answer is recorded on the
durable commit record, so a commit resumed after a crash honours it. A row's
`suffix` must be a bare filename fragment and `kind` one of `tags`,
`description`, `ignore`, else `400`. `mode: "reference"` refuses the field
outright (`400`), `null` and `[]` included: a reference folder's sidecar
suffixes are its own `PATCH /reference-folders/{folder_id}` fields.

**The root must be inside `image_root` or the commit fails.** There is no
separate error status for this — the check runs inside the background commit,
same as every other commit-time refusal, and surfaces as `status: "failed"`
with `error` set once the client polls `GET .../commit/status` (see below),
not as a synchronous 4xx on the `POST`. A client offering `local_import` in its
UI should therefore only ever construct the request against the active
library's own folder — this is a server-side backstop, not something the
mapping screen is expected to let the owner trigger by hand against an
arbitrary path.

### `GET /api/v1/folder-structure/commit/status?task_id=…`

Polled per §11's task-id branch, same shape as §20's read status:

```jsonc
{
  "task_id": "…",
  "status": "running",        // queued | running | completed | failed
  "stage": "indexing",        // registering | indexing | assigning | done
  "processed": 149,
  "total": 352,
  "progress": 42.3,
  "error": null,
  "result": null
}
```

**A commit is never `cancelled`.** In `mode: "reference"`, once the reference
folder is registered its scan runs to completion regardless of what the screen
does next — the in-place indexing this route starts is not something a
"Cancel and organise later" on a *later* screen can safely stop mid-write, and
it is also the whole reason the mapping screen stays reachable from the
sidebar afterwards: the scan and the mapping are two different steps, and
abandoning the second does not undo the first. `mode: "local_import"` has no
separate scan to keep running, but the same rule applies for the same
underlying reason: there is no cancel route on this API in either mode, so a
commit once started always runs to `completed` or `failed`.

`stage` progresses `registering` (creating the reference folder row) →
`indexing` → `assigning` (creating the accepted entities and linking pictures
— no filesystem work happens here at all) → `done`. In `mode: "reference"`,
`indexing` means waiting for the reference folder's first scan pass;
`processed`/`total` are pictures indexed so far, out of the read's own
`picture_count`. In `mode: "local_import"` there is no reference folder to
register, so `stage` goes straight from `registering` (the commit's initial
state, before its background thread has reported anything) to `indexing`,
where `processed`/`total` instead count files as `local_import_pictures`
resolves them — both the ones already indexed (an idempotent hit, counted
immediately) and the newly-imported ones (counted as each batch commits).

The result, once `status` is `completed`:

```jsonc
{
  "reference_folder_id": 7,     // null for mode: "local_import" — no ref folder
  "pictures_indexed": 28412,
  "projects_created": 12, "projects_matched": 1,
  "people_created": 114, "people_matched": 4,
  "sets_created": 31, "sets_matched": 0,
  "tags_created": 4
}
```

### Not in this API

- **No re-mapping an already-committed folder.** Accepting a mapping is
  one-shot and **enforced**, not merely a convention the client is trusted to
  follow: the read is marked committed the instant a commit for it starts
  (§25), and a second `POST` against the same `task_id` — whether the first
  commit is still running or long since `completed` — is refused with a
  **409**, never re-run. Changing what a folder means afterwards is ordinary
  entity editing (rename a project, move a picture between sets), not a
  second commit.
- **No placement of *future* pictures.** This writes the accepted mapping onto
  the pictures the read found; where a new picture goes on import is the
  layout, v1.11 Phase 4.

---

---

## 23. Layout & Move API (v1.11, Phases 4b and 4c)

How a library's folders are laid out, the one action the client offers over it,
and the one gesture that moves everything. Backend design is
`docs/backend_architecture.md` §26; the release plan is
`docs/plans/v1.11.0-existing-library.md` §4 Phase 4.

**Three rules the client must hold to.**

1. **A picture moves only when its folder stops being true.** Not whenever
   something about it changes. Adding a second project or a second person moves
   nothing, and the UI must not suggest otherwise — the copy that sits next to
   the layout builder is a table of what does and does not move, not a warning.
2. **Choosing a layout reorganises nothing.** Every path already in the library
   is what its assignments were read from, so every path is already true. A
   confirmation dialog saying "this will move your files" would be false, and
   `PATCH /server-config/layout` will not have moved one when it returns.
   *Offering* the Phase 4c migration afterwards is the correct shape, and it is
   a separate, previewed, explicitly-consented action — never a side effect of
   the PATCH.
3. **Drift is offered, never taken.** A picture whose folder is still true but is
   not what the layout would pick today is *not wrong*. `suggested_folder` is an
   offer the owner accepts; nothing in the product acts on it by itself. The
   Phase 4c migration is the one thing that does sweep a folder of the owner's
   own into the layout, and only because it is the owner acting, on the whole
   library at once, after a preview and with one undo: **the rule and the drift
   offer treat a folder of the owner's own as a permanent override; "Move them
   now" flattens it.**

### The layout string

One field, `layout`, in the form `project/person,set`:

- `/` separates **segments** — one folder level each, in order.
- `,` separates a segment's **alternatives**; the first the picture has a value
  for wins.
- A segment nothing fills is **skipped**, not left as an empty folder, which is
  what keeps the tree two deep instead of five.
- Facets: `project`, `person`, `set`, `tag`. `person` is the user-facing word;
  `character` is the database's.

`null` or `""` means **no layout**, which is the default and the only state in
which nothing is ever placed or moved. `layout_unfiled` is the folder a picture
with nothing to file it by goes to — one safe path component, `Unassigned`
when null. It is deliberately not the library root: the root is where an unmigrated
flat library lives and those files must never move.

**Both PATCHes are patches, not puts.** A field you do not send keeps its stored
value, so sending `layout_unfiled` alone renames the unfiled folder and does not
turn the layout off. Send `layout: null` explicitly to turn it off. An unfiled
name that is not a single safe path component is `400`, and so is an
unparseable layout — checked independently, so a bad unfiled name is refused
even when there is no layout to parse beside it.

### Routes

| Method | Path | Tier | Returns |
|---|---|---|---|
| `GET` | `/api/v1/server-config/layout` | `local_owner_only` | `{layout, layout_unfiled, default_layout}` for the library's own picture root |
| `PATCH` | `/api/v1/server-config/layout` | `local_owner_only` | the same, after recording. `400` with the reason if the layout cannot be read |
| `GET` | `/api/v1/server-config/captions` | `local_owner_only` | `{sync_tags, sync_descriptions, tags_suffix, description_suffix, default_tags_suffix, default_description_suffix}`: caption-file sync for the library's own picture root, the same four fields a reference folder carries |
| `PATCH` | `/api/v1/server-config/captions` | `local_owner_only` | the same, after recording. A toggle sent as `null` is no change; an empty suffix clears it. `400` for a suffix that is not a bare filename fragment, or one suffix for both kinds. Turning a kind on asks for a root rescan, which reads existing caption files in and writes one beside every picture that has content but no file |
| `PATCH` | `/api/v1/reference-folders/{folder_id}` | `local_owner_only` | the folder, now carrying `layout` / `layout_unfiled`. The same two fields on the folder the owner indexed in place, and they are read back by `GET /reference-folders` too |
| `GET` | `/api/v1/pictures/{id}/layout` | `picture_scoped` | `{layout, current_folder, suggested_folder}` |
| `POST` | `/api/v1/pictures/layout/move-to-match` | `picture_scoped` | `{moved_count, moved_picture_ids, skipped, operation_id}` |
| `GET` | `/api/v1/server-config/layout/migration` | `local_owner_only` | what moving the whole library onto its layout would do. Moves nothing |
| `POST` | `/api/v1/server-config/layout/migration` | `local_owner_only` | one pass of that move: `{batch_id, moved_count, moved_picture_ids, examined, next_after_id, done, skipped, operation_id}` |

`GET /pictures/{id}/layout` answers `{"layout": null, "current_folder": null,
"suggested_folder": null}` — **not** a 404 — for a picture in a root with no
layout. A 404 there means the picture does not exist.

`suggested_folder` is `null` whenever there is nothing to offer, and the client
must treat all four cases the same way (no button): the root has no layout, the
picture is not in a laid-out root, its folder is one of the owner's own, or it
is already where the layout would put it.

### `move-to-match`

Body `{"picture_ids": [int, ...]}`, at most 200 — the same cap as `POST /pictures/rotate`, because every id is a file operation on the owner's disk and the whole request is one transaction and one undo. A larger selection is `422`; send it in batches. Every picture that is already
where the layout would put it, or is in a folder of the owner's own, comes back
in `skipped` as `{"picture_id": int, "reason": str}` and is **left exactly where
it is**. Reasons the client may see: `already_matches`, `no_layout`,
`destination_taken`, `source_file_missing`, `source_is_symlink`,
`path_outside_root`, `destination_outside_root`.

The whole request is recorded as **one** `pictures.layout.move` operation, so
one Ctrl+Z puts every file back — `operation_id` names it. A folder the move
leaves empty is kept, never deleted.

### The migration (Phase 4c)

The one operation in this release that deliberately moves everything, offered
whenever a layout is **set or changed** and never automatic, never on import.

> **It is not rule 1 and the UI must not describe it as one.** Under that rule a
> flat path parses against nothing, can never be false, and never moves — which
> is why an existing library needs no migration and why rule 2 is true. This is
> the owner asking for something else: *make it all match, now.*

`GET .../migration` **moves nothing** and is the consent screen:

```
{ "layout": "project/person,set",
  "picture_count": 4109, "folder_count": 312,
  "samples": [ {"picture_id": 12, "from": "0412.png", "to": "2024 Shoots/Mira/0412.png"} ],
  "collision_count": 3, "collisions": [ ... ],
  "cross_volume_count": 0,
  "skipped_counts": {"source_is_symlink": 1} }
```

**`skipped_counts` is a different shape from the `POST`'s `skipped`, on
purpose**, and the name says so: the preview answers `{reason: count}` because a
per-picture list over a whole library would be a listing of it, and the `POST`
answers `[{picture_id, reason}]` because a pass is 200 pictures and the client
may want to name them.

Every path is **relative to the library root**, never absolute. Three numbers
carry the whole consent and the client must show all three:

- `picture_count` / `folder_count` — *"4,109 pictures will move into 312
  folders"*, with `samples` under it. A picture the layout cannot place is in
  none of these and does not move: sweeping it into the unfiled folder would be
  movement for no gain, since it already contradicts nothing.
- `collision_count` — pictures rendering onto a path something already occupies.
  They are suffixed `-2`, `-3`… **The file already sitting there is never
  renamed and never overwritten**; what is suffixed is the file being moved,
  and its sidecars with it (a sidecar pairs with its picture by stem).
  Show the count and the `collisions` samples rather than hiding it, and never
  present it as a failure.
- `cross_volume_count` — pictures sitting across a mount point from where the
  layout would put them. **Those cannot be moved at all**: the destination is
  claimed with `os.link` and then `os.replace`, and both refuse to cross a
  device, so they are refused in the plan rather than attempted. They are also
  in `skipped_counts` as `destination_other_volume`, they are not in
  `picture_count`, and they stay exactly where they are. Non-zero is worth
  saying out loud before the run — it is the one case where "make it all match"
  cannot, and the owner may want to move the mount rather than the pictures.

`POST .../migration` runs **one pass**. Body `{"after_id": 0, "batch_id": null}`;
call it again with the `next_after_id` and the `batch_id` it returned until
`done` is `true`. That loop is the progress bar.

- **Omit `batch_id` on the first pass and echo it on every one after.** Every
  pass records its own `pictures.layout.move` operation, all under that one id,
  and a batch is a single undo unit — so **one undo puts every file back at the
  path it had**. A `batch_id` outside the `srv-layout-migration-` namespace is
  `400`; the check is on the value's shape, so what it guarantees is that a
  migration's passes cannot be grouped into some other gesture's undo unit, not
  that the id came from this server.
- **A pass that fails is finishable, not restartable.** The tree is left
  half-moved and wholly consistent; call again with the same cursor and id. A
  picture already where the layout wants it plans no move, so re-running is
  safe and re-moves nothing.
- **Every picture a pass planned is accounted for**, in `moved_picture_ids` or
  in `skipped`. A file that could not be moved after all — a name that appeared
  at the destination since the plan, a file locked on Windows — comes back as
  `move_failed` rather than vanishing from both lists while the pass reports a
  clean finish. Re-run to retry it.
- `skipped` uses §23's own vocabulary plus `destination_other_volume` and
  `move_failed`, both above.
- Only the library's **own** picture root is migrated. A reference folder's
  layout has no migration route; it would need its own consent naming that
  folder.

### Events

A move — whether the owner asked for it, the rule decided it, or the migration
made it — broadcasts
`CHANGED_PICTURES` with `change_kind: "updated"` and
`fields: ["file_path", "pixels"]`. **`pixels` is not decoration.** The thumbnail
URL is derived from the file path and does not come back from
`GET /pictures/{id}/metadata`, so a client that re-reads metadata alone goes on
painting a thumbnail that is no longer at that address — the same marker an
in-place rotate raises, for the same reason.

There is no event for "a picture became due a layout check", and there should
not be: the check is debounced by design, almost always decides nothing, and a
client that drew a spinner for it would be drawing one for every membership edit
in the product.

## 24. Move Reconciliation API (v1.11, Phase 5)

The mirror of §23: that surface moves a file when an assignment change makes
its folder untrue; this one reads a file the owner already moved outside
PixlStash and says whether an assignment should change to match. Backend
design is `docs/backend_architecture.md` §27; the release plan is
`docs/plans/v1.11.0-existing-library.md` §4 Phase 5.

### Routes

All three `owner_only`, vault-wide like `/operations` — none of it is
boundable to a single resource-scoped grant.

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/api/v1/moves/pending` | — | `{unambiguous, ambiguous, off_layout}` |
| `POST` | `/api/v1/moves/apply` | `{"review_ids": [int, ...]}` | `{applied_picture_ids, skipped_review_ids}` |
| `POST` | `/api/v1/moves/dismiss` | `{"review_ids": [int, ...]}` | `{dismissed_review_ids}` |

Each item in a bucket:

```jsonc
{
  "review_id": 42,
  "picture_id": 1001,
  "old_path": "/library/refs/2024 Shoots/mira.png",
  "new_path": "/library/refs/Client · Nordvik/mira.png",
  "removals": [{"facet": "project", "name": "2024 Shoots"}],
  "additions": [{"facet": "project", "name": "Client · Nordvik"}],
  // ambiguous bucket only — the picture's own current names for each facet a
  // removal is ambiguous about, i.e. why leaving one folder does not say
  // which the owner meant:
  "current": {"project": ["2024 Shoots", "Client · Nordvik"]}
}
```

`facet` is one of `"project"` \| `"set"` \| `"person"` — the same three
`Facet` values §23's layout builder uses, minus `"tag"` (deliberately
unreconciled; `docs/backend_architecture.md` §27).

**Four contract points the client must hold to.**

1. **There is no cache to invalidate, on either side.** Every `GET` is
   reclassified live against current assignments and the current layout —
   the same "Look again" shape as `GET /insights` (§21). A row that no longer
   implies anything is quietly dropped rather than returned; the client
   should not expect a `review_id` it saw once to still be there.
2. **`apply` recomputes fresh too, never trusting an earlier `GET`.** Passing
   every currently-unambiguous `review_id` is how the client requests "apply
   the whole bucket" — it is not submitting a decision the server already
   made, it is asking the server to decide again, right now, and act. A
   picture whose memberships changed in the gap is applied against what is
   true at that moment, which may differ from what the `GET` said.
3. **A single `review_id` sent to `apply` is how an ambiguous row is
   resolved** — the ambiguity gate only blocks the *bulk* "apply every
   unambiguous row" action, never a caller naming one row explicitly.
   `dismiss` on the same id ("Keep both") changes nothing and only clears the
   queue. The resolve button's own label is derived client-side from `current`
   and `removals`, not sent by the server — see
   `docs/frontend_architecture.md` §9.4 for why it must name the destination
   rather than a generic verb.
4. **`applied_picture_ids` and `skipped_review_ids` are disjoint, and neither
   implies the row is still in the queue.** Every `review_id` the caller sent
   is cleared once acted on, whether or not anything changed. A `review_id`
   lands in `skipped_review_ids` when it had a genuine removal or addition to
   make but the entity name it needed could not be resolved uniquely (§27) —
   the client must not read an empty `applied_picture_ids` as "nothing was
   asked for" without also checking whether anything was skipped.

**`off_layout` carries no decision.** Every item in it already has its path
followed (the scan already updated `Picture.file_path`); the bucket exists so
the client can say so, not so the client can act on it. Both endpoints accept
any `review_id` and clear the row either way, so applying or dismissing an
`off_layout` one is never an error — but it is not guaranteed to be a pure
no-op, because `apply` reclassifies fresh (contract point 2, above): if a
matching entity was created in the gap between the `GET` and the click, the
row may no longer be `off_layout` by the time `apply` acts on it, and it is
applied against what is true then. The client's own `off_layout` bucket
carries no button for this reason — the row is not offered as something to
apply, only as something to dismiss along with the rest of the queue, and it
never persists past `RETENTION_S` regardless (§27).

### Events

`external_moves_pending` (§8) is the only event on this surface, and it
carries no picture ids or counts — see the table entry above. There is no
"reconciled" event: applying and dismissing are both client-initiated `POST`s
the caller already has the result of, and `CHANGED_PICTURES` (with
`change_kind: "updated"`) is emitted separately for the pictures an `apply`
actually changed, the same envelope every other membership write uses.

---

## 25. Text in Pictures (#1197)

Text read out of a picture is its own data: never `description`, never tags.

| Route | Purpose | Response |
|---|---|---|
| `GET /pictures/search` | every search also matches the text | each row gains `text_match: bool`, true when every query word appears in the picture's text |
| `GET /pictures/{id}/text?query=` | the Text tab and the word boxes | `{ state: "none" \| "pending" \| "read", lines: [[{ text, box: [x, y, w, h], matched }]] }` |
| `POST /pictures/{id}/text/read` | *Read again*; the stored text is kept until the new read succeeds | `{ state: "pending", lines: [] }` |

- `box` is in fractions of the picture **as displayed** (EXIF orientation applied), so the overlay draws it without knowing the pixel size.
- `matched` is computed server-side with the rule search uses (`database.ocr_word_matches`: no edits under five letters, one under nine, two beyond, never on the first letter), so the Text tab and the result pill never disagree about "C0FFEE" matching "coffee". Words are marked only when the whole query matched the picture (every word), the same test that sets `text_match`. Omit `query` when no search is active.
- `pending` means the picture is not in the scrapheap, its `text_score` qualifies and it has not been read yet; `none` covers both "not worth reading" and "read, nothing found".
- A finished read emits `pictures_changed` with `fields: ["ocr_text"]`. The field affects no sort or filter, so the grid ignores it; the open overlay refetches.
- Ranking: a text match adds `OCR_TEXT_MATCH_WEIGHT` (0.35) to the combined score and nothing otherwise, so a page of words gains nothing on a search its words do not answer. The SPA searches with `threshold=0.1`; at the route's own default of 0.5 a picture matched by its text alone (0.35) is filtered out, so an API caller wanting text-only matches must pass a lower threshold. `text_match` is computed only for the rows a search returns. `tests/test_ocr_text.py` holds the queries that must not match known text-heavy pictures.
- The result pill's *All · In text* switch filters on `text_match` client-side; nothing is refetched and nothing is remembered.

---

*Last updated: 2026-08-24. Update this document whenever any integration contract (URL prefix, event names, auth mode, build output path, CORS policy, share-token mechanism, settings field names) changes.*

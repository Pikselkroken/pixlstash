# Picture-set locking — implementation plan (target: 1.7.0 RC2)

**Goal:** let the user lock a picture set so it becomes fully read-only — no name/description
changes, no membership changes, and no tag/metadata edits on any picture in the set — with the
lock visible (transparent lock icon + explanatory tooltip) everywhere the pictures appear, and
with locked sets greyed out and unselectable in tag review. Motivation: freeze eval and
training sets for model fine-tunes once their tags are cleaned.

**Branch/target:** developed on `develop` after the `v1.7.0rc1` cut, to land in `v1.7.0rc2`.
This plan is scoping + design; implementation follows in the same branch.

---

## 1. Semantics (the rules everything below implements)

1. A set has a persistent boolean `locked`. Locking and unlocking are always allowed (the lock
   protects data, it is not a permission system — the owner can toggle it freely).
2. While a set is locked:
   - The set's own fields (name, description, project, icon, color) cannot be changed. The only
     accepted PATCH is one that changes nothing but `locked` (i.e. unlock).
   - The set cannot be deleted (unlock first — deliberate friction so a misclick can't destroy
     a frozen eval set).
   - No pictures can be added to or removed from the set (single, bulk, and bulk-replace paths).
3. A picture that belongs to **at least one** locked set has its *label data* locked everywhere:
   - No confirmed-tag changes (add / remove one / remove-all / clear / replace-via-PATCH).
   - No description changes.
   - No user score changes, and no soft-delete of the picture (deleting would silently mutate
     the frozen set; both are label/curation data).
   - Review decisions (accept/dismiss on tag suggestions, prediction confirm/reject) are
     refused for that picture — they write the POS/NEG label ledger, which is exactly the data
     the lock exists to freeze.
4. Explicitly **not** locked (stays editable):
   - Membership of the picture in *other, unlocked* sets, and project assignment — these are
     organisation, not label data.
   - Machine-derived fields written by background tasks (embeddings, quality metrics, smart
     score recompute, new tag *predictions*). Predictions may accumulate; they just can't be
     confirmed onto a locked picture.
5. Tag review:
   - A locked set is greyed out with a lock icon and not selectable as a review scope.
   - Pictures in locked sets never appear as review *suspects* (the editable item). They may
     still appear as *twins* — the comparison/guide image — marked with a lock so the user
     knows why there are no controls for it.
6. Every place a picture or set is shown locked carries a tooltip that names the locking
   set(s) and says how to unlock.

Decision points folded into the rules above (flagging them so review can veto): blocking
set-delete (2), blocking picture soft-delete and score (3), allowing project/other-set
membership (4). Each is one line of guard code to flip if a different call is preferred.

---

## 2. Backend

### 2.1 Model + migration

- `pixlstash/db_models/picture_set.py` — add to `PictureSet` (after `set_color`, line 51):
  `locked: bool = Field(default=False)` (mirrors the `deleted` flag pattern on `Picture`,
  `picture.py:201`).
- New migration `pixlstash/migrations/versions/0073_add_pictureset_locked.py`,
  `down_revision = 0072_...`. Copy the defensive shape of `0043_pictureset_icon_color.py`
  (same table): inspect → skip if table absent → guarded
  `op.add_column("pictureset", sa.Column("locked", sa.Boolean(), nullable=False,
  server_default=sa.false()))`. Idempotent upgrade/downgrade; covered by
  `tests/test_migrations.py`.

### 2.2 Lock guard helper (one source of truth)

New module `pixlstash/services/set_lock_service.py`:

- `locked_set_names_for_pictures(session, picture_ids) -> dict[int, list[str]]` — one query
  joining `PictureSetMember` → `PictureSet WHERE locked`. Used by guards and by the
  members-listing endpoint below.
- `enforce_pictures_not_locked(session, picture_ids, action: str)` — raises
  `HTTPException(423, detail={"code": "pictures_locked", "action": action,
  "sets": [{"id","name"}], "picture_ids": [...]})`.
- `enforce_set_not_locked(session, picture_set, action: str)` — same shape with
  `code="set_locked"`.

`423 Locked` is semantically exact and cannot be confused with the existing 403 (token scope)
or 409 (name conflict) meanings. The structured `detail` lets the frontend build the "why"
tooltips without string-parsing. Guards are plain function calls at the top of each mutation
closure (inside `run_task` where the session lives) — the same threading pattern as
`enforce_picture_scope` (`routes/pictures/_helpers.py:340`).

Stack note: membership mutations are stack-atomic via `expand_picture_ids_to_stacks`; guards
must run on the **post-expansion** id list so a stack sibling inside a locked set blocks the
whole operation.

### 2.3 Guard call sites

Set-level (`routes/picture_sets.py`, logic is inline in route closures):

| Endpoint | Function (line) | Behaviour when locked |
|---|---|---|
| `PATCH /picture_sets/{id}` | `update_picture_set` (1086) | allow iff the only effective change is `locked`; else 423 |
| `DELETE /picture_sets/{id}` | `delete_picture_set` (1277) | 423 |
| `POST .../members/{pid}` | `add_picture_to_set` (1371) | 423 |
| `DELETE .../members/{pid}` | `remove_picture_from_set` (1464) | 423 |
| `POST .../members` (bulk) | `bulk_add_pictures_to_set` (1513) | 423 |
| `PUT .../members` (replace) | `bulk_replace_pictures_in_set` (1592) | 423 |

Picture-level (`enforce_pictures_not_locked` on the target ids):

- `routes/tags.py` — all five confirmed-tag mutations (add 135, delete-one 254,
  remove_all 321, clear 378; list stays open).
- `routes/pictures/_crud.py` — `patch_picture` (1962): block `description`, `score`, and the
  `tags` replacement branch (`_replace_tags`, 2031). Also the soft-delete endpoints
  (single ~2362, bulk ~2419) — bulk paths **skip** locked ids and report
  `{"skipped_locked": [...]}` rather than failing the whole batch.
- `services/impossible_tag_clear_service.py` — bulk clear/restore: exclude locked pictures
  from the affected set, report the skipped count.
- Tag-review decision endpoints (`routes/tag_suggestions.py`, `routes/tag_predictions.py`) —
  refuse accept/dismiss/confirm/reject when the *suspect* picture is locked. (These write
  `label_state`; see rule 3.)

Ingest/sync check during implementation: any path that writes tags to an *existing* picture
outside these routes (watch-folder re-import, reference-folder sidecar sync) must be audited
and either guarded or confirmed not to touch confirmed tags. `_sync_sidecar` (export
direction) is unaffected.

### 2.4 API surface

- `PictureSetResponse` (picture_sets.py:47) and `PictureSetPicturesResponse` (:70) gain
  `locked: bool = False`. List/read endpoints serialize via `safe_model_dict`, so the column
  flows automatically; the response models must still be updated for the OpenAPI schema
  (enforced by `tests/test_openapi_response_schemas.py`).
- `PATCH /picture_sets/{id}` accepts `locked: bool` using the existing `_UNSET` sentinel
  pattern (`:1097`, `:1154-1157`) — this is both the checkbox and context-menu toggle. No
  separate lock endpoint needed.
- **New:** `GET /picture_sets/locked-members` →
  `{"sets": [{"id", "name", "picture_ids": [...]}]}`. One round-trip for the frontend to know
  which pictures are locked and by which set names (grid badges, overlay, context-menu
  tooltips). Cheap: it only touches locked sets, which are few. Owner + scoped-read tokens
  may call it (read-only).
- `GET /pictures/{id}/metadata` (`_crud.py:1051`) gains `locked_by_sets: [{"id","name"}]` so
  the overlay has the reason without a second request.
- Lock/unlock PATCH fires `server.vault.notify(EventType.CHANGED_PICTURES, {ids: members})`
  after commit — the grid's existing coalesced-refresh pill picks it up and other clients
  refresh badges.

### 2.5 Review scan exclusion

`services/tag_scan_service.py` selects scan candidates with `Picture.deleted.is_(False)`
(lines 276 and 329). Add `~Picture.id.in_(select(PictureSetMember.picture_id).join(
PictureSet).where(PictureSet.locked))` at both points — suspects only. Twin selection is
untouched, which is precisely the "guide only" behaviour. Additionally:

- `POST /reviews` / `GET /reviews/preview` with `set_id` of a locked set → 423 (backstop;
  the UI already greys it out).
- Existing *open* review sessions created before a lock: the scan-side exclusion applies on
  refresh; the decision-endpoint guard (2.3) protects already-materialised suggestion rows.
  The card for a locked suspect simply can't be decided — frontend shows the lock reason
  (see 3.6).

---

## 3. Frontend

Membership knowledge is the one structural gap: grid picture objects don't carry set ids
(`Picture.grid_fields()` excludes them). Fix with a small dedicated store, not by widening
the grid payload.

### 3.1 New store: `frontend/src/stores/useLockedSetsStore.js`

- Fetches `GET /picture_sets/locked-members` on app start and whenever sets change
  (`refresh-sidebar` emit path / `CHANGED_PICTURES` websocket event, same triggers the
  sidebar uses).
- Exposes `isLocked(pictureId)`, `lockedSetNames(pictureId)`, `lockReason(pictureId)`
  (ready-made tooltip string), and `lockedSetIds` (Set) for greying set rows.
- Tooltip copy (single source, reused everywhere):
  *"Locked — this picture is in the locked set '<name>'. To edit it, unlock the set: right-
  click the set in the sidebar and choose Unlock, or untick Locked in Edit set."*
  Multiple sets → names joined with commas.
- Co-located `useLockedSetsStore.test.js` (vitest) for the mapping + reason strings.

### 3.2 Picture set dialog — `components/editors/PictureSetEditor.vue`

- Add `locked` to `localSet` (:164) and the PATCH body in `saveSetFromEditor()` (:229).
- A "Locked" checkbox row (native checkbox styled with design tokens — the dialog has no
  checkbox widget yet) with helper text: *"Locked sets are read-only: no edits to the set,
  its pictures' tags, or descriptions until unlocked."*
- When the set is already locked, name/description/project/icon/color inputs render
  `disabled` with `title` = lock reason; only the checkbox stays active, and save sends only
  `{locked: false}`. This mirrors the server rule in 2.3 so the user can't type into fields
  whose save would 423.

### 3.3 Sidebar — `components/panels/SideBar.vue`

- Context menu, set branch (:6001-6186): new entry **Lock set / Unlock set**
  (`mdi-lock-outline` / `mdi-lock-open-variant-outline`) above Delete. Handler follows
  `applySetAppearance` (:2052): PATCH `{locked: !set.locked}`, refresh sidebar + locked-sets
  store. Edit stays enabled when locked (the dialog is the unlock surface); Delete gets
  `:disabled` + lock-reason `title` when locked.
- Set rows (:4365, :4520 areas): trailing small `mdi-lock-outline` when `set.locked`, with
  `title` tooltip.

### 3.4 Image grid — `components/views/ImageGrid.vue`

- New badge in the top-left permanent badges column (:507-552), modelled byte-for-byte on the
  share badge (:543-551): `v-if="lockedSetsStore.isLocked(img.id)"`, class
  `thumbnail-lock-badge thumbnail-badge`, icon `mdi-lock-outline`,
  `:title="lockedSetsStore.lockReason(img.id)"`. Style: same `.thumbnail-badge` chrome but
  with reduced-opacity background (design-token surface at ~55%) — the requested
  "transparent" look — sized via the existing `badgeIconSizes` (:1219).
- `ImageGridContextMenu.vue`: compute a `lockReason` for the current selection (any selected
  id locked → reason string naming the picture count + set). Pass it into the existing
  disabled/tooltip plumbing exactly like `groupingLockReason` (:29, :47): tag-editing,
  description, score, and delete entries become `:disabled` with the reason as `title`.
  Add-to-set (`AddToEntityControl`) stays enabled but locked sets inside it render greyed
  with the lock icon and are unselectable (membership in *other* sets is allowed).

### 3.5 Image overlay — `components/views/ImageOverlay.vue` + panels

- Toolbar: persistent translucent lock chip (`mdi-lock-outline`) using the toolbar's
  `v-tooltip #activator` pattern (:57, :273), tooltip = lock reason. Edit actions already
  gated by `!isReadOnly` additionally gate on `lockedSetsStore.isLocked(currentId)`.
- `OverlayTagsPanel.vue` / `OverlayDescriptionPanel.vue`: read-only presentation when locked
  (inputs disabled, add-tag hidden), one inline lock note with the tooltip text — the panel
  equivalent of "tell the user why".
- `OverlayMetadataPanel.vue` (:340-421): new row "Locked by" listing set names, fed by
  `locked_by_sets` from the metadata endpoint (2.4).

### 3.6 Tag review — `components/reviews/*`

- `NewReviewDialog.vue` (:88-96): the set scope `<select>` can't render icons in options, so
  convert it to the same custom dropdown pattern used elsewhere (AppSelect/listbox). Locked
  sets: greyed row, `mdi-lock-outline`, not selectable, `title` = *"'<name>' is locked —
  its pictures are read-only. Unlock it to review its tags."* `useReviewSessionsStore.js`
  `fetchScopeOptions` (:546) keeps the `locked` flag on `store.sets` (it arrives free via
  `safe_model_dict`).
- `ReviewRail.vue` (:305): lock icon next to a session's set-scope label when that set is
  locked.
- `ReviewPairCard.vue` / `ReviewBinaryCard.vue`: when the *twin/neighbour* is locked, a small
  corner lock with `title` = *"Reference only — this image is in the locked set '<name>'."*
  When a *suspect* is locked (pre-lock session, 2.5), the card's decision buttons disable
  with the lock reason instead of surfacing a 423.

### 3.7 Read-only interplay

The app-wide `isReadOnly` (`utils/apiClient.js:10`) is orthogonal (token capability vs data
state). Gates compose as `disabled = isReadOnly || isLocked(id)` with the lock reason taking
tooltip precedence, since it is the actionable one.

---

## 4. Tests

Backend (style: `tests/test_picture_sets.py` — real `Server` + `TestClient`):

- Lock/unlock via PATCH; `locked` present in list/read responses (schema test
  `test_openapi_response_schemas.py` updated).
- Locked-set rejection matrix: rename/description/icon/color PATCH, delete, all four
  membership mutations → 423 with `code=set_locked`; unlock-only PATCH succeeds.
- Cross-set rule: picture in locked set A and unlocked set B — tag add/remove, description
  PATCH, score PATCH, soft-delete → 423 naming A; add/remove picture to/from B succeeds;
  after unlocking A everything succeeds.
- Stack expansion: sibling-in-locked-set blocks the membership op post-expansion.
- Bulk delete skips locked ids and reports them.
- Review: scan on a library with a locked set yields no locked suspects but still uses
  locked pictures as twins; `POST /reviews` with locked `set_id` → 423; accept/dismiss on a
  locked suspect's suggestion → 423 (authz-test style of `test_picture_mutation_scope.py`).
- Migration round-trip in `tests/test_migrations.py`.

Frontend (vitest, co-located): locked-sets store mapping/reason strings; NewReviewDialog
option disabling; grid badge visibility given store state. One Playwright e2e happy path
(lock via context menu → badge appears → tag edit blocked → unlock restores) in
`frontend/e2e/`.

Per the rc1-readiness lesson (CI runs a subset of backend files): add the new/touched test
files to the CI backend gate in the same PR.

## 5. Sequencing

1. **Backend core** — column, migration, guard service, set-level + picture-level guards,
   response models, locked-members + metadata endpoints, notify. Full rejection-matrix tests.
2. **Frontend surfaces** — locked-sets store, set dialog checkbox, sidebar menu + row icon,
   grid badge, overlay chip/panels, context-menu gating.
3. **Review integration** — scan exclusion, review-creation backstop, decision guards,
   NewReviewDialog/rail/card UI.
4. **Polish** — tooltip copy pass against `docs/design/visual-language.md` register, e2e,
   CHANGELOG entry, CI gate additions.

Phases 1–2 are independently mergeable (lock enforced + visible); 3 completes the spec.
Rough effort: 1 ≈ a day, 2 ≈ a day, 3 ≈ half a day, plus test/polish.

## 6. Relationship to existing freeze/split infrastructure

The tag-review work already has per-tag eval slices (`POST /tag_eval_slices`) and per-picture
train/eval `PictureSplit` (see `docs/reviews/tag-review-accuracy-freeze-conflicts-ux-spec.md`).
Set-locking is deliberately simpler and user-facing: an explicit, whole-set, hard freeze of
label data. It composes with (and does not replace) those: a locked set is the natural
container for a frozen eval slice's pictures. Follow-up idea (out of scope for RC2): offer
"assign split + lock" as one action when freezing an eval slice.

## 7. Risks / open questions

- **Guard coverage is enumerative** — the main correctness risk is a missed mutation path
  (e.g. a future bulk endpoint). Mitigation: single guard service, and a guardrail test that
  greps mutation routes for the guard call, in the spirit of `test_architecture_guardrails.py`.
- **`PATCH /picture_sets` semantics**: "only-unlock allowed while locked" needs care with
  no-op fields the frontend echoes back (compare against current values, not key presence).
- **Locked-members payload size**: thousands of ids per locked set is fine as JSON; if it
  ever isn't, the endpoint can grow an `If-None-Match`/version guard.
- **Decision points** listed in §1 (set delete blocked, picture delete/score blocked,
  project/other-set membership allowed) — cheap to flip, but should be confirmed before
  Phase 1 lands.

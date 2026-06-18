# Security sign-off: `feature/slick-grid-updates`

Reviewer: Chief Security Officer
Date: 2026-06-14
Scope: uncommitted full-stack diff on `feature/slick-grid-updates` (origin-aware
WebSocket event envelope + `X-Client-Id` request pipeline). Required by CLAUDE.md
because the change touches the HTTP request pipeline and the owner-only WS payload.

## Verdict: PASS

This is a clean, well-scoped change. The `X-Client-Id` mechanism is echo-matching
only and never reaches an authorization decision, the WS stream stays owner-only,
header handling is bounded and quiet, and no endpoint lost a scope check. All six
claimed invariants hold against the code. Tests pass (24/24 in
`tests/test_websocket_auth.py`) and ruff is clean on every touched Python file.

There is one pre-existing object-level authz gap (write-capable resource-scoped
tokens can mutate pictures outside their scope on the picture-mutation handlers).
It is **not introduced or worsened by this diff** — every touched mutation handler
already lacked the check at HEAD, and this change adds `request: Request` for the
origin id only, touching no auth path. I am **not blocking the merge on it**, but
it is logged below as an accepted/tracked risk with an owner, because several of
the affected handlers were modified here and CLAUDE.md's coverage discipline says
the decision must be recorded. See Finding F1.

---

## The six invariants

1. **`X-Client-Id` is NEVER used for authorization or scoping — CONFIRMED.**
   Grepped every read of `origin_client_id` / `request.state.origin_client_id` /
   `origin_client_id_var` across `pixlstash/`. Every single one flows into the
   event `data` dict (`{"origin_client_id": ...}`) or is the middleware capture
   itself. A second grep for the id appearing in any `if`/scope/auth/filter/owner/
   enforce context returned **nothing**. The id never gates a branch, a query, or
   an access check. (`request_origin.py:43-54`, `server.py:1276-1281`,
   `server.py:1385-1386`, and the handler emit sites.)

2. **WS stream stays owner-only — CONFIRMED.** `server.py:1401` still reads
   `if not client.get("owner"): continue` *before* `ws.send_json`. The uniform
   envelope (`source`, `origin_client_id`, `change_kind`) is appended to `payload`
   at `server.py:1385-1389`, *above* the per-client loop, so it is computed once
   and only ever delivered to owner clients. Scoped/READ tokens authenticate the
   socket but receive nothing (verified by `test_broadcast_delivers_only_to_owner_clients`,
   passing). The echoed `origin_client_id` is the requester's own opaque random
   UUID (`crypto.randomUUID()`), reaches only owner peers, and carries no PII or
   resource data. No leak.

3. **No endpoint lost its scope enforcement — CONFIRMED (nothing removed).**
   For every touched mutation handler I diffed HEAD vs. working tree: the change
   adds `request: Request` to the signature and one
   `origin_client_id = getattr(request.state, "origin_client_id", None)` line, then
   threads that value into the `notify(...)` `data` dict. No scope check was moved,
   removed, weakened, or short-circuited. The guarded read siblings keep their
   `enforce_picture_scope` calls verbatim (`tags.py:170` `list_picture_tags`,
   `_crud.py:724/899/1110/1707`, `comfyui.py:1661`). See the coverage matrix and F1
   for the pre-existing state of the *write* handlers.

4. **Header handling is safe — CONFIRMED.** `_sanitize_client_id`
   (`request_origin.py:43-54`) drops a value over `MAX_CLIENT_ID_LENGTH` (200)
   entirely rather than truncating, so a crafted long value can never alias a
   legitimate short one. The id is opaque and is never interpolated into a log
   line at INFO, a response body, a SQL string, or a filesystem path — it only
   ever lands in a JSON `data` dict that is serialized by `send_json`. The
   `set_filters` WS path independently re-caps at 200 (`server.py:2412-2416`). The
   frontend mirror also slices to 200 (`apiClient.js`). Oversized/garbage headers
   do not break the pipeline (`test_x_client_id_header_populates_request_state`,
   passing). No injection surface.

5. **`change_kind:"removed"` on soft-delete — CONFIRMED safe.**
   `delete_picture` (`_crud.py:2103`) emits `CHANGED_PICTURES` with
   `change_kind:"removed"` and the deleted id, behind the same owner-only gate, so
   non-owners get nothing. Delete authorization is unchanged: the diff adds the
   broadcast *after* the existing `delete_pic` task and its 404-on-not-found; the
   id coercion is wrapped and logs a warning on failure instead of swallowing it
   (`_crud.py:2130-2135`, satisfies the no-`except/pass` rule). The id sent is one
   the owner just acted on — no new information disclosed.

6. **ComfyUI client_id not conflated — CONFIRMED.** In both `comfyui.py:1365-1371`
   and `:1483-1488`, the ComfyUI websocket `client_id` (from the request payload)
   and the PixlStash `origin_client_id` (from `request.state`) are separate
   variables with an explanatory comment. Only `origin_client_id` is carried into
   the PixlStash WS envelope (`comfyui.py:1022`, `:1455`, `:1593`). The ComfyUI
   `client_id` never enters the PixlStash event stream, and the PixlStash id is
   never sent to the ComfyUI host (the apiClient interceptor attaches `X-Client-Id`
   only on same-origin requests — external absolute URLs like ComfyUI get neither
   token nor client id). No cross-system leak.

---

## Coverage matrix — touched data endpoints

State key: **(a)** calls a scope chokepoint · **(b)** deliberate scope-exempt ·
**(pre)** pre-existing state, unchanged by this diff. "READ-blocked" = the global
auth middleware (`auth.py:1542-1553`) 403s all non-GET for READ-scoped tokens.

**Update (2026-06-17, F1 closure pass):** the empty cells below are now filled.
Every F1 mutation handler calls the chokepoint; the scrapheap pair is owner-only
(F2 decided). State now reflects the closure work in this PR.

| Endpoint | Method | Touched by diff | Object-level scope check | Changed here? | Notes |
|---|---|---|---|---|---|
| `/pictures/{id}/tags` (`add_tag_to_picture`) | POST | sig + origin | **(a)** `enforce_picture_scope` `tags.py:97` | F1 closed | was none at HEAD |
| `/pictures/{id}/tags/{tag_id}` (`remove_tag_from_picture`) | DELETE | sig + origin | **(a)** `enforce_picture_scope` `tags.py:213` | F1 closed | |
| `/pictures/{id}/tags/remove_all` (`remove_tag_from_picture_everywhere`) | POST | sig + origin | **(a)** `enforce_picture_scope` `tags.py:276` | F1 closed | |
| `/pictures/{id}/tags` (`clear_all_tags_on_picture`) | DELETE | sig + origin | **(a)** `enforce_picture_scope` `tags.py:328` | F1 closed | |
| `/pictures/{id}/tags` (`list_picture_tags`) | GET | not modified | **(a)** `enforce_picture_scope` `tags.py` | no | reference sibling, intact |
| `/pictures/project` (`set_project_for_pictures`) | PATCH | sig + origin | **(a)** `fetch_scope_allowed_picture_ids` `_crud.py:394` | F1 closed | batch; 403 if none in scope |
| `/pictures/apply-scores` (`apply_scores_for_pictures`) | POST | sig + origin | **(a)** `fetch_scope_allowed_picture_ids` `_crud.py:608` | F1 closed | batch; 403 if none in scope |
| `/pictures/{id}/face` (`create_picture_face`) | POST | sig + origin | **(a)** `enforce_picture_scope` `_crud.py:959` | F1 closed | |
| `/pictures/{id}/face/{index}` (`delete_picture_face`) | DELETE | sig + origin | **(a)** `enforce_picture_scope` `_crud.py:1033` | F1 closed | |
| `/pictures/{id}` (`delete_picture`, soft-delete) | DELETE | sig + origin + `removed` broadcast | **(a)** `enforce_picture_scope` `_crud.py:2147` | F1 closed | broadcast safe (inv. 5) |
| `/pictures/scrapheap/restore` (`restore_scrapheap`) | POST | sig + origin | **(b)** `require_unscoped_owner` `_crud.py:1927` | F1/F2 closed | owner-only, library-wide |
| `/pictures/scrapheap` (`delete_scrapheap_selection`) | DELETE | sig + origin | **(b)** `require_unscoped_owner` `_crud.py:1990` | F1/F2 closed | owner-only, irreversible |
| `/pictures/{id}` (`patch_picture`) | PATCH | sig has `request` + origin notify | **(a)** `enforce_picture_scope` `_crud.py` (added this PR) | **S1 closed** | was **UNGUARDED** at HEAD — the matrix previously misstated this as a guarded sibling (CSO S1). Mutates score/description/tags. |
| `characters` `assign_face_to_character` | POST | sig + origin | **(a)** `_enforce_face_mutation_scope` `characters.py:1291` | F1 closed | both face_ids + picture_ids branches |
| `characters` `remove_character_from_faces` | DELETE | sig + origin | **(a)** `_enforce_face_mutation_scope` `characters.py` | F1 closed | both face_ids + picture_ids branches |
| `/pictures/import` (`import_pictures`) | POST | sig + origin | n/a — creates new owner-uploaded rows | no | not per-object read/mutate |
| `/pictures/plugins/{name}` (`run_picture_plugin`) | POST | sig + origin | **(a)** `fetch_scope_allowed_picture_ids` `_misc.py:174` | F1 closed | all-or-nothing (captions aligned) |
| `/comfyui/run_i2i` (`run_comfyui_i2i`) | POST | origin passthrough | **(a)** `enforce_picture_scope` per id `comfyui.py` (added this PR) | **S2 closed** | was **UNGUARDED** at HEAD — reads source bytes + uploads to ComfyUI host; matrix previously implied guarded (CSO S2) |
| `/comfyui/run_t2i` (`run_comfyui_t2i`) | POST | origin passthrough | **(a)** `enforce_picture_scope` on `source_picture_id` `comfyui.py` (added this PR) | **S2 closed** | optional source-picture reference now scoped |
| `/comfyui/...workflow` (`get_picture_comfyui_workflow`) | GET | not modified | **(a)** `enforce_picture_scope` `comfyui.py` | no | reference sibling, intact (the only comfyui path guarded before this PR) |

No empty "object-level scope check" cells remain. State (a) = calls the
chokepoint; state (b) = deliberate owner-only exemption via
`require_unscoped_owner`.

---

## Findings

### F1 — (Pre-existing, not a regression) Picture-mutation handlers have no object-level scope check
**Severity: Medium (tracked, not blocking this merge). CWE-639 (BOLA / IDOR).**

The picture-mutation handlers in the matrix above (tags add/remove/clear, faces
add/delete, set-project, apply-scores, scrapheap restore/delete, soft-delete,
character face assign/remove) enforce no per-object scope. They rely solely on the
global middleware rule that 403s **READ**-scoped tokens on any non-GET
(`auth.py:1542-1553`). But an **ALL-scope token can be resource-restricted**
(`token_scope is not None` with a `resource_type`, issuable via the token-create
API at `auth.py:903-912`), and such a token is write-capable — the READ block does
not catch it. With that token, `POST /pictures/{id}/tags` (or any of the above)
against a picture *outside* the token's granted resource would mutate it, because
the handler never calls `enforce_picture_scope`. This is the "ALL+resource_type
footgun" called out in CLAUDE.md and `backend_architecture.md` §16.2.

- **Why it is not a blocker for this diff:** I diffed HEAD vs. working tree for
  every one of these handlers. None had the check at HEAD; this change adds only
  `request: Request` and an origin-id read, threading it into the event `data`.
  No check was removed or bypassed. The diff neither creates nor widens the gap.
- **What an attacker does with it:** a holder of a write-capable, resource-scoped
  ALL token tags/scores/deletes pictures it was not granted. Blast radius is the
  whole picture library for any such token. It requires the owner to have minted a
  resource-scoped *write* token and handed it out, which is the less common token
  shape (most shares are READ), which is why I rate it Medium not High.
- **Fix (separate hardening task, not this PR):** add
  `enforce_picture_scope(server, request, pic_id)` immediately after id parse on
  each single-picture handler, and gate the batch handlers
  (`set_project_for_pictures`, `apply_scores_for_pictures`,
  `remove_character_from_faces` by `picture_ids`, scrapheap by id subset) by
  filtering against `fetch_scope_allowed_picture_ids(server, request)` (403 / drop
  out-of-scope ids). Because `request` is *now already wired into every one of
  these handlers by this very diff*, the follow-up is a one-liner per handler. Add
  tests in both directions per CLAUDE.md.
- **Owner / revisit:** `senior-backend-developer`, as the §16.2 centralised-chokepoint
  work; track as the next authz hardening item. Until then this is an
  **accepted, documented risk** (rationale: pre-existing, no regression, narrow
  token shape required).

**RESOLVED in this PR (2026-06-17, `senior-backend-developer`).** Every handler in
the matrix now calls the chokepoint (state (a)) or is a deliberate owner-only
exemption (state (b)); see the matrix above for the exact helper + line per
handler. The character handlers resolve `face_ids` → their `picture_id`s and
check the full affected set, so the `face_ids` alternate branch is guarded, not
just `picture_ids`. New both-directions tests live in
`tests/test_picture_mutation_scope.py` (15 tests, all green): out-of-scope target
→ 403 on each handler including the `face_ids`/`picture_ids` branches, owner /
unscoped → still succeeds (no over-block). **Caveat — read F3:** these
per-handler guards close the gap for any token whose `request.state.token_scope`
is populated, but the auth middleware does **not** populate `token_scope` for an
`ALL`-scope token carrying a `resource_type`. So the real-world ALL+resource
attacker shape F1 describes is **not** actually stopped by these guards alone; it
needs the middleware fix in F3. F1's *handler-level* gap is closed and is the
correct building block for the §16.2 chokepoint; the *footgun* it leans on is
re-filed as F3 because closing it is a middleware change with cross-cutting blast
radius, out of scope for a per-handler pass.

### F2 — Scrapheap mutators: owner-only decision — RESOLVED
**Severity: Low / informational. Decision recorded.**

**Decision: owner-only via `require_unscoped_owner`** (state (b)), applied to both
`restore_scrapheap` (`_crud.py:1927`) and `delete_scrapheap_selection`
(`_crud.py:1990`). Rationale:
1. Both have a **library-wide** branch — with no `picture_ids` they restore /
   permanently purge *every* deleted picture. `fetch_scope_allowed_picture_ids`
   would only constrain the explicit-ids path; the "all deleted" branch runs an
   unfiltered `Picture.deleted.is_(True)` scan that a scoped token must never
   reach. Retrofitting id-subset filtering onto a "do everything deleted"
   operation is exactly the alternate-branch miss CLAUDE.md warns about.
2. `delete_scrapheap_selection` is **irreversible and destructive** (drops DB
   rows + disk files + writes the deletion ledger) — the same risk class as the
   snapshot/restore ops that already use `require_unscoped_owner`.
3. `require_unscoped_owner` is genuinely fail-closed for this case: it inspects
   `request.state.matched_token.resource_type` directly (`auth.py:700`), so —
   unlike `enforce_picture_scope` — it **does** reject an ALL+resource_type token
   (it is not subject to the F3 footgun). This is the one part of the F1 pass
   that is effective against the ALL+resource shape today.

---

### F3 — (NEW, surfaced during F1 closure) `ALL`+`resource_type` tokens bypass `token_scope`, so every `enforce_picture_scope` guard is a no-op for them
**Severity: Medium (latent; not reachable by the current share-token UI). CWE-639.**

While closing F1 I reproduced the root cause F1 only half-named. The auth
middleware builds `request.state.token_scope` **only when `matched_token.scope !=
"ALL"`** (`auth.py:1463`). An `ALL`-scope token carrying a `resource_type`
therefore has `token_scope is None`. Both chokepoint helpers read *only*
`request.state.token_scope` (`enforce_picture_scope` `_helpers.py:342`,
`fetch_scope_allowed_picture_ids` `filter_helpers.py:234`), so for an ALL+resource
token they return "owner / full access" and **every BOLA guard — the new
mutation guards AND the pre-existing read-sibling guards (`get_picture`,
`get_picture_field`, `list_picture_tags`, batch likeness, …) — is bypassed.**

- **Reproduced (empirically, before/after, then reverted the throwaway test):**
  minted an `ALL` token scoped to a picture_set containing only picture #1, then
  hit the *guarded* read sibling `GET /pictures/2/tags` with it →
  **`200` with the real tag data**, not 403. The guard ran but `token_scope` was
  `None`, so it passed straight through. This is the exact attack F1 described,
  and it is not stopped by the per-handler `enforce_picture_scope` calls.
- **This is documented as the intended-but-unimplemented fix in
  `backend_architecture.md` §16.2** ("reject `ALL`+`resource_type` at
  `create_token`, and/or have the central check also consult
  `request.state.matched_token`"). It is a latent hole: the share-token UI only
  mints `scope=READ` for resource-scoped tokens, and a READ resource token is
  blocked at the middleware before it reaches a mutation handler. So no current
  *UI* flow can exploit it — but the token-create API will persist an
  ALL+resource token, and any already-persisted one bypasses every guard.
- **Why this was filed separately, not fixed in the F1 pass:** the fix is in the
  auth middleware, not the handlers. Two options, both with broader blast radius
  than a per-handler change:
  - (i) **Reject `ALL`+`resource_type` at `create_token`** — smallest, but does
    nothing for already-persisted tokens and would make the F1 mutation guards
    untestable with a real token (no reachable scoped *write* shape would remain).
  - (ii) **Populate `token_scope` from `matched_token` regardless of scope** (or
    have the helpers consult `matched_token` like `require_unscoped_owner` does) —
    this is what actually makes the F1 guards effective against the attacker
    shape, but it changes `request.state.token_scope` semantics read by ~15 call
    sites (`create_token`'s "scoped tokens can't mint tokens" gate, the
    filesystem/reference-folder owner-only checks, the projects scoping, the
    READ-block keyed on `token_scope.scope == "READ"`). Each needs verification;
    most changes are *tightening*, but it is security-critical middleware work.
**RESOLVED in this PR (2026-06-17).** Closed at the source with two defences,
chosen as option (i) + a residual backstop rather than the broader option (ii)
`token_scope` refactor (which stays as the §16.2 direction):

1. **Mint vector — `create_token` (`auth.py`).** Rejects `scope == "ALL" and
   resource_type is not None` (400) before the `UserToken` is constructed. There
   is exactly one mint path (`/users/me/token` → `create_token`), so no
   ALL+resource token can be created any more. Tests:
   `tests/test_read_token_security.py::TestAllScopeResourceTokenRejected`
   (the dangerous combo → 400; READ+resource and ALL+no-resource still mint).
2. **Residual — auth middleware (`auth.py`).** A legacy or snapshot-restored
   ALL+resource token (created before fix 1, or copied in via a DB restore) is
   rejected **fail-closed** (403) the moment it is matched, before any handler
   runs — so it can never reach the `token_scope is None` bypass. This neutralises
   the residual the mint guard alone could not.

Net effect: the ALL+resource shape can neither be created nor used. The per-handler
F1 guards remain the correct §16.2 building blocks (effective against any populated
`token_scope`), and `require_unscoped_owner` (F2) remains effective directly on
`matched_token`. Option (ii) (deriving `token_scope` from `matched_token` for all
scopes) is still the cleaner long-term model and is left to the §16.2 chokepoint
work, but is no longer required for safety.

---

### S4 — (Tracked, not this PR) Picture↔set membership mutators lack object scope
**Severity: Low. CWE-639. Pre-existing, out of this PR's scope.**

`add_picture_to_set` and `bulk_add_pictures_to_set` (`picture_sets.py`) mutate
picture↔set membership by `picture_id`, take no `request`, and have no
object-scope check. They were not touched by this PR (no `origin_client_id`),
so they are outside the F1 closure surface, but they confirm the BOLA class is
wider than this matrix. Feed to the §16.2 centralised-chokepoint backlog.

### Note on test methodology (CSO S3)
`tests/test_picture_mutation_scope.py` exercises the guards by **monkeypatching
the chokepoint helpers** to simulate a token scoped to one id (the same
technique as `test_batch_character_likeness_scope.py`), not by driving a real
scoped *write* token end-to-end. That is deliberate and now accurate: after F3,
no scoped-write token shape is reachable (READ resource tokens are blocked at
the middleware; ALL+resource can neither be minted nor used). So the tests
assert "the handler invokes the guard, both directions"; the "a real token is
blocked end-to-end" property is now enforced by the F3 middleware reject +
READ-block, covered in `test_read_token_security.py`.

---

## What I verified by running it

- `tests/test_picture_mutation_scope.py` → **15 passed** (`--fast-captions
  --force-cpu`): out-of-scope target → 403 on every newly-guarded handler
  including the character `face_ids` and `picture_ids` branches; owner / unscoped
  → still 200 (no over-block).
- Regression suites with the changes applied: `tests/test_read_token_security.py`
  → **58 passed**; `tests/test_batch_character_likeness_scope.py` +
  `tests/test_restore.py` → **47 passed**; `tests/test_characters_api.py` →
  **passed** (exit 0). Nothing regressed.
- `ruff format --check` and `ruff check pixlstash` → **All checks passed**;
  `ruff check` on the new test file → clean.
- F3 reproduced empirically with a throwaway test (an ALL+picture_set token read
  an out-of-scope picture's tags and got `200`), confirming the §16.2 footgun;
  the throwaway test was removed afterward.

## Release gate (updated 2026-06-17, F1/F3 closure pass)

- **F1 — CLOSED.** Every touched picture-mutation handler now calls the
  chokepoint (state (a)) or is a deliberate owner-only exemption (state (b)),
  including the two the original matrix misstated (`patch_picture` S1,
  `run_comfyui_i2i`/`t2i` S2).
- **F2 — CLOSED.** Scrapheap mutators owner-only via `require_unscoped_owner`.
- **F3 — CLOSED.** ALL+resource tokens can neither be minted (`create_token`
  reject) nor used (middleware fail-closed reject of any matched ALL+resource
  token). This is what actually stops the BOLA shape F1 described.
- **S1 / S2 — CLOSED** (guards added; matrix corrected — the false "(a)" cells
  were the merge-blocker the independent sign-off caught).
- **Tracked (not this PR):** S4 (picture↔set membership mutators) → §16.2
  backlog. Option (ii) (`token_scope` from `matched_token` for all scopes)
  remains the cleaner long-term model for the §16.2 centralised chokepoint, but
  is no longer required for safety.

## Independent re-verification (2026-06-17) — PASS

A second CSO pass (not the author of the S1/S2/F3 fixes) re-verified the closure
against the code and ran the suites: **PASS, safe to merge.** Confirmed S1
(`patch_picture` guard before all writes), S2 (i2i per-id + t2i source guards
before fetch/exfiltration; no other unguarded comfyui handler — upscale does not
exist), F3 (sole mint path rejects ALL+resource; middleware fail-closed reject
before `token_scope` is computed; `/share` and WS independently cannot leak to
the shape), and matrix integrity (every cell matches code; S4 tracked, not
falsely closed). Combined `test_picture_mutation_scope` + `test_read_token_security`
+ `test_websocket_auth` → **104 passed**.

**Follow-ups (non-blocking, → next authz hardening item):**
1. **WS entrypoint parity.** `authenticate_websocket` (`auth.py`) does not carry
   the F3 fail-closed reject the HTTP middleware has; an ALL+resource token there
   authenticates as `is_owner=False`. Non-exploitable today (non-owner WS clients
   receive no broadcast data and `/ws/comfyui` requires owner), but the two
   entrypoints should agree — add the same `scope == "ALL" and resource_type is
   not None → reject` to `authenticate_websocket`.
2. **Backstop test — DONE in this PR.** `tests/test_snapshots_auth.py` now forges
   an ALL+resource `UserToken` straight into the DB (`_inject_picture_scoped_all_token`,
   bypassing the `create_token` mint ban) and asserts the middleware 403s it
   (`"misconfigured"` in the body) on a real HTTP request against every read-shaped
   snapshot route. This covers exactly the legacy/restored-token path the mint
   guard cannot reach, so no separate backstop test is outstanding.

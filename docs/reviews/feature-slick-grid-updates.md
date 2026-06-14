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

| Endpoint | Method | Touched by diff | Object-level scope check | Changed here? | Notes |
|---|---|---|---|---|---|
| `/pictures/{id}/tags` (`add_tag_to_picture`) | POST | sig + origin | none (relies on READ-block only) | no (none at HEAD) | F1 |
| `/pictures/{id}/tags/{tag_id}` (`remove_tag_from_picture`) | DELETE | sig + origin | none (READ-block only) | no | F1 |
| `/pictures/{id}/tags/everywhere` (`remove_tag_from_picture_everywhere`) | POST/DELETE | sig + origin | none (READ-block only) | no | F1 |
| `/pictures/{id}/tags` (`clear_all_tags_on_picture`) | DELETE | sig + origin | none (READ-block only) | no | F1 |
| `/pictures/{id}/tags` (`list_picture_tags`) | GET | not modified | **(a)** `enforce_picture_scope` `tags.py:170` | no | reference sibling, intact |
| `/pictures/project` (`set_project_for_pictures`) | POST | sig + origin | none (READ-block only) | no | F1 (batch) |
| `/pictures/apply-scores` (`apply_scores_for_pictures`) | POST | sig + origin | none (READ-block only) | no | F1 (batch) |
| `/pictures/{id}/faces` (`create_picture_face`) | POST | sig + origin | none (READ-block only) | no | F1 |
| `/pictures/{id}/faces` (`delete_picture_face`) | DELETE | sig + origin | none (READ-block only) | no | F1 |
| `/pictures/{id}` (`delete_picture`, soft-delete) | DELETE | sig + origin + `removed` broadcast | none (READ-block only) | no | F1; broadcast safe (inv. 5) |
| scrapheap restore (`restore_scrapheap`) | POST | sig + origin | none; `require_unscoped_owner`? see below | no | F1 / F2 |
| scrapheap delete (`delete_scrapheap_selection`) | DELETE/POST | sig + origin | none; see below | no | F1 / F2 |
| `/pictures/{id}` PATCH (description, `_crud.py:1844`) | PATCH | origin in existing notify | **(a)** `enforce_picture_scope` `_crud.py:1707` | no | guarded sibling, intact |
| `characters` `assign_face_to_character` | POST | sig + origin | none (READ-block only) | no | F1 |
| `characters` `remove_character_from_faces` | POST/DELETE | sig + origin | none (READ-block only) | no | F1 |
| `/pictures/import` (`import_pictures`) | POST | sig + origin | n/a — creates new owner-uploaded rows | no | not per-object read/mutate |
| `/pictures/plugin/{name}` (`run_picture_plugin`) | POST | sig + origin | (pre) — operates on caller-supplied ids | no | F1-adjacent (batch of ids) |
| comfyui in-app run/upscale | POST | origin passthrough | **(a)** `enforce_picture_scope` `comfyui.py:1661` on the id-bearing path | no | source path guarded |

Empty "object-level scope check" cells are the pre-existing F1 gap, not new holes.

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

### F2 — Confirm scrapheap mutators intend owner-only (low, informational)
**Severity: Low / informational.**

`restore_scrapheap` and `delete_scrapheap_selection` were modified here (signature
+ origin). They have no object-level check and rely on the READ-block. Snapshot/
restore-class operations elsewhere use `require_unscoped_owner` (`auth.py:683-702`).
These two are not snapshot ops, but they do mutate library-wide deleted state. This
is pre-existing and unchanged by the diff. Recommend the F1 follow-up explicitly
decide whether scrapheap mutation should require an unscoped owner (state (a) via
`require_unscoped_owner`) rather than merely not-READ. No action required for this
merge.

---

## What I verified by running it

- `python -m pytest tests/test_websocket_auth.py -q --force-cpu` → **24 passed**,
  including the owner-only delivery test and the new envelope/header tests.
- `ruff check` on all nine touched Python files → **All checks passed**.
- `Request` is imported (not merely referenced) in all six touched route modules,
  so the new `request: Request` params will not NameError at import.
- Frontend: `X-Client-Id` is attached only on same-origin mutating requests
  (`apiClient.js` `isMutatingRequest` + `isSameOrigin`/relative-path branches),
  so it cannot leak to an external ComfyUI host.

## Release gate

- **Block on:** nothing in this diff.
- **Accepted/tracked risk:** F1 (pre-existing object-level authz gap on
  picture-mutation handlers), owned by `senior-backend-developer`, to be closed by
  the §16.2 chokepoint work; F2 folded into that review.

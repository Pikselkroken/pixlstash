# Authz Coverage Matrix — Backend Refactor Phase 1 Step 2 (registry back-fill)

- **Branch:** `backend-refactoring`
- **Scope:** Phase 1 **Step 2 only** of the centralised-authz refactor (backend refactor plan §3.5). This PR **declares** the access policy of every mounted HTTP route in `pixlstash/authz/registry.py::ROUTE_POLICIES`. It does **not** enforce anything (`AUTHZ_GATE_ENFORCING = False`), remove any inline check, or change any handler. It is the regenerated coverage matrix the adversarial security review consumes.
- **WebSockets:** the 1 WS route (`/api/v1/ws/updates`; `/ws/comfyui`, the ComfyUI proxy, was removed in #1862) is **out of the HTTP registry by design** — its chokepoint is `authenticate_websocket` (plan §6). It remains acknowledged in `tests/test_architecture_guardrails.py::test_websocket_routes_are_acknowledged`, and `registry.py` carries the `# WS routes: see authn/websocket.py` sentinel.

## How each policy was derived (preserve-today's-behaviour rule)

Each route is mapped to the single `AccessPolicy` that reproduces its behaviour **today**, from the auth middleware gating (`AUTH_EXCLUDED_*`, `READ_BLOCKED_GET_PATHS`, `READ_SAFE_POST_PATHS`, the non-GET block for READ tokens, `require_local_for_write`, the `ALL`+`resource_type` fail-closed rejection) **plus** the inline object checks (`enforce_picture_scope`, `fetch_scope_allowed_*`, `require_unscoped_owner`, the `_require_scope_allows_{picture_set,character,project}` ladders). Load-bearing facts, verified against the code:

- Every resource-scoped share token is a **READ** token (`ALL`+`resource_type` is refused at mint and fail-closed at the middleware). So a mutating route **not** in `READ_SAFE_POST_PATHS` is reachable **only by an unscoped owner** today — and since issue #962 that holds by declaration rather than by omission: the middleware admits a non-GET only for a scope in `auth.WRITE_ENABLED_SCOPES` (`WRITE`, which nothing mints), where it previously admitted every scope string that was not literally `READ` — `OWNER_ONLY` is a no-op there and cannot over-deny.
- `fetch_scope_allowed_picture_ids` and the `_require_scope_allows_*` ladders return **"no restriction" for BOTH an owner token AND an unscoped-READ token** (`token_scope.resource_type is None`); they only narrow/deny a **resource-scoped** token (`filter_helpers.py:236-238`). An inline scope filter therefore only affects resource-scoped share tokens.
- Inline checks **remain until Step 5**; these declarations record the intended end-state so the gate can take over (Steps 3–4) without a behaviour change.

### Policy meanings (as they will enforce in Steps 3–4)

| Policy | Enforcement | Derived from |
|---|---|---|
| `public` | no auth | path in `AUTH_EXCLUDED_*` |
| `any_token` | any authenticated principal; **no** object check | handler has no scope check and returns global / non-per-object data (or is deliberately reachable by READ tokens) |
| `picture_scoped` | `enforce_picture_scope(id)` | inline `enforce_picture_scope` (or the F2/#504 carry-forward mandate) |
| `set_scoped` / `character_scoped` / `project_scoped` | membership check on the object id | inline `_require_scope_allows_{picture_set,character,project}` |
| `scoped_list` | list/search result filtered by the scope-allowed id set (handler logic; gate records only) | inline `fetch_scope_allowed_*` / `token_scope` filter / self-empty for scoped |
| `owner_only` | `require_unscoped_owner` (rejects scoped tokens) | inline `require_unscoped_owner`, OR a write blocked for READ tokens by the middleware, OR a `READ_BLOCKED_GET_PATHS` GET, OR a bespoke inline "reject scoped" gate |
| `local_owner_only` | `owner_only` + loopback/LAN/Tailscale IP, or a remote owner iff `allow_remote_host_ops=true` | **none in Step 2** — the §16.3 retarget is a deliberate Step-3 behaviour change (see below) |
| `loopback_owner_only` | `owner_only` + strict loopback only (127.0.0.0/8 + ::1); `allow_remote_host_ops` can NOT loosen it | **none in Step 2** — §16.3.1 host-shell red line; a deliberate behaviour change (see below) |

## Completeness

The route *rows* below are machine-checked against the registry by
`tests/test_architecture_guardrails.py::test_coverage_matrix_document_matches_the_registry`,
which fails if a declared route has no row, a row names an undeclared route, a
route is tabled twice, or a row states a different policy than the registry
enforces. That check is what makes the matrix complete.

This document deliberately records **no route counts**. A hand-written total
was never checked, went stale with every new route, and made any two branches
that added a route conflict on the same line. For a current figure, count
`ROUTE_POLICIES`:

```
python -c "from collections import Counter; from pixlstash.authz.registry import ROUTE_POLICIES as R; print(len(R), Counter(p.policy.name.lower() for p in R.values()))"
```

---

## The matrix (one row per route)

Rationale column is empty where it equals the policy-meaning table above (e.g. `picture_scoped` ⇒ `enforce_picture_scope`, `scoped_list` ⇒ `fetch_scope_allowed_*` filter). It is filled where the route is `public`/`owner_only` (justification mandatory for public) or where the derivation is non-obvious.


### 0. app-level (server.py) + auth

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/` | public |  | Frontend SPA index; auth-excluded; no owner data |
| GET | `/api/v1/check-session` | public |  | Session status probe; auth-excluded (/check-session) |
| GET | `/api/v1/libraries` | owner_only |  | Registry read (multi-library hub). `library_independent=True`: returns the registry, not library content, so it must keep answering while a token is being refused or a switch is in flight. `owner_only` rather than `local_owner_only` (CSO ruling 2026-08-01, plan §11 q3) so the Settings tab renders for any owner; the host detail it would otherwise leak (folder paths, the CLI hint) is omitted for a non-local caller by the handler instead of the whole route being denied. Returns no per-object data |
| POST | `/api/v1/libraries` | **local_owner_only** |  | Adds a library (`library_access=HUB_ONLY`). §16.3 path-authority class: it takes a caller-supplied host path and, when the folder holds no vault, writes a SQLite database into it and restricts the folder to the owner (0700) — write authority inside a host folder, alongside `POST /filesystem/folders`, which is already on this tier. It creates no directory: the folder must already exist (404 otherwise), which is what keeps its authority to the one folder the owner named. The path goes through the same `validate_reference_folder_path` chokepoint as the folder picker. Attaching moves, renames and copies nothing, and the incoming vault's `user`/`user_token` rows are never read, so a library folder from elsewhere cannot import somebody's credentials. `library_independent` left at its safe default `False`: the route has no need to answer mid-switch |
| GET | `/api/v1/libraries/inspect` | **local_owner_only** |  | Says which of five things a folder is (`library_access=HUB_ONLY`). §16.3 host-filesystem read authority — it takes a caller-supplied host path and walks it — the same class as `GET /filesystem/browse`, through the same `validate_reference_folder_path` chokepoint. Reads only; it registers nothing. Returns no per-object library content beyond the name and (locality-conditioned, as the listing's is) the path of the one registered library the verdict is about |
| PATCH | `/api/v1/libraries/{library_uuid}` | **local_owner_only** |  | Renames a library (`library_access=HUB_ONLY`). On this tier by consistency rather than by capability: it writes one hub column, takes no host path, and renames nothing on disk. It sits with its siblings because the Settings pane gates the whole management menu on the same `can_manage` locality answer; a looser tier would give that pane two rules to explain and buy no reachability the owner does not already have. The uuid is resolved through `by_uuid`, not the registry's `get`, so the row-id and name fallbacks are not reachable over HTTP |
| DELETE | `/api/v1/libraries/{library_uuid}` | **local_owner_only** |  | Detaches a library (`library_access=HUB_ONLY`). On this tier for the `POST /libraries/active` reason, **not** the path-authority one: it takes a registry uuid, never a host path, and removes no file. What it exercises is authority over other principals' state — every resource-scoped share link pointing at that library stops working until the same folder is added again. The row is kept rather than deleted, so those links revive rather than being silently revoked. The active library is refused by the registry (`ActiveLibraryError` → 409). Same `by_uuid` resolution as PATCH |
| POST | `/api/v1/libraries/{library_uuid}/discard` | **local_owner_only** |  | Gives a folder back: deletes the temporary database and the thumbnail cache of a first import the owner did not keep, drops the registration, and lands the session on the app's own scratch library (`library_access=SWITCH_WRITER`). The one route here that **deletes files inside a host folder**, which is why it sits on the strictest tier its siblings hold a half of each - `POST /libraries`' write authority inside a folder, and `POST /libraries/active`' authority over every client's session. It takes a registry uuid, never a caller-supplied path, and the registry refuses anything but a library still on its first import (409), so the only files reachable are ones PixlStash created itself in a folder the owner had already named. **No picture file is touched** |
| POST | `/api/v1/libraries/active` | **local_owner_only** |  | Switches the active library (`library_access=SWITCH_WRITER`, `library_independent=True`). §16.3 locality tier but NOT the path-authority class: it takes a registry uuid, never a caller-supplied host path. Local-only because it resets every connected client's session and takes the outgoing library's share links offline, i.e. authority over other principals' state. **Not** a confidentiality pivot (corrected 2026-08-07): the original 2026-08-01 ruling justified the tier as stopping a stolen token reaching every library by switching, which held for the unpinned-token design it was written against; the library pin landed later and closes it independently, so a thief who switches is refused on every data route and cannot mint either. Loopback/LAN/Tailscale all pass; a genuinely remote owner needs `allow_remote_host_ops` |
| GET | `/api/v1/login` | public |  | Registration-status probe; auth-excluded (/login) |
| POST | `/api/v1/login` | public |  | Password login / first-owner claim; auth-excluded (/login) |
| POST | `/api/v1/logout` | public |  | Logout; auth-excluded (/logout) |
| GET | `/api/v1/network/info` | any_token |  |  |
| GET | `/api/v1/protected` | any_token |  |  |
| GET | `/docs` | public |  | Swagger UI; auth-excluded (/docs/ prefix) |
| GET | `/docs/oauth2-redirect` | public |  | Swagger oauth2 redirect; auth-excluded |
| GET | `/favicon.ico` | public |  | Static favicon; auth-excluded |
| GET | `/openapi.json` | public |  | OpenAPI schema; auth-excluded |
| GET | `/scalar` | public |  | API docs UI; auth-excluded |
| GET | `/version` | public |  | Health/version probe; auth-excluded |
| GET | `/{full_path:path}` | public |  | Frontend SPA fallback serving the static shell/assets; returns no owner resource data. NEEDS REVIEW: this template is not statically in AUTH_EXCLUDED_*, so the middleware requires auth for a concrete non-excluded deep path; the planned PUBLIC-consistency check must reconcile (add to exclusions or special-case). |

### share.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/share/{token_slug}` | public |  | Share-link landing; resolves its own token; auth-excluded (/share/ prefix) |

### config.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/server-config/filesystem-roots` | owner_only |  | require_unscoped_owner; also READ_BLOCKED; owner only |
| POST | `/api/v1/server-config/open` | **loopback_owner_only** |  | §16.3.1 RED LINE (CSO Condition 1): opens the config path in the host file browser via `_open_in_os` (os.startfile/open/xdg-open) — byte-identical host-GUI spawn as pictures/open-location & reference-folders/open; strict loopback only; `allow_remote_host_ops` can NOT loosen it |
| GET | `/api/v1/server-config/scrapheap-retention` | owner_only |  | Owner server-config read; returns no per-object data. Sibling of GET /server-config/snapshots (same owner-settings tier). Not a host-capability route (§16.3): it neither touches the host filesystem browser nor spawns a host GUI/shell, so no locality tier applies |
| GET | `/api/v1/server-config/scrapheap-retention/impact` | owner_only |  | Reports a per-LIBRARY destruction count (how many scrapheap pictures a retention reduction would purge), exactly the kind of aggregate a resource-scoped share token must not see → owner_only, not any_token. Pure read: no config write, no purge, no reduced_at stamp |
| PATCH | `/api/v1/server-config/scrapheap-retention` | owner_only |  | Owner server-config write; PATCH is blocked for READ tokens, so only an unscoped owner reaches it. Sibling of PATCH /server-config/snapshots. Sets the auto-purge window but performs NO destruction itself (the scheduled task is the only deleter), so the §16.3 tiers do not apply |
| GET | `/api/v1/server-config/layout` | **local_owner_only** |  | §16.3 (v1.11 Phase 4b): reads back how this library's own picture root is laid out, and is the control surface of the PATCH beside it — the tier that alone may decide where the owner's files get written is the tier that may see the decision. Same argument as `GET /model-moves`. Returns no per-object data: a layout string and the unfiled folder name, never a path or a picture |
| PATCH | `/api/v1/server-config/layout` | **local_owner_only** |  | §16.3 (v1.11 Phase 4b): decides the folder names PixlStash writes into the library root from here on, and therefore where the background move engine later renames the owner's files to. It writes no file and moves nothing itself — the release's rule is that every path already in the library is true the moment it is written, so choosing a layout reorganises nothing — but the authority it hands out is host-filesystem authority, and the tier that may grant it is the tier that holds it. It accepts no host path at all: the root is the library's own. Sibling of `POST /server-config/layout/migration`, which acts on what it sets. A layout that cannot be parsed is refused 400 rather than stored, so a malformed one can never behave as "no layout" by accident |
| GET | `/api/v1/library/folders` | owner_only |  | #2458: the Media sidebar Folders tree. Every folder of the library root a picture is filed in or below, read from stored paths, with the project, person or set id each level names by the layout, and a count. Folder names relative to the library root, never a host path; on `READ_BLOCKED_GET_PATHS` like every untemplated owner-class GET |
| POST | `/api/v1/library/folders/open` | **loopback_owner_only** |  | §16.3.1 RED LINE (#2448, #2458): the Media sidebar Folders tree's `Open in File Manager` shows a folder pictures are filed in, in the file manager of the machine PixlStash runs on, through `pixlstash/utils/host_open.py` like `pictures/{id}/open-location`, so the same tier: loopback only, and `allow_remote_host_ops` is never consulted. Takes a `path`, but opens it only if `GET /library/folders` lists it (a folder some library picture is filed in or below), and the realpath is contained in the library root (a symlink out is refused, as is a Windows junction to another drive). Any other path is 404, a listed folder gone from disk is 404, and a host with no desktop is a 500 that says so |
| GET | `/api/v1/server-config/captions` | **local_owner_only** |  | §16.3: reads back whether PixlStash writes caption files beside the pictures in the library's own root and under what filename suffix, and is the control surface of the PATCH beside it, the same argument as `GET /server-config/layout`. Returns no per-object data and no path: two toggles and two suffixes |
| PATCH | `/api/v1/server-config/captions` | **local_owner_only** |  | §16.3: decides whether, and under what filename, PixlStash writes a file beside every picture in the library root from here on, and asks for the root scan that does the first export. The suffix it stores is appended to picture paths on every later write, so it is validated to a bare filename fragment here (no separators, no `..`, no media extension), as `PATCH /reference-folders/{folder_id}`'s is; that route is `local_owner_only` for these same fields, so the root's copy cannot sit lower. One suffix for both kinds is refused 400 |
| GET | `/api/v1/server-config/layout/migration` | **local_owner_only** |  | §16.3 (v1.11 Phase 4c): counts what moving every picture in the library's own root onto its layout would do, and is the consent screen of the POST beside it — the tier that alone may rearrange the owner's whole tree is the tier that may see the count. Moves nothing. It does return host-filesystem facts nothing else exposes (how many files would cross a mount point inside the library, and sample paths), which is the second reason it is not `owner_only`; every path it returns is relative to the library root, never absolute |
| POST | `/api/v1/server-config/layout/migration` | **local_owner_only** |  | §16.3 (v1.11 Phase 4c): renames every picture in the library's own root into the folders the layout renders — the most host-filesystem authority any route here exercises. Strictly above `POST /pictures/layout/move-to-match`, which is `picture_scoped` because the caller names the pictures; this caller names none and the scope is the whole library, so the scope check the gate could perform is not the check that matters. It accepts no caller-supplied path: the root is the library's own and every destination is computed from a layout only this same tier could have set (`PATCH /server-config/layout`), while the Phase 4b planner still refuses a source outside the root, a symlink, or a destination that would escape it. A collision is suffixed `-2`, never overwritten; a destination across a mount point is refused rather than attempted (`os.link`/`os.replace` cannot cross a device); and every pass is one `pictures.layout.move` operation under one `srv-layout-migration-` batch id, so the whole run is one undo |
| GET | `/api/v1/server-config/ghost-retention` | owner_only |  | Owner server-config read; returns no per-object data. Sibling of GET /server-config/scrapheap-retention. Reports a PRIVACY position — which thumbnails and prompts of destroyed pictures this install keeps — so owner_only rather than any_token |
| PATCH | `/api/v1/server-config/ghost-retention` | owner_only |  | Owner server-config write; PATCH is blocked for READ tokens, so only an unscoped owner reaches it. Sibling of PATCH /server-config/scrapheap-retention. Sets which picture ghosts a purge may keep and performs NO destruction itself |
| DELETE | `/api/v1/server-config/ghost-retention/ghosts` | owner_only |  | Owner privacy erase; DELETE is blocked for READ tokens, so only an unscoped owner reaches it. Destroys the active library's picture ghosts and nothing on disk, so owner data hygiene rather than a §16.3 host capability |
| DELETE | `/api/v1/server-config/ghost-retention/model-ghosts` | owner_only |  | Owner privacy purge; DELETE is blocked for READ tokens, so only an unscoped owner reaches it. Sibling of the picture-ghost erase. Forgets the model filenames hub recipes keep for models not on the shelf and nothing on disk |
| GET | `/api/v1/server-config/snapshots` | owner_only |  | require_unscoped_owner |
| PATCH | `/api/v1/server-config/snapshots` | owner_only |  | require_unscoped_owner |
| GET | `/api/v1/server-config/watch-folders` | owner_only |  | require_unscoped_owner; also READ_BLOCKED; owner only |
| GET | `/api/v1/session/context` | any_token |  |  |
| GET | `/api/v1/users/me/auth` | owner_only |  | Owner account state (username + has_password). **Step-3 F-c hardening rider (decided 2026-07-21):** tightened `any_token` → `owner_only` so a share token cannot read owner identity. Gate now rejects scoped/unscoped-READ tokens. |
| POST | `/api/v1/users/me/auth` | owner_only |  | Change owner password; POST blocked for READ tokens; owner only |
| GET | `/api/v1/users/me/config` | owner_only |  | Owner config; READ_BLOCKED_GET_PATHS blocks READ tokens; only owner reaches |
| PATCH | `/api/v1/users/me/config` | owner_only |  | require_unscoped_owner; owner config write. Refuses `ai_toolkit_repo_path`, `ai_toolkit_python` and `ai_toolkit_commit` with 422 naming the PUT below |
| GET | `/api/v1/users/me/penalised-tags` | any_token |  |  |
| POST | `/api/v1/users/me/shared-picture-ids/batch` | owner_only |  | POST not in READ_SAFE; READ tokens blocked; owner only |
| GET | `/api/v1/users/me/shared-resource-ids` | owner_only | auth.py:1314 | get_shared_resource_ids rejects token_scope is not None; scoped/READ 403'd; owner only |
| GET | `/api/v1/users/me/token` | owner_only | auth.py:1178 | list_tokens rejects token_scope is not None; scoped/READ 403'd; owner only |
| POST | `/api/v1/users/me/token` | owner_only |  | Mint API token; POST blocked for READ tokens; owner only |
| PATCH | `/api/v1/users/me/token/{token_id}` | owner_only |  | Update API token; PATCH blocked for READ tokens; owner only |
| DELETE | `/api/v1/users/me/token/{token_id}` | owner_only |  | Revoke API token; DELETE blocked for READ tokens; owner only |
| PUT | `/api/v1/users/me/token/{token_id}/libraries` | local_owner_only |  | Sets whether a token is pinned to one library or covers every one; widening a credential across libraries is the owner's decision on the local network; the handler refuses every token caller, so no token lifts a pin (#1787) |
| DELETE | `/api/v1/users/me/tokens/by-resource` | owner_only |  | Revoke tokens for a resource; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/users/me/watermark` | any_token |  |  |
| POST | `/api/v1/users/me/watermark` | owner_only |  | Upload watermark; POST blocked for READ tokens; owner only |
| DELETE | `/api/v1/users/me/watermark` | owner_only |  | Delete watermark; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/workers/progress` | any_token |  | Worker counts and process usage for any token. `runs`, the ComfyUI runs in flight (#1862: run id, prompt id, label, workflow id, state, percent), is the owner's: narrowed in the handler by `is_unscoped_owner_request`, so a READ or resource-scoped token is answered `[]`. A label names a workflow or a saved recipe, which no object scope describes. `finished`, the runs that ended (#2050: label, workflow id, when, how, and the ids of the pictures each made wherever in the library they were filed), is narrowed the same way and answered `[]`. `resources`, the VRAM arbiter's snapshot (GPUs, holders, lease labels naming workflows), is narrowed the same way and is `null` for any other token |
| POST | `/api/v1/resources/leases/{lease_id}/run-anyway` | owner_only |  | Starts the owner's own waiting GPU work (a ComfyUI run) now; a lease id names no library object; POST blocked for READ tokens; owner only |
| POST | `/api/v1/resources/leases/{lease_id}/cancel` | owner_only |  | Cancels the owner's own waiting GPU work and the ComfyUI run behind it; POST blocked for READ tokens; owner only |
| POST | `/api/v1/resources/comfyui/free` | owner_only |  | Asks the owner's configured ComfyUI to unload its models, the same reach the run route has; refused while it is busy; owner only |
| GET | `/api/v1/compute/gpus` | local_owner_only |  | Names this host's GPUs, their memory and what runs on them: host hardware, local owner only (§16.3); also in `READ_BLOCKED_GET_PATHS` |
| PATCH | `/api/v1/compute/gpu` | local_owner_only |  | Changes which of this host's GPUs the server runs on at its next start: host capability, local owner only (§16.3) |

### filesystem.py (§16.3)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/filesystem/browse` | local_owner_only |  | §16.3 host FS browse; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/filesystem/folders` | local_owner_only |  | §16.3 host FS mkdir; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |

### folder_structure.py (§16.3, v1.11 Phase 2)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| POST | `/api/v1/folder-structure/read` | local_owner_only |  | §16.3 host FS read: takes a caller-supplied host path and walks it recursively, decoding pictures out of it — `GET /filesystem/browse`'s path-authority class and then some, with the blocklist run on the **realpath** and re-run per directory during the walk, so neither a symlink nor a parent directory can hand it a restricted subtree; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/folder-structure/read/status` | local_owner_only |  | §16.3: carries the read's result, which **is** the folder map — polling must not be a lower bar than starting; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| DELETE | `/api/v1/folder-structure/read` | local_owner_only |  | §16.3: cancels the owner's in-flight read, same tier as the route that starts it; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/folder-structure/commit` | local_owner_only |  | §16.3: commits an accepted mapping over the same host path the read already walked — registers it for in-place indexing (the reference-folders/POST write) and creates the projects/people/sets/tags it names; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/folder-structure/commit/status` | local_owner_only |  | §16.3: carries the commit's result, the same host-path class as `GET .../read/status`; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) `library_access=HUB_ONLY`: the commit state it reports lives on the Server, not in any vault, and it must keep answering while the library is closed and reopened, which is what the end of a first import does. It keeps the library pin, because what it returns is library content. |
| DELETE | `/api/v1/folder-structure/commit` | local_owner_only |  | §16.3: stops the owner's in-flight commit (abort, or 'organise later') — authority over another principal's operation, on the same tier as the route that starts it, exactly as `DELETE .../read` is to `POST .../read`; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) `library_access=HUB_ONLY` alongside its status sibling: it writes one flag on server-held state and touches no vault, and refusing it mid-swap would leave the owner unable to stop work they can still see running. It keeps the library pin. |

### import_folders.py (§16.3)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/import-folders` | scoped_list |  |  |
| POST | `/api/v1/import-folders` | local_owner_only |  | §16.3 import-folder create; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| PATCH | `/api/v1/import-folders/{folder_id}` | local_owner_only |  | §16.3 import-folder update; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| DELETE | `/api/v1/import-folders/{folder_id}` | local_owner_only |  | §16.3 import-folder delete; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |

### reference_folders.py (§16.3)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/reference-folders` | scoped_list |  |  |
| POST | `/api/v1/reference-folders` | local_owner_only |  | §16.3 reference-folder create; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/reference-folders/detect-sidecars` | local_owner_only |  | §16.3 walks host path; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| PATCH | `/api/v1/reference-folders/{folder_id}` | local_owner_only |  | §16.3 reference-folder update; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| DELETE | `/api/v1/reference-folders/{folder_id}` | local_owner_only |  | §16.3 reference-folder delete; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/reference-folders/{folder_id}/metadata/export` | local_owner_only |  | §16.3 write sidecars to host FS; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/reference-folders/{folder_id}/metadata/import` | local_owner_only |  | §16.3 read sidecars from host FS; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/reference-folders/{folder_id}/move-pictures` | local_owner_only |  | §16.3 move pictures on host FS; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/reference-folders/{folder_id}/open` | **loopback_owner_only** |  | §16.3.1 RED LINE: opens a folder in the host file manager (host shell); strict loopback only; `allow_remote_host_ops` can NOT loosen it |
| POST | `/api/v1/reference-folders/{folder_id}/relocate` | local_owner_only |  | §16.3 reference-folder relocate; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` |
| POST | `/api/v1/server/restart` | **loopback_owner_only** |  | §16.3.1 RED LINE: restarts the server process (host shell); strict loopback only; `allow_remote_host_ops` can NOT loosen it |

### pictures/*

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/pictures` | scoped_list |  |  |
| DELETE | `/api/v1/pictures` | picture_scoped | body=picture_ids |  |
| POST | `/api/v1/pictures/apply-scores` | scoped_list |  |  |
| POST | `/api/v1/pictures/favourite` | owner_only |  | Marks or unmarks `picture.favourite`, the owner's own heart beside the score, on the pictures named in the body. One recorded operation, so one undo reverts it. |
| POST | `/api/v1/pictures/character_likeness/batch` | scoped_list |  |  |
| GET | `/api/v1/pictures/comfyui_loras` | scoped_list |  |  |
| GET | `/api/v1/pictures/comfyui_models` | scoped_list |  |  |
| GET | `/api/v1/pictures/count` | scoped_list |  |  |
| POST | `/api/v1/pictures/detect` | scoped_list |  |  |
| GET | `/api/v1/pictures/export` | scoped_list |  |  |
| GET | `/api/v1/pictures/export/download/{task_id}` | any_token |  |  |
| POST | `/api/v1/pictures/export/folder` | **loopback_owner_only** |  | §16.3 RED LINE (#291): writes exported pictures straight onto the host disk and, once done, opens the destination in the host file manager (same host-GUI spawn as `pictures/{id}/open-location`, via `pixlstash/utils/host_open.py`); loopback-only, `allow_remote_host_ops` can NOT loosen it |
| POST | `/api/v1/pictures/share-copies` | **loopback_owner_only** |  | §16.3 host write (#2417): writes picture copies into the staging folder beside `server-config.json` and returns their absolute host paths, which only a process on that machine (the desktop app) can use; loopback-only like `pictures/export/folder`, `allow_remote_host_ops` can NOT loosen it. Takes picture ids, never a path. Remote and scoped sessions use the ZIP export |
| GET | `/api/v1/open-with` | **loopback_owner_only** |  | §16.3 host capability: lists the applications the OS of the machine running PixlStash offers for this picture's file type, editors first. That is owner data about the host, not about the picture, so it takes its POST sibling's tier rather than `picture_scoped`. The picture is `?picture_id=`, which keeps the path untemplated so it is in `READ_BLOCKED_GET_PATHS` as well |
| POST | `/api/v1/open-with` | **loopback_owner_only** |  | §16.3.1 RED LINE: writes an edit copy of the picture into `edit-copies/` beside `server-config.json` and spawns a host GUI program on it (`gio launch` / `open -a` / `IAssocHandler.Invoke`), the authority of `pictures/{id}/open-location`. The body names the picture (`picture_id`) and an app id from the GET, never a program or a path: an id the OS does not list for the type right now is 400 and nothing is spawned. Loopback-only, `allow_remote_host_ops` can NOT loosen it |
| GET | `/api/v1/pictures/export/status` | any_token |  |  |
| POST | `/api/v1/pictures/face-search` | scoped_list |  |  |
| POST | `/api/v1/pictures/import` | owner_only |  | Import pictures; POST blocked for READ tokens; owner only |
| GET | `/api/v1/pictures/import/status` | owner_only |  | **Corrected 2026-08-01 (`any_token` → `owner_only`).** The earlier `any_token` cell was wrong on its face: `ANY_TOKEN` means the route returns no per-object resource data, and the completed payload carries `results[].picture_id`, `results[].file` (the vault-relative filename) and `scrapheaped_picture_ids`. A resource-scoped READ token refused picture 1's thumbnail was handed picture 1's id and filename here. Owner only: the route serves the owner's own import UI and its `POST /pictures/import` sibling is already owner only, so no live caller is narrowed |
| POST | `/api/v1/pictures/import/staging` | owner_only |  | (#459, v1.8.0) Open async streaming-staging session; upload path, streams client bytes into vault — NOT a §16.3 host-FS read; mirrors `POST /pictures/import`; POST blocked for READ tokens; gate-enforced owner_only |
| POST | `/api/v1/pictures/import/staging/{staging_id}/files` | owner_only |  | (#459, v1.8.0) Stream upload bytes into a staging session; owner only |
| POST | `/api/v1/pictures/import/staging/{staging_id}/commit` | owner_only |  | (#459, v1.8.0) Hand staging off to the background `PictureImportTask`; owner only |
| DELETE | `/api/v1/pictures/import/staging/{staging_id}` | owner_only |  | (#459, v1.8.0) Cancel an uncommitted staging session, discard streamed files; owner only |
| GET | `/api/v1/pictures/import/staging/{staging_id}/status` | owner_only |  | **Corrected 2026-08-01 (`any_token` → `owner_only`).** The original cell claimed "progress/stage/counts only (no per-object data)"; that was false when written, because the completed payload carries `scrapheaped_picture_ids`. The unguessable uuid4 `staging_id` was doing the whole job, which is a capability URL rather than an access policy. Now owner only, matching its open/files/commit/cancel siblings and the corrected `GET /pictures/import/status` |
| POST | `/api/v1/pictures/impossible-tags/clear` | scoped_list |  |  |
| POST | `/api/v1/pictures/impossible-tags/restore` | scoped_list |  |  |
| GET | `/api/v1/pictures/likeness-groups` | scoped_list |  |  |
| POST | `/api/v1/pictures/likeness-search` | scoped_list |  |  |
| GET | `/api/v1/pictures/plugins` | owner_only |  | Image plugin list; third-party plugin text, and the run endpoint beside it is owner-only |
| POST | `/api/v1/pictures/plugins/{name}` | scoped_list |  |  |
| PATCH | `/api/v1/pictures/project` | scoped_list |  |  |
| GET | `/api/v1/pictures/{id}/layout` | picture_scoped | id | (v1.11 Phase 4b) Returns one picture's folder relative to its own library root and the folder the layout would render for it — the **Move to match** offer. Per-object data about exactly the picture named, so the ordinary picture-scoped tier, the same as `GET /api/v1/pictures/{id}/metadata`. It names no absolute path and no other picture; a picture in a root with no layout gets nulls rather than a refusal |
| POST | `/api/v1/pictures/layout/move-to-match` | picture_scoped | body=picture_ids | (v1.11 Phase 4b) The owner taking the drift offer. `picture_scoped` and **not** the §16.3 local tier, on the same line `POST /api/v1/pictures/rotate` sits on: the caller supplies no host path. It names pictures; the server derives the root from the picture's own row and the destination from a layout only the `local_owner_only` tier could have set, so the authority a caller exercises here is over pictures it already reaches, not over the filesystem. The planner refuses a source that resolves outside its root and refuses a symlink outright (the `os.link` in `publish_no_clobber` follows one, which would pull an outside file into the library — the #1024 shape), and a destination whose name is taken is declined rather than overwritten. READ tokens never reach the gate: POST is not in `READ_SAFE_POST_PATHS`. The gate loops `enforce_picture_scope` over every id and raises on the first one out of scope, so a mixed batch is refused whole and moves nothing |
| POST | `/api/v1/pictures/move-to-library` | local_owner_only |  | §16.3 tier for `POST /api/v1/libraries/active`'s reason. Copies the named pictures into another attached library, then soft-deletes them here (the `DELETE /api/v1/pictures` operation, one undo). It opens a non-active library with write authority, runs its pending migrations and holds the switch lock, and its refusals carry that library's host folder path, so a remote owner is refused unless `allow_remote_host_ops=true`. The target is a registry uuid in the body, never a host path; the gate pins the credential to the active library and the handler asks the same `token_covers_library` of the target, so a token for one library, or a session made from one, cannot write into another. A picture is committed there before anything is touched here, and a file the target already holds, live or in its Scrapheap, is left in both. POST not in READ_SAFE |
| POST | `/api/v1/pictures/rotate` | picture_scoped | body=picture_ids | (#950, in-place rotate) `picture_scoped`, the same tier every other per-picture mutation on this surface carries, and the same shape as `DELETE /api/v1/pictures`. The in-place write is a **metadata-only EXIF-orientation splice**: the entropy-coded pixel stream is copied through byte for byte, the whole prior state is one enumerated value 1–8 so the operation is exactly reversible by the ordinary undo machinery (§21.5), and a reference-folder file is refused at the sink and reported `unsupported` rather than rewritten. A write-enabled grant that already reaches the picture is therefore the right level. READ tokens never reach the gate here: POST is not in `READ_SAFE_POST_PATHS`, so the auth middleware refuses them first — that is what makes "write-enabled" the operative condition. The gate loops `enforce_picture_scope` over every id in `picture_ids` and raises on the first one out of scope, before the handler runs, so a mixed batch is refused whole |
| POST | `/api/v1/pictures/score_character_likeness` | owner_only |  | Owner scoring op; POST not in READ_SAFE; owner only |
| DELETE | `/api/v1/pictures/scrapheap` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/pictures/scrapheap/delete-preview` | owner_only |  | (v1.8.0) Authoritative delete-forever preview. Returns absolute on-disk paths of protected reference-folder originals; per-object data → owner_only, not any_token (POST not in READ_SAFE; gate-enforced). Rows constrained to `Picture.deleted.is_(True)`, so it cannot leak paths of live/non-scrapheap ids |
| POST | `/api/v1/pictures/scrapheap/restore` | owner_only |  | require_unscoped_owner |
| GET | `/api/v1/pictures/search` | scoped_list |  |  |
| GET | `/api/v1/pictures/stats` | scoped_list |  |  |
| GET | `/api/v1/pictures/stream` | scoped_list |  |  |
| POST | `/api/v1/pictures/thumbnails` | scoped_list |  |  |
| GET | `/api/v1/pictures/thumbnails/{id}.webp` | picture_scoped | id=id |  |
| PATCH | `/api/v1/pictures/{id}` | picture_scoped | id=id |  |
| DELETE | `/api/v1/pictures/{id}` | picture_scoped | id=id |  |
| GET | `/api/v1/pictures/{id}.{ext}` | picture_scoped | id=id |  |
| GET | `/api/v1/pictures/{id}/anomaly_region` | picture_scoped | id=id |  |
| GET | `/api/v1/pictures/{id}/character_likeness` | picture_scoped | id=id |  |
| GET | `/api/v1/pictures/{id}/detections` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/{id}/face` | picture_scoped | id=id |  |
| DELETE | `/api/v1/pictures/{id}/face/{index}` | picture_scoped | id=id |  |
| GET | `/api/v1/pictures/{id}/faces` | picture_scoped | id=id | Projected face list (`id`, `picture_id`, `character_id`, `frame_index`, `face_index`, `bbox`); replaced serving the `faces` relationship through `/{id}/{field}` (#721) |
| GET | `/api/v1/pictures/{id}/metadata` | picture_scoped | id=id |  |
| GET | `/api/v1/pictures/{id}/text` | picture_scoped | id=id | Words read out of the picture with their boxes (#1197) |
| POST | `/api/v1/pictures/{id}/text/read` | picture_scoped | id=id | Re-reads the picture's text; derived data only (#1197) |
| POST | `/api/v1/pictures/{id}/mask` | picture_scoped | id=id | Renders a selection's mask on the picture; derived data only, nothing written |
| POST | `/api/v1/pictures/{id}/open-location` | **loopback_owner_only** |  | §16.3.1 RED LINE: opens the file location in the host file manager (host shell); strict loopback only; `allow_remote_host_ops` can NOT loosen it |
| GET | `/api/v1/pictures/{id}/{field}` | picture_scoped | id=id |  |

### tags.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| POST | `/api/v1/pictures/tags/bulk_fetch` | scoped_list |  |  |
| GET | `/api/v1/pictures/{id}/tags` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/{id}/tags` | picture_scoped | id=id |  |
| DELETE | `/api/v1/pictures/{id}/tags` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/{id}/tags/remove_all` | picture_scoped | id=id |  |
| DELETE | `/api/v1/pictures/{id}/tags/{tag_id}` | picture_scoped | id=id |  |
| GET | `/api/v1/tags` | scoped_list |  |  |

### tag_predictions.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| POST | `/api/v1/pictures/{id}/reset_description` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/{id}/reset_tags` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/reset_description` | picture_scoped | body=picture_ids |  |
| POST | `/api/v1/pictures/reset_tags` | picture_scoped | body=picture_ids |  |
| GET | `/api/v1/pictures/{id}/tag_predictions` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/{id}/tag_predictions/delete` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/{id}/tag_predictions/{tag}/confirm` | picture_scoped | id=id |  |
| POST | `/api/v1/pictures/{id}/tag_predictions/{tag}/reject` | picture_scoped | id=id |  |
| GET | `/api/v1/tagger/label-thresholds` | any_token |  |  |

### stacks.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/pictures/{picture_id}/stack` | scoped_list |  |  |
| POST | `/api/v1/stacks` | owner_only |  | Create stack; POST blocked for READ tokens; owner only |
| POST | `/api/v1/stacks/keep-cover-only` | owner_only |  | Soft-deletes stack members to the Scrapheap and writes the metadata union (tags, score, pending character) onto their covers, across pictures named only by a stack or picture id. With `keep_recipes` + `keep_every_ghost` it also writes the server-wide `workflow_ghost_retention` privacy setting (the same write as PATCH /server-config/ghost-retention, which is owner_only too, so no privilege is gained). Stacks are set-membership-atomic, so this also changes what collections effectively contain. Same reasoning as POST /dedup/verdicts/stack and the mixed-stack mutations; POST is also blocked for READ tokens |
| POST | `/api/v1/stacks/keep-cover-only/preview` | owner_only |  | Dry run over stacks named only by stack or picture id, which can reach any stack in the vault. Returns per-stack membership, the names of the locked picture sets freezing a stack and of the characters a collapse would strand, plus a byte total, none of which can be narrowed to a share token's scope without either leaking that out-of-scope members exist or reporting counts measured over a subset (wrong numbers rather than narrower ones, the same reasoning as GET /dedup/mixed-stacks). POST is also blocked for READ tokens |
| GET | `/api/v1/stacks/{stack_id}` | scoped_list |  |  |
| POST | `/api/v1/stacks/{stack_id}/members` | owner_only |  | Add stack members; POST blocked for READ tokens; owner only |
| DELETE | `/api/v1/stacks/{stack_id}/members` | owner_only |  | Remove stack members; DELETE blocked for READ tokens; owner only |
| PATCH | `/api/v1/stacks/{stack_id}/members/{picture_id}` | owner_only |  | Set member position; PATCH blocked for READ tokens; owner only |
| PATCH | `/api/v1/stacks/{stack_id}/order` | owner_only |  | Reorder stack; PATCH blocked for READ tokens; owner only |
| GET | `/api/v1/stacks/{stack_id}/pictures` | scoped_list |  |  |

### dedup.py

Added 2026-07-28 with the v1.9 near-duplicate sweep (Lane E). Both routes are new,
carry no inline authz code (the gate is the sole enforcement, §16.1), and are
covered in both directions by `tests/test_dedup_sweep_api.py`
(`test_scoped_read_token_is_denied_on_both_routes` /
`test_owner_reaches_both_routes`).

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/dedup/sweep/policy` | owner_only |  | Sweep policy defaults/bounds; operator surface, returns no per-object data |
| POST | `/api/v1/dedup/sweep/dry-run` | owner_only |  | Vault-wide near-duplicate plan (counts + picture ids across the whole library); cannot be narrowed to a share token's scope without leaking out-of-scope counts, same reasoning as tag_health. POST also blocked for READ tokens |

The nine routes below were added with the v1.9 tiered Duplicates queue, eight on
2026-07-29 (Lane 1A) and `GET /dedup/stacks/{stack_id}/members` on 2026-08-01 with
the stack-units work (B1). All are new, none carries inline authz code (the gate is
the sole enforcement, §16.1), and all nine are covered **in both directions** by
`tests/test_dedup_tiers_api.py` — negative via `Authorization: Bearer` *and* via
the `?token=` query-parameter path
(`test_scoped_read_token_is_denied_on_every_route`), plus
`test_a_denied_verdict_route_changed_nothing` (the 403 is fail-closed: no write
happened) and `test_unauthenticated_is_denied`; positive on every route via the
owner cookie session across the policy, queue, counts, scan, verdict and
auto-stack tests. The deck expansion adds its own both-direction pair,
`test_the_owner_expands_a_deck_and_a_scoped_token_cannot`, whose negative half
uses a stack containing a picture the token IS granted so the 403 is a refusal
of the route, not an accident of which pictures the token can reach.

Two rationales apply. **Read routes:** a duplicate group is defined by *content
identity*, not by collection membership, so it routinely spans a share token's
scope boundary; narrowing it would leak that out-of-scope copies exist, which is
the tag_health reasoning. **Write routes:** a verdict is addressed by a content
signature that can name any picture in the vault and mutates stack membership,
tags, project/set membership and scores, so there is no coherent scoped form of it.

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/dedup/policy` | owner_only |  | Tier defaults/bounds; operator surface, returns no per-object data |
| GET | `/api/v1/dedup/groups` | owner_only |  | Returns duplicate groups with picture ids, dimensions and (for reference-folder pictures) file paths from anywhere in the vault; content-identity grouping crosses any token scope |
| GET | `/api/v1/dedup/stacks/{stack_id}/members` | owner_only |  | The queue's deck expansion: every live member of one existing stack, with the same per-picture fields a queue candidate carries. Lazy half of `GET /dedup/groups`, owner-only for the same content-identity reason. Deliberately NOT `scoped_list` like `GET /stacks/{stack_id}/pictures`: this surface must report the stack's TRUE depth (the whole point of the deck), so a scope-filtered list would be a wrong number rather than a narrower one |
| POST | `/api/v1/dedup/counts` | owner_only |  | Vault-wide and per-scope duplicate counts. Read-only but POST because the scope list does not fit a URL; POST also blocked for READ tokens |
| POST | `/api/v1/dedup/scan` | owner_only |  | Queues a background scan over the vault or a chosen scope; owner-only maintenance trigger. POST also blocked for READ tokens |
| POST | `/api/v1/dedup/verdicts/stack` | owner_only |  | Mutates stack membership, tags, project/set membership and scores across pictures named only by a content signature. POST also blocked for READ tokens |
| POST | `/api/v1/dedup/verdicts/keep-separate` | owner_only |  | Writes a permanent verdict about an arbitrary set of vault pictures. POST also blocked for READ tokens |
| POST | `/api/v1/dedup/verdicts/reopen` | owner_only |  | Reverses a stored verdict about an arbitrary set of vault pictures. POST also blocked for READ tokens |
| POST | `/api/v1/dedup/verdicts/batch` | owner_only |  | Atomically applies several stack or keep-separate verdicts over arbitrary vault pictures; same owner-only boundary as the single verdict routes above. POST also blocked for READ tokens |
| POST | `/api/v1/dedup/auto-stack` | owner_only |  | Bulk stacking across the whole vault under one undo batch; the most far-reaching mutation on this surface. POST also blocked for READ tokens |

The five routes below were added on 2026-08-01 with **Mixed stacks** (design D5/B5).
All are new, none carries inline authz code (the gate is the sole enforcement,
§16.1), and all five are covered **in both directions** by
`tests/test_mixed_stacks.py`: negative via `Authorization: Bearer` *and* via the
`?token=` query-parameter path
(`test_scoped_read_token_is_denied_on_every_mixed_stack_route`), plus
`test_scoped_read_token_denial_is_fail_closed_before_any_write` (the 403 on split
and unstack left every picture's `stack_id`/`stack_position` untouched); positive
on all five via the owner cookie session
(`test_owner_reaches_every_mixed_stack_route`, plus the behavioural tests that
exercise each route's real answer).

The same two rationales apply, at stack rather than group granularity. **The read
route** enumerates every live stack in the vault that is not one connected
cluster: cohesion is a fact about the *whole* stack, so narrowing it to a token's
scope would either leak that out-of-scope members exist or report a component
count measured over a subset: a wrong number rather than a narrower one, the
same argument that makes `GET /dedup/stacks/{stack_id}/members` owner-only.
**The write routes** remove pictures from, or dissolve, a stack anywhere in the
vault, addressed only by a stack id; stacks are set-membership-atomic, so that
also changes what collections effectively contain. The `Keep` pair changes no
picture, but it is owner state on an owner-only surface and a scoped token has no
listing to suppress.

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/dedup/mixed-stacks` | owner_only |  | Enumerates every live stack in the vault that is not one cluster, with its member picture ids, component sizes and stranded members. Cohesion is a whole-stack fact, so a scope-filtered list would be a wrong component count rather than a narrower list, the same reasoning as the deck expansion |
| POST | `/api/v1/dedup/mixed-stacks/{stack_id}/split` | owner_only |  | Removes pictures from a stack anywhere in the vault, named only by a stack id; stacks are set-membership-atomic, so this changes what collections effectively contain. POST also blocked for READ tokens |
| POST | `/api/v1/dedup/mixed-stacks/{stack_id}/unstack` | owner_only |  | Dissolves a stack anywhere in the vault, freeing every member. Same reasoning as the split, at whole-stack scale. POST also blocked for READ tokens |
| POST | `/api/v1/dedup/mixed-stacks/{stack_id}/keep` | owner_only |  | Writes a durable dismissal against a stack anywhere in the vault. Changes no picture, but it is owner state on an owner-only surface. POST also blocked for READ tokens |
| DELETE | `/api/v1/dedup/mixed-stacks/{stack_id}/keep` | owner_only |  | Clears the dismissal above; owner-only for the same reason. DELETE also blocked for READ tokens |

### characters.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/characters` | scoped_list |  |  |
| POST | `/api/v1/characters` | owner_only |  | Create character; POST blocked for READ tokens; owner only |
| POST | `/api/v1/characters/likeness-search` | scoped_list |  |  |
| POST | `/api/v1/characters/membership` | scoped_list |  |  |
| POST | `/api/v1/characters/{character_id}/faces` | owner_only |  | Assign face; POST blocked for READ tokens; owner only |
| DELETE | `/api/v1/characters/{character_id}/faces` | owner_only |  | Remove faces; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/characters/{id}` | character_scoped | id=id |  |
| PATCH | `/api/v1/characters/{id}` | owner_only |  | Update character; PATCH blocked for READ tokens; owner only |
| DELETE | `/api/v1/characters/{id}` | owner_only |  | Delete character; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/characters/{id}/faces` | character_scoped | id=id | Projected face list; replaced serving the `faces` relationship through `/{id}/{field}` (#721) |
| GET | `/api/v1/characters/{id}/reference_pictures` | character_scoped | id=id |  |
| GET | `/api/v1/characters/{id}/summary` | character_scoped | id=id |  |
| GET | `/api/v1/characters/{id}/{field}` | character_scoped | id=id |  |

### picture_sets.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/picture_sets` | scoped_list |  |  |
| POST | `/api/v1/picture_sets` | owner_only |  | Create set; POST blocked for READ tokens; owner only |
| GET | `/api/v1/picture_sets/locked-members` | scoped_list |  |  |
| POST | `/api/v1/picture_sets/membership` | scoped_list |  |  |
| GET | `/api/v1/picture_sets/{id}` | set_scoped | id=id |  |
| PATCH | `/api/v1/picture_sets/{id}` | owner_only |  | Update set; PATCH blocked for READ tokens; owner only |
| DELETE | `/api/v1/picture_sets/{id}` | owner_only |  | Delete set; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/picture_sets/{id}/members` | set_scoped | id=id |  |
| POST | `/api/v1/picture_sets/{id}/members` | owner_only |  | Bulk add set members; POST blocked for READ tokens; owner only |
| PUT | `/api/v1/picture_sets/{id}/members` | owner_only |  | Bulk replace set members; PUT blocked for READ tokens; owner only |
| POST | `/api/v1/picture_sets/{id}/members/{picture_id}` | owner_only |  | Add set member; POST blocked for READ tokens; owner only |
| DELETE | `/api/v1/picture_sets/{id}/members/{picture_id}` | owner_only |  | Remove set member; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/picture_sets/{id}/thumbnail` | set_scoped | id=id |  |

### projects.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/projects` | scoped_list |  |  |
| POST | `/api/v1/projects` | owner_only |  | Create project; POST blocked for READ tokens; owner only |
| POST | `/api/v1/projects/membership` | scoped_list |  |  |
| GET | `/api/v1/projects/{id_or_name}` | project_scoped | id=id_or_name |  |
| GET | `/api/v1/projects/{id_or_name}/picture_sets` | project_scoped | id=id_or_name |  |
| PUT | `/api/v1/projects/{project_id}` | owner_only |  | Update project; PUT blocked for READ tokens; owner only |
| DELETE | `/api/v1/projects/{project_id}` | owner_only |  | Delete project; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/projects/{project_id}/attachments` | project_scoped | id=project_id |  |
| POST | `/api/v1/projects/{project_id}/attachments` | owner_only |  | Upload attachment; POST blocked for READ tokens; owner only |
| POST | `/api/v1/projects/{project_id}/attachments/url` | owner_only |  | Add URL attachment; POST blocked for READ tokens; owner only |
| GET | `/api/v1/projects/{project_id}/attachments/{attachment_id}` | project_scoped | id=project_id |  |
| DELETE | `/api/v1/projects/{project_id}/attachments/{attachment_id}` | owner_only |  | Delete attachment; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/projects/{project_id}/export` | project_scoped | id=project_id |  |
| GET | `/api/v1/projects/{project_id}/summary` | project_scoped | id=project_id |  |
| GET | `/api/v1/projects/{project_name}/characters/{character_name}` | character_scoped | id=character_name |  |
| GET | `/api/v1/projects/{project_name}/picture_sets/{picture_set_name}` | set_scoped | id=picture_set_name |  |

### guest_scores.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/pictures/guest-scores` | scoped_list |  |  |
| POST | `/api/v1/pictures/guest-scores` | scoped_list |  |  |
| DELETE | `/api/v1/pictures/guest-scores/session` | scoped_list |  |  |

### comfyui.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| POST | `/api/v1/comfyui/abort` | owner_only |  | Abort generation; POST blocked for READ tokens; owner only |
| GET | `/api/v1/comfyui/pixlstash-node` | owner_only |  | Whether the owner's ComfyUI has the ComfyUI-PixlStash node; reaches the owner's ComfyUI; owner only |
| GET | `/api/v1/comfyui/link` | owner_only |  | Whether the owner's ComfyUI is linked and through which URL; owner only |
| POST | `/api/v1/comfyui/link` | local_owner_only |  | Mints a ComfyUI-scope token and has this server push it to whatever answers at the saved ComfyUI address on its own network: the server's network position is the authority, so a local owner only (§16.3, #1810) |
| DELETE | `/api/v1/comfyui/link` | local_owner_only |  | Revokes the ComfyUI link token and has this server write into the saved ComfyUI's settings on its own network; a local owner only, like the link it undoes (§16.3, #1810) |
| POST | `/api/v1/comfyui/pack/install` | loopback_owner_only |  | Writes code (the ComfyUI-PixlStash nodes) into a ComfyUI custom_nodes folder on this machine, trashes an older copy and restarts ComfyUI: the host-shell class, loopback only (§16.3) |
| POST | `/api/v1/comfyui/node-packs/{pack_id}/install` | loopback_owner_only |  | Has ComfyUI-Manager install third-party code (a pack from node_packs.KNOWN_PACKS, at its pinned version) into the saved ComfyUI and restarts it: the host-shell class, loopback only (§16.3, #2054) |
| GET | `/api/v1/comfyui/node-packs/{pack_id}` | owner_only |  | Whether the saved ComfyUI lists a known pack's node classes; no per-object data; owner only |
| GET | `/api/v1/comfyui/connections` | owner_only |  | Names every ComfyUI address the owner saved and what its card is; owner config like GET /users/me/config; owner only (#2167) |
| POST | `/api/v1/comfyui/connections` | owner_only |  | Saves an address the owner names for this server to fetch once chosen; the same reach comfyui_url already has; owner only (#2167) |
| PATCH | `/api/v1/comfyui/connections/{connection_id}` | local_owner_only |  | Renames a ComfyUI connection or moves it, and moving a linked one revokes its key and has this server write into that ComfyUI's settings on its own network; a local owner only, like DELETE /comfyui/link (§16.3, #2194) |
| DELETE | `/api/v1/comfyui/connections/{connection_id}` | local_owner_only |  | Removes a ComfyUI connection, revoking its link key and having this server write into that ComfyUI's settings on its own network; a local owner only, like DELETE /comfyui/link (§16.3, #2194) |
| POST | `/api/v1/comfyui/connections/{connection_id}/link` | local_owner_only |  | Mints a ComfyUI-scope token and has this server push it to whatever answers at that connection's address on its own network: the server's network position is the authority, so a local owner only, like POST /comfyui/link (§16.3, #2194) |
| DELETE | `/api/v1/comfyui/connections/{connection_id}/link` | local_owner_only |  | Revokes a connection's ComfyUI link token and has this server write into that ComfyUI's settings on its own network; a local owner only, like DELETE /comfyui/link (§16.3, #2194) |
| POST | `/api/v1/comfyui/probe` | owner_only |  | Makes this server fetch an address the owner names; the same reach comfyui_url already has; owner only |
| GET | `/api/v1/comfyui/templates` | owner_only |  | Makes this server read the owner's ComfyUI template list and says which model files the shelf holds; owner only like `GET /comfyui/pixlstash-node` (#2000) |
| GET | `/api/v1/comfyui/templates/thumbnail` | owner_only |  | Passes through a picture from the owner's ComfyUI for a template the list holds, never a caller-made path; owner only like the list (#2000) |
| POST | `/api/v1/comfyui/templates/add` | owner_only |  | Stores templates read from the owner's ComfyUI as manual workflows, the authority `POST /comfyui/workflows/import` already has; owner only (#2000) |
| GET | `/api/v1/comfyui/pictures/{picture_id}/recipe` | picture_scoped | id=picture_id |  |
| GET | `/api/v1/comfyui/pictures/{picture_id}/workflow` | picture_scoped | id=picture_id |  |
| GET | `/api/v1/comfyui/workflows` | owner_only |  | Names every stored workflow file, hidden workflows included; owner only like `GET /workflows` |
| POST | `/api/v1/comfyui/workflows/import` | owner_only |  | Import workflow; POST blocked for READ tokens; owner only |
| POST | `/api/v1/comfyui/workflows/convert` | owner_only |  | ComfyUI's API conversion of an editor-format workflow (#1530), sent by the ComfyUI-PixlStash node: stored beside the matching stored editor file (stored first, as an import would, when there is none), never over it. Owner only, for the import's reasons |
| DELETE | `/api/v1/comfyui/workflows/{workflow_name}` | owner_only |  | Delete workflow; DELETE blocked for READ tokens; owner only |
| POST | `/api/v1/comfyui/workflows/{workflow_name}/card` | owner_only |  | Files a stored workflow, user or built-in, on its Workflows card as an import would and returns the card key; Edit with ComfyUI opens the Run popup on the built-in image edit workflow through it. Writes the hub only; owner only, for the import's reasons |

### snapshots.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/snapshots` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/snapshots` | owner_only |  | require_unscoped_owner |
| GET | `/api/v1/snapshots/status` | owner_only |  | require_unscoped_owner |
| PATCH | `/api/v1/snapshots/{snapshot_id}` | owner_only |  | require_unscoped_owner |
| DELETE | `/api/v1/snapshots/{snapshot_id}` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/snapshots/{snapshot_id}/hash-compare` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/snapshots/{snapshot_id}/restore` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/snapshots/{snapshot_id}/restore/batch` | owner_only |  | require_unscoped_owner |
| GET | `/api/v1/snapshots/{snapshot_id}/restore/preview` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/snapshots/{snapshot_id}/restore/preview/batch` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}` | owner_only |  | require_unscoped_owner |
| GET | `/api/v1/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}/preview` | owner_only |  | require_unscoped_owner |

### reviews.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/reviews` | owner_only |  | Owner-only review queue (inline rejects scoped tokens) |
| POST | `/api/v1/reviews` | owner_only |  | Owner-only review surface (inline rejects scoped tokens); write |
| DELETE | `/api/v1/reviews` | owner_only |  | Owner-only review surface; write |
| GET | `/api/v1/reviews/preview` | owner_only |  | Owner-only review preview (inline rejects scoped tokens) |
| GET | `/api/v1/reviews/{review_id}` | owner_only |  | Owner-only review read (inline rejects scoped tokens) |
| DELETE | `/api/v1/reviews/{review_id}` | owner_only |  | Owner-only review surface; write |
| POST | `/api/v1/reviews/{review_id}/abort` | owner_only |  | Owner-only review surface; write |
| POST | `/api/v1/reviews/{review_id}/archive` | owner_only |  | Owner-only review surface; write |
| POST | `/api/v1/reviews/{review_id}/refresh` | owner_only |  | Owner-only review surface; write |
| GET | `/api/v1/reviews/{review_id}/suggestions` | owner_only |  | Owner-only review read (inline rejects scoped tokens) |

### operations.py (added 2026-07-28 — DAM 1.2 operation log)

Vault-wide change history plus the undo/redo stack. `owner_only` throughout: the
log enumerates every change to the **whole library** (a resource-scoped share
token must not read it), and undo/redo write metadata back onto arbitrary
pictures across the vault, which no resource-scoped grant can bound. Every write
here is a POST outside `READ_SAFE_POST_PATHS`, so a READ (⇒ scoped) token is
already middleware-blocked; the reads are the rows `owner_only` actually
tightens. **No inline authz check exists in these handlers** — the gate is the
sole enforcement (pinned by
`tests/test_operation_log.py::test_operations_routes_have_no_inline_authz_check`),
and the declarations themselves are pinned by
`tests/test_operation_log.py::test_every_operations_route_is_declared_owner_only`.

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/operations` | owner_only |  | Vault-wide change history; owner-only read |
| GET | `/api/v1/operations/undo-state` | owner_only |  | Vault-wide undo/redo availability; owner-only read |
| GET | `/api/v1/operations/{operation_id}` | owner_only |  | One operation incl. the recorded before/after metadata of its targets (arbitrary vault pictures); owner-only read |
| POST | `/api/v1/operations/undo` | owner_only |  | Reverts metadata across the vault; owner-only write |
| POST | `/api/v1/operations/redo` | owner_only |  | Re-applies metadata across the vault; owner-only write |
| POST | `/api/v1/operations/{operation_id}/undo` | owner_only |  | Reverts metadata across the vault; owner-only write |
| POST | `/api/v1/operations/batches/{batch_id}/undo` | owner_only |  | Reverts a whole bulk action across the vault; owner-only write |

### insights.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/insights` | owner_only |  | Vault-wide findings: folder names, counts and the absolute path of the folder each finding points at. Same reasoning as tag_health and the dedup queue — the numbers ARE the aggregate, so narrowing them to a share token's scope would leak that out-of-scope pictures exist or report a wrong total. Reads only; no write, no queued work |

### moves.py (v1.11 Phase 5, reconciling moves made outside PixlStash)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/moves/pending` | owner_only |  | Vault-wide reconciliation queue; owner-only read |
| POST | `/api/v1/moves/apply` | owner_only |  | Writes project/set/person membership across the vault; owner-only write |
| POST | `/api/v1/moves/dismiss` | owner_only |  | Clears rows from the vault-wide reconciliation queue; owner-only write |

### tag_health.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/tag_health` | owner_only |  | Vault-wide aggregates; inline _reject_scoped_tokens; owner/full only |
| POST | `/api/v1/tag_health/rebuild` | owner_only |  | Vault-wide rebuild; inline _reject_scoped_tokens; owner/full only |

### tag_suggestions.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/tag_suggestions` | scoped_list |  |  |
| POST | `/api/v1/tag_suggestions/bulk-accept` | scoped_list |  |  |
| POST | `/api/v1/tag_suggestions/bulk-reopen` | picture_scoped | body=ids |  |
| POST | `/api/v1/tag_suggestions/scan` | owner_only |  | Rebuild suggestions for a tag; POST blocked for READ tokens; owner only |
| POST | `/api/v1/tag_suggestions/{suggestion_id}/accept` | picture_scoped | id=suggestion_id |  |
| POST | `/api/v1/tag_suggestions/{suggestion_id}/dismiss` | picture_scoped | id=suggestion_id |  |
| POST | `/api/v1/tag_suggestions/{suggestion_id}/fix-twin` | picture_scoped | id=suggestion_id |  |
| POST | `/api/v1/tag_suggestions/{suggestion_id}/reopen` | picture_scoped | id=suggestion_id |  |
| POST | `/api/v1/tag_suggestions/{suggestion_id}/skip` | picture_scoped | id=suggestion_id |  |
| POST | `/api/v1/tag_suggestions/{suggestion_id}/swap` | picture_scoped | id=suggestion_id |  |

### tagger_runs.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/tagger-runs` | any_token |  |  |
| POST | `/api/v1/tagger-runs` | owner_only |  | Ingest tagger eval run; POST blocked for READ tokens; owner only |

### taggers.py

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/taggers` | owner_only |  | Plugin list + the caller's own tagger_settings; owner only — a scoped or READ token cannot run a tagger anyway |
| GET | `/api/v1/taggers/plugin-diagnostics` | local_owner_only |  | §16.3 host-path disclosure: names the scanned tagger-plugin folders on the server's disk and returns plugin import errors carrying host paths; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1) |
| DELETE | `/api/v1/taggers/{name}/artifacts/{artifact_id}` | owner_only |  | Delete tagger artifact; DELETE blocked for READ tokens; owner only |
| POST | `/api/v1/taggers/face-crop-retag` | owner_only |  | Library-wide face-crop retag (or its dry-run count); POST blocked for READ tokens; owner only |
| POST | `/api/v1/taggers/{name}/download` | owner_only |  | Download tagger plugin; POST blocked for READ tokens; owner only |

### telemetry.py (added 2026-08-05; the rows were declared on 2026-08-02 but never tabled)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/telemetry/install-id` | owner_only |  | Owner-only read of the installation's anonymous install ID. Not any_token: the ID is a stable installation identifier and a resource-scoped share token must not be able to read it |
| POST | `/api/v1/telemetry/install-id/recreate` | owner_only |  | Owner-only rotation of the install ID; POST is blocked for READ tokens, so only an unscoped owner reaches it. Sibling of GET /telemetry/install-id |

### model_shelf.py (added 2026-08-09 — model shelf B5 read routes)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/adapters` | owner_only |  | Model-shelf list. `owner_only` on the **default** library pin: `library_independent` is left `False` even though `model` is a hub table, because the response joins hub content to the active vault's `adapter_attachment` rows, so it does return library content and a token stamped for a non-active library must be refused. `character_id=` / `set_id=` filter through that vault table, not through a column, so the filter is a hub query plus a vault query intersected in Python — no join spans the two databases. Accepted residual (shelf plan B5): an owner pinned to library A sees machine-level model filenames, including ones only used in B |
| GET | `/api/v1/adapters/{sha256}` | owner_only |  | Detail for one adapter (or one `unknown`, which is hashed on sight and therefore hash-addressable). Same pin reasoning as the list. A checkpoint hash 404s here rather than being served, so the two blocks stay separate |
| GET | `/api/v1/adapters/{sha256}/file` | **local_owner_only** |  | Streams one registered adapter's bytes so a generator on another machine can *use* what this one catalogues; `GET /adapters` serves `locations[].folder_path` and `relpath`, but those are **this** host's paths and name nothing over there. **The first shelf read off the `owner_only` tier, and the sentence that kept the others on it is why.** That sentence is "they surface host paths but take none": this one takes none either (a sha256 the scanner registered is the whole input, and the join of `model_folder.path` with `model_file.relpath` never touches caller data) but it does not *surface* a path — it returns the raw bytes of a file inside a registered model folder, which is `GET /model-folders/{folder_id}/runs/{run_name}/samples/{filename}`'s authority class exactly. Per the correction recorded against that route on 2026-08-11, bytes are a **new capability** rather than a narrower view of the metadata route beside it, so "a subset of what `GET /adapters` already serves" is not the argument and is not being made. Loopback / LAN / Tailscale is every deployment the route exists for, so the tier costs the feature nothing; a genuinely remote generator needs `allow_remote_host_ops`, the safe direction to fail in for a route whose output is a multi-gigabyte file. Handler narrowings, none of them the tier: `present` copies only (a forgotten folder tombstones its rows rather than deleting them, so any other state would serve out of a folder the owner un-registered), a checkpoint hash 404s as on the detail route, the join is contained with `path_is_within` (lexical first — a symlinked model is ordinary practice) against a `..` from a faulty scan or a restored hub, and a known hash with no readable copy is **409, not 404**. **This is the GET the `test_share_tokens_never_reach_a_folder_mutator` docstring warns about**: the middleware's non-GET rule says nothing here and `READ_BLOCKED_GET_PATHS` matches literal paths, so a templated one could not be covered by it — the gate's owner check is the only thing refusing a share token, and `tests/test_model_shelf_api.py::test_no_share_token_can_download_a_model_file` asserts it rather than assuming it |
| PUT | `/api/v1/adapters/{sha256}/attachments` | owner_only |  | The assignment path: replaces which characters and sets in the **active library** use one adapter. Same default library pin as the shelf reads, and for a stronger reason — it *writes* vault rows, so a token stamped for another library must not reach it. Not `local_owner_only`: it names a hash and two row ids, never a host path, so it is outside the §16.3 filesystem-authority class. Every entity id is checked against this library before anything is written, because `adapter_attachment` carries no foreign key (its other end is in the hub) and nothing else would ever notice a dangling id |
| PATCH | `/api/v1/models` | owner_only |  | The verb layer's write path (F3): Rename, Set base model, Set kind and Set trigger words, which differ only in which curated column they set. `owner_only` on the same default library pin as the shelf reads. Deliberately **not** `local_owner_only`: the body is a list of hub `model.id`s and a value to write, it names no host path, and nothing here touches the filesystem — so it is outside the §16.3 class that `POST /model-folders` sits in. The handler's own guards are data checks, not scope checks: `display_name` is refused for more than one id unless they are members of one stack, and is then written to the whole stack (a name is a fact about one file, or one run), and a correction that would violate the hub's `CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)` is refused by name rather than left to surface as a 500 |
| POST | `/api/v1/models/base-model-lookup` | owner_only |  | *Look up base model on Civitai* (#1517): fills `base_model` on selected adapters and checkpoints that have none and no ruling from the owner, with Civitai's by-hash answer (`base_model_source = 'civitai'`). The same curated column as `PATCH /api/v1/models`, so the same tier. Outbound: only each file's SHA-256 leaves the machine, to civitai.com, redirects refused. Refused (409) under `PIXLSTASH_DEMO_MODE` and for a built-in engine. |
| POST | `/api/v1/models/forget` | owner_only |  | The verb layer's destructive path (F3): drops the `model` row and its `model_file` rows, taking the name, base model, kind and trigger words with it. Still `owner_only` and still not §16.3, because it deletes **database rows and never unlinks a file** — the files are already gone, which is the precondition for the call. Gated on row state rather than on caller: a model with a `present` or `unreachable` copy is refused (with a reason, not an error), so the we-could-not-look state cannot be turned into a deletion. Vault `adapter_attachment` rows are deliberately left alone; they are keyed by content hash, unreachable for libraries that are not open, and re-link if the file returns |
| PUT | `/api/v1/models/hidden` | owner_only |  | Hide or show models in the active library; returns every model the library now hides. Per-library view of the shelf: hub `model_hidden` rows keyed by `(library_uuid, model.id)`, which no share token is ever scoped to. Writes those rows only, never a file, a `model` row or a set member, and skips an id that names no model; the shelf's tier, the owner's own view of it, and the same hub-row class as the set hide (`PUT /api/v1/models/workflow-sets/hidden`). |
| POST | `/api/v1/models/companions` | owner_only |  | The question in front of the shelf's Delete (#1314): given hub `model.id`s about to be deleted, which VAEs and text encoders they ran with would be left with nothing that uses them, which a kept model still uses, and which cannot be told apart from a same-named file. Reads hub rows only (`model`, `model_file`, `workflow_recipe_asset`) and changes nothing; it takes no host path and returns model names the shelf reads already serve, so it sits on the shelf read tier rather than the delete's §16.3 one. The recipe evidence spans every library on the hub, the same machine-level scope the shelf's own filenames already have |
| GET | `/api/v1/models/workflow-sets` | owner_only |  | The set grid's evidence (#1438): every distinct set of shelf models a kept picture or a stored ComfyUI run proves ran together, read off the same `workflow_recipe_asset` self-join as `POST /api/v1/models/companions` above and `comfyui_history_model` (#1565), plus one `GROUP BY workflow_structural_hash` and one `ROW_NUMBER()` window over the active library's non-deleted pictures for the counts and cover thumbnails. Owner-only for the reason the workflows grid's reads are: it sizes the whole vault one card at a time and returns a thumbnail URL for every card's best three pictures, and there is no per-object scope to narrow it to — a set is a fact about the machine's models rather than about a picture a token was granted. Reads only, and stores no membership: overlapping sets are derived per request |
| GET | `/api/v1/models/workflow-templates` | owner_only |  | The New workflow set picker (#1999, #2001): ComfyUI's workflow templates, from a list shipped in the package and never fetched at runtime, each file matched to the shelf by name and each template checked against the hand-made sets for coverage. Owner-only for the reason `GET /api/v1/models/workflow-sets` is: the answer names the machine's models and sets, and there is no per-object scope to narrow it to. Asks the owner's configured ComfyUI for its version, as `POST /api/v1/comfyui/probe` does. Reads only. |
| POST | `/api/v1/models/workflow-sets` | owner_only |  | Create a set, empty or with members (by `model.id`, or by sha256 for an undo). Hand-made workflow sets (#1520): the owner's own groupings of shelf models, stored in the hub (`model_workflow_set`, `model_workflow_set_member`). Writes hub set rows only, never a file or a `model` row, and answers with the set's evidence from the same whole-library read as `GET /api/v1/models/workflow-sets`, so it sits on that tier. Addressed by a hub set id no share token is ever scoped to. |
| PATCH | `/api/v1/models/workflow-sets/{set_id}` | owner_only |  | Rename a set. Hand-made workflow sets (#1520): the owner's own groupings of shelf models, stored in the hub (`model_workflow_set`, `model_workflow_set_member`). Writes hub set rows only, never a file or a `model` row, and answers with the set's evidence from the same whole-library read as `GET /api/v1/models/workflow-sets`, so it sits on that tier. Addressed by a hub set id no share token is ever scoped to. |
| DELETE | `/api/v1/models/workflow-sets/{set_id}` | owner_only |  | Delete a set; returns its snapshot for an undo. Hand-made workflow sets (#1520): the owner's own groupings of shelf models, stored in the hub (`model_workflow_set`, `model_workflow_set_member`). Writes hub set rows only, never a file or a `model` row, and answers with the set's evidence from the same whole-library read as `GET /api/v1/models/workflow-sets`, so it sits on that tier. Addressed by a hub set id no share token is ever scoped to. |
| POST | `/api/v1/models/workflow-sets/{set_id}/members` | owner_only |  | Add members. Hand-made workflow sets (#1520): the owner's own groupings of shelf models, stored in the hub (`model_workflow_set`, `model_workflow_set_member`). Writes hub set rows only, never a file or a `model` row, and answers with the set's evidence from the same whole-library read as `GET /api/v1/models/workflow-sets`, so it sits on that tier. Addressed by a hub set id no share token is ever scoped to. |
| POST | `/api/v1/models/workflow-sets/{set_id}/members/remove` | owner_only |  | Remove members by sha256. Hand-made workflow sets (#1520): the owner's own groupings of shelf models, stored in the hub (`model_workflow_set`, `model_workflow_set_member`). Writes hub set rows only, never a file or a `model` row, and answers with the set's evidence from the same whole-library read as `GET /api/v1/models/workflow-sets`, so it sits on that tier. Addressed by a hub set id no share token is ever scoped to. |
| PUT | `/api/v1/models/workflow-sets/{set_id}/declines` | owner_only |  | Replace the models removed from a set (#1523, #1972: Remove / Restore / Reset to automatic); returns the previous list for an undo. Writes hub `model_workflow_set_decline` rows only, never a member, a file or a `model` row, and answers with the set's evidence from the same whole-library read as `GET /api/v1/models/workflow-sets`, so it sits on that tier. Addressed by a hub set id no share token is ever scoped to. |
| POST | `/api/v1/models/workflow-sets/{set_id}/merge` | owner_only |  | Merge sets on the same checkpoint into this one (#1972); refused unless every set's checkpoint digests match. Writes hub set rows only (this set's members and removals, the merged sets' rows and every library's hide row for them, this set's hide row in the active library), never a file or a `model` row, and answers with the merged sets' snapshots for an undo plus the set's evidence from the same whole-library read as `GET /api/v1/models/workflow-sets`, so it sits on that tier. Addressed by hub set ids no share token is ever scoped to. |
| PUT | `/api/v1/models/workflow-sets/{set_id}/seen` | owner_only |  | Record the models the owner has seen in a set's tray (#1972), so a model the pictures brought in stops being marked new. Writes hub `model_workflow_set_seen` rows only, never a member, a file or a `model` row. Addressed by a hub set id no share token is ever scoped to; the tier of the set mutators beside it. |
| PUT | `/api/v1/models/workflow-sets/hidden` | owner_only |  | Hide or show workflow sets in the active library (#1869); returns every set the library now hides. Per-library view of the set grid: hub `workflow_set_hidden` rows keyed by the set's own key (`model:<head id>`, or `hand:<id>`; `missing:<filename>` when the head is off the shelf), which no share token is ever scoped to. Writes those rows only, never a member, a file or a `model` row; the grid's tier, the owner's own view of their shelf, and the same hub-row class as the verdicts and declines beside it. |
| GET | `/api/v1/favourites` | owner_only |  | Every favourite on the hub (#1578), per kind: model sha256s, workflow card keys, workflow set keys. Hub `favourite` rows the owner wrote, keyed by identities no share token is ever scoped to; reads nothing from the vault. Untemplated, so `READ_BLOCKED_GET_PATHS` refuses READ tokens ahead of the gate. |
| PUT | `/api/v1/favourites` | owner_only |  | Mark or unmark favourites of one kind (#1578); returns every favourite after the call. Writes `favourite` rows only, never a file, a `model` row, a workflow or a set member, and keeps a key that names nothing today so a favourite comes back with its model. |
| PUT | `/api/v1/models/workflow-sets/verdicts/{combo_key}` | owner_only |  | Record the set verdict (`yes`/`no`); returns the previous one for an undo. The owner's verdicts on a workflow set (docs/ideas/workflow-set-verdicts.md): hub `model_set_verdict` / `model_set_member_verdict` rows keyed by `combo_key`, a combination of `model.id`s no share token is ever scoped to. Writes those rows only, never a file, a `model` row or a picture, and reads nothing back but the shelf ids a PUT validates; the grid's tier, because the verdict is the owner's judgement about the machine's models and the check it sits beside is the whole-library read of `GET /api/v1/models/workflow-sets`. |
| DELETE | `/api/v1/models/workflow-sets/verdicts/{combo_key}` | owner_only |  | Clear the set verdict; returns the previous one. The owner's verdicts on a workflow set (docs/ideas/workflow-set-verdicts.md): hub `model_set_verdict` / `model_set_member_verdict` rows keyed by `combo_key`, a combination of `model.id`s no share token is ever scoped to. Writes those rows only, never a file, a `model` row or a picture, and reads nothing back but the shelf ids a PUT validates; the grid's tier, because the verdict is the owner's judgement about the machine's models and the check it sits beside is the whole-library read of `GET /api/v1/models/workflow-sets`. |
| PUT | `/api/v1/models/workflow-sets/verdicts/{combo_key}/members/{model_id}` | owner_only |  | Record whether a member is a problem in the set (`problem`/`not_problem`); `model_id` must be one of the key's ids. The owner's verdicts on a workflow set (docs/ideas/workflow-set-verdicts.md): hub `model_set_verdict` / `model_set_member_verdict` rows keyed by `combo_key`, a combination of `model.id`s no share token is ever scoped to. Writes those rows only, never a file, a `model` row or a picture, and reads nothing back but the shelf ids a PUT validates; the grid's tier, because the verdict is the owner's judgement about the machine's models and the check it sits beside is the whole-library read of `GET /api/v1/models/workflow-sets`. |
| DELETE | `/api/v1/models/workflow-sets/verdicts/{combo_key}/members/{model_id}` | owner_only |  | Clear a member verdict; returns the previous one. The owner's verdicts on a workflow set (docs/ideas/workflow-set-verdicts.md): hub `model_set_verdict` / `model_set_member_verdict` rows keyed by `combo_key`, a combination of `model.id`s no share token is ever scoped to. Writes those rows only, never a file, a `model` row or a picture, and reads nothing back but the shelf ids a PUT validates; the grid's tier, because the verdict is the owner's judgement about the machine's models and the check it sits beside is the whole-library read of `GET /api/v1/models/workflow-sets`. |
| POST | `/api/v1/models/{model_id}/open-location` | **loopback_owner_only** |  | §16.3.1 RED LINE (#933): shows a model's folder in the file manager of the machine PixlStash runs on, which is the shelf's `Open in file manager` verb. It is the **fourth** file-manager spawn on this tier — `reference-folders/{folder_id}/open`, `pictures/{id}/open-location` and `server-config/open` are the other three; `server/restart` re-execs the process and spawns no GUI at all — and it carries their tier: loopback only, and `allow_remote_host_ops` is never consulted, so a LAN or Tailscale owner is refused as firmly as a public one. It is also the first of the four to read the opener's exit status (`pixlstash/utils/host_open.py`) rather than discard it. The tier is about the **spawn**, not the input — the body is empty and the path comes from the hub: a `present` copy, joined to the folder the scanner recorded and contained with `path_is_within` through the same `_present_copy` as `GET /adapters/{sha256}/file`, so a `..` from a faulty scan or a restored hub cannot point the file manager outside a registered folder. That containment is **lexical first** by design, so a symlinked directory component recorded in `model_file` would be followed — the deliberate weakening that lets a symlinked model be served at all (`path_utils`), and it opens a window on bytes the route beside this one would already stream over the network, to a caller who by then has to be sitting at the machine. The **folder** is opened rather than the file selected, because that is the one gesture every platform answers in a single call. Handler narrowings, neither of them the tier: an unknown id is 404 and a model whose copies are all `missing` or on an unplugged drive is **409, not 404** (the shelf row exists; the bytes do not), and a host with no desktop at all — headless, containerised — is a 500 that says so rather than a click that silently does nothing |
| GET | `/api/v1/models/base-models` | owner_only |  | Completion targets for the free-text `base_model` field: the labels `known_base_models` ships plus every distinct `base_model` string recorded on this machine's `model` rows. `owner_only` on the same default library pin as the rest of the shelf, and deliberately not the any-authenticated-principal tier: that tier's contract is that a route returns no per-object data, and the distinct half of this list is exactly that — it is derived from `model` rows, so it names what this machine holds even though the shipped half beside it is a compile-time constant. Reads only; the field it completes stays free text, so nothing here constrains what can be stored |
| GET | `/api/v1/checkpoints` | owner_only |  | The same `model` query filtered to `file_kind='checkpoint'`. No by-hash detail sibling: a checkpoint registers with `sha256` NULL until `MissingCheckpointHashFinder` reads it, so `model.id` is its only identifier. `unknown` is never returned here |

### model_folders.py (added 2026-08-09 — model shelf B5; §16.3 host-capability for the writes)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/model-folders` | owner_only |  | Registered model folders, with a per-folder copy count. Reads host paths but takes none, so it stays on the shelf's `owner_only` tier rather than the locality tier, alongside the other shelf reads (`GET /adapters`, `GET /checkpoints`). `GET /reference-folders` and `GET /import-folders` are **not** the precedent, despite an earlier revision of this cell citing them: both are declared `scoped_list`, which admits any token and self-filters, whereas `owner_only` admits the owner alone. This route is therefore on the stricter of the two tiers, not on theirs. **Note it does NOT mirror `GET /libraries`:** that route redacts `path` to `null` for a remote owner and names redaction as its compensating control, whereas this one serves the host path in full to a remote owner, as `GET /adapters` does via `locations[].folder_path`. That is inside the trust-the-owner scope and matches the rest of the shelf, but it is disclosure, not redaction |
| GET | `/api/v1/model-folders/devices` | owner_only |  | Capacity of the drives the registered folders sit on, for the shelf's drive bands. `owner_only`, not the locality tier the mutators below carry: it takes no caller-supplied path, reads no file content and walks nothing, it stats folders that are **already** registered and whose paths `GET /api/v1/model-folders` serves to this same token. The delta over that route is the mount point (a prefix of a path the caller can already read) and the drive's size, so the locality tier would strip the meter from a remote owner without withholding anything that route does not already disclose. It does touch the filesystem, which is why it is a route of its own rather than fields on the folder list: an offline mount can make it slow, and the folder list must stay a database-only answer |
| POST | `/api/v1/model-folders` | **local_owner_only** |  | §16.3 model-folder create; takes a caller-supplied host path, exactly the reference-folder create class. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| PATCH | `/api/v1/model-folders/{folder_id}` | **local_owner_only** |  | §16.3 model-folder update; sets the Docker bind host path. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-folders/{folder_id}/rename` | **local_owner_only** |  | §16.3 renames a registered model folder on the host within its own parent and repoints its `model_folder` row: the rename half of `POST /model-folders/{folder_id}/dirs/move`, applied to the registered root. Takes no host path: one name, refused with the reason rather than rewritten (`refusal_for_folder_name`), joined to the folder's own parent. Only per-item folders the owner added; a locked folder, a taken name or a registered path is a 409, the route holds `SHELF_IO_LOCK` and the folder's scan slot, and it is refused in Docker. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| DELETE | `/api/v1/model-folders/{folder_id}` | **local_owner_only** |  | §16.3 model-folder delete; drops a registered host path and tombstones its `model_file` rows (the `model` rows and their curation survive). Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-folders/pick-up` | **local_owner_only** |  | §16.3 walks every registered host path the background folder scan walks and registers the model files it finds by name: a model that landed after its folder's last scan, which a workflow then reports missing (#1985). The folder scan's authority narrowed to the files named, so it sits on that tier. Takes no host path: the body is filenames, matched by basename against what the walk finds. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |

### model_moves.py (added 2026-08-09 — model shelf B7; §16.3 host-capability, whole block)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| POST | `/api/v1/model-moves` | **local_owner_only** |  | §16.3 the shelf's strongest filesystem authority: it writes new files into one registered host folder and unlinks files out of another. Strictly more than `reference-folders/{id}/move-pictures`, which is already on this tier. The batch is validated (destination, every item, path containment, free space) before the first byte, so a refusal touches nothing. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/model-moves` | **local_owner_only** |  | §16.3 progress for the in-flight or last move: destination folder id, per-file relpaths and outcomes. Deliberately **not** the shelf's `owner_only` read tier, but **not because the data is otherwise unreachable** — a remote owner is 200 on `GET /adapters`, which already serves `locations[].folder_path` and `locations[].relpath` for every copy of every model, so the filenames are not what the tier protects. What it protects is the *control surface of a host-filesystem operation*: this route is how a move is watched, and the DELETE beside it is how one is stopped, so a caller who may not start a move may not observe or steer one either. Same tier as the POST that alone can create one; no object data beyond what that POST already named. **Corrected 2026-08-09 (B7 sign-off): the earlier rationale — "a lower tier would hand the operation's filenames to a caller barred from every route that could produce them" — was false in its second half and is not why the tier is right** |
| POST | `/api/v1/model-folders/{folder_id}/relocate` | **local_owner_only** |  | §16.3 takes a caller-supplied host path and moves **everything a folder PixlStash owns holds** into it, then removes the originals — the `reference-folders/{folder_id}/relocate` class, carrying the file movement of `POST /model-moves`. Three folders qualify, all named by `relocatable_identity`: the managed store; PixlStash's own download folder (#905), whose new location is recorded so every downloader follows it; and the InsightFace packs (#906), recorded the same way, moved per pack *directory*, and the one whose path names the InsightFace root rather than the folder. Refused with 409 for anything else: an ordinary folder is one the owner registered and moving it is the owner's own act, and the HuggingFace cache is `fixed` because `HF_HOME` owns where it lives. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| DELETE | `/api/v1/model-moves` | **local_owner_only** |  | §16.3 cancels an in-flight host-filesystem move. Halting the owner's own file operation is the same authority as starting it, so it carries the same tier. Stops the queue between files; nothing already moved is rolled back, which is the ruling (shelf plan §7: no undo for shelf operations) |

### model_imports.py (added 2026-08-09 — model shelf B7; §16.3 host-capability, whole block)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| POST | `/api/v1/models/{model_id}/icon` | owner_only |  | Stores an uploaded image in the hub's content-addressed icon store (`<hub_dir>/icons/<sha256>.webp`) and sets `model.icon_sha256`. `owner_only` rather than §16.3: the store is PixlStash's own directory beside the hub, and no host path is taken from the caller. The payload is checked by **magic bytes**, not by filename or client content type, and capped — an icon is served back from our own origin, so "it is an image" has to be a fact about the bytes. POST is blocked for READ tokens by the middleware |
| GET | `/api/v1/model-icons/{sha256}` | owner_only |  | Serves one stored icon. The path segment is validated as a 64-char hex digest **before** it becomes a path and is then contained against the icon directory, so a segment that is not a digest is a 400 rather than a read. Addressed by content hash rather than model id, so one cached response serves every model sharing that mark. The served media type is sniffed from the bytes, never taken from the `.webp` suffix the store names everything with |
| POST | `/api/v1/models/icons/clear` | owner_only |  | Clears `icon_sha256` on the named models, reading which ones actually had one and clearing them in the same transaction so the receipt reports what changed rather than how many ids were sent. The stored **file is deliberately not deleted**: the store is shared, so another model may name the same hash. POST is blocked for READ tokens |
| GET | `/api/v1/model-stacks/proposals` | owner_only |  | Dry run: groups loose adapters that differ only by a training step or a version token and **writes nothing**, so the whole list is drawn before the owner decides. `owner_only` and deliberately **not** the §16.3 locality tier its shelf neighbours sit on — it takes, walks, writes and unlinks no host path; it reads `model` rows the scan already wrote and surfaces folder **ids**, not paths. Same reasoning that kept `GET /adapters` off that tier |
| POST | `/api/v1/model-stacks` | owner_only |  | The applying half of the same pair. Writes hub columns (`adapter_stack` plus each member's `stack_id` / `stack_position`) and touches no filesystem, so `owner_only` rather than the locality tier. POST is blocked for READ tokens by the middleware. The gate is re-read **inside** the write transaction, so a row stacked between the dry run and the confirmation is dropped rather than torn out of the stack it already has. `fuse` relaxes that gate deliberately — it absorbs the named models' stacks **whole** and deletes the emptied rows, which is how stacking two stacks fuses them — and even then a row is only admitted from a stack this call is absorbing, never from a third one that appeared meanwhile. The `MAX_MEMBERS_PER_STACK` ceiling is re-counted *after* that widening, on the service rather than the route: fusing turns two submitted ids into two whole stacks, so a route-only check is a limit the request can walk past |
| DELETE | `/api/v1/model-stacks/{stack_id}` | owner_only |  | The undo for both of the above. Clears `stack_id` / `stack_position` on every member and deletes the `adapter_stack` row; **no filesystem access at all** — nothing is moved, renamed or unlinked — so `owner_only` rather than the locality tier. DELETE is blocked for READ tokens by the middleware. An unknown `stack_id` is a 404 raised inside the transaction, so a wrong address cannot half-release rows on its way to reporting itself |
| PATCH | `/api/v1/model-stacks/{stack_id}/cover` | owner_only |  | The owner overruling the filename heuristic: moves one member to `stack_position` 0 and renumbers the rest, so the shelf draws the file they chose for the run. Writes two hub columns and **no filesystem at all**, so `owner_only` rather than the locality tier; PATCH is blocked for READ tokens by the middleware. A `model_id` that is not in that stack is a 404 raised inside the transaction, so a wrong address writes nothing — and because the whole renumber happens there, **this route** can never leave the stack with two position-0 members or none. Other writers can — deleting or forgetting a member drops the `model` row somewhere that knows nothing about stacks — which is why `model_shelf_service._purge` calls `stack_detector.repair_stacks` |
| DELETE | `/api/v1/model-stacks/{stack_id}/members/{model_id}` | owner_only |  | The single-member counterpart to breaking the whole stack up. Clears `stack_id` / `stack_position` on one model and renumbers the survivors, or dissolves the stack when it would be left with one member; **no filesystem access at all**, so `owner_only` rather than the locality tier. DELETE is blocked for READ tokens by the middleware. A `model_id` that is not in that stack is a 404 raised inside the transaction |
| GET | `/api/v1/model-folders/{folder_id}/runs` | **local_owner_only** |  | §16.3 walks a registered ai-toolkit output root and reads every run folder and `config.yaml` under it — the same host-filesystem authority as the background folder scan and `reference-folders/detect-sidecars`. Reads only: nothing is hashed, copied, moved or written, which is what lets the whole card grid be drawn before the user commits to anything. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/model-folders/{folder_id}/dirs` | **local_owner_only** |  | §16.3 lists one directory level inside a registered model folder and reports the names of subdirectories PixlStash never registered, each with recursive counts of what sits under it. That is the authority `models/{model_id}/samples` exercises (a walk of a registered root, narrowed to one directory), so it takes that tier, raised from the `owner_only` the issue specified. The folder list beside it reports what is registered; this reports what is on disk. Locked folders (managed store, HuggingFace cache, InsightFace packs, source and downloads folders) answer `locked: true` with no `dirs`, and their disk is never read. Templated, so the `/api/v1/model-folders/` prefix in `READ_BLOCKED_GET_PREFIXES` covers it; no literal entry is added. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-folders/{folder_id}/dirs` | **local_owner_only** |  | §16.3 creates one directory inside a registered model folder on the host (#2206) — `POST /filesystem/folders`' mkdir authority, narrowed: the body names a folder id, a POSIX parent contained against that folder by `resolve_subfolder` (no `..`, no link or dot-name on the way) and one name, which is refused with the reason rather than rewritten (`refusal_for_folder_name`: separators, reserved Windows names, a leading dot, a trailing dot or space). A locked folder is a 409 and the route is refused in Docker as that route is. Writes no row. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-folders/{folder_id}/dirs/move` | **local_owner_only** |  | §16.3 moves or renames a directory tree inside or between registered model folders (#2206): a rename on one drive, a copy, verify by `sha256`, publish and remove across drives, then one transaction repointing every `model_file` row under it. `POST /model-moves`' write-and-unlink authority over a whole subtree, on the same job slot and the same `GET /model-moves`. Both ends are contained against registered folders; the registered root, a target inside the source, a locked folder, a taken name and a registered folder inside the source are refused before anything is touched. Takes no host path. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-folders/{folder_id}/dirs/remove` | **local_owner_only** |  | §16.3 moves a directory tree inside a registered model folder to the OS trash, or deletes it on request, and drops the `model_file` rows under it (#2207): `POST /model-files/delete`' unlink authority over a whole subtree, files the shelf does not list included. Takes no host path: the directory is contained against its folder by `resolve_subfolder` (no `..`, no link or dot-name on the way), links inside are removed as links and never walked, only per-item folders the shelf may unlink from are eligible, and the removal must match the dry run's fingerprint. |
| POST | `/api/v1/model-folders/{folder_id}/open` | **loopback_owner_only** |  | §16.3.1 RED LINE: the folder tree's `Open in file manager` shows a registered model folder, or a directory inside it, in the file manager of the machine PixlStash runs on. The same host-GUI spawn as `POST /api/v1/models/{model_id}/open-location`, through the same `pixlstash/utils/host_open.py`, so the same tier: loopback only, and `allow_remote_host_ops` is never consulted. Takes no host path: `path` is POSIX relative to the folder, refused with `..`, `.` or empty segments and contained by `resolve_path_within`; nothing is written and nothing is returned but `status` |
| GET | `/api/v1/model-folders/comfyui-reads` | **local_owner_only** |  | §16.3 asks the saved ComfyUI, only one on this computer and at the address `locate` vetted, where it loads each kind of model from (`/internal/folder_paths`), and returns the subdirectories of registered model folders that match: names PixlStash never registered, the authority of `model-folders/{folder_id}/dirs`. `known: false` and no reads when nothing matches. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1); untemplated, so also on `READ_BLOCKED_GET_PATHS` |
| GET | `/api/v1/model-folders/{folder_id}/runs/{run_name}/samples/{filename}` | **local_owner_only** |  | §16.3 reads inside a registered ai-toolkit output root and writes nothing, so a step can be judged before it is imported — the same authority class as the background folder scan, which is why it sits on this tier. **It is NOT a subset of the listing above it**, and an earlier draft of this row claimed it was: the listing returns metadata for sample names matching ai-toolkit's own regex, while this returns *raw bytes* for any file with an allowlisted extension, including names the listing never reported. That is a new capability class, not a narrower one (corrected in the adversarial review of #878). **Three containment joins, not one**: the run name against the registered folder path; the `samples/` directory itself, because `resolve_path_within` realpaths the base it is handed and a symlinked `samples` would otherwise become its own safe base (a live escape found by that same review — a `source` folder's contents are third-party tool output the owner merely pointed at, and tarballs and git repos carry symlinks); then the filename against that resolved directory, because a single run-level join would let `samples/../config.yaml` pass. The extension is checked against an allowlist (`SAMPLE_MEDIA_TYPES`) rather than guessed, so an `.html` dropped into `samples/` cannot be served from our own origin. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-files` | **local_owner_only** |  | §16.3 the shelf's `Add file` (plan F6): copies one loose `.safetensors` from anywhere on this machine into a registered folder — the managed store unless another is named — and registers it. **The one shelf route that takes a host path in the body**, which the import block above it deliberately does not; it cannot be otherwise, because the whole point is a file in a folder nobody registered. It is therefore the `POST /model-folders` path-taking class carrying the file *writing* of `POST /model-moves`, and it is on this tier for both halves rather than for one. What is contained is the **write**: the destination is `resolve_path_within(folder.path, basename)`, so a symlink standing at the destination name is refused. The read is bounded instead — a regular file, `.safetensors`, and refused outright if it already sits inside a registered folder (the folder scan is what puts that one on the shelf). **Nothing is ever unlinked**: the source is the owner's own file and the copy that fails verification is discarded. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-imports` | **local_owner_only** |  | §16.3 copies a run's checkpoints into a registered host folder and, when the source folder carries `delete_after_import`, unlinks them from the output root — the same authority as `POST /model-moves`. The run is named, never pathed: the body gives a registered `source` folder id and a run *name*, and the server joins them with `resolve_path_within`, so a name resolving outside the registered root is a 400 rather than a read. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/models/{model_id}/samples` | **local_owner_only** |  | §16.3 lists one directory inside a registered model folder — the `<stem>_samples/` that an import copied a run's previews into and that a move carries along. It walks a registered host path, which is the folder scan's authority narrowed to one directory, and it reports the names of files PixlStash never registered: the trainer named them and anything the owner drops in there is listed too (filtered to the extensions `SAMPLE_MEDIA_TYPES` allows, so the list never advertises a name the byte route would 400). **Not `owner_only`, which is what the plan for this change asked for.** Its reasoning was that the route is addressed by a `model.id` with no host path crossing the wire — the exact argument the `GET /api/v1/adapters/{sha256}/file` row above records as *not* the argument, because the tier follows the authority exercised rather than what the route accepts. Kept on the byte route's tier besides, so a caller who may not fetch a preview is not handed a list of them. The derived directory is contained against the registered folder path, and a `model_file.relpath` that escapes its folder is a broken row: it is logged and skipped, never read. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/models/{model_id}/samples/{filename}` | **local_owner_only** |  | §16.3 serves raw image bytes out of a registered model folder, which is `GET /adapters/{sha256}/file`'s authority class exactly and the shelf-side twin of `model-folders/{folder_id}/runs/{run_name}/samples/{filename}` — the same previews, after the import moved them from the output root onto the shelf. It takes no host path (a `model.id` addresses a row the importer wrote), and per the correction recorded on 2026-08-11 that is not what would put it on `owner_only`. **Two containment joins, not one**, for the two reasons the run-sample row states at length: the `<stem>_samples` directory is contained against the registered `model_folder.path` first, because `resolve_path_within` realpaths the base it is handed and a *symlinked* samples directory would otherwise become its own safe base; then the filename against that resolved directory, because a folder-level join alone would happily pass `../alice.safetensors`, which lands inside the registered folder and is not a file this route serves. The extension is checked against the same `SAMPLE_MEDIA_TYPES` allowlist rather than guessed, so an `.html` dropped into the directory cannot be served from our own origin, and `{filename}` is one URL path segment. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-files/delete` | **local_owner_only** |  | §16.3 the shelf's only destructive verb (#933): removes every registered copy of the named models — OS trash by default, `permanent=true` unlinks — and then their hub rows. It is the **unlink half of `POST /model-moves` standing alone**, without the copy that justifies it, which is why it sits on that route's tier rather than the shelf's `owner_only` read tier. It takes **no host path**: the body is a list of `model.id`, every path is contained against the folder the scanner recorded — lexically for the file so a symlinked model loses its link rather than its target, and by `realpath` for the directory holding it so no symlinked component can redirect the unlink — and a row that escapes is refused rather than unlinked. The eligible folders are `user` and `managed` only, so PixlStash's own engine roots, the InsightFace packs and the shared HuggingFace cache are refused whole, as is any model with an `unreachable` copy (an unplugged drive is not a deletion). A `<stem>_samples/` directory holding **nothing but** the trainer's previews goes with the model, non-fatally — the lifecycle the import opens and a move carries has to close here or the orphan refuses that run's whole re-import — while a directory holding anything else is the owner's and stays, because its path is inferred from the model's filename rather than named by the caller. Bytes go before rows, so a failure leaves a row naming a file that is not there — which the next scan marks `missing` — rather than a file nothing on the shelf can see. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-files/merge` | **local_owner_only** |  | §16.3 the per-copy delete (#1439): keeps the copy the body names and removes every other `present` copy of that model — OS trash by default, `permanent=true` unlinks — so it is `POST /model-files/delete`'s unlink narrowed to the copies the caller did **not** name, and sits on that route's tier for the same reason: it destroys the owner's bytes. It takes no host path (the body is a `model.id` and a `(folder_id, relpath)` the scanner wrote), every path is contained by the same `_contained_path`, and the naming is inverted on purpose — naming the keeper is what makes it impossible for a request to empty a model, and a keeper the shelf does not hold as `present` is refused rather than acted on. Only the copies being **removed** need a folder whose contents are the owner's (`user`/`managed`, or the download folder's unclaimed files), so keeping the shared HuggingFace cache's copy and removing a user-folder one is legitimate; a model with an `unreachable` copy is refused whole, as on the delete. **No hub row is deleted**: the removed copies keep their `model_file` rows at `state = 'removed'`, which is what keeps a recipe's filename resolving to the model afterwards. It also reads the owner's configured ComfyUI for its advertised model list, the same outbound read the run pre-flight makes on the same credential, and `dry_run=true` plans it and removes nothing. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-downloads` | **local_owner_only** |  | §16.3 downloads a model a workflow is missing into the registered download folder (the one marked `is_download_target`, else the default folder) and registers it. `POST /model-files`' file-*writing* class, with the bytes read off the network instead of the disk. It takes **no host path and no URL**: the body is the file name the workflow's loader asks for, the link is looked up server-side in the committed ComfyUI template list (`data/workflow_templates.json`, https only), so no caller can point the server at an address of its choosing. The write is contained with `resolve_path_within` against the folder, the name must carry a shelf suffix, an existing name is refused, and bytes go to a `.pixlstash-partial` that is published no-clobber only when complete. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-downloads/{download_id}/cancel` | owner_only |  | Stops a running download and discards its own partial file. Writes nothing else and returns only whether one was running, so it carries the shelf's owner tier rather than the start's locality tier. POST is blocked for READ tokens |

### workflows.py (added 2026-08-23 — the workflow library view, plan §F1/§F2)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/workflows` | owner_only |  | The Workflows grid: one entry per workflow (#1623), and the counts of what was left out. `variant_activity` groups every non-deleted picture in the vault, and the cover strip hands back a **thumbnail URL** for each workflow's best three, which is the picture-ids disclosure of the row below repeated once per workflow and pointed straight at the image. It also names the owner's model files, and `recipe_values` the checkpoint and LoRA values every kept picture used with how many pictures each, which on a real shelf name people. Hidden and one-off workflows are counted, never listed. No membership to check: a workflow id is a core hash or a random group id, and nothing here mutates |
| GET | `/api/v1/workflows/{workflow_id}` | owner_only |  | One workflow opened. It computes the whole grid to rank this card (the Bayesian prior is the library's own mean rating), so it reads exactly what the grid reads, and it adds what the grid does not: the **parameter values** the owner's best pictures were made with, read out of stored instance documents (capped at the newest `DEFAULT_SAMPLE` runs, so the read is bounded by the code rather than by how much somebody has generated). Those are the settings a person chose, which is the reason this cannot fall below the tier its list sits on. A hidden workflow still opens here — hiding is a decision about the grid, not a deletion — and an id this hub does not hold is a 404 rather than an empty 200 |
| GET | `/api/v1/workflows/{workflow_id}/pictures` | owner_only |  | Picture **ids** for one workflow, newest first, capped at 60 by the route rather than by the caller. The plainest reason for the tier in the block: ids straight out of the vault with no scope filter, so a scoped token would be handed ids its scope does not contain. It is deliberately not `scoped_list` — a scoped answer would need a narrowing parameter and a policy to check it against, which is work for whoever needs it and not something to invent here |
| PATCH | `/api/v1/workflows/{workflow_id}` | owner_only |  | The workflow writes start here (`workflow_group_attr`, #1623), and for them `owner_only` is not even a judgement about disclosure: this is the owner naming their own workflow, and there is no picture, set, character or project the decision belongs to, so no narrower policy could describe it. The verb carries a second belt - the READ-token middleware refuses PATCH before the gate reads a declaration - and the response is `GET /workflows/{workflow_id}`'s payload, so it discloses exactly what that row is owner-only for. An unknown id is a 404 rather than an attribute row written against a workflow this hub does not hold |
| POST | `/api/v1/workflows/{workflow_id}/thumbnail` | owner_only |  | The owner's own picture for their own workflow: the upload goes into the hub's content-addressed icon store and a `workflow_thumbnail` row names it, pinned. No vault row, no file of the owner's, no host path. It answers with `GET /workflows/{workflow_id}`'s payload, so it discloses exactly what that row is owner-only for; POST is blocked for READ tokens before the gate is reached. An unknown id is a 404 before anything is stored |
| PUT | `/api/v1/workflows/{workflow_id}/model-fix` | owner_only |  | Replace a model the workflow's base graph names (a missing checkpoint, VAE or text encoder) with a shelf model, or undo that. It **re-keys cards internally**: the replacement is read as the original in the card key, so every card of the base topology is re-keyed in one transaction (`hub/workflow_card_writes.set_model_fix`); the workflow id does not move. Owner-only because it rewrites what the owner's runs load and reads the vault's per-variant picture counts. Answers with the opened workflow, the same disclosure as the detail read |
| PUT | `/api/v1/workflows/{workflow_id}/default-lora` | owner_only |  | One LoRA of a workflow in or out of its default recipe (`workflow_group_default`, the `lora:<sha256>` row only), named by the `asset:` reference `lora-summary` lists and resolved to one shelf digest; 404 for a LoRA the workflow's pictures do not load, 409 where the shelf cannot name it. Owner-only as its siblings are, and it answers with the opened workflow, the same payload and disclosure as the detail read |
| PUT | `/api/v1/workflows/{workflow_id}/defaults` | owner_only |  | The parameter edits of a workflow's default recipe, replaced whole (`workflow_group_default`, parameter rows only - an address naming a model or a `lora:` is a 422). Owner-only as its siblings are, and it answers with the opened workflow so the new values come back marked `edited` - the same payload, and the same disclosure, as the detail read |
| GET | `/api/v1/workflows/{workflow_id}/form-inputs` | owner_only |  | Expose a parameter's read: the literal inputs of this workflow's graph a run can set by address, each with its value and ComfyUI's type and options. The graph is resolved out of the whole library like the LoRA chain's, and the values are the owner's settings (prompts, seeds, model files and credentials are left out), so no scope could describe it. Reaches the owner's configured ComfyUI for the cached `object_info`; unreachable is still a 200 with the types null |
| PUT | `/api/v1/workflows/{workflow_id}/pins` | owner_only |  | Which parameters the Run form asks for each run, the rest being fixed (`workflow_group_pins`). Addressed by `<slot label>/<input name>` and never by node id. It answers with what it was given rather than reading the workflow, so it discloses nothing beyond the request; the tier is the owner's decision about their own library |
| PUT | `/api/v1/workflows/{workflow_id}/inputs` | owner_only |  | How a workflow's picture inputs are filled, kept per library (`workflow_group_picture_input`). A `fixed` input names a picture by **content** (`pixel_sha`), which is why the setup is library-scoped: a second library gets its own rather than a picture it does not hold. Owner-only rather than picture-scoped because a content-addressed picture is not a body id a scope check could resolve. A pin may be sent as a `picture_id` (#1457); it is resolved against the kept pictures and stored as that picture's content, never as the id |
| GET | `/api/v1/workflows/{workflow_id}/graph` | owner_only |  | This workflow's base graph as it runs, for *Open in ComfyUI* on the Workflow tab: the ComfyUI-PixlStash node fetches it when ComfyUI is opened with `?pixlstash_workflow=<workflow_id>`. Resolved as Run… resolves it (linked file, best kept picture, stored instance, against ComfyUI's own model list), and **not scrubbed** beyond blanking credential widgets — it is for the owner's own ComfyUI and is meant to run, which is Duplicate's reasoning too; the key is blanked because the graph crosses the network into ComfyUI's page. That makes it the most revealing read in the module, so it is owner-only with no narrower policy possible |
| GET | `/api/v1/workflows/{workflow_id}/export` | owner_only |  | This workflow's base graph as a ComfyUI file (v1.12 B8). Owner-only for `POST /workflows/run`'s reason and one more: the graph is resolved out of the whole library — the linked file, the best kept picture's embedded metadata, or a stored instance document — so it is never one object a scope could name. The **scrub** (`services/workflow_export.py`) is not what makes this tier safe; it is what makes the FILE safe for whoever it is given to. It blanks every widget `workflow_hash.carries_prose` calls prose — **by name, across the whole graph, never by asking which node the sampler reads**, since that question returns nothing for a two-sampler hires-fix graph and names widgets an SDXL encoder does not have — nulls seeds, applies the default recipe first (values and models set, LoRA loaders outside it and stages it runs without bypassed where ComfyUI says how) and empties every LoRA slot that does not load a default-recipe LoRA by filename **and by digest**, strips `_meta` titles, blanks picture file names, resets output paths, drops `checkpoint_id`, and drops any model name the shelf cannot vouch for along with the folder of the ones it can (`hub/workflows.unvouched_model_values`) — which is what stops a forgotten model name travelling back out of a picture's embedded graph. A graph that will not reduce is refused (409) rather than exported unscrubbed |
| POST | `/api/v1/workflows/{workflow_id}/duplicate` | owner_only |  | Writes this workflow's base graph into the owner's workflow folder under a free name, so it can be changed in ComfyUI without touching the original — and so a card the library only knows from its pictures gets a file at all. Unscrubbed on purpose: the copy stays on this machine and is meant to run. Owner-only as its siblings are, plus the plainer one that writing into the owner's workflow folder is not a disclosure question; POST is blocked for READ tokens before the gate is reached |
| POST | `/api/v1/workflows/{workflow_id}/inpaint-copy` | owner_only |  | Stores a new manual workflow made from this one, rewritten to inpaint with Crop & Stitch; the original is not changed. Resolves the card's graph out of the whole library as duplicate does, unscrubbed for the same reason (the copy stays on this machine and is meant to run). 422 with the reason when the graph cannot be converted. POST is blocked for READ tokens before the gate is reached |
| POST | `/api/v1/workflows/{workflow_id}/fixed-copy` | owner_only |  | Save fixed workflow: a copy of this workflow's base graph with the repairs a run on this ComfyUI makes (the owner's model fixes, same-model renames, a LoRA this ComfyUI lacks loaded through the ComfyUI-PixlStash loader by hash, missing seed and text nodes replaced), written as a new file. Reaches the owner's configured ComfyUI for `object_info` (503 without it), 409 when nothing needs fixing, and never changes the original file |
| GET | `/api/v1/workflows/{workflow_id}/lora-summary` | owner_only |  | The Workflow inspector's LoRA pile: which LoRA files every kept picture of this workflow loaded, and which change between them, with picture counts and best picture ids. Counted over the whole vault, which no scope could describe |
| GET | `/api/v1/workflows/{workflow_id}/lora-chain` | owner_only |  | The LoRA chain editor's read (#1478): this workflow's LoRA loaders in the order a run applies them, their strengths, and the shelf LoRA each one loads (digest, or a unique case-folded basename match). The graph is resolved out of the whole library like its siblings', and the shelf digests are the owner's model inventory, so no scope could describe it. Reaches the owner's configured ComfyUI for `object_info`; unreachable is still a 200 with `editable: false` and the loaders read untyped |
| PUT | `/api/v1/workflows/{workflow_id}/lora-chain` | owner_only |  | The chain as the owner left it, written as a new workflow: loaders deleted (through `bypass_node`), added from the shelf, moved (node ids kept) and re-weighted, in one call and one new card. `dry_run` answers the change list and writes nothing. The original is not changed unless `overwrite` asks for the edit to be saved over it (the workflow's next version; the versions before are kept). Refuses 409 where the chain cannot be edited honestly and 503 without ComfyUI; PUT is blocked for READ tokens before the gate is reached |
| GET | `/api/v1/workflows/{workflow_id}/model-swap` | owner_only |  | What the Clone with new models dialog draws: the model files this workflow's base graph names, the shelf's checkpoints, VAEs and text encoders, and with `?checkpoint_id=` the VAEs and text encoders recipes on this machine, or the ComfyUI runs already recorded, have run beside it, plus the LoRAs and ControlNets trained on another family. The graph is resolved from the whole library as the export's is, and the proposal reads co-occurrence across every recipe on the hub, so none of it is about an object a scoped token could hold |
| POST | `/api/v1/workflows/{workflow_id}/set-clone-plans` | owner_only |  | What Clone onto a workflow set draws: for each set asked (shelf model ids), the files the clone would take and each model loader of this workflow's base graph before and after, run through the clone's own rewrite on a copy. Resolves the graph from the whole library as Duplicate does and reads the whole shelf and the owner's configured ComfyUI; writes nothing. POST is blocked for READ tokens before the gate is reached |
| POST | `/api/v1/workflows/{workflow_id}/clone-with-models` | owner_only |  | Duplicate with model filenames replaced: writes a copy of this workflow's base graph into the owner's workflow folder, every loader naming a replaced file pointed at the new one, and names the new card as asked. Reaches the owner's configured ComfyUI for `object_info` to spell each name as ComfyUI lists it, and proceeds unchecked when it cannot; never changes the original file. POST is blocked for READ tokens before the gate is reached |
| GET | `/api/v1/workflows/{workflow_id}/changes` | owner_only |  | The changes waiting on this workflow, unsaved, what its last run changed, and which of Save and Save as new is open for them (#1849). The changes name the workflow's loaders and shelf models, and the verdict reads the default recipe (the whole library's pictures) and the owner's ComfyUI model list, so no scope could describe it. Templated under `/api/v1/workflows/`, held for the rollback by `READ_BLOCKED_GET_PREFIXES` |
| PUT | `/api/v1/workflows/{workflow_id}/changes` | owner_only |  | Replaces this workflow's waiting changes (model picks, LoRA strengths, loaders removed, stages). Writes one hub row and nothing in the graph. A LoRA attached to a person is dropped from the set. PUT is blocked for READ tokens before the gate is reached |
| DELETE | `/api/v1/workflows/{workflow_id}/changes` | owner_only |  | Discards this workflow's waiting changes. DELETE is blocked for READ tokens before the gate is reached |
| POST | `/api/v1/workflows/{workflow_id}/save` | owner_only |  | Save: applies the waiting changes, with the request's on top, through the run planner (the whole library's graph, the owner's ComfyUI for `object_info`) and stores the result as this workflow's next version. Refuses 409 for a change of a checkpoint this machine has and for changes made against another version; never writes a LoRA attached to a person. POST is blocked for READ tokens before the gate is reached |
| POST | `/api/v1/workflows/{workflow_id}/save-as-new` | owner_only |  | Save as new: the same graph stored as a new manual workflow under the name asked, this one left as it was. POST is blocked for READ tokens before the gate is reached |
| GET | `/api/v1/workflows/{workflow_id}/versions` | owner_only |  | The versions kept of this workflow's graph, newest first, with a few words on what each save changed. The owner's own history of a workflow resolved out of the whole library. Templated under `/api/v1/workflows/` |
| POST | `/api/v1/workflows/{workflow_id}/versions/{version}/restore` | owner_only |  | Makes an earlier version of this workflow's graph its newest, as a version of its own: nothing is removed. POST is blocked for READ tokens before the gate is reached |
| POST | `/api/v1/workflows/{workflow_id}/versions/{version}/opening` | owner_only |  | Holds one kept version of this workflow's graph, built as Open in ComfyUI builds the current one, for ComfyUI to open; stores nothing. POST is blocked for READ tokens before the gate is reached |
| PUT | `/api/v1/workflows/{workflow_id}/graph` | owner_only |  | A whole API-format graph saved over this workflow as its next version, for a graph edited outside PixlStash (ComfyUI, the MCP server). A graph that replaces a base model this machine has is stored as a new workflow instead; a LoRA attached to a person is left out and reported. It adds a version or a workflow, keeps the editor document sent with it as the workflow's layout, deletes no workflow and changes no recipe. Versions saved from ComfyUI are counted apart from the owner's own (49 each beside version 1) and prune only each other, the version a save supersedes within one opening goes only where no picture ran it and no recipe was saved on it, and a key bound to an address (the ComfyUI link key) always saves as `comfyui` whatever `source` it sends (#1852). PUT is blocked for READ tokens before the gate is reached |
| GET | `/api/v1/workflows/{workflow_id}/people` | owner_only |  | Each person with a LoRA attached that is for the base model of this workflow's checkpoint, with those LoRAs. It names people across the whole library and models across the whole shelf, which is why it is not narrower. Templated under `/api/v1/workflows/` |
| DELETE | `/api/v1/workflows/{workflow_id}` | owner_only |  | Sends this workflow's imported file (its base card's, else the first of its graphs that has one) to the system trash through the watched inbox and takes it off the card, sharing `routes/comfyui.py::trash_user_workflow` with `DELETE /comfyui/workflows/{workflow_name}` so there is one deletion path rather than two. Only the **user** folder resolves, so a built-in is a 404; a card with no file is a 409 ("hide it instead") rather than a silent success. The card, its variants and its pictures stay |
| POST | `/api/v1/workflows/run` | owner_only |  | Run a workflow (`workflow_id`, or `target` over the pictures selected) with its default recipe - read off the whole library's best pictures - applied by the server. Narrower than the `picture_scoped` run routes in `comfyui.py`: those replay one named picture's own embedded graph, so a scoped token is asking about its own object, while this resolves a graph from the WHOLE library - the linked workflow file, the library's best kept picture, or its stored instance documents - whatever the caller was scoped to. The body names LoRAs by shelf digest and the answer reports every workflow a selection grouped into together with the model filenames this ComfyUI is missing, which is the owner's model inventory. No per-object check could describe any of that. **It also reads and uploads pictures (#1457)**: by id from `picture_ids` and `inputs`, and by **content** from the workflow's stored `fixed` pins, each then uploaded into the owner's ComfyUI input folder. A pin is not a body id at all, so no body-id scope check could cover it, which is the same reason `PUT .../inputs` is owner-only |
| POST | `/api/v1/workflows/run/preflight` | owner_only |  | The dry run of the above, submitting nothing. Declared on the same tier rather than one below it on purpose: it carries exactly the same disclosure - the resolved source, the grouping, the missing model filenames and, since #1463, the node ids and class types of the LoRA loaders it took out (`bypassed_loras`) and of the seed nodes it replaced (`replaced_nodes`) - so a scoped token allowed here would get the whole leak with none of the generation, and since #1457 every picture input of the graph with the picture each pin resolves to (`picture_inputs`) |
| POST | `/api/v1/workflows/run/opening` | owner_only |  | The Run popup's *Open in ComfyUI*: the run's own body, building the graph that run would submit and holding it in memory for the owner's ComfyUI to fetch through `GET /api/v1/workflows/{workflow_id}/graph?run=<opening>`. On the run's tier for the run's reason: the graph is resolved from the whole library and comes back unscrubbed but for credential widgets. It queues nothing and stores nothing; where the run would go ahead it uploads the pictures the run puts in into ComfyUI's input folder, as the run does. The opening is 128 random bits, kept ten minutes, at most eight at once, and answered only for the workflow it was made for. POST is blocked for READ tokens before the gate is reached, and a ComfyUI-scoped key does not reach it |
| POST | `/api/v1/workflows/upscale` | owner_only |  | Upscale… (#1868): each picture named is uploaded into the owner's ComfyUI and upscaled there through an upscale model on the shelf (named by digest) or a Lanczos resize, and the result is stacked on top of it. On the run's tier for the run's reasons: it uploads library pictures to the owner's ComfyUI and queues work there, and the answer is the run's (prompt ids followed through `GET /api/v1/workflows/runs/{prompt_id}`). POST is blocked for READ tokens before the gate is reached |
| POST | `/api/v1/workflows/upscale/preflight` | owner_only |  | The dry run of the above: each picture's size and whether the run that made it upscaled it already, read off its embedded graph. Submits and uploads nothing; on the same tier because it reads each picture's own graph |
| GET | `/api/v1/workflows/runs/{prompt_id}` | owner_only |  | How one run of the above stands (#1839), by the `prompt_id` it answered with: `running`, `completed` with the ids of the pictures it added, or `failed` with the message the app shows (the node that failed and why, from ComfyUI's history). Owner-only because the run is: the message is ComfyUI's own exception text, which can carry model file names and paths on the owner's machine, and the picture ids are named whichever set, project or person they were filed under, so no object scope could gate them. Read from memory only (the newest 500 runs); a prompt id nobody queued here is a 404, as is a run of another library, which tells a caller nothing an owner did not already know. Under the workflow reads' transport rule (`ensure_secure_when_required`). `/api/v1/workflows/` in `READ_BLOCKED_GET_PREFIXES` holds it under the rollback |
| DELETE | `/api/v1/workflows/runs` | owner_only |  | The Tasks tab's Finished group's Clear (#2050): forgets the finished runs of the active library, keeping any whose changes from the workflow can still be saved. Writes hub `workflow_run_log` rows only. DELETE is blocked for READ tokens before the gate is reached |
| DELETE | `/api/v1/workflows/runs/{run_id}` | owner_only |  | Dismisses one finished run (#2050), by the batch id `GET /api/v1/workers/progress` lists it under in `finished`, and in the same transaction the run set its workflow kept from it (a `workflow_change` row, as `DELETE …/{workflow_id}/changes` drops the waiting one). A run id names no library object a scope could hold. DELETE is blocked for READ tokens before the gate is reached |

### recipes.py (added 2026-09-18 — saved recipes, plan §5.5, step B6)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/recipes` | owner_only |  | The owner's saved recipes — their prompts, the LoRA filenames they load and the parameters they changed — plus **credit**, which counts the kept pictures of every workflow in the recipe's stack whose prompt and LoRA names match. That count is `workflows.py`'s whole-library disclosure in a narrower shape: a picture-, set- or project-scoped token holding it would learn how much of the library one look accounts for. `owner_only` is therefore a decision and not a default. Not §16.3: no host path, nothing walked, no file read |
| POST | `/api/v1/recipes` | owner_only |  | Saves a look against one `workflow_key`. Writes a vault row and nothing else; POST is blocked for READ tokens before the gate is reached, and the gate refuses every scoped token besides. The body's `source_picture_id` is checked to exist and then stored, never read back: an unknown id is a 422 rather than a foreign-key 500, a picture deleted later nulls the column rather than taking the recipe with it, and no picture data crosses the wire on this route |
| PUT | `/api/v1/recipes/order` | owner_only |  | Sets the order of the recipes the caller lists, by a complete ordered id list. Refused whole when an id is unknown, so a half-applied order is never left behind — and because it addresses recipes by id it is the one write here that could reach a row the caller did not create, which is precisely what `owner_only` closes |
| GET | `/api/v1/recipes/used` | owner_only |  | The looks the kept pictures of the named workflows' stacks were actually made with — each distinct prompt and LoRA-name combination, its picture count, one cover picture and whether a saved recipe keeps it (`saved`, a boolean about rows `GET /api/v1/recipes` already returns to the same caller) — minus the group with neither a prompt nor a LoRA, which matches everything and identifies nothing. **On both belts**: its own path in `READ_BLOCKED_GET_PATHS`, because an untemplated owner-class GET is not held by a prefix under the gate rollback, and under the `/api/v1/recipes/` prefix with its templated siblings. **The same picture rows as the credit figure on `GET /api/v1/recipes`, and a strictly WIDER disclosure of them.** That route groups these pictures and returns, per *saved* recipe, an integer; it says nothing at all about a look the owner never saved. This one returns those unsaved groups: the prompt verbatim, the LoRA file names, and a `cover_picture_id` that dereferences to a thumbnail. On the library this feature exists for — one that has never pressed Save — `GET /recipes` discloses nothing and this discloses every prompt in the stack. Same class, same tier, more of it: owner-only is therefore the same decision made a second time and not an inheritance, and nothing weaker was ever a candidate. A literal path declared **before** the `{recipe_id}` siblings in the router, as `/recipes/order` already is, so no templated route can shadow it. `workflow_key` repeats for a multiple selection and is capped at `MAX_UNION_KEYS`; stacks are folded before the read to keep the `IN` short, and an overlapping selection cannot count one look twice because both reads end in an `IN` over a set |
| PATCH | `/api/v1/recipes/{recipe_id}` | owner_only |  | Edits the fields the request carries. `workflow_key` is not writable, so a recipe cannot be moved onto another workflow, and `position` is not either — ordering is the route above. A recipe id is a vault row id with no scope of its own; the gate's owner check is the whole of its authorization |
| DELETE | `/api/v1/recipes/{recipe_id}` | owner_only |  | Forgets one recipe. The pictures it made are untouched — credit is computed on read from the picture rows, so there is nothing else to unlink |
| GET | `/api/v1/recipes/{recipe_id}/export` | owner_only |  | The recipe as a file, whole (v1.12 B8): prompt, negative, LoRA names with strengths, settings and seed. It withholds nothing — a recipe with those taken out makes nothing — so the response also carries `shares`, the plain list the export dialog shows before the owner agrees, including a line naming any model the shelf cannot vouch for, which is the same forgotten-name check the workflow export enforces by blanking. The row's id, its position in the tab and the picture it was saved from are dropped: they are this library's bookkeeping and mean nothing elsewhere. **It is also the first templated owner-class GET in `recipes.py`**, so this change adds `/api/v1/recipes/` to `auth.READ_BLOCKED_GET_PREFIXES` — the belt that keeps it closed if `AUTHZ_GATE_ENFORCING` is ever rolled back. Every other route in the module is a write, which the READ-token middleware refuses on the verb whatever the gate is doing; a GET returning the owner's prompt verbatim is not |
| POST | `/api/v1/recipes/{recipe_id}/extract-workflow` | owner_only |  | Makes a saved recipe a manual workflow of its own: the graph the run planner builds for it (`_plan`, the one way a recipe becomes a graph, with ComfyUI not asked), stored as a `workflow_document` row with the recipe's name and the workflow it came from. The graph is resolved out of the whole library as `POST /workflows/run` resolves it, and a recipe id is a vault row id with no scope of its own, so the gate's owner check is the whole of its authorization; POST is also refused to READ tokens by method |

### other

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/sort_mechanisms` | any_token |  |  |
| GET | `/api/v1/training/jobs` | owner_only |  | Lists the owner's LoRA training jobs. Reads job rows only. |
| POST | `/api/v1/training/jobs` | **local_owner_only** |  | §16.3 starts an ai-toolkit trainer process on the host and writes the job's dataset and output under the image root. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true`. |
| GET | `/api/v1/training/jobs/{job_id}` | owner_only |  | One job's record, reads rows only. |
| GET | `/api/v1/training/jobs/{job_id}/status` | owner_only |  | Progress snapshot for one job (step, total steps, latest loss, progress, info). Reads rows only. |
| GET | `/api/v1/training/jobs/{job_id}/live` | owner_only |  | What the Training runs view draws for one job: its phase, checkpoint columns, sample prompts and the preview filename in each cell, the loss curve, the cover step, speed and time left from the trainer's step log, and the card's and RAM's figures with the trainer's own share. Reads the job row, the run folder, the trainer's two SQLite files read-only and the machine's memory; returns no host path. |
| GET | `/api/v1/training/jobs/{job_id}/snapshot` | owner_only |  | One job's dataset snapshot: file name, sha256 and frozen caption per picture the trainer was given. Reads hub rows only; no host path. |
| GET | `/api/v1/training/jobs/{job_id}/snapshot/export` | owner_only |  | The same snapshot as a zip: the image files from the job's own dataset folder (only those still matching their recorded sha256), the frozen captions and `snapshot.json`. Reads the job's rows and its dataset folder; returns no host path. |
| PUT | `/api/v1/training/jobs/{job_id}/cover` | owner_only |  | The owner choosing which checkpoint covers a trained run: on the shelf it calls `stack_detector.set_cover` and moves the set's or character's LoRA attachment to that checkpoint; before the import it records `cover_step`, which the import applies. Writes hub and vault rows and **no file**, so `owner_only` like `PATCH /model-stacks/{stack_id}/cover`; PUT is blocked for READ tokens by the middleware. A step the run kept no checkpoint at is a 409 and writes nothing. |
| POST | `/api/v1/training/jobs/{job_id}/cancel` | **local_owner_only** |  | §16.3 signals the host process group of a running trainer. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true`. |
| DELETE | `/api/v1/training/jobs/{job_id}` | **local_owner_only** |  | §16.3 removes a job's host work folder, its dataset snapshot and its rows; never a folder in the shelf. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true`. |
| GET | `/api/v1/training/ai-toolkit/discover` | **local_owner_only** |  | §16.3 discloses host paths: searches the shelf's folders, the saved path and the usual places under the home directory for an ai-toolkit checkout, and returns every directory it tried. Untemplated, so also on `READ_BLOCKED_GET_PATHS`. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true`. |
| POST | `/api/v1/training/ai-toolkit/check` | **loopback_owner_only** |  | §16.3 RED LINE (#2295): starts the Python it is given on the host (a torch/CUDA probe and `run.py --help`) with an argument list and a timeout, so choosing it is choosing argv[0]. The checkout must be one (`run.py` and `toolkit/`) and the Python must be in its `.venv`/`venv` or be the one `find_python` picks. One check at a time (409 while another runs). Loopback only; `allow_remote_host_ops` can NOT loosen it. POST is also refused to READ tokens by method. |
| GET | `/api/v1/training/ai-toolkit/status` | **local_owner_only** |  | §16.3 discloses host paths: the configured checkout, interpreter, datasets folder and run folder, with run counts. Untemplated, so also on `READ_BLOCKED_GET_PATHS`. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true`. |
| PUT | `/api/v1/training/ai-toolkit/config` | **loopback_owner_only** |  | §16.3 RED LINE (#2295): stores the checkout and the Python that is argv[0] of every later trainer, both checked on this host as `/check` checks them; both null disconnects. Loopback only; `allow_remote_host_ops` can NOT loosen it. Refused to READ tokens by method. |
| GET | `/api/v1/training/presets` | owner_only |  | Lists the shipped training recipes and whether the saved checkout ships the model code each needs. Reads no picture data. |
| POST | `/api/v1/training/presets/check` | owner_only |  | Checks pixlstash.dev for newer training recipes and keeps the ones that verify, in the cache beside the hub. Takes no URL or path; reads no picture data. Refused to READ tokens by method. |
| GET | `/api/v1/training/sample-prompts` | owner_only |  | Writes a run's default sample prompts from the set's or character's captions and tags; the description it returns is taken from them. |
| GET | `/api/v1/training/dataset` | owner_only |  | Counts the pictures a run of the set or character would hold and the Train dialog's checks (no tags, near copies, faces named as somebody else), and names the character a reference set belongs to. Untemplated, so also on `READ_BLOCKED_GET_PATHS`. |
| POST | `/api/v1/training/resolve` | owner_only |  | Resolves a recipe from the dialog's choices, a picture count and this machine's free GPU and RAM, as `POST /training/jobs` would, without queueing; reads no picture data. Refused to READ tokens by method. |

---

## NEEDS REVIEW — flagged for the CSO adversarial review

These are the honest ambiguities: routes where the current authorization is inconsistent with siblings, where the declaration documents an existing over-exposure, or where the vocabulary does not cleanly fit. None were "papered over" with a confident guess.

### N1. `GET /{full_path:path}` (frontend SPA fallback) declared `public` but not in `AUTH_EXCLUDED_*`

`server.py::frontend_fallback` serves the static SPA shell/assets and returns no owner resource data, so `PUBLIC` matches intent. But the template is **not** statically in `AUTH_EXCLUDED_PATHS/PREFIXES` — the middleware requires auth for any concrete non-excluded deep path that falls through to it. The plan's PUBLIC-consistency check (§3.3 item 3: a `PUBLIC` declaration must match `AUTH_EXCLUDED_*` or boot fails) would trip here. **Decision needed:** add the SPA fallback to `AUTH_EXCLUDED_*`, or special-case the catch-all in the consistency check.

### N2. `reviews.py` (10 routes) and `tag_health.py` (2 routes) — `owner_only` vs. the unscoped-READ token

Both modules gate with a **bespoke inline check** (`_token_scope_ids(...) is not None → 403` / `_reject_scoped_tokens`), whose comments state "Owner-only surface". But that check keys on `fetch_scope_allowed_picture_ids`, which returns `None` (allow) for **both** an owner **and** an unscoped-READ token (`resource_type is None`) — it only 403s a **resource-scoped** token. So today an **unscoped-READ** token can `GET` the review queue / tag-health board (writes are middleware-blocked). I declared these `owner_only` to match the code's stated intent and the eventual end-state. **Divergence to confirm:** when the gate flips to enforcing `OWNER_ONLY` (Step 3), it will **newly 403 an unscoped-READ token** on these reads — a behaviour change for that (rare, owner-equivalent read-all) token. Confirm no unscoped-READ token is minted/relied upon before Step 3, and that `test_read_token_security.py` covers it. Alternative if that token must keep read access: these become `any_token` (with the inline check retained through Step 4), but then Step 5 removal would need a gate equivalent to keep resource-scoped tokens out.

### N3. Derived-id `*_SCOPED` routes — the gate's `id_param` resolution does not cover them (Step-4 work)

Four routes are scope-checked today via a **derived** id (resolved from a name / a suggestion), not a numeric path id. The `*_SCOPED` policy is the correct scope class, but the `id_param` I recorded is the name/suffix param so the declaration validates; the **Step-4 gate must resolve name→id (or suggestion→picture)** or these keep an inline check.

| Route | Policy | Recorded `id_param` | Resolution the gate needs |
|---|---|---|---|
| `GET /api/v1/projects/{project_name}/picture_sets/{picture_set_name}` | `set_scoped` | `picture_set_name` | (project_name, set_name) → set id |
| `GET /api/v1/projects/{project_name}/characters/{character_name}` | `character_scoped` | `character_name` | (project_name, char_name) → character id |
| `GET /api/v1/projects/{id_or_name}` and `/picture_sets` | `project_scoped` | `id_or_name` | id-or-name → project id |

**Step-4 resolution (principal ruling 2026-07-21 D2):** these 4 routes are marked
`resolved_inline=True` (a typed, validator-checked `RoutePolicy` field, not a
comment). The gate does **not** object-check them — resolving name→id at the gate
would duplicate each handler's own int-or-name lookup and risk a
gate/handler divergence (the exact defect this refactor exists to kill; there is
**no** shared name→id resolver today, verified). Their inline
`_require_scope_allows_*` checks remain the live enforcement and must **not** be
removed in Step 5 until a shared resolver exists.

**#708 condition-2 amendment (2026-08-04):** all four also name a **project** in
their path — a second scope question, over the project space rather than the
object, that neither the gate's `id_param` resolution nor
`enforce_project_filter_scope` (query params only) can see. Each handler resolved
that project *before* any scope check, so its 404 branches answered from it: a
`picture_set`-scoped token could tell "project exists and holds my set" (200) from
"exists and does not" (404 *Picture set not found*) from "does not exist" (404
*Project not found*), and `GET /projects/{id_or_name}` answered 403 for an
existing project vs 404 for a missing one. All four now call
`enforce_project_path_scope(server, request, resolved_id_or_None)` on the resolved
id **before** the membership query, refusing with one constant 403 body in all
three cases; the two `project_scoped` routes have it *instead of*
`_require_scope_allows_project` (a strict superset of it), the two name-derived
`set`/`character` routes have it *in addition to* their own inline check. Do not
reorder it after the resolution. See `docs/backend_architecture.md` §16.6; both
directions pinned in the R1d section of
`tests/multi_project_authz/test_multi_project_membership_authz.py`. Note: `GET
/projects/{project_id}/summary|export|attachments*` are **numeric** `project_id`
(or the aggregate `UNASSIGNED`, which the gate fails closed to 403 for a scoped
token — matching the handler), so those are gate-enforced, not `resolved_inline`.

### N4. `tag_suggestions` single-item mutators + `bulk-reopen` (F2 carry-forward) — `picture_scoped` on a `suggestion_id`

Per the plan carry-forward and the prior CSO sign-off (`docs/reviews/1.7.0rc1-authz-coverage-matrix.md`, 2026-07-18), the 7 mutators (`accept`/`reopen`/`fix-twin`/`swap`/`skip`/`dismiss` + `bulk-reopen`) shipped **without** `enforce_picture_scope`. They are **latent, not live**: all are POSTs not in `READ_SAFE_POST_PATHS`, so a READ (⇒ scoped) token is middleware-blocked; only the owner reaches them today. I declared them `picture_scoped` per the plan mandate. **Step-4 work:** the id on the single-item routes is a `suggestion_id` and on `bulk-reopen` a body list `ids` of **suggestion** ids — the gate must resolve `suggestion → picture_id` before the membership check. `bulk-reopen` is the highest risk of the set (an enumerable id list, no handler-level scope filter at all) and is covered explicitly via `body_ids="ids"`.

**Step-4 resolution:** the 7 routes carry `id_resolver="tag_suggestion"` (a typed,
validator-checked field naming `membership.ID_RESOLVERS["tag_suggestion"]`, which
maps `TagSuggestion.picture_id`). The gate resolves each suggestion id → picture
id, then runs `enforce_picture_scope`; `bulk-reopen` resolves and checks **every**
id behind `body_ids="ids"`, not just the first (a suggestion that does not resolve
fails closed). Latent end-state — these POSTs are middleware-blocked for scoped
tokens today — proven by the `AuthzGate(enforcing=True)` decoy tests in
`tests/test_authz_gate_step4.py`.

### N5. `tagger_runs` — the plan said `PICTURE_SCOPED`, but these carry no picture id

The plan carry-forward text lists "`tagger_runs` endpoints → `PICTURE_SCOPED`", but neither endpoint has a picture id: `POST /tagger-runs` upserts a vault-wide `TaggerRun` (model-eval report), `GET /tagger-runs` lists those rows (`db_models/tagger_run.py` has no `picture_id`). **`PICTURE_SCOPED` is not implementable here.** I declared by actual behaviour — `POST` → `owner_only` (write, middleware-blocked for READ), `GET` → `any_token` — which **matches the prior CSO sign-off** (2026-07-18): the ingest should be owner-gated and `GET /tagger-runs` is a scoped-reachable **info-exposure** (model-eval metadata), not a BOLA leak. See §Existing-exposure F-b below.

### N6. A scalar `body_ids` is supported, and nothing declares one

**Retired.** `POST /api/v1/comfyui/run_t2i` was the only route that declared a
single, optional scalar `body_ids` (`source_picture_id`, checked with
`enforce_picture_scope` only when the body carried one), and #1410 deleted it
along with the rest of the old run routes. **No declaration in `ROUTE_POLICIES`
uses the scalar shape today.**

The gate keeps handling it: `_read_body_ids` takes a single scalar (checked as
one id) and an absent/`None` value (no-op) beside a list, so the next route that
needs the shape is covered the day it declares it rather than silently passing
its body unchecked. That is a deliberate retention, recorded at
`pixlstash/authz/gate.py::_read_body_ids` and `authz/policy.py`'s `body_ids`
docstring, and it is pinned on a decoy route by
`tests/test_authz_gate_step4.py::test_body_ids_single_optional_scalar` — a decoy
precisely because there is no real route left to exercise it against, and an
unexercised branch of the gate is how a shape stops working without anyone
noticing.

---

## Existing exposures this back-fill DOCUMENTS (declared, not fixed here)

Per the task: where declaring current behaviour records an under-protected route, surface it — do not silently fix it in Step 2. These are `any_token` today because a resource-scoped (or unscoped-READ) token can currently reach them; the gate flip does **not** change that until a deliberate tightening is chosen.

- **F-a. `GET /api/v1/users/me/token` (`list_me_tokens`) — NOT a live exposure (corrected by CSO adversarial review).** The original F-a claim ("a share/READ token can enumerate the owner's API tokens") was wrong: `auth.list_tokens` begins with `if token_scope is not None: raise 403` (`auth.py:1178`), and `token_scope` is populated for every non-ALL token, so **all** scoped/READ tokens are already 403'd inline — only the owner (cookie session / unscoped-ALL) reaches it. The real defect was a **mis-declaration**: this route was declared `any_token` when its behaviour is `owner_only`. Fixed by redeclaring `owner_only` (C1); no handler change needed, and **do not** add it to `READ_BLOCKED_GET_PATHS`/`require_unscoped_owner` — that would harden an already-closed hole while leaving the wrong declaration in place. Same fix applied to its sibling `GET /users/me/shared-resource-ids` (C2, `auth.py:1314`).
- **F-b. `GET /api/v1/tagger-runs` — `any_token`.** Scoped-reachable model-eval metadata (no per-object picture data). Prior CSO sign-off already classified this as info-exposure, not BOLA, and recommended `READ_BLOCKED_GET_PATHS` as before-final hardening. Recorded, not fixed here.
- **F-c. Lower-sensitivity `any_token` owner-account reads** reachable by a resource-scoped share token today, each returning owner-account/config info rather than other users' per-object data (single-owner product): `GET /users/me/auth` (owner username + has_password — the top candidate to tighten), `GET /users/me/watermark`, `GET /users/me/penalised-tags` (documented as intentionally READ-accessible), `GET /session/context` (returns the caller's own scope; intentionally accepts `?token=` for share recipients — benign), `GET /workers/progress` (process CPU/RAM/VRAM + worker telemetry), `GET /network/info` (LAN IP). **Correction (CSO):** `GET /users/me/shared-resource-ids` is struck from this list — it 403s scoped tokens (`auth.py:1314`), so it is not READ-reachable (see C2/M2). Declarations reproduce current behaviour and are correct; tightening any of these is a code change for a later hardening step, not a Step-2 matrix blocker.
- **F-d. §16.3 folder lists — `scoped_list` self-empty.** `GET /import-folders` and `GET /reference-folders` return an **empty** list to any scoped token (they short-circuit on `token_scope is not None`) and full host-folder config only to the owner. Declared `scoped_list` (self-filtering) so the gate does not over-deny; no leak.

**Disposition (decided 2026-07-21, founder/CSO-of-record):** F-b and F-c are accepted as pre-existing low-severity info-exposures on a single-owner product and tracked as **before-final hardening**, not Step-2 blockers. The one exception: `GET /users/me/auth` (owner username + `has_password`) is tightened as a rider on **Step 3**'s §16.3 behaviour change. All other F-b/F-c rows stand with the written justification above; the export capability-URL note (unguessable `uuid4`) is deferred to the central-chokepoint design. F-a was withdrawn (not a live leak; fixed as the M1/M2 declaration corrections C1/C2).

## §16.3 host-capability endpoints — Step-2 `owner_only`, Step-3 retarget to `local_owner_only` / `loopback_owner_only`

The filesystem / import-folder / reference-folder / `server/restart` / `pictures/{id}/open-location` capability endpoints are gated today by `require_user_id` + the middleware write/READ-block, i.e. **owner-only in effect** (a remote **cookie** session can still reach them — `require_local_for_write` only pins `ALL` **tokens**, and only at `/login`, not per-request on these handlers). I declared them `owner_only` to preserve exactly that (declaring `local_owner_only` now would make the Step-3 flip newly deny a remote cookie session — a behaviour change out of place in Step 2). The plan's Step-3 §16.3 opportunistic tightening (`require_user_id` → the host-capability tiers) is the deliberate retarget of these specific rows.

**§16.3.1 decided access design (three-lens CSO/Principal/CEO ruling, 2026-07-21).** The 16 rows split into two tiers, matching backend_architecture.md §16.3.1:

- **13 `local_owner_only`** (filesystem / folder authority). Locality uses the scoped predicate `is_local_or_tailscale_ip` = loopback ∪ RFC1918 ∪ **Tailscale CGNAT `100.64.0.0/10`** ∪ Tailscale ULA `fd7a:115c:a1e0::/48`. The shared `is_local_ip` is deliberately **not** widened (it also backs `_require_local_for_write`, the middleware remote-`ALL`-token block, and the HTTPS-skip carve-out — coupling Tailscale into those is an unrelated remote-login decision the debate refused). A genuinely remote owner is admitted only when the dedicated `allow_remote_host_ops` server-config flag (default `false`) is set; the deny is a 403 whose message names that flag. `allow_remote_host_ops` is **not** `require_local_for_write` (remote-login risk ≠ remote-host-ops risk).
- **4 `loopback_owner_only`** (host-shell RED LINE): `POST /server/restart`, `POST /reference-folders/{folder_id}/open`, `POST /pictures/{id}/open-location`, `POST /server-config/open`. All four spawn a host GUI process (`os.startfile`/`open`/`xdg-open`); `server-config/open` was folded in by **CSO Condition 1** (it shipped `owner_only` with no locality check despite the identical `_open_in_os` spawn — corrected arithmetic: host-capability locality total 17 = 13 local + 4 loopback, and `owner_only` 76 → 75). Strict loopback only (`is_loopback_ip` — 127.0.0.0/8 + ::1); **not** RFC1918, **not** Tailscale. `allow_remote_host_ops` never loosens them (the enforcement branch does not consult the flag). `loopback_owner_only` is a new, deliberate member of the closed `AccessPolicy` enum (principal ruling: closed-enum extension, added to `policy.py` + tests).

> **Superseded 2026-07-23 (tier arithmetic).** The loopback tier is now **5**, not 4: the
> conditionally-mounted e2e hook `POST /api/v1/test-hooks/ws-event` joined it (see
> "Conditionally-mounted routes" below). The host-capability locality total is therefore
> **18 = 13 local + 5 loopback**. The four routes enumerated above remain the *host-shell
> GUI-spawn* subset; the hook is on the same tier for a different reason (authority over
> other clients' state), and it is the only member that is not always mounted.
> `docs/backend_architecture.md` §16.3.1 still states "4 routes" / "17 = 13 local + 4
> loopback" and **must be updated to match** — see CSO Condition C1 in the sign-off below.

Declared and armed behind the report-only gate (`AUTHZ_GATE_ENFORCING` stays `False`); no runtime change until the Step-6 flip. Both-direction tests: `tests/test_authz_host_capability_16_3.py`.

---

## Async streaming-staging import (#459, v1.8.0) — independent adversarial sign-off

**Reviewer:** CSO adversarial review (independent of the author). **Branch:** `v1.8.0-foundations` (uncommitted working tree). **Verdict: CERTIFY.** No release blocker found; the two hardening items below are owner-only resource-hygiene, not authz holes.

**Scope:** the 5 new routes in `pixlstash/routes/pictures/_import.py` (4 mutating OWNER_ONLY + 1 ANY_TOKEN status), plus `PictureImportTask` and the unchanged `DELETE /pictures/scrapheap`.

1. **Coverage / gate resolution — COMPLETE.** All 5 routes are declared in `ROUTE_POLICIES`; `test_all_routes_declare_access_policy` passes with `_CURRENT_ROUTE_ALLOWLIST = frozenset()` (0 undeclared). The gate (`AUTHZ_GATE_ENFORCING = True`) keys by route-object identity, so the nested prefixed paths resolve correctly — proven live: `test_staging_files_and_commit_denied_for_read_token` gets **403** on files/commit/delete with a READ token, and `test_staging_open_denied_for_read_token_allowed_for_owner` confirms owner **200** (no over-block). A hypothetical undeclared sub-route would hard-deny (403), not fail open.
2. **OWNER_ONLY vs §16.3 LOCAL_OWNER_ONLY — author's choice UPHELD.** These stream client-provided upload bytes into `image_root/.staging/`; they never read/walk the host FS. Verified no path-escape: every on-disk destination is `os.path.join(staging_dir, f"{uuid4()}{ext}")` where `ext` comes from `os.path.splitext` (cannot contain a separator), and the vault write in `ImageUtils.create_picture_from_bytes` uses `file_name = os.path.basename(uuid4())` — **no client-controlled component reaches any write path.** Zip entries are staged under fresh uuids (`base_name`/`inner_ext` used only for the sidecar stem + extension), so **zip-slip is structurally impossible**. `original_file_name` is a pure DB string, never a path. Decompression-bomb guards (≤50k entries, ≤50 GB decompressed, ≤20 GB/file) mirror the one-shot import.
3. **Input space — no BOLA.** Single-owner model: the mutating routes are gate-enforced OWNER_ONLY, so only the unscoped owner reaches them; `set_id`/`character_id` are validated fail-closed at both open and commit (404 missing / 409 locked-set — `test_open_with_nonexistent_{set,character}_errors`). No cross-tenant surface exists. Status (ANY_TOKEN) returns only counts/stage/task_id (no picture data) and requires the unguessable uuid4 `staging_id`; consistent with the existing `import/status` sibling. Cancel/commit state machine is guarded (`stage != "staging"` → 409), so a committed import cannot be cancelled or double-committed cross-session.
   > **CORRECTION 2026-08-01: this clause was wrong, and the sign-off above is amended accordingly.** `GET /pictures/import/staging/{staging_id}/status` does **not** return "only counts/stage/task_id": its completed payload carries `scrapheaped_picture_ids`, and the `GET /pictures/import/status` sibling it was reconciled against carries `results[].picture_id` and `results[].file` (the vault-relative filename) as well. Both were therefore per-object data behind an `ANY_TOKEN` declaration, so a resource-scoped READ token could read ids and filenames of pictures outside its grant. The unguessable `staging_id` was the only thing standing in the way, which is a capability URL, not an access policy, and it does not apply to `import/status` at all (the `task_id` is equally unguessable but the *scoped listing* of what an import touched is still owner data). **Both routes are now `owner_only`**; the two matrix rows above are corrected, and both directions are pinned in `tests/test_import_scrapheap_match.py` (scoped token 403 on header and `?token=`, owner still 200). The reasoning error to learn from is that the reviewer reconciled the new route against a *sibling declaration* rather than against the *payload*, and the sibling was itself mis-declared.
4. **`DELETE /pictures/scrapheap` — unchanged, correctly `owner_only`** (`require_unscoped_owner`; POST/DELETE blocked for READ tokens). Confirmed still declared and gate-enforced.
5. **Tests assert both directions and are not hollow** — 16/16 pass (`test_async_import_staging.py`): READ-token 403 on open/files/commit/delete AND owner 200/works, plus happy-path, dedupe, zip, sidecar, cancel, and association coverage.

**Hardening that can wait (owner-only; not blockers):**
- **H1 — orphaned staging leak.** A session opened but never committed/cancelled (tab closed mid-stream) leaves files under `.staging/` and a record in the in-memory `server.staging_sessions` dict with no TTL/reaper; completed sessions are also never popped. Owner-triggered disk/memory growth. Add a reaper or bound the dict.
- **H2 — `project_id` not validated on the drop.** `_validate_association_targets` checks `set_id`/`character_id` but not `project_id`; a nonexistent `project_id` is caught only downstream in `PictureImportTask._apply_project` (after pictures are already imported), an inconsistency with the fail-fast 404 the set/character path gives. Data-integrity, not authz. Consider validating `project_id` alongside the others.

## Round-3 delta: `POST /pictures/scrapheap/delete-preview` (v1.8.0) — CSO sign-off

**Reviewer:** CSO adversarial review (independent). **Verdict: CERTIFY-WITH-CONDITIONS** — one missing regression test (C1 below); the enforcement itself is correct and reproduced.

**Location:** `pixlstash/routes/pictures/_crud.py::preview_scrapheap_delete` (NOT `_import.py`). Declared `OWNER_ONLY` in `ROUTE_POLICIES`.

1. **Tier is right (UPHELD).** The response returns per-object absolute on-disk `file_path`s of protected reference-folder originals, so `OWNER_ONLY` is correct — not `ANY_TOKEN`/`PUBLIC`. Reproduced both directions: owner → **200**; READ token → **403** (`{"detail":"Token is read-only"}` — middleware POST-block is the first gate, OWNER_ONLY the second). POST is not in `READ_SAFE_POST_PATHS`, so no scoped token reaches it.
2. **Gate resolves it.** `test_all_routes_declare_access_policy` passes with the route declared and `_CURRENT_ROUTE_ALLOWLIST = frozenset()` (0 undeclared). Route-identity keying resolves the new path; matrix row added above.
3. **Input space — fail-closed, no leak.** `_fetch_scrapheap_rows` unconditionally constrains `Picture.deleted.is_(True)` and only ANDs `Picture.id.in_(ids)` when ids are supplied. Reproduced: a non-scrapheap id (`{"ids":[999999]}`) returns `{total_count:0, protected:[]}` — the endpoint **cannot** be used to enumerate or return `file_path`s for live/non-scrapheap pictures. Single-owner + OWNER_ONLY ⇒ no cross-tenant surface. `ids` parsing rejects empty/non-integer lists (400).
4. **`DELETE /pictures/scrapheap` scope unchanged.** Still `owner_only`; the new `include_protected` body flag only chooses whether protected originals are skipped vs. destroyed — it does not alter enforcement (POST/DELETE blocked for READ tokens; gate OWNER_ONLY). The 3 scrapheap tests pass in file order.

**Condition to clear before merge:**
- **C1 — missing negative-direction regression test.** `test_scrapheap_delete_preview_reports_full_protected_set` asserts only the owner-200 / correctness direction. Per the authz discipline ("tests assert both directions"), add a READ-token → 403 case for `POST /pictures/scrapheap/delete-preview` (the 403 is reproduced here but not pinned by a test, so a future policy regression would go uncaught). Small, mechanical; not a runtime hole.

## Readiness

- **For the CSO adversarial review:** the matrix is arithmetically complete (217 declared = 216 mounted + 1 conditional, allowlist zero, guardrails green) and every `public`/`owner_only` cell carries a rationale. The refute-target list is §N1–N6 (classification ambiguities) and §F-a–F-d (documented existing exposures). Nothing is committed — the review runs against the working tree.
- **For Step 3 (first enforcing step, `OWNER_ONLY`/`LOCAL_OWNER_ONLY`/`PUBLIC`-consistency):** the two behaviour-sensitive spots to clear first are **N2** (unscoped-READ vs. `owner_only` on reviews/tag_health reads) and **N1** (the SPA fallback PUBLIC-consistency). `tests/test_read_token_security.py` (ML-heavy) must be green before Step 3, per the plan.
- **Not in this step:** enforcement, inline-check removal (Step 5), `SCOPED_LIST`/`body_ids` filtering logic (Step 4), and the §16.3 `local_owner_only` retarget (Step 3).

---

## The ComfyUI scope (#1811)

A token with scope `COMFYUI` (the ComfyUI link key) is the owner on the 31 routes below and is refused on every other route that asks for a credential. This is a property of the token, not a new access policy: the route keeps the policy in the matrix above, and `AuthzGate._admit_comfyui_scope` runs before it. On a listed route the gate clears the request's `token_scope`, so the route's own policy then sees the owner (locality and the library pin still apply). On any other route it answers 403. Admission is by the matched route's identity and method.

The list is `auth.COMFYUI_SCOPE_ROUTES`. It is every route the ComfyUI-PixlStash pack calls and nothing else: `tests/test_architecture_guardrails.py::test_the_comfyui_scope_admits_every_route_the_pack_calls` reads the pack's source and fails in both directions, and `test_the_comfyui_scope_section_of_the_matrix_matches_the_code` holds this table to the list. `GET /version` is public and needs no entry.

The middleware checks the same list first, by the path's shape (`auth.comfyui_scope_admits`). That check is looser than the gate's and is the one that survives `AUTHZ_GATE_ENFORCING = False`. A `COMFYUI` token cannot sign in for a session, is refused on WebSockets, is not accepted in the query string, names no resource, and is held to `require_local_for_write` like an `ALL` token.

| Method | Path | Policy | What the pack does with it |
|---|---|---|---|
| GET | `/api/v1/adapters` | owner_only | Adapter picker |
| GET | `/api/v1/adapters/{sha256}` | owner_only | One adapter's record, to name a node saved with a hash |
| GET | `/api/v1/adapters/{sha256}/file` | local_owner_only | Downloads a model ComfyUI does not have; keeps its locality check |
| GET | `/api/v1/characters` | scoped_list | Character picker |
| POST | `/api/v1/characters/{character_id}/faces` | owner_only | The saver files the picture on a character |
| GET | `/api/v1/characters/{id}/reference_pictures` | character_scoped | Face likeness gate reads the references |
| GET | `/api/v1/characters/{id}/{field}` | character_scoped | A character's face on a model (`thumbnail`); the route serves any field of the character, which `GET /characters` already lists |
| GET | `/api/v1/checkpoints` | owner_only | Checkpoint loader |
| POST | `/api/v1/comfyui/workflows/convert` | owner_only | Convert for PixlStash |
| GET | `/api/v1/model-icons/{sha256}` | owner_only | Model icons in the pickers |
| GET | `/api/v1/models/workflow-sets` | owner_only | Workflow-set loader |
| GET | `/api/v1/picture_sets` | scoped_list | Set picker |
| GET | `/api/v1/picture_sets/{id}/members` | set_scoped | Picture likeness gate reads the set |
| POST | `/api/v1/picture_sets/{id}/members` | owner_only | The saver files the picture in a set |
| GET | `/api/v1/picture_sets/{id}/thumbnail` | set_scoped | A set's face on a model in the adapter picker |
| GET | `/api/v1/pictures` | scoped_list | Picture picker and the loader nodes |
| GET | `/api/v1/pictures/count` | scoped_list | The picture picker's total beside Everything |
| GET | `/api/v1/pictures/comfyui_models` | scoped_list | The checkpoint picker's panel matches recorded checkpoint names by basename |
| POST | `/api/v1/pictures/face-search` | scoped_list | Face search |
| POST | `/api/v1/pictures/import` | owner_only | The saver imports a picture |
| GET | `/api/v1/pictures/import/status` | owner_only | The saver waits for the import |
| POST | `/api/v1/pictures/likeness-search` | scoped_list | Likeness search and gate |
| POST | `/api/v1/pictures/score_character_likeness` | owner_only | Face likeness gate |
| GET | `/api/v1/pictures/search` | scoped_list | Semantic search node |
| GET | `/api/v1/pictures/thumbnails/{id}.webp` | picture_scoped | Picker thumbnails |
| GET | `/api/v1/pictures/{id}.{ext}` | picture_scoped | The loader and search nodes fetch the picture |
| GET | `/api/v1/pictures/{id}/metadata` | picture_scoped | The loader and search nodes read the format before the file |
| GET | `/api/v1/projects` | scoped_list | Project picker |
| GET | `/api/v1/sort_mechanisms` | any_token | Sort orders; also the link's round-trip check (`TOKEN_BIND_CHECK`) |
| GET | `/api/v1/workflows/{workflow_id}/graph` | owner_only | Open in ComfyUI |
| PUT | `/api/v1/workflows/{workflow_id}/graph` | owner_only | Save back from ComfyUI; ComfyUI-scoped and bound keys always save as `comfyui`, with their own version retention limit |
| GET | `/api/v1/workflows/{workflow_id}/people` | owner_only | People picker for the opened workflow |

## Conditionally-mounted routes (added 2026-07-23)

Three declared routes are **not mounted in the default configuration**:

| Method | Path | Policy | Mounted when |
|---|---|---|---|
| POST | `/api/v1/test-hooks/ws-event` | `loopback_owner_only` | server-config `enable_test_hooks: true` |
| POST | `/api/v1/test-hooks/reference-folders/{folder_id}/rescan` | `loopback_owner_only` | server-config `enable_test_hooks: true` |
| POST | `/api/v1/test-hooks/file-pictures` | `loopback_owner_only` | server-config `enable_test_hooks: true` |

**Why it is declared even though it is usually absent.** The gate resolves declarations against the routes actually mounted at startup, and an undeclared route is denied at runtime *and* aborts boot. With the flag on and no declaration, `enforce_startup` aborted — which is precisely what took the Playwright e2e backend down (`frontend/e2e/serve_e2e_backend.py` sets the flag), pre-existing at `3803476f`. The gate behaved correctly; the route really was undeclared.

**Why the absence needs a waiver.** The same check also treats a declaration with no mounted route as a *dead declaration* and aborts. A static registry cannot satisfy both flag states, so `CONDITIONALLY_MOUNTED_ROUTES` in `pixlstash/authz/registry.py` waives the dead-declaration complaint for exactly this set. The waiver:

- **only suppresses an absence complaint.** `undeclared` is computed from the mounted set against the registry and never consults it, so it cannot admit an undeclared route.
- **cannot weaken enforcement.** When the route *is* mounted it resolves and enforces exactly like any other.
- **cannot be used to smuggle coverage.** An import-time invariant requires every member to also appear in `ROUTE_POLICIES` (`RuntimeError` at import otherwise), and `test_conditionally_mounted_routes_are_all_declared` asserts it.
- **costs** only that a listed declaration will not be flagged as rot if its route is deleted outright. Keep the set tiny.

**Why `loopback_owner_only` and not `owner_only`.** The hook calls `vault.notify` with a caller-supplied payload, i.e. it synthesises arbitrary grid WebSocket events broadcast to *every connected client*. That is authority over other clients' state, not over the caller's own data, which places it with the host-shell red line rather than with ordinary owner writes. Loopback is free: the router is mounted only by the e2e backend, which binds `127.0.0.1` and is driven by Playwright on the same host (CI runs Playwright directly on the runner, no container), so there is no legitimate remote caller by construction. `loopback` rather than `local_owner_only` specifically so that `allow_remote_host_ops` — a filesystem-operations flag — can never expose a test hook. Net effect: if `enable_test_hooks` were ever switched on in a network-reachable deployment, the hook still cannot be reached remotely; the safety stops depending on the flag being off. The handler's existing inline `require_unscoped_owner` remains as defence in depth.

**The rescan hook** (`/test-hooks/reference-folders/{folder_id}/rescan`, #2325) marks one reference folder due for a rescan, which is what the filesystem watcher does when a file in it changes; the e2e Media specs use it because the watcher needs an inotify instance a busy machine may not have. It returns no resource data. It is `loopback_owner_only` for the same reasons as `ws-event`, and keeps the same inline `require_unscoped_owner`.

**The picture-filing hook** (`/test-hooks/file-pictures`, #2315) sets `Picture.run_workflow_id` on the pictures it names, as a ComfyUI run's import does, or clears it; the e2e Workflows specs use it to give a workflow they made pictures before deleting it, because every product writer of the field is background work the e2e backend leaves off. It returns no resource data. It is `loopback_owner_only` for the same reasons as `ws-event`, and keeps the same inline `require_unscoped_owner`.

Covered both directions by `tests/test_authz_host_capability_16_3.py`: loopback owner reaches the handler (200, event emitted); LAN / Tailscale / public owner is 403 *even with* `allow_remote_host_ops=true`; the route is absent from the mounted table without the flag; and the normal configuration still boots enforcing.

---

## CSO independent adversarial sign-off — `CONDITIONALLY_MOUNTED_ROUTES` + the e2e hook declaration (2026-07-23)

**Reviewer:** CSO adversarial review, independent of the author (author did not certify).
**Branch:** `v1.8.0-foundations`, uncommitted working tree at HEAD `3803476f`.
**Scope:** `pixlstash/authz/registry.py`, `pixlstash/authz/gate.py`,
`tests/test_authz_host_capability_16_3.py`, this matrix. A parallel design lane's
`frontend/**` + `docs/design/**` churn was out of scope.

**Verdict: CERTIFY WITH CONDITIONS.** No release blocker. The escape hatch is genuinely
absence-only and the tier is a strict tightening. Two documentation conditions (C1, C2)
and three hardening items (H1–H3) below.

### The escape hatch — all three author claims survived refutation

Each claim was attacked against the code, not the description, and reproduced.

1. **"Cannot admit an undeclared route."** *Upheld.* `resolve_routes` computes `undeclared`
   from `live` against `self._registry` and never consults the waiver set
   (`gate.py:246`). Reproduced: put an always-mounted route (`GET /api/v1/pictures`) into
   `CONDITIONALLY_MOUNTED_ROUTES` **and** delete its declaration → the route is still
   reported `undeclared`, is **not** absorbed into `dead`, and `enforce_startup` raises
   `RuntimeError: authz gate is ENFORCING but the coverage matrix is incomplete: 1
   undeclared route(s)`. The waiver is subtracted from `dead` only.
2. **"Cannot weaken the policy when the route IS mounted."** *Upheld.* Reproduced against a
   real `Server(enable_test_hooks=True)` booting with the shipped
   `AUTHZ_GATE_ENFORCING = True`: loopback owner **200** (`emitted: 1`); XFF
   `192.168.0.1` / `10.0.0.1` / `100.64.0.5` / `8.8.8.8` all **403** with the
   "restricted to loopback" body (the run used the RFC 1918 vectors this suite
   carried at the time; #965 moved them to the equivalents in
   `tests/network_vectors.py`, same blocks, same branch, same result); `8.8.8.8` with `allow_remote_host_ops=true` still
   **403**. Waiver membership changes nothing about enforcement — the policy map is built
   by iterating `live` and looking up the registry by exact `(method, path)`, so a waived
   declaration for an absent route maps onto no route object and cannot bleed onto a
   sibling.
3. **"Cannot smuggle coverage."** *Upheld.* The import-time invariant in `registry.py`
   raises `RuntimeError` if any waiver member lacks a `ROUTE_POLICIES` entry. Note that
   `test_conditionally_mounted_routes_are_all_declared` is **redundant** for that direction
   (the module import would already have failed); its load-bearing assertion is the
   non-empty check.

**Renamed / mis-pathed declaration.** Attempted and *not* a hole. If the hook's path
changes, the flag-on configuration mounts a key the registry does not have → `undeclared`
→ boot abort (reproduced: removing the declaration while the flag is on aborts boot). The
waiver only silences the complaint in the configuration where the route does not exist,
which is the configuration in which the mis-path is unreachable. Fail-closed where it
matters.

**Conditional inventory is complete.** `pixlstash/server.py` has exactly one conditional
`include_router` (line 1162, `enable_test_hooks`); every other router is unconditional. The
waiver set of size 1 is arithmetically correct, not a sample.

### Tier — `LOOPBACK_OWNER_ONLY` upheld, but one claim over-states

Loopback is genuinely enforced for this route: `_enforce_unscoped_owner` runs **before**
`_enforce_loopback` (`gate.py:467-470`), `_enforce_loopback` never consults
`allow_remote_host_ops`, and unauthenticated loopback is **401**. Verified.

The tier is a strict tightening over what shipped (inline `require_unscoped_owner` only),
so it cannot be refuted as *too weak relative to today*. The residual locality caveat is
**pre-existing and already documented** in `backend_architecture.md` §16.3.1 ("CSO
Condition 2", same-host-proxy assumption) and applies identically to the other four
loopback routes. Reproduced against `get_real_client_ip` + `is_loopback_ip` — four
configurations resolve a remote caller to "loopback": same-host proxy that sets no XFF;
a proxy that passes inbound XFF through unchanged (attacker sends `127.0.0.1` or `::1`);
an unparseable XFF hop (`is_loopback_ip` fails **open** on unparseable input); and
`request.client is None` (UDS), which defaults to `127.0.0.1`. A correctly-configured
appending proxy (`$proxy_add_x_forwarded_for`) blocks all of them.

Container port-mapping is **not** a bypass: Docker bridge / rootless slirp source addresses
sit inside `172.16.0.0/12` (Docker's default bridge) and `10.0.0.0/8`
(rootless slirp's NAT), not loopback. SSH local port-forwarding *is* (the hop
originates on the host), but that presupposes shell access.

**Over-blocking checked — no regression.** The e2e topology is loopback end to end:
`playwright.config.js` `BASE_URL = http://127.0.0.1:9600`, `serve_e2e_backend.py` binds
`host: 127.0.0.1` and sets no `trusted_proxies`, CI runs Playwright directly on the runner
with no container, and the specs call the hook through `apiContext` on the same host.
Verified live: flag-on server admits the loopback owner with **200**.

### Arithmetic — verified independently

Built the default app and counted: **216 mounted**, **217 declared**, waiver set **1**,
`undeclared = []`, `dead` before waiver `= [POST /api/v1/test-hooks/ws-event]`, `dead`
after waiver `= []`. `217 == 216 + 1` holds. Policy distribution matches this document
exactly (`owner_only` 83, `loopback_owner_only` 5, …). `_CURRENT_ROUTE_ALLOWLIST` is still
`frozenset()`. Suites: `test_authz_host_capability_16_3.py` 20 passed,
`test_architecture_guardrails.py` 17 passed, all authz-related suites 68 passed. `ruff
check` + `ruff format --check` clean.

**Guardrail caveat:** `test_all_routes_declare_access_policy` checks `live - declared` and
`allowlist - live - declared`; it does **not** check `declared - live`. The dead-declaration
arithmetic is enforced solely by `enforce_startup`. The waiver therefore did not need a
guardrail change — but the guardrail also never protected that direction.

### Conditions (documentation; must land with the change)

- **C1 — `docs/backend_architecture.md` §16.3.1 is now stale and contradicts the registry.**
  It states "`LOOPBACK_OWNER_ONLY` (4 routes)", enumerates the four GUI-spawn routes, and
  gives "17 = 13 local + 4 loopback". The registry has **5** and the total is **18**.
  CLAUDE.md names §16.3 as the authority for these tiers, so the authoritative doc must be
  corrected. (This matrix has been annotated in-place; the architecture doc has not — it is
  outside this reviewer's edit mandate.)
- **C2 — `CONDITIONALLY_MOUNTED_ROUTES` is a structural change to the gate and is absent
  from `docs/backend_architecture.md` §16.2**, which documents the shipped gate design. A
  new bypass surface in a deny-by-default chokepoint must be described where an
  implementer will read it, not only in a review artifact. Add the absence-only semantics
  and the "keep the set tiny" rule to §16.2.

### Hardening (not blockers)

- **H1 — waiver rot has no expiry.** The author correctly flagged that deleting
  `test_hooks.py` leaves the declaration silently un-flagged. Accepted as low risk: a stale
  declaration grants nothing (it maps onto no route object) and the set is size 1. Cheapest
  durable mitigation is not a periodic re-justification ritual but an assertion that each
  waiver member's owning module still imports — e.g. a test that
  `pixlstash.routes.test_hooks` is importable and exposes `create_router`. Consider it if
  the set ever exceeds one entry; a set of one is self-policing by inspection.
- **H2 — `is_loopback_ip` fails OPEN on unparseable input** (`auth.py:220-223`, returns
  `True` for `"testclient"`). For the red-line tier specifically this is the wrong default:
  an attacker-controlled XFF hop of `"garbage"` reads as loopback wherever the proxy does
  not overwrite XFF. Pre-existing and shared with the other four loopback routes, so out of
  scope for this diff, but the test-sentinel accommodation should be narrowed to the
  literal `"testclient"` rather than "anything unparseable".
- **H3 — waiver set is not injectable.** `registry` is a constructor parameter but
  `CONDITIONALLY_MOUNTED_ROUTES` is read as a module global inside `resolve_routes`, so a
  gate constructed with a test registry still gets the production waiver subtracted. Not
  exploitable (subtraction can only hide an absence), but the asymmetry invites a future
  test to assert against a waiver it did not configure. Make it a constructor parameter
  defaulting to the module constant.

### Test quality

Both directions are asserted and neither test is hollow. The negative test matches on the
**"restricted to loopback"** body, so it proves the loopback branch fired rather than
accepting any 403; the positive test asserts `200` **and** `emitted == 1`, proving the
handler body ran rather than merely passing the gate. One gap: the tests cover the locality
dimension only — no case asserts that a READ / resource-scoped token from **loopback** is
rejected. That direction is structurally covered (`_enforce_unscoped_owner` runs first, and
the handler retains its inline `require_unscoped_owner`), and unauthenticated loopback was
verified **401**, so this is a completeness nit rather than a gap in enforcement.

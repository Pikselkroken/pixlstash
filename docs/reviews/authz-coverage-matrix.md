# Authz Coverage Matrix — Backend Refactor Phase 1 Step 2 (registry back-fill)

- **Branch:** `backend-refactoring`
- **Scope:** Phase 1 **Step 2 only** of the centralised-authz refactor (backend refactor plan §3.5). This PR **declares** the access policy of every mounted HTTP route in `pixlstash/authz/registry.py::ROUTE_POLICIES`. It does **not** enforce anything (`AUTHZ_GATE_ENFORCING = False`), remove any inline check, or change any handler. It is the regenerated coverage matrix the adversarial security review consumes.
- **Arithmetic completeness:** **207 / 207** mounted HTTP routes declared. Gate `enforce_startup` (both report-only and, as a dry check, `enforcing=True`) resolves the app with **0 undeclared, 0 dead declarations, 0 authoring problems** (every `PUBLIC`/`LOCAL_OWNER_ONLY` has a justification; every `*_SCOPED` `id_param` is a real template param). The audit allowlist in `tests/test_architecture_guardrails.py` has burned to **zero** (`_CURRENT_ROUTE_ALLOWLIST = frozenset()`); the registry is now the sole coverage matrix. Guardrail suite: **17 passed**.
- **WebSockets:** the 2 WS routes (`/ws/comfyui`, `/api/v1/ws/updates`) are **out of the HTTP registry by design** — their chokepoint is `authenticate_websocket` (plan §6). They remain acknowledged in `tests/test_architecture_guardrails.py::test_websocket_routes_are_acknowledged`, and `registry.py` carries the `# WS routes: see authn/websocket.py` sentinel.

## How each policy was derived (preserve-today's-behaviour rule)

Each route is mapped to the single `AccessPolicy` that reproduces its behaviour **today**, from the auth middleware gating (`AUTH_EXCLUDED_*`, `READ_BLOCKED_GET_PATHS`, `READ_SAFE_POST_PATHS`, the non-GET block for READ tokens, `require_local_for_write`, the `ALL`+`resource_type` fail-closed rejection) **plus** the inline object checks (`enforce_picture_scope`, `fetch_scope_allowed_*`, `require_unscoped_owner`, the `_require_scope_allows_{picture_set,character,project}` ladders). Load-bearing facts, verified against the code:

- Every resource-scoped share token is a **READ** token (`ALL`+`resource_type` is refused at mint and fail-closed at the middleware). So a mutating route **not** in `READ_SAFE_POST_PATHS` is reachable **only by an unscoped owner** today — `OWNER_ONLY` is a no-op there and cannot over-deny.
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

## Policy distribution (207 total)

| Policy | Count |
|---|---|
| `public` | 13 |
| `any_token` | 15 |
| `owner_only` | 75 |
| `picture_scoped` | 33 |
| `scoped_list` | 39 |
| `set_scoped` | 4 |
| `character_scoped` | 5 |
| `project_scoped` | 6 |
| `local_owner_only` | 13 |
| `loopback_owner_only` | 4 |

> **Updated for Step 3 (2026-07-21).** The §16.3 host-capability retarget moved 16
> rows `owner_only` → the host-capability tiers, and the F-c rider tightened
> `GET /users/me/auth` `any_token` → `owner_only`. Step-2 baseline was `owner_only`
> 91 / `any_token` 16 / `local_owner_only` 0.
>
> **Updated for the §16.3.1 access design (2026-07-21, three-lens ruling + CSO
> Condition 1).** Host-capability routes carrying a locality tier now total **17**:
> **13 `local_owner_only`** (filesystem/folder authority; locality widened to
> include Tailscale CGNAT `100.64.0.0/10`; a remote owner admitted only when the
> dedicated `allow_remote_host_ops` flag is set, whose name the deny message
> surfaces) + **4 `loopback_owner_only`** (host-shell red line — `POST
> /server/restart`, `POST /reference-folders/{folder_id}/open`, `POST
> /pictures/{id}/open-location`, and — added by CSO Condition 1 — `POST
> /server-config/open`, all strict loopback only; the flag can never loosen them).
> Corrected arithmetic: the original §16.3 set was 16 (= 13 + 3); `server-config/open`
> was previously `owner_only` with no locality check (a byte-identical host-GUI-spawn
> sibling that slipped the tier), so folding it in makes the host-capability locality
> total **17 = 13 local + 4 loopback**, and drops `owner_only` 76 → 75.
> `loopback_owner_only` is a new, deliberate member of the closed `AccessPolicy`
> enum. See backend_architecture.md §16.3.1.

---

## The matrix (one row per route)

Rationale column is empty where it equals the policy-meaning table above (e.g. `picture_scoped` ⇒ `enforce_picture_scope`, `scoped_list` ⇒ `fetch_scope_allowed_*` filter). It is filled where the route is `public`/`owner_only` (justification mandatory for public) or where the derivation is non-obvious.


### 0. app-level (server.py) + auth

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/` | public |  | Frontend SPA index; auth-excluded; no owner data |
| GET | `/api/v1/check-session` | public |  | Session status probe; auth-excluded (/check-session) |
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
| GET | `/api/v1/server-config/snapshots` | owner_only |  | require_unscoped_owner |
| PATCH | `/api/v1/server-config/snapshots` | owner_only |  | require_unscoped_owner |
| GET | `/api/v1/server-config/watch-folders` | owner_only |  | require_unscoped_owner; also READ_BLOCKED; owner only |
| GET | `/api/v1/session/context` | any_token |  |  |
| GET | `/api/v1/users/me/auth` | owner_only |  | Owner account state (username + has_password). **Step-3 F-c hardening rider (decided 2026-07-21):** tightened `any_token` → `owner_only` so a share token cannot read owner identity. Gate now rejects scoped/unscoped-READ tokens. |
| POST | `/api/v1/users/me/auth` | owner_only |  | Change owner password; POST blocked for READ tokens; owner only |
| GET | `/api/v1/users/me/config` | owner_only |  | Owner config; READ_BLOCKED_GET_PATHS blocks READ tokens; only owner reaches |
| PATCH | `/api/v1/users/me/config` | owner_only |  | require_unscoped_owner; owner config write |
| GET | `/api/v1/users/me/penalised-tags` | any_token |  |  |
| POST | `/api/v1/users/me/shared-picture-ids/batch` | owner_only |  | POST not in READ_SAFE; READ tokens blocked; owner only |
| GET | `/api/v1/users/me/shared-resource-ids` | owner_only | auth.py:1314 | get_shared_resource_ids rejects token_scope is not None; scoped/READ 403'd; owner only |
| GET | `/api/v1/users/me/token` | owner_only | auth.py:1178 | list_tokens rejects token_scope is not None; scoped/READ 403'd; owner only |
| POST | `/api/v1/users/me/token` | owner_only |  | Mint API token; POST blocked for READ tokens; owner only |
| PATCH | `/api/v1/users/me/token/{token_id}` | owner_only |  | Update API token; PATCH blocked for READ tokens; owner only |
| DELETE | `/api/v1/users/me/token/{token_id}` | owner_only |  | Revoke API token; DELETE blocked for READ tokens; owner only |
| DELETE | `/api/v1/users/me/tokens/by-resource` | owner_only |  | Revoke tokens for a resource; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/users/me/watermark` | any_token |  |  |
| POST | `/api/v1/users/me/watermark` | owner_only |  | Upload watermark; POST blocked for READ tokens; owner only |
| DELETE | `/api/v1/users/me/watermark` | owner_only |  | Delete watermark; DELETE blocked for READ tokens; owner only |
| GET | `/api/v1/workers/progress` | any_token |  |  |

### filesystem.py (§16.3)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/filesystem/browse` | local_owner_only |  | §16.3 host FS browse; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/filesystem/folders` | local_owner_only |  | §16.3 host FS mkdir; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |

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
| POST | `/api/v1/pictures/character_likeness/batch` | scoped_list |  |  |
| GET | `/api/v1/pictures/comfyui_loras` | scoped_list |  |  |
| GET | `/api/v1/pictures/comfyui_models` | scoped_list |  |  |
| GET | `/api/v1/pictures/count` | scoped_list |  |  |
| POST | `/api/v1/pictures/detect` | scoped_list |  |  |
| GET | `/api/v1/pictures/export` | scoped_list |  |  |
| GET | `/api/v1/pictures/export/download/{task_id}` | any_token |  |  |
| GET | `/api/v1/pictures/export/status` | any_token |  |  |
| POST | `/api/v1/pictures/face-search` | scoped_list |  |  |
| POST | `/api/v1/pictures/import` | owner_only |  | Import pictures; POST blocked for READ tokens; owner only |
| GET | `/api/v1/pictures/import/status` | any_token |  |  |
| POST | `/api/v1/pictures/import/staging` | owner_only |  | (#459, v1.8.0) Open async streaming-staging session; upload path, streams client bytes into vault — NOT a §16.3 host-FS read; mirrors `POST /pictures/import`; POST blocked for READ tokens; gate-enforced owner_only |
| POST | `/api/v1/pictures/import/staging/{staging_id}/files` | owner_only |  | (#459, v1.8.0) Stream upload bytes into a staging session; owner only |
| POST | `/api/v1/pictures/import/staging/{staging_id}/commit` | owner_only |  | (#459, v1.8.0) Hand staging off to the background `PictureImportTask`; owner only |
| DELETE | `/api/v1/pictures/import/staging/{staging_id}` | owner_only |  | (#459, v1.8.0) Cancel an uncommitted staging session, discard streamed files; owner only |
| GET | `/api/v1/pictures/import/staging/{staging_id}/status` | any_token |  | (#459, v1.8.0) Progress/stage/counts only (no per-object data); mirrors `GET /pictures/import/status`; staging_id is an unguessable server-minted uuid4 |
| POST | `/api/v1/pictures/impossible-tags/clear` | scoped_list |  |  |
| POST | `/api/v1/pictures/impossible-tags/restore` | scoped_list |  |  |
| GET | `/api/v1/pictures/likeness-groups` | scoped_list |  |  |
| POST | `/api/v1/pictures/likeness-search` | scoped_list |  |  |
| GET | `/api/v1/pictures/plugins` | any_token |  |  |
| POST | `/api/v1/pictures/plugins/{name}` | scoped_list |  |  |
| PATCH | `/api/v1/pictures/project` | scoped_list |  |  |
| POST | `/api/v1/pictures/score_character_likeness` | owner_only |  | Owner scoring op; POST not in READ_SAFE; owner only |
| DELETE | `/api/v1/pictures/scrapheap` | owner_only |  | require_unscoped_owner |
| POST | `/api/v1/pictures/scrapheap/delete-preview` | owner_only |  | (v1.8.0) Authoritative delete-forever preview; returns protected reference-original absolute file paths (per-object data) → owner_only, not any_token |
| POST | `/api/v1/pictures/scrapheap/delete-preview` | owner_only |  | (v1.8.0) Returns absolute on-disk paths of protected reference-folder originals; per-object data → owner_only (POST not in READ_SAFE; gate-enforced). Rows constrained to `Picture.deleted.is_(True)` — cannot leak paths of live/non-scrapheap ids |
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
| GET | `/api/v1/pictures/{id}/metadata` | picture_scoped | id=id |  |
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
| GET | `/api/v1/stacks/{stack_id}` | scoped_list |  |  |
| POST | `/api/v1/stacks/{stack_id}/members` | owner_only |  | Add stack members; POST blocked for READ tokens; owner only |
| DELETE | `/api/v1/stacks/{stack_id}/members` | owner_only |  | Remove stack members; DELETE blocked for READ tokens; owner only |
| PATCH | `/api/v1/stacks/{stack_id}/members/{picture_id}` | owner_only |  | Set member position; PATCH blocked for READ tokens; owner only |
| PATCH | `/api/v1/stacks/{stack_id}/order` | owner_only |  | Reorder stack; PATCH blocked for READ tokens; owner only |
| GET | `/api/v1/stacks/{stack_id}/pictures` | scoped_list |  |  |

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
| GET | `/api/v1/comfyui/pictures/{picture_id}/workflow` | picture_scoped | id=picture_id |  |
| POST | `/api/v1/comfyui/run_i2i` | picture_scoped | body=picture_ids |  |
| POST | `/api/v1/comfyui/run_t2i` | picture_scoped | body=source_picture_id |  |
| GET | `/api/v1/comfyui/workflows` | any_token |  |  |
| POST | `/api/v1/comfyui/workflows/import` | owner_only |  | Import workflow; POST blocked for READ tokens; owner only |
| DELETE | `/api/v1/comfyui/workflows/{workflow_name}` | owner_only |  | Delete workflow; DELETE blocked for READ tokens; owner only |

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
| GET | `/api/v1/taggers` | any_token |  |  |
| DELETE | `/api/v1/taggers/{name}/artifacts/{artifact_id}` | owner_only |  | Delete tagger artifact; DELETE blocked for READ tokens; owner only |
| POST | `/api/v1/taggers/{name}/download` | owner_only |  | Download tagger plugin; POST blocked for READ tokens; owner only |

### other

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| GET | `/api/v1/sort_mechanisms` | any_token |  |  |

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
removed in Step 5 until a shared resolver exists. Note: `GET
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

### N6. `run_t2i` body id is single + optional

`POST /api/v1/comfyui/run_t2i` calls `enforce_picture_scope(source_picture_id)` only when a `source_picture_id` is present in the body (t2i may have none). I recorded `body_ids="source_picture_id"` (a single, optional field, not a list) so the declaration validates and Step 4 knows where to look. The gate's batch `body_ids` resolution must tolerate a single/absent value here.

**Step-4 resolution:** the gate's `_read_body_ids` handles a single scalar (checked
as one id) and an absent/`None` value (no-op), in addition to a list — so `run_t2i`
with no `source_picture_id` passes and with an out-of-scope one is 403'd. Covered
by `test_body_ids_single_optional_scalar_run_t2i`.

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

Declared and armed behind the report-only gate (`AUTHZ_GATE_ENFORCING` stays `False`); no runtime change until the Step-6 flip. Both-direction tests: `tests/test_authz_host_capability_16_3.py`.

---

## Async streaming-staging import (#459, v1.8.0) — independent adversarial sign-off

**Reviewer:** CSO adversarial review (independent of the author). **Branch:** `v1.8.0-foundations` (uncommitted working tree). **Verdict: CERTIFY.** No release blocker found; the two hardening items below are owner-only resource-hygiene, not authz holes.

**Scope:** the 5 new routes in `pixlstash/routes/pictures/_import.py` (4 mutating OWNER_ONLY + 1 ANY_TOKEN status), plus `PictureImportTask` and the unchanged `DELETE /pictures/scrapheap`.

1. **Coverage / gate resolution — COMPLETE.** All 5 routes are declared in `ROUTE_POLICIES`; `test_all_routes_declare_access_policy` passes with `_CURRENT_ROUTE_ALLOWLIST = frozenset()` (0 undeclared). The gate (`AUTHZ_GATE_ENFORCING = True`) keys by route-object identity, so the nested prefixed paths resolve correctly — proven live: `test_staging_files_and_commit_denied_for_read_token` gets **403** on files/commit/delete with a READ token, and `test_staging_open_denied_for_read_token_allowed_for_owner` confirms owner **200** (no over-block). A hypothetical undeclared sub-route would hard-deny (403), not fail open.
2. **OWNER_ONLY vs §16.3 LOCAL_OWNER_ONLY — author's choice UPHELD.** These stream client-provided upload bytes into `image_root/.staging/`; they never read/walk the host FS. Verified no path-escape: every on-disk destination is `os.path.join(staging_dir, f"{uuid4()}{ext}")` where `ext` comes from `os.path.splitext` (cannot contain a separator), and the vault write in `ImageUtils.create_picture_from_bytes` uses `file_name = os.path.basename(uuid4())` — **no client-controlled component reaches any write path.** Zip entries are staged under fresh uuids (`base_name`/`inner_ext` used only for the sidecar stem + extension), so **zip-slip is structurally impossible**. `original_file_name` is a pure DB string, never a path. Decompression-bomb guards (≤50k entries, ≤50 GB decompressed, ≤20 GB/file) mirror the one-shot import.
3. **Input space — no BOLA.** Single-owner model: the mutating routes are gate-enforced OWNER_ONLY, so only the unscoped owner reaches them; `set_id`/`character_id` are validated fail-closed at both open and commit (404 missing / 409 locked-set — `test_open_with_nonexistent_{set,character}_errors`). No cross-tenant surface exists. Status (ANY_TOKEN) returns only counts/stage/task_id (no picture data) and requires the unguessable uuid4 `staging_id`; consistent with the existing `import/status` sibling. Cancel/commit state machine is guarded (`stage != "staging"` → 409), so a committed import cannot be cancelled or double-committed cross-session.
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

- **For the CSO adversarial review:** the matrix is arithmetically complete (207/207, allowlist zero, guardrails green) and every `public`/`owner_only` cell carries a rationale. The refute-target list is §N1–N6 (classification ambiguities) and §F-a–F-d (documented existing exposures). Nothing is committed — the review runs against the working tree.
- **For Step 3 (first enforcing step, `OWNER_ONLY`/`LOCAL_OWNER_ONLY`/`PUBLIC`-consistency):** the two behaviour-sensitive spots to clear first are **N2** (unscoped-READ vs. `owner_only` on reviews/tag_health reads) and **N1** (the SPA fallback PUBLIC-consistency). `tests/test_read_token_security.py` (ML-heavy) must be green before Step 3, per the plan.
- **Not in this step:** enforcement, inline-check removal (Step 5), `SCOPED_LIST`/`body_ids` filtering logic (Step 4), and the §16.3 `local_owner_only` retarget (Step 3).

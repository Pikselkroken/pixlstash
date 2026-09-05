# Authz Coverage Matrix — Backend Refactor Phase 1 Step 2 (registry back-fill)

- **Branch:** `backend-refactoring`
- **Scope:** Phase 1 **Step 2 only** of the centralised-authz refactor (backend refactor plan §3.5). This PR **declares** the access policy of every mounted HTTP route in `pixlstash/authz/registry.py::ROUTE_POLICIES`. It does **not** enforce anything (`AUTHZ_GATE_ENFORCING = False`), remove any inline check, or change any handler. It is the regenerated coverage matrix the adversarial security review consumes.
- **Arithmetic completeness (re-derived 2026-08-31, v1.11 Phase 4c library migration, CURRENT):** **310 declared**. Counted from `ROUTE_POLICIES`, not carried forward from prose: `len(ROUTE_POLICIES) == 310`. By policy class: `owner_only` 137, `local_owner_only` **47**, `scoped_list` 39, `picture_scoped` 39, `public` 13, `any_token` 12, `loopback_owner_only` 7, `character_scoped` 6, `project_scoped` 6, `set_scoped` 4. This change adds exactly **+2 `local_owner_only`** — `GET` and `POST /api/v1/server-config/layout/migration` — and retargets nothing. The `+1` in the line below (`DELETE /api/v1/folder-structure/commit`) is other people's and was already rowed; it did, however, land without moving `tests/test_authz_host_capability_16_3.py`'s locality counter, which was therefore red on `develop` and is corrected in this change rather than quietly folded in.
- **Arithmetic completeness (re-derived 2026-08-26, resumable folder-mapping commit, CURRENT):** **308 declared**. Counted from `ROUTE_POLICIES`, not carried forward from prose: `len(ROUTE_POLICIES) == 308`. By policy class: `owner_only` 137, `local_owner_only` **45**, `scoped_list` 39, `picture_scoped` 39, `public` 13, `any_token` 12, `loopback_owner_only` 7, `character_scoped` 6, `project_scoped` 6, `set_scoped` 4. This change adds exactly **+1 `local_owner_only`** — `DELETE /api/v1/folder-structure/commit` — and retargets nothing. It is the commit's counterpart to the read's existing `DELETE`, and takes the same tier for the same reason: stopping somebody's in-flight host-path operation is authority over that operation, so it cannot be a lower bar than starting it.
- **Arithmetic completeness (re-derived 2026-08-24, merging export-to-folder #291 with v1.11 Phase 5 move reconciliation, CURRENT):** **307 declared**. Counted from `ROUTE_POLICIES`, not carried forward from prose: `len(ROUTE_POLICIES) == 307`. By policy class: `owner_only` **137**, `local_owner_only` **44**, `scoped_list` 39, `picture_scoped` **39**, `public` 13, `any_token` 12, `loopback_owner_only` **7**, `character_scoped` 6, `project_scoped` 6, `set_scoped` 4. Both branches diverged from the same 303-route Phase 4b baseline (line below) and neither retargets the other's declarations: #291 adds exactly **+1 `loopback_owner_only`** — `POST /api/v1/pictures/export/folder`, which writes the exported pictures straight onto the host disk and then opens the destination in the host file manager, the same host-GUI spawn as the tier's other file-manager routes (`pictures/{id}/open-location`, `reference-folders/{folder_id}/open`, `models/{model_id}/open-location`, `server-config/open`) — and Phase 5 adds exactly **+3 `owner_only`** — `GET /api/v1/moves/pending`, `POST /api/v1/moves/apply`, `POST /api/v1/moves/dismiss`.
- **Arithmetic completeness (re-derived 2026-08-24 on top of `pw/phase-3`, v1.11 Phase 4b move engine, superseded by the line above):** **303 declared**. Counted from `ROUTE_POLICIES`, not carried forward from prose: `len(ROUTE_POLICIES) == 303`. By policy class: `owner_only` **134**, `local_owner_only` **44**, `scoped_list` 39, `picture_scoped` **39**, `public` 13, `any_token` 12, `loopback_owner_only` 6, `character_scoped` 6, `project_scoped` 6, `set_scoped` 4. This change adds exactly **+2 `local_owner_only`** (`GET` and `PATCH /api/v1/server-config/layout`) and **+2 `picture_scoped`** (`GET /api/v1/pictures/{id}/layout`, `POST /api/v1/pictures/layout/move-to-match`), and retargets nothing. **Re-derived rather than added to, three times now**, which is the point of counting: this branch has been rebased onto `pw/phase-3` and previously merged `develop` twice, and each time another phase had landed routes in between — Phase 6's `GET /api/v1/insights`, then Phase 3's two folder-structure commit routes. A figure carried forward from any of those derivations would have been short, and would have blamed this branch for somebody else's additions. **The count is restated rather than patched**, because that is what this paragraph is for: it is not machine-checked, while the rows are. `tests/test_architecture_guardrails.py::test_coverage_matrix_document_matches_the_registry` fails the build in both directions on a missing or orphaned **row**, so coverage itself cannot drift; only this summary can, and a stale summary has never been the mechanism behind a BOLA.
- **Arithmetic completeness (re-derived 2026-08-24 after merging `develop`, v1.11 Phase 3 folder-structure commit + Phase 6 insights, superseded by the line above):** **299 declared**. Counted from `ROUTE_POLICIES`: `len(ROUTE_POLICIES) == 299`. By policy class: `owner_only` **134**, `scoped_list` 39, `local_owner_only` **42**, `picture_scoped` 37, `public` 13, `any_token` 12, `loopback_owner_only` 6, `character_scoped` 6, `project_scoped` 6, `set_scoped` 4. This PR itself adds exactly **+2 `local_owner_only`** — `POST` and `GET .../status` on `/api/v1/folder-structure/commit` (298 total at the time). The other **+1 `owner_only`** is other people's, already rowed: Phase 6's `GET /api/v1/insights`, merged into `develop` and picked up by this merge, which did not itself re-derive this summary paragraph — the row-level check below is what catches that, not this line.
- **Arithmetic completeness (re-derived 2026-08-23 after merging `develop`, v1.11 Phase 2 folder-structure read):** **296 declared**. Counted from `ROUTE_POLICIES`, not carried forward from prose: `len(ROUTE_POLICIES) == 296`. By policy class: `owner_only` 133, `scoped_list` 39, `local_owner_only` **40**, `picture_scoped` 37, `public` 13, `any_token` 12, `loopback_owner_only` 6, `character_scoped` 6, `project_scoped` 6, `set_scoped` 4. This change adds exactly **+3 `local_owner_only`** — `POST`, `GET .../status` and `DELETE /api/v1/folder-structure/read` — and retargets nothing. The other movement since the 249 line below is other people's: v1.11 Phase 1's four library-lifecycle routes and Phase 7's two `/server-config/views` routes, both already rowed. **The count was 294/38 before `develop` was merged in and is restated rather than patched**, because that is what this paragraph is for: it is not machine-checked, while the rows are. `tests/test_architecture_guardrails.py::test_coverage_matrix_document_matches_the_registry` fails the build in both directions on a missing or orphaned **row**, so coverage itself cannot drift; only this summary can, and a stale summary has never been the mechanism behind a BOLA.
- **Arithmetic completeness (re-derived 2026-08-05, #721 projected face routes, CURRENT):** **249 declared**, covering the **248** routes mounted in the default configuration plus **1 conditionally-mounted** route (`POST /api/v1/test-hooks/ws-event`). Measured, not carried forward from prose: `len(ROUTE_POLICIES) == 249`, `len(api_endpoint_set(app)) == 248`, `live - declared == ∅`, `declared - live == {POST /api/v1/test-hooks/ws-event}`. By policy class: `owner_only` **113**, `scoped_list` 39, `picture_scoped` **36**, `any_token` 14, `public` 13, `local_owner_only` 13, `character_scoped` **6**, `project_scoped` 6, `loopback_owner_only` 5, `set_scoped` 4. #721 itself adds exactly **+1 `picture_scoped`** (GET `/pictures/{id}/faces`) and **+1 `character_scoped`** (GET `/characters/{id}/faces`). The third route in the delta over the 246 below is **not from #721**: `POST /api/v1/dedup/verdicts/batch` (`owner_only`, so 112 -> 113) was declared on 2026-08-04 by `cb656898` "Make bulk dedup verdicts atomic", which did not re-derive this count. The 246 line was correct when written on 2026-08-02 and went stale two days later. That is exactly the drift the row-level check below now prevents for rows, though not yet for these aggregates. Every declared route now also has a table row.
- **Arithmetic completeness (re-derived 2026-08-02, telemetry install ID, v1.9 Lane F; superseded by the line above):** **246 declared**, covering the **245** routes mounted in the default configuration plus **1 conditionally-mounted** route (`POST /api/v1/test-hooks/ws-event`). Counted from `ROUTE_POLICIES`, not carried forward from prose: `len(ROUTE_POLICIES) == 246`. By policy class: `owner_only` **112**, `scoped_list` 39, `picture_scoped` 35, `any_token` **14**, `public` 13, `local_owner_only` 13, `project_scoped` 6, `loopback_owner_only` 5, `character_scoped` 5, `set_scoped` 4. The delta is **+2 `owner_only`**, both on `telemetry.py`: GET `/telemetry/install-id` and POST `/telemetry/install-id/recreate`. No other policy class moved.
- **Arithmetic completeness (re-derived 2026-08-01, import-status retarget):** **244 declared**, covering the **243** routes mounted in the default configuration plus **1 conditionally-mounted** route (`POST /api/v1/test-hooks/ws-event`). Counted from `ROUTE_POLICIES`, not carried forward from prose: `len(ROUTE_POLICIES) == 244`. By policy class: `owner_only` **110**, `scoped_list` 39, `picture_scoped` 35, `any_token` **14**, `public` 13, `local_owner_only` 13, `project_scoped` 6, `loopback_owner_only` 5, `character_scoped` 5, `set_scoped` 4. The delta is **no new routes** and **2 retargeted declarations**, `any_token` → `owner_only`: GET `/api/v1/pictures/import/status` and GET `/api/v1/pictures/import/staging/{staging_id}/status`. Unlike every delta above this one, it is a **correction, not an addition**: both routes return per-object picture data (ids and vault filenames) and `ANY_TOKEN`'s contract is that a route returns none, so the two cells were wrong from the day they were written. See the corrected rows below and the amended v1.8.0 sign-off. No other policy class moved.
- **Arithmetic completeness (previous re-derivation, 2026-08-01, Keep cover only, superseded by the line above):** **244 declared**, covering the **243** routes mounted in the default configuration plus **1 conditionally-mounted** route (`POST /api/v1/test-hooks/ws-event`). Counted from `ROUTE_POLICIES` and the live route inventory, not carried forward from prose: `len(ROUTE_POLICIES) == 244`, `len(api_endpoint_set(app)) == 243`, `live - declared == ∅`, `declared - live == {POST /api/v1/test-hooks/ws-event}`. By policy class: `owner_only` **108**, `scoped_list` 39, `picture_scoped` 35, `any_token` 16, `public` 13, `local_owner_only` 13, `project_scoped` 6, `loopback_owner_only` 5, `character_scoped` 5, `set_scoped` 4. The delta since the previous re-derivation is **+2 `owner_only`**, both on `stacks.py`, both for Keep cover only (`docs/design/keep-cover-only.md`): POST `/stacks/keep-cover-only/preview` and POST `/stacks/keep-cover-only`. No other policy class moved and no existing declaration was retargeted.
- **Arithmetic completeness (previous re-derivation, 2026-08-01, Mixed stacks D5/B5 merged, superseded by the line above):** **242 declared**, covering the **241** routes mounted in the default configuration plus **1 conditionally-mounted** route (`POST /api/v1/test-hooks/ws-event`). Counted from `ROUTE_POLICIES` and the live route inventory, not carried forward from prose: `len(ROUTE_POLICIES) == 242`, `len(api_endpoint_set(app)) == 241`, `live - declared == ∅`, `declared - live == {POST /api/v1/test-hooks/ws-event}`. By policy class: `owner_only` **106**, `scoped_list` 39, `picture_scoped` 35, `any_token` 16, `public` 13, `local_owner_only` 13, `project_scoped` 6, `loopback_owner_only` 5, `character_scoped` 5, `set_scoped` 4. The delta since the previous re-derivation is **+5 `owner_only`**, all on `dedup.py`, all for Mixed stacks: GET `/dedup/mixed-stacks`, POST `/dedup/mixed-stacks/{stack_id}/split`, POST `/dedup/mixed-stacks/{stack_id}/unstack`, POST and DELETE `/dedup/mixed-stacks/{stack_id}/keep`. No other policy class moved and no existing declaration was retargeted.
- **Arithmetic completeness (previous re-derivation, 2026-08-01, stack-units B1 merged, superseded by the line above):** **237 declared**, covering the **236** routes mounted in the default configuration plus **1 conditionally-mounted** route (`POST /api/v1/test-hooks/ws-event`). Counted from `ROUTE_POLICIES` and the live route inventory, not carried forward from prose: `len(ROUTE_POLICIES) == 237`, `len(api_endpoint_set(app)) == 236`, `live - declared == ∅`, `declared - live == {POST /api/v1/test-hooks/ws-event}`. By policy class: `owner_only` **101**, `scoped_list` 39, `picture_scoped` 35, `any_token` 16, `public` 13, `local_owner_only` 13, `project_scoped` 6, `loopback_owner_only` 5, `character_scoped` 5, `set_scoped` 4. The delta since the last re-derivation is **+9 `owner_only`**, all on `dedup.py`: **+8** for the v1.9 tiered Duplicates queue (Lane 1A, 2026-07-29, those rows were added to the tables below but this count line was not re-derived at the time, so the previously stated 228/227 was stale by exactly those 8), and **+1** for `GET /dedup/stacks/{stack_id}/members`, the deck expansion (stack units B1, 2026-08-01). No other policy class moved and no existing declaration was retargeted.
- **Arithmetic completeness (previous re-derivation, 2026-07-29, v1.9 backbone merged, superseded, and it was stale by the 8 tiered-queue routes):** **228 declared**, covering the **227** routes mounted in the default configuration plus **1 conditionally-mounted** route (`POST /api/v1/test-hooks/ws-event`). These numbers are **counted from `ROUTE_POLICIES` and the live route inventory**, not carried forward from prose: `len(ROUTE_POLICIES) == 228`, `len(api_endpoint_set(app)) == 227`, `live - declared == ∅`, `declared - live == {POST /api/v1/test-hooks/ws-event}`. The v1.9 backbone delta over the 217-declaration baseline is **+11**: **+7 `owner_only`** for the DAM 1.2 operation log (#626, `operations.py`), **+2 `owner_only`** for the near-duplicate sweep dry run (#625, `dedup.py`, GET `/dedup/sweep/policy`, POST `/dedup/sweep/dry-run`), and **+2 `picture_scoped`** for Remix (#627, GET `/comfyui/pictures/{picture_id}/recipe`, POST `/comfyui/run_recipe`). That takes `owner_only` 83 → **92** and `picture_scoped` 33 → **35**; no other policy class moved and no existing declaration was retargeted. Multi-project membership (#629) declared no new routes. Earlier interim counts in this document (219 / 224) were prose carried across merges and are superseded by this count.
- **Arithmetic completeness (pre-v1.9 baseline):** **217 declared**, covering the **216** routes mounted in the default configuration plus **1 conditionally-mounted** route (was 207 at the Step-2 back-fill; +6 from the async streaming-staging import (#459), +2 from the v1.8.0 scrapheap-retention config pair GET/PATCH `/server-config/scrapheap-retention` (both `owner_only`), +1 GET `/server-config/scrapheap-retention/impact` (`owner_only`), +1 POST `/api/v1/test-hooks/ws-event` (`loopback_owner_only`)). Gate `enforce_startup` (both report-only and, as a dry check, `enforcing=True`) resolves the app with **0 undeclared, 0 dead declarations, 0 authoring problems** (every `PUBLIC`/`LOCAL_OWNER_ONLY` has a justification; every `*_SCOPED` `id_param` is a real template param). The audit allowlist in `tests/test_architecture_guardrails.py` has burned to **zero** (`_CURRENT_ROUTE_ALLOWLIST = frozenset()`); the registry is now the sole coverage matrix. Guardrail suite: **17 passed**.
- **WebSockets:** the 2 WS routes (`/ws/comfyui`, `/api/v1/ws/updates`) are **out of the HTTP registry by design** — their chokepoint is `authenticate_websocket` (plan §6). They remain acknowledged in `tests/test_architecture_guardrails.py::test_websocket_routes_are_acknowledged`, and `registry.py` carries the `# WS routes: see authn/websocket.py` sentinel.

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

## Policy distribution (249 total)

Recounted from `ROUTE_POLICIES` on 2026-08-05, when #721 added the two projected
face routes. **The recount also corrected pre-existing drift**: the previous
block claimed 228 routes as of 2026-07-29 and was stale by 19 before this change
(`owner_only` alone had grown 92 -> 113). Only the two `faces` rows below belong
to #721.

The route *rows* in this document are machine-checked against the registry by
`tests/test_architecture_guardrails.py::test_coverage_matrix_document_matches_the_registry`,
which parses the tables below and fails if a declared route has no row, a row
names an undeclared route, a route is tabled twice, or a row states a different
policy than the registry enforces. That test was added on 2026-08-05 by the
#721 adversarial sign-off, which found that **nothing had ever read this file**:
the enforcement claim previously made here pointed at
`test_all_routes_declare_access_policy`, which compares the registry against the
live app and never opens the markdown. Six declared routes had no row and one
row was duplicated. Both are fixed, and the check is now real.

The **aggregate counts in the table below are still not machine-checked**; only
the rows are. Re-derive them from `ROUTE_POLICIES` when you touch them rather
than editing the previous figure.

**Re-derived 2026-08-15 (#326), counted from `ROUTE_POLICIES`, not carried
forward: 277 declared.** The previous figures below the header (`249` total,
`local_owner_only` 13) had gone stale by a long way — the shelf's host-capability
routes alone took the local tier 13 → 26 without this table being touched. #326
itself adds exactly **+1 `local_owner_only`** (`GET /api/v1/taggers/plugin-diagnostics`)
and retargets two routes `any_token` -> `owner_only` (`GET /api/v1/taggers`, which
carries the caller's own `tagger_settings`, and `GET /api/v1/pictures/plugins`, its
image-plugin sibling); every other movement here is drift being written down, not a
change made now.

**Re-derived again 2026-08-15 (#950 merge), counted from `ROUTE_POLICIES`: 280
declared.** #950 itself adds exactly **+1 `picture_scoped`**
(`POST /api/v1/pictures/rotate`); the other two (`owner_only` 129 → 130,
`local_owner_only` 27 → 28) were already drift on the base branch at the time of
the re-derivation above, and are written down here rather than carried forward.

**Re-derived again 2026-08-16 (training-run samples), counted from
`ROUTE_POLICIES` after merging `develop`: 284 declared.** This change itself adds
exactly **+2 `local_owner_only`** (`GET /api/v1/models/{model_id}/samples` and
`GET /api/v1/models/{model_id}/samples/{filename}`). The other movements are the
base branch's, written down here rather than carried forward: `local_owner_only`
28 → 29 was already drift, and `loopback_owner_only` 5 → 6 is
`POST /api/v1/models/{model_id}/open-location`, which landed on `develop` while
this branch was open and did not re-derive these aggregates.

| Policy | Count |
|---|---|
| `public` | 13 |
| `any_token` | 12 |
| `owner_only` | 130 |
| `picture_scoped` | 37 |
| `scoped_list` | 39 |
| `set_scoped` | 4 |
| `character_scoped` | 6 |
| `project_scoped` | 6 |
| `local_owner_only` | 31 |
| `loopback_owner_only` | 6 |

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
| GET | `/api/v1/libraries` | owner_only |  | Registry read (multi-library hub). `library_independent=True`: returns the registry, not library content, so it must keep answering while a token is being refused or a switch is in flight. `owner_only` rather than `local_owner_only` (CSO ruling 2026-08-01, plan §11 q3) so the Settings tab renders for any owner; the host detail it would otherwise leak (folder paths, the CLI hint) is omitted for a non-local caller by the handler instead of the whole route being denied. Returns no per-object data |
| POST | `/api/v1/libraries` | **local_owner_only** |  | Adds a library (`library_access=HUB_ONLY`). §16.3 path-authority class: it takes a caller-supplied host path and, when the folder holds no vault, writes a SQLite database into it and restricts the folder to the owner (0700) — write authority inside a host folder, alongside `POST /filesystem/folders`, which is already on this tier. It creates no directory: the folder must already exist (404 otherwise), which is what keeps its authority to the one folder the owner named. The path goes through the same `validate_reference_folder_path` chokepoint as the folder picker. Attaching moves, renames and copies nothing, and the incoming vault's `user`/`user_token` rows are never read, so a library folder from elsewhere cannot import somebody's credentials. `library_independent` left at its safe default `False`: the route has no need to answer mid-switch |
| GET | `/api/v1/libraries/inspect` | **local_owner_only** |  | Says which of five things a folder is (`library_access=HUB_ONLY`). §16.3 host-filesystem read authority — it takes a caller-supplied host path and walks it — the same class as `GET /filesystem/browse`, through the same `validate_reference_folder_path` chokepoint. Reads only; it registers nothing. Returns no per-object library content beyond the name and (locality-conditioned, as the listing's is) the path of the one registered library the verdict is about |
| PATCH | `/api/v1/libraries/{library_uuid}` | **local_owner_only** |  | Renames a library (`library_access=HUB_ONLY`). On this tier by consistency rather than by capability: it writes one hub column, takes no host path, and renames nothing on disk. It sits with its siblings because the Settings pane gates the whole management menu on the same `can_manage` locality answer; a looser tier would give that pane two rules to explain and buy no reachability the owner does not already have. The uuid is resolved through `by_uuid`, not the registry's `get`, so the row-id and name fallbacks are not reachable over HTTP |
| DELETE | `/api/v1/libraries/{library_uuid}` | **local_owner_only** |  | Detaches a library (`library_access=HUB_ONLY`). On this tier for the `POST /libraries/active` reason, **not** the path-authority one: it takes a registry uuid, never a host path, and removes no file. What it exercises is authority over other principals' state — every resource-scoped share link pointing at that library stops working until the same folder is added again. The row is kept rather than deleted, so those links revive rather than being silently revoked. The active library is refused by the registry (`ActiveLibraryError` → 409). Same `by_uuid` resolution as PATCH |
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
| GET | `/api/v1/server-config/layout` | **local_owner_only** |  | §16.3 (v1.11 Phase 4b): reads back how this library's own picture root is laid out, and is the control surface of the PATCH beside it — the tier that alone may decide where the owner's files get written is the tier that may see the decision. Same argument as `GET /server-config/views` and `GET /model-moves`. Returns no per-object data: a layout string and the unfiled folder name, never a path or a picture |
| PATCH | `/api/v1/server-config/layout` | **local_owner_only** |  | §16.3 (v1.11 Phase 4b): decides the folder names PixlStash writes into the library root from here on, and therefore where the background move engine later renames the owner's files to. It writes no file and moves nothing itself — the release's rule is that every path already in the library is true the moment it is written, so choosing a layout reorganises nothing — but the authority it hands out is host-filesystem authority, and the tier that may grant it is the tier that holds it. It accepts no host path at all: the root is the library's own. Sibling of `PATCH /server-config/views`. A layout that cannot be parsed is refused 400 rather than stored, so a malformed one can never behave as "no layout" by accident |
| GET | `/api/v1/server-config/layout/migration` | **local_owner_only** |  | §16.3 (v1.11 Phase 4c): counts what moving every picture in the library's own root onto its layout would do, and is the consent screen of the POST beside it — the tier that alone may rearrange the owner's whole tree is the tier that may see the count. Moves nothing. It does return host-filesystem facts nothing else exposes (how many files would cross a mount point inside the library, and sample paths), which is the second reason it is not `owner_only`; every path it returns is relative to the library root, never absolute |
| POST | `/api/v1/server-config/layout/migration` | **local_owner_only** |  | §16.3 (v1.11 Phase 4c): renames every picture in the library's own root into the folders the layout renders — the most host-filesystem authority any route here exercises. Strictly above `POST /pictures/layout/move-to-match`, which is `picture_scoped` because the caller names the pictures; this caller names none and the scope is the whole library, so the scope check the gate could perform is not the check that matters. It accepts no caller-supplied path: the root is the library's own and every destination is computed from a layout only this same tier could have set (`PATCH /server-config/layout`), while the Phase 4b planner still refuses a source outside the root, a symlink, or a destination that would escape it. A collision is suffixed `-2`, never overwritten; a destination across a mount point is refused rather than attempted (`os.link`/`os.replace` cannot cross a device); and every pass is one `pictures.layout.move` operation under one `srv-layout-migration-` batch id, so the whole run is one undo |
| GET | `/api/v1/server-config/snapshots` | owner_only |  | require_unscoped_owner |
| PATCH | `/api/v1/server-config/snapshots` | owner_only |  | require_unscoped_owner |
| GET | `/api/v1/server-config/views` | **local_owner_only** |  | §16.3 (v1.11 Phase 7): reads back the host folder this library publishes its PixlStash Views tree to, and is the control surface of the PATCH beside it — the tier that alone may publish the tree is the tier that may see where it went (the `GET /model-moves` argument). It is also on `READ_BLOCKED_GET_PATHS`, so an `AUTHZ_GATE_ENFORCING = False` rollback does not hand the path back to share tokens |
| PATCH | `/api/v1/server-config/views` | **local_owner_only** |  | §16.3 (v1.11 Phase 7): takes a caller-supplied host path and writes a folder tree of links into it, removing and rebuilding the subtrees it owns — the `POST /model-folders` class for the path it accepts and the `POST /model-moves` class for the filesystem it writes. It creates only links, and the only thing it unlinks is a name that is not the last one — a symlink, or a regular file with `st_nlink > 1` — so no file whose sole copy is in the tree can be removed by it; anything else is reported back as `kept_by_owner` and left where it stands. `shutil.rmtree` is deliberately not used, because it is not link-aware and would destroy a file the owner had dropped into a view folder. A folder that already holds content and carries no `.pixlstash-views` marker is refused rather than adopted, so a views root aimed at a pictures folder never becomes one. Each destination path is built with `resolve_path_within` against its kind folder and each kind folder against the root, and a symlink standing where a kind folder goes is unlinked as a link rather than descended, so neither a vault-supplied name nor a planted symlink can take the rebuild outside the views root |
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

### folder_structure.py (§16.3, v1.11 Phase 2)

| Method | Effective path | Policy | id_param / body_ids | Rationale (current enforcement) |
|---|---|---|---|---|
| POST | `/api/v1/folder-structure/read` | local_owner_only |  | §16.3 host FS read: takes a caller-supplied host path and walks it recursively, decoding pictures out of it — `GET /filesystem/browse`'s path-authority class and then some, with the blocklist run on the **realpath** and re-run per directory during the walk, so neither a symlink nor a parent directory can hand it a restricted subtree; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/folder-structure/read/status` | local_owner_only |  | §16.3: carries the read's result, which **is** the folder map — polling must not be a lower bar than starting; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| DELETE | `/api/v1/folder-structure/read` | local_owner_only |  | §16.3: cancels the owner's in-flight read, same tier as the route that starts it; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/folder-structure/commit` | local_owner_only |  | §16.3: commits an accepted mapping over the same host path the read already walked — registers it for in-place indexing (the reference-folders/POST write) and creates the projects/people/sets/tags it names; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/folder-structure/commit/status` | local_owner_only |  | §16.3: carries the commit's result, the same host-path class as `GET .../read/status`; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| DELETE | `/api/v1/folder-structure/commit` | local_owner_only |  | §16.3: stops the owner's in-flight commit (abort, or 'organise later') — authority over another principal's operation, on the same tier as the route that starts it, exactly as `DELETE .../read` is to `POST .../read`; owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |

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
| POST | `/api/v1/pictures/export/folder` | **loopback_owner_only** |  | §16.3 RED LINE (#291): writes exported pictures straight onto the host disk and, once done, opens the destination in the host file manager (same host-GUI spawn as `pictures/{id}/open-location`, via `pixlstash/utils/host_open.py`); loopback-only, `allow_remote_host_ops` can NOT loosen it |
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
| POST | `/api/v1/stacks/keep-cover-only` | owner_only |  | Soft-deletes stack members to the Scrapheap and writes the metadata union (tags, score, pending character) onto their covers, across pictures named only by a stack or picture id. Stacks are set-membership-atomic, so this also changes what collections effectively contain. Same reasoning as POST /dedup/verdicts/stack and the mixed-stack mutations; POST is also blocked for READ tokens |
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
| GET | `/api/v1/comfyui/pictures/{picture_id}/recipe` | picture_scoped | id=picture_id |  |
| GET | `/api/v1/comfyui/pictures/{picture_id}/workflow` | picture_scoped | id=picture_id |  |
| POST | `/api/v1/comfyui/run_i2i` | picture_scoped | body=picture_ids |  |
| POST | `/api/v1/comfyui/run_recipe` | picture_scoped | body=picture_id | required single body id; re-extracts the graph from the scoped picture |
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
| PATCH | `/api/v1/models` | owner_only |  | The verb layer's write path (F3): Rename, Set base model and Set kind, which differ only in which curated column they set. `owner_only` on the same default library pin as the shelf reads. Deliberately **not** `local_owner_only`: the body is a list of hub `model.id`s and a value to write, it names no host path, and nothing here touches the filesystem — so it is outside the §16.3 class that `POST /model-folders` and the rescan sit in. The handler's own guards are data checks, not scope checks: `display_name` is refused for more than one id (a name is a fact about one file), and a correction that would violate the hub's `CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)` is refused by name rather than left to surface as a 500 |
| POST | `/api/v1/models/forget` | owner_only |  | The verb layer's destructive path (F3): drops the `model` row and its `model_file` rows, taking the name, base model, kind and trigger words with it. Still `owner_only` and still not §16.3, because it deletes **database rows and never unlinks a file** — the files are already gone, which is the precondition for the call. Gated on row state rather than on caller: a model with a `present` or `unreachable` copy is refused (with a reason, not an error), so the we-could-not-look state cannot be turned into a deletion. Vault `adapter_attachment` rows are deliberately left alone; they are keyed by content hash, unreachable for libraries that are not open, and re-link if the file returns |
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
| DELETE | `/api/v1/model-folders/{folder_id}` | **local_owner_only** |  | §16.3 model-folder delete; drops a registered host path and tombstones its `model_file` rows (the `model` rows and their curation survive). Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-folders/{folder_id}/rescan` | **local_owner_only** |  | §16.3 walks a registered host path and reads every model file under it — the same host-filesystem authority as `reference-folders/detect-sidecars`. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |

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
| GET | `/api/v1/model-folders/{folder_id}/runs` | **local_owner_only** |  | §16.3 walks a registered ai-toolkit output root and reads every run folder and `config.yaml` under it — the same host-filesystem authority as `model-folders/{folder_id}/rescan` and `reference-folders/detect-sidecars`. Reads only: nothing is hashed, copied, moved or written, which is what lets the whole card grid be drawn before the user commits to anything. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/model-folders/{folder_id}/runs/{run_name}/samples/{filename}` | **local_owner_only** |  | §16.3 reads inside a registered ai-toolkit output root and writes nothing, so a step can be judged before it is imported — the same authority class as `model-folders/{folder_id}/rescan`, which is why it sits on this tier. **It is NOT a subset of the listing above it**, and an earlier draft of this row claimed it was: the listing returns metadata for sample names matching ai-toolkit's own regex, while this returns *raw bytes* for any file with an allowlisted extension, including names the listing never reported. That is a new capability class, not a narrower one (corrected in the adversarial review of #878). **Three containment joins, not one**: the run name against the registered folder path; the `samples/` directory itself, because `resolve_path_within` realpaths the base it is handed and a symlinked `samples` would otherwise become its own safe base (a live escape found by that same review — a `source` folder's contents are third-party tool output the owner merely pointed at, and tarballs and git repos carry symlinks); then the filename against that resolved directory, because a single run-level join would let `samples/../config.yaml` pass. The extension is checked against an allowlist (`SAMPLE_MEDIA_TYPES`) rather than guessed, so an `.html` dropped into `samples/` cannot be served from our own origin. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-files` | **local_owner_only** |  | §16.3 the shelf's `Add file` (plan F6): copies one loose `.safetensors` from anywhere on this machine into a registered folder — the managed store unless another is named — and registers it. **The one shelf route that takes a host path in the body**, which the import block above it deliberately does not; it cannot be otherwise, because the whole point is a file in a folder nobody registered. It is therefore the `POST /model-folders` path-taking class carrying the file *writing* of `POST /model-moves`, and it is on this tier for both halves rather than for one. What is contained is the **write**: the destination is `resolve_path_within(folder.path, basename)`, so a symlink standing at the destination name is refused. The read is bounded instead — a regular file, `.safetensors`, and refused outright if it already sits inside a registered folder (a rescan is what puts that one on the shelf). **Nothing is ever unlinked**: the source is the owner's own file and the copy that fails verification is discarded. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-imports` | **local_owner_only** |  | §16.3 copies a run's checkpoints into a registered host folder and, when the source folder carries `delete_after_import`, unlinks them from the output root — the same authority as `POST /model-moves`. The run is named, never pathed: the body gives a registered `source` folder id and a run *name*, and the server joins them with `resolve_path_within`, so a name resolving outside the registered root is a 400 rather than a read. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/models/{model_id}/samples` | **local_owner_only** |  | §16.3 lists one directory inside a registered model folder — the `<stem>_samples/` that an import copied a run's previews into and that a move carries along. It walks a registered host path, which is `model-folders/{folder_id}/rescan`'s authority narrowed to one directory, and it reports the names of files PixlStash never registered: the trainer named them and anything the owner drops in there is listed too (filtered to the extensions `SAMPLE_MEDIA_TYPES` allows, so the list never advertises a name the byte route would 400). **Not `owner_only`, which is what the plan for this change asked for.** Its reasoning was that the route is addressed by a `model.id` with no host path crossing the wire — the exact argument the `GET /api/v1/adapters/{sha256}/file` row above records as *not* the argument, because the tier follows the authority exercised rather than what the route accepts. Kept on the byte route's tier besides, so a caller who may not fetch a preview is not handed a list of them. The derived directory is contained against the registered folder path, and a `model_file.relpath` that escapes its folder is a broken row: it is logged and skipped, never read. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| GET | `/api/v1/models/{model_id}/samples/{filename}` | **local_owner_only** |  | §16.3 serves raw image bytes out of a registered model folder, which is `GET /adapters/{sha256}/file`'s authority class exactly and the shelf-side twin of `model-folders/{folder_id}/runs/{run_name}/samples/{filename}` — the same previews, after the import moved them from the output root onto the shelf. It takes no host path (a `model.id` addresses a row the importer wrote), and per the correction recorded on 2026-08-11 that is not what would put it on `owner_only`. **Two containment joins, not one**, for the two reasons the run-sample row states at length: the `<stem>_samples` directory is contained against the registered `model_folder.path` first, because `resolve_path_within` realpaths the base it is handed and a *symlinked* samples directory would otherwise become its own safe base; then the filename against that resolved directory, because a folder-level join alone would happily pass `../alice.safetensors`, which lands inside the registered folder and is not a file this route serves. The extension is checked against the same `SAMPLE_MEDIA_TYPES` allowlist rather than guessed, so an `.html` dropped into the directory cannot be served from our own origin, and `{filename}` is one URL path segment. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |
| POST | `/api/v1/model-files/delete` | **local_owner_only** |  | §16.3 the shelf's only destructive verb (#933): removes every registered copy of the named models — OS trash by default, `permanent=true` unlinks — and then their hub rows. It is the **unlink half of `POST /model-moves` standing alone**, without the copy that justifies it, which is why it sits on that route's tier rather than the shelf's `owner_only` read tier. It takes **no host path**: the body is a list of `model.id`, every path is contained against the folder the scanner recorded — lexically for the file so a symlinked model loses its link rather than its target, and by `realpath` for the directory holding it so no symlinked component can redirect the unlink — and a row that escapes is refused rather than unlinked. The eligible folders are `user` and `managed` only, so PixlStash's own engine roots, the InsightFace packs and the shared HuggingFace cache are refused whole, as is any model with an `unreachable` copy (an unplugged drive is not a deletion). A `<stem>_samples/` directory holding **nothing but** the trainer's previews goes with the model, non-fatally — the lifecycle the import opens and a move carries has to close here or the orphan refuses that run's whole re-import — while a directory holding anything else is the owner's and stays, because its path is inferred from the model's filename rather than named by the caller. Bytes go before rows, so a failure leaves a row naming a file that is not there — which the next scan marks `missing` — rather than a file nothing on the shelf can see. Owner + loopback/LAN/Tailscale, or remote owner iff `allow_remote_host_ops=true` (§16.3.1) |

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

## Conditionally-mounted routes (added 2026-07-23)

One declared route is **not mounted in the default configuration**:

| Method | Path | Policy | Mounted when |
|---|---|---|---|
| POST | `/api/v1/test-hooks/ws-event` | `loopback_owner_only` | server-config `enable_test_hooks: true` |

**Why it is declared even though it is usually absent.** The gate resolves declarations against the routes actually mounted at startup, and an undeclared route is denied at runtime *and* aborts boot. With the flag on and no declaration, `enforce_startup` aborted — which is precisely what took the Playwright e2e backend down (`frontend/e2e/serve_e2e_backend.py` sets the flag), pre-existing at `3803476f`. The gate behaved correctly; the route really was undeclared.

**Why the absence needs a waiver.** The same check also treats a declaration with no mounted route as a *dead declaration* and aborts. A static registry cannot satisfy both flag states, so `CONDITIONALLY_MOUNTED_ROUTES` in `pixlstash/authz/registry.py` waives the dead-declaration complaint for exactly this set. The waiver:

- **only suppresses an absence complaint.** `undeclared` is computed from the mounted set against the registry and never consults it, so it cannot admit an undeclared route.
- **cannot weaken enforcement.** When the route *is* mounted it resolves and enforces exactly like any other.
- **cannot be used to smuggle coverage.** An import-time invariant requires every member to also appear in `ROUTE_POLICIES` (`RuntimeError` at import otherwise), and `test_conditionally_mounted_routes_are_all_declared` asserts it.
- **costs** only that a listed declaration will not be flagged as rot if its route is deleted outright. Keep the set tiny.

**Why `loopback_owner_only` and not `owner_only`.** The hook calls `vault.notify` with a caller-supplied payload, i.e. it synthesises arbitrary grid WebSocket events broadcast to *every connected client*. That is authority over other clients' state, not over the caller's own data, which places it with the host-shell red line rather than with ordinary owner writes. Loopback is free: the router is mounted only by the e2e backend, which binds `127.0.0.1` and is driven by Playwright on the same host (CI runs Playwright directly on the runner, no container), so there is no legitimate remote caller by construction. `loopback` rather than `local_owner_only` specifically so that `allow_remote_host_ops` — a filesystem-operations flag — can never expose a test hook. Net effect: if `enable_test_hooks` were ever switched on in a network-reachable deployment, the hook still cannot be reached remotely; the safety stops depending on the flag being off. The handler's existing inline `require_unscoped_owner` remains as defence in depth.

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

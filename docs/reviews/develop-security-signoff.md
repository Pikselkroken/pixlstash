# Security sign-off — `develop` → `main` (target v1.6.0)

**Reviewer:** Chief Security Officer
**Scope:** everything on `develop` not in `main` (`git diff main...develop`, 141 files, +18.6k/-3k). Commit range `4c680564..f2b50ac5`.
**Date:** 2026-06-13

## MERGE VERDICT: ~~READY WITH FIXES~~ → ~~NOT READY~~ → **READY** (B1 fix verified closed, CI cleared — independent adversarial sign-off, 2026-06-13)

> **CORRECTION (reconciliation with backend review).** This sign-off originally graded the
> batch likeness endpoint **M1 / 0 blockers**, reasoning the middleware READ-POST block
> (`auth.py:1545`) closes it. That reasoning only covers `scope == "READ"` tokens. It misses the
> **WRITE- and ALL-scoped *resource* token** vector: that branch is the *only* scope gate in the
> middleware, so a non-READ resource-scoped token passes `call_next` into a handler that never
> calls `enforce_picture_scope` and can read `character_likeness` + existence/eligibility signals
> for **any picture id in the vault**. Verified directly against `_crud.py:1219` (no `request`
> param, no scope call) and `auth.py:1542-1561`. This is a **live BOLA leak**, the recurring class
> CLAUDE.md §16.1 calls "law", and it was a **merge blocker**. The miss itself is the
> "trace the whole input space / sibling vectors" failure the security process exists to catch.
> **M1 was re-graded HIGH and was BLOCKER #1.** See `develop-backend-merge-review.md` B1 for the fix.

> **B1 CLOSED — independent adversarial verification, 2026-06-13 (CSO, not the fix author).**
> The fix is present in the working tree and is correct. `get_pictures_character_likeness_batch`
> (`_crud.py:1222`) now takes `request: Request`, calls `fetch_scope_allowed_picture_ids(server, request)`
> at `:1273` **before any DB read**, and computes `scoped_ids` (`:1274-1277`). Every DB read and
> scoring pass is driven off `scoped_ids`: `gather_signals` (`:1382`), `scorable_ids` (`:1404-1406`),
> and `find_pictures_by_character_likeness_sql(..., candidate_ids=scorable_ids)` (`:1416`, bounded by
> `Face.picture_id.in_(candidate_ids)` at `picture_scoring.py:580`). `classify()` still iterates the
> full `unique_ids` list, but every branch is gated by the scoped `live_ids` set: an out-of-scope id
> is absent from `live_ids` (it was never queried), so the very first check (`:1428 if pid not in
> live_ids` → `deny_result`) fires before the `UNASSIGNED`/`matched_ids` branches are ever reached.
> No path bypasses the filter. Out-of-scope ids return the exact `deny_result` shape
> (`character_likeness:null, eligible:false, ready:true`), byte-identical to a genuinely missing id —
> tests assert this across all signal fields (`test_..._scope.py:191-193`). The `reference_character_id`
> echo (`:1488`) is the client's own input, not resource data, and the single-id sibling does not
> scope-check it either, so no new leak. `fetch_scope_allowed_picture_ids` is fail-closed (empty set on
> unrecognised `resource_type`, `filter_helpers.py:284-289`). Tests pass (4/4, 22s, `--force-cpu`).
> **Confirmed NOT in `READ_SAFE_POST_PATHS`** (`auth.py:79-96`; asserted by test at `:104-107`).
>
> **DISPUTED CLAIM ADJUDICATED — the author's exploitability downgrade is FALSE.** The backend author
> asserted "the only API-creatable resource token is scope=READ, which the middleware already blocks."
> Not true. `create_token` (`auth.py:881-927`) validates `scope ∈ {ALL, READ}` (`:901`) and
> `resource_type ∈ {picture_set, character, project, picture}` (`:903`) **independently — there is no
> coupling rule.** `POST /users/me/token` (`config.py:353`) passes both straight through. So an owner
> can mint `scope="ALL"` + `resource_type="character"` + `resource_id=N` through the documented API. An
> ALL-scoped resource token sets `token_scope` (non-None) yet is **not** blocked by the middleware
> (which only blocks `scope=="READ"`, `auth.py:1545`), so before the fix it reached the handler unscoped
> and could read likeness/existence for any picture id. **B1 was genuinely live-exploitable via an
> API-minted ALL-scoped resource token, not merely a latent gap.** The §16.1 fix is correct and was
> required. (Caveat: minting requires owner/ALL-session credentials — scoped tokens cannot create tokens,
> `auth.py:896` — so the principal is an owner who delegated an over-broad token, or a leaked ALL token.
> That is exactly the share-delegation threat model BOLA scoping exists for. The fix stands regardless.)
>
> **ALL+resource_type footgun — confirmed pre-existing, NOT newly reachable via this endpoint.**
> Both `enforce_picture_scope` (`_helpers.py:340-374`) and `fetch_scope_allowed_picture_ids`
> (`filter_helpers.py:234-236`) treat `token_scope` with `resource_type is None` as full access, and an
> ALL-scoped *resource* token (resource_type set) is now correctly filtered by both. The residual footgun
> is a token with `scope` set but `resource_type=None`; that behaves identically across the batch endpoint
> and its guarded single-id sibling, so this endpoint introduces nothing new. Steer the fix toward the
> centralized chokepoint (§16.2), as L3 already records.
>
> **Residual test gap (does NOT block merge).** The scoped-token test monkeypatches
> `fetch_scope_allowed_picture_ids` rather than minting a real `scope=ALL`+`resource_type=character`
> token and driving it through the live middleware + handler. The only end-to-end token test uses a
> READ token (blocked at the middleware, never exercises the new filter). So the realistic exploit path
> (ALL-scoped resource token → middleware passes → handler filters) is unit-covered but not
> integration-covered. Given this is the third recurrence of this BOLA class, **add an integration test
> that mints an ALL-scoped resource token and asserts out-of-scope ids deny end-to-end** as a fast
> follow-up. The fix itself is correct; this is coverage hardening.
>
> **CI (M2 / electron.yml action pinning) — CLEARED FOR PUSH.** `git diff` shows only `uses:` lines
> changed (no logic/permissions/trigger/secret/`with:` change). All 7 actions are now full-SHA pinned
> with version comments; every SHA was resolved against its claimed tag via `gh api` and matches,
> including the security-critical third-party `softprops/action-gh-release@3bb12739…` (= v2.6.2,
> the only non-`actions/*` action and the one holding `RELEASE_BOT_TOKEN`). No unpinned `uses:` remain.

No other Critical or High findings, and no other live BOLA hole. The release-blocking work (Electron remote-access account takeover, navigation lockdown, supply-chain integrity) was already reviewed in two prior adversarial passes and I confirmed every claimed-closed fix is actually present in the **committed** tree (the prior reviews inspected an uncommitted working tree that was later rebased; the fixes carried through, see verification below).

**Blockers remaining: 0.** The one blocker (B1, the batch-likeness BOLA) is now **fixed and independently verified closed** (see the B1 CLOSED note above). M2 (action pinning) is **done and cleared for push**. Nothing remaining risks user data, credentials, or RCE on a default configuration.

Resolved with the merge:
- **B1/M1** — the batch likeness endpoint now calls `fetch_scope_allowed_picture_ids` before any DB read and routes out-of-scope ids through `deny_result`. Verified closed against the working tree; tests pass. One follow-up: add an end-to-end test that mints an ALL-scoped resource token (the real exploit vector, not just the monkeypatched unit test).
- **M2** — `electron.yml` actions are now full-SHA pinned with version comments; all 7 SHAs verified against their tags. Cleared for push.

The rest are accepted/owned LOW follow-ups carried from the prior reviews.

---

## What was reviewed and where the prior reviews stand

The high-risk Electron + external-listener + supply-chain surface was already covered by:
- `docs/reviews/develop-electron-security.md` (two independent adversarial passes, H1/H2 + Mediums)
- `docs/reviews/electron-external-server.md` (external listener)
- `docs/reviews/auraface-model-pack-config.md` (AuraFace model-pack download)
- `docs/reviews/bulk-token-scoping.md` + `v1.5.1-security-signoff.md` (read-token BOLA, R1/R2 deferred)

Those reviews inspected an **uncommitted working tree** at a HEAD (`185b387b`) that is **not an ancestor of current `develop`** — the branch was squashed/rebased before landing. I therefore re-verified every claimed-closed fix against the live committed code. **All survived the rebase intact**, and the two feature commits that landed afterward (`6952a5f4` ROCm, `f2b50ac5` screenshots) do not regress them (the ROCm `--force-backend` change in `main.ts` is confined to a validated `Accel`-enum override and never touches the nav guard, `openExternalSafely`, `webPreferences`, or the auth code).

Re-verified present in committed code:
- **H1** registration loopback gate (`auth.py:352` `_require_loopback_for_registration` → `is_loopback_ip`, called first in the credential-set branch of `_do_login:1299` and `change_password:844` before any DB write); desktop-session backstop pins to loopback in both HTTP middleware (`auth.py:1416`) and WS (`auth.py:741`); external-listener bind refusal omits the `0.0.0.0` row when the owner has no password and fails closed (`server.py:1551` `_external_listener_password_ready`).
- **H2** `isAllowedNavigation` deny-by-default, wired to both `will-navigate` and `will-redirect` (`main.ts:256-257`), `file://` pinned to `RENDERER_DIR`.
- **M2 (electron)** `openExternalSafely` allows only https/mailto, deny-by-default; exactly one `shell.openExternal` in the tree, behind the helper; both call sites route through it.
- **webPreferences** `contextIsolation:true`, `nodeIntegration:false`, sandbox/webviewTag not weakened. Preload exposes a minimal fixed IPC surface (no generic passthrough).
- **CPython tarball** SHA256-verified before extraction, fail-closed (`build_desktop_runtime.py`); `electron-updater` removed from `electron/package.json`.

---

## Coverage matrix — authz on data endpoints (changed/new on this branch)

The chokepoint is `enforce_picture_scope` (`pixlstash/routes/pictures/_helpers.py:340`); list endpoints filter through `fetch_scope_allowed_picture_ids`. The middleware (`auth.py:1544`) blocks `scope=="READ"` tokens from any non-GET method unless the path is in `READ_SAFE_POST_PATHS` (`auth.py:79`), and from GET paths in `READ_BLOCKED_GET_PATHS` (`auth.py:102`). Token scope is creation-restricted to `ALL` or `READ` only (`auth.py:901`); `token_scope` is populated only for non-`ALL` scopes (`auth.py:1463`).

| Method + Path | New/Mod | Returns resource data | Scope check (file:line) | Verdict |
|---|---|---|---|---|
| POST `/pictures/character_likeness/batch` (`_crud.py:1222`) | **NEW** | Yes (per-id existence/eligibility/likeness) | `fetch_scope_allowed_picture_ids(server, request)` at `:1273` before any DB read; all branches gated via scoped `live_ids`; out-of-scope → `deny_result`. NOT in `READ_SAFE_POST_PATHS`. | **COVERED (B1 fixed, CSO-verified)** |
| POST `/pictures/score_character_likeness` (`_crud.py:1501`) | **NEW** | Scores uploaded images; reference = `character_id` | `fetch_scope_allowed_character_ids` + 403 (`_crud.py:1514`), deny-by-default | COVERED |
| GET `/pictures/{id}` (`:688`), `/metadata` (`:867`) | unchanged | Yes | `enforce_picture_scope` `:705`, `:880` | COVERED |
| GET `/pictures/{id}/{field}` (`:1642`) | unchanged | Yes | `enforce_picture_scope` `:1657` (B2 closed) | COVERED |
| GET `/pictures/{id}/character_likeness` (`:1059`) | unchanged | Yes | `enforce_picture_scope` `:1075` (R2 closed) | COVERED |
| GET `/pictures`, `/stream`, `/count` (`_listing.py`) | modified (filter refactor) | Yes (lists) | scope woven into candidate branches; **UNASSIGNED** branch `fetch_scope_allowed_picture_ids:755` → fail-closed `_empty_result` (B1 fixed and intact) | COVERED |
| GET `/pictures/comfyui-models`/`-loras` (`_misc.py:198,233`) | modified (refactor) | Yes | `fetch_scope_allowed_picture_ids:203,238` | COVERED |
| GET `/pictures/likeness-groups` (`_misc.py:269`) | modified (refactor) | Yes | `fetch_scope_allowed_picture_ids` + `&` intersection upstream of refactor | COVERED |
| GET `/pictures/stats` (`_misc.py:794`) | unchanged | Aggregates | `fetch_scope_allowed_picture_ids:877` | COVERED |
| GET `/pictures/search` (`_search.py:59`) | modified (refactor) | Yes | `fetch_scope_allowed_picture_ids:328` | COVERED |
| GET `/picture_sets/{id}` (`picture_sets.py:880`) | modified (refactor) | Yes | `_require_scope_allows_picture_set:916` before any return | COVERED |
| Picture/set CRUD mutations (PATCH/POST/DELETE) | scrapheap modified | Owner mutations | Owner-only; READ tokens blocked by middleware; no resource-scoped write scope exists | COVERED (owner-only) |

**No empty cell is a live BOLA hole.** The `_listing/_misc/_search/picture_sets` changes are pure refactors that extracted intrinsic-attribute predicate building into `PredicateFilter` (`pixlstash/utils/query/predicate_filter.py`); they do not touch any scope check, scope-id intersection, or the soft-delete clause. The B1 `character_id=UNASSIGNED` fail-closed handling lives in `_listing.py:749-768`, was not in the refactored code, and is intact. The refactor uses parameterized SQLAlchemy throughout (no string-interpolated SQL); tests `tests/test_predicate_filter.py` pass (28).

---

## Findings (worst first)

### B1 (was M1) — Batch likeness endpoint BOLA. WAS HIGH BLOCKER → **FIXED, CSO-VERIFIED CLOSED**

**Where:** `pixlstash/routes/pictures/_crud.py:1222` (`get_pictures_character_likeness_batch`, POST `/pictures/character_likeness/batch`).

**What it was.** The handler took no `request: Request` and never called `enforce_picture_scope` / `fetch_scope_allowed_picture_ids`. It accepts up to 1000 arbitrary `picture_ids` and returns per-id existence/eligibility/likeness, so an ALL-scoped *resource* token could read those signals for any picture id in the vault. The docstring claimed deny-by-default for out-of-scope ids; the code did not implement it.

**Live exploitability (adjudicated, corrects the author).** It was **live-exploitable**, not merely a latent gap. The author claimed only `scope=READ` resource tokens are API-creatable and those are middleware-blocked. False: `create_token` (`auth.py:881-927`) validates `scope` and `resource_type` independently with no coupling, so `POST /users/me/token` can mint `scope=ALL`+`resource_type=character`+`resource_id=N`. An ALL-scoped resource token sets `token_scope` (non-None) but is NOT blocked by the middleware (it only blocks `scope=="READ"`), so it reached the unscoped handler. Principal: an owner who delegated an over-broad token, or a leaked ALL token (scoped tokens themselves can't mint tokens, `auth.py:896`).

**The fix (verified present and correct).** `request: Request` added; `fetch_scope_allowed_picture_ids(server, request)` called at `:1273` before any DB read; `scoped_ids` computed (`:1274-1277`) and drives `gather_signals` (`:1382`), `scorable_ids` (`:1404`), and the bounded SQL scoring (`candidate_ids=scorable_ids`, `:1416` → `picture_scoring.py:580`). `classify()` iterates `unique_ids` but the `pid not in live_ids → deny_result` gate (`:1428`) fires first for any out-of-scope id, before the `UNASSIGNED`/`matched_ids` branches, so no branch bypasses the filter. Out-of-scope ids are byte-identical to missing ids across all signal fields. `fetch_scope_allowed_picture_ids` is fail-closed. Endpoint confirmed NOT in `READ_SAFE_POST_PATHS`. Tests `tests/test_batch_character_likeness_scope.py` pass 4/4.

**Residual (follow-up, non-blocking).** The scoped-token test monkeypatches the scope helper instead of minting a real ALL-scoped resource token end-to-end. Add an integration test that mints `scope=ALL`+`resource_type=character` and asserts out-of-scope ids deny through the live middleware + handler. Coverage hardening only; the fix is correct.

### M2 — `electron.yml` third-party action pinning. WAS MEDIUM (hardening) → **DONE, CLEARED FOR PUSH**

**Where:** `.github/workflows/electron.yml`. Previously all actions used floating major tags (`actions/checkout@v6`, `softprops/action-gh-release@v2`, etc.).

**Risk (now mitigated).** A floating tag can be re-pointed (the `tj-actions/changed-files` / `reviewdog` tag-mutability incidents in 2025). The `build-electron` job holds `contents: write` and `RELEASE_BOT_TOKEN` and ships **unsigned** installers, so pipeline integrity was the only artifact protection.

**Verification (CSO, 2026-06-13).** `git diff .github/workflows/electron.yml` changes only `uses:` lines — no logic, permissions, trigger, secret, or `with:` change. All 7 actions are now full-SHA pinned with `# vX.Y.Z` comments. Each SHA resolved against its claimed tag via `gh api` and matched: `actions/checkout` v6.0.3 (`df4cb1c0…`), `actions/setup-node` v6.4.0 (`48b55a01…`), `actions/setup-python` v5.6.0 (`a26af69b…`), `actions/upload-artifact` v4.6.2 (`ea165f8d…`), `actions/download-artifact` v4.3.0 (`d3f86a10…`), `actions/cache` v4.3.0 (`00578528…`), and the security-critical third-party `softprops/action-gh-release` v2.6.2 (`3bb12739c298aeb8a4eeaf626c5b8d85266b0e65`). No unpinned `uses:` remain. **CLEARED.** Still recommended: add a Dependabot `github-actions` entry so SHAs get bumped via PR.

### L1 — Torch/ROCm GPU wheels installed without `--require-hashes`. LOW (accepted)

`electron/src/backend/BackendManager.ts` + `.github/workflows/rocm-overlay-install.yml`: wheels are version-pinned and fetched over HTTPS from the official `download.pytorch.org/whl/rocm*` index (hardcoded constant `TORCH_INDEX`, never caller-derived; `--force-backend` validates to an `Accel` enum and feeds only hardware detection, no injection path). No hash pinning. Trust boundary = TLS + the official index. This is the standard pip-from-PyTorch posture; the index does not publish a hash-pinnable manifest for these GPU wheels. **Accepted, owner: ROCm feature owner**, revisit if PyTorch publishes hashable manifests.

### L2 — Unsigned Windows/Linux installers; macOS build disabled. LOW (accepted, documented)

`electron.yml` ships unsigned installers (no Authenticode / Developer ID). Explicit, commented decision with a re-enable path; `docs/signing-evidence.md` exists. Raises the importance of M2 (pipeline integrity is the only artifact protection). **Accepted, owner: release owner**, revisit when signing is set up. Do not wire any auto-update against unsigned artifacts (electron-updater already removed — keep it out until signing lands).

### L3 — Carried LOW follow-ups from prior reviews (still owned, not blockers)

- **NEW-2** CPython `.sha256` sidecar is same-origin as the tarball (TLS/redirect/truncation defence, not end-to-end provenance). Pin the digest as a committed constant in a follow-up.
- **NEW-3** `file://` nav pin uses `resolve` (collapses `..`) not `realpath` (follows symlinks); planting a symlink inside the packaged renderer dir requires local install-dir write (already game-over).
- **`trusted_proxies=127.0.0.1` footgun** — document, don't ship a default that trusts loopback as a proxy (would let `X-Forwarded-For: 127.0.0.1` reach the registration/desktop-session loopback gates).
- **L1 `cookie_secure=false` in electron mode** — accepted given loopback transport.
- **ALL+resource_type token footgun** (`auth.py:1463`) — pre-existing, repo-wide; not reachable by the share-token principal (the UI only mints `scope=READ` resource tokens). Close it via the centralized chokepoint direction in `backend_architecture.md` §16.2; that also covers M1's residual.
- **R1 `GET /comfyui/pictures/{id}/workflow`** — deferred MEDIUM from v1.5.1, unrelated to this diff but still open; close before scoped share links are promoted/issued against the public demo.

---

## Other surfaces checked (clean)

- **Privacy (`PRIVACY.md`, `website/privacy.html`) vs code — claims match.** The update check (`frontend/src/composables/useVersionCheck.js`) is off by default (`check_for_updates` defaults `None`/`false`, `useVersionCheck` no-ops until enabled), contacts only `https://pixlstash.dev/latest-version/{version}/{bucket}.json`, sends only the app version (URL path) and a coarse install-type bucket (`docker`/`pip`/`electron`/`other`), and the documented 24h interval + high-severity-security override + `?v=&i=` upgrade-link params all match the code exactly. The "downloads from third parties" section (HuggingFace/PyPI/PyTorch index) matches the verified model/wheel download paths. No library content, login, or personal identifier is sent. The policy does not over-claim.
- **AuraFace model-pack download** — no regression: pinned to 40-char commit SHA `af6d057c...` (`insightface_model_utils.py:46`), `allow_patterns=["*.onnx"]`, hardcoded `repo_id`, fail-closed pack validation, path-containment on flatten. (Full sign-off: `auraface-model-pack-config.md`.)
- **Migrations 0052–0055** — linear chain (0051→…→0055), conditional `op.add_column` guarded by `sa.inspect`, `__all__` present, parameterized backfills. Per CLAUDE.md migration policy. 0052 (import_excluded drop) is a deletion-semantics change with no BOLA surface.
- **release-version.yml** (+9) — adds an `electron.json` version manifest with the same payload as existing install types; no new secret/permission/trigger.

---

## Release gate

**Block on:** nothing. B1 is fixed and verified closed; M2 is done and cleared for push.

**Resolved (verified in working tree):**
- **B1/M1** — batch endpoint scoped via `fetch_scope_allowed_picture_ids`; out-of-scope ids deny indistinguishably; tests pass; not in `READ_SAFE_POST_PATHS`.
- **M2** — `electron.yml` actions full-SHA pinned; all 7 SHAs verified against tags; cleared.

**Owned follow-ups (not blockers):** B1 end-to-end ALL-scoped-token integration test, L1 GPU-wheel hashing, L2 signing decision, NEW-2/NEW-3, `trusted_proxies` footgun doc, ALL+resource_type centralization (§16.2), R1, Dependabot `github-actions` entry.

**Accepted risk (documented):** L1, L2, `cookie_secure=false` (electron loopback). Owner: desktop/backend/release author. Revisit at the v1.6.0 release cut.

Reviewer: Chief Security Officer. Owner: develop branch author.

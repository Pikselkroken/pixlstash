# Backend Merge Review: `develop` → `main`

**Reviewer:** senior-backend-developer (with fan-out to four scoped sub-reviewers)
**Date:** 2026-06-13
**Scope:** backend changes on `develop` not in `main` (`git diff main...develop`), focus areas: Picture/Face/User models + migrations 0052–0055, PredicateFilter refactor, `routes/pictures/*` (batch + upload likeness endpoints), InsightFace model-pack task system, server external listener + ROCm checks, picture_stats / user_settings.

---

## MERGE VERDICT: **NOT READY**

Two BLOCKERS. The first is a fresh recurrence of the exact BOLA-by-omission class CLAUDE.md §16.1 declares "law" — a new data endpoint that returns per-object data with no scope check. The second is a correctness defect in the face-model-refresh task that causes redundant GPU work and, on a persistent write failure, genuine infinite reprocessing. Both must be fixed (and tested in both directions) before merge.

`ruff check pixlstash` is **clean** ("All checks passed!").

---

## BLOCKERS

### B1 — BOLA: `POST /pictures/character_likeness/batch` returns per-picture data with NO scope enforcement
`pixlstash/routes/pictures/_crud.py:1257` (`get_pictures_character_likeness_batch`).

The handler signature is `(payload: BatchCharacterLikenessRequest = Body(...))` — **no `request: Request`, no `enforce_picture_scope` call.** It takes a client-supplied `picture_ids` list and, via `gather_signals` (`_crud.py:316`) and `find_pictures_by_character_likeness_sql` (`picture_scoring.py:488`), returns for each id: existence/`live` signal, `ready`, `eligible`, and the actual `character_likeness` score.

`find_pictures_by_character_likeness_sql` does **not** scope `candidate_ids` (verified `picture_scoring.py:488-582`: it filters only on `deleted`, `stack_leaders_only`, `character_id`, and the caller-supplied `candidate_ids`). The batch handler passes the raw client ids straight through (`scorable_ids` at `_crud.py:1421`). `gather_signals` likewise queries arbitrary ids with no `fetch_scope_allowed_picture_ids` intersection.

The single-id sibling `get_picture_character_likeness` (`_crud.py:1059`) does it correctly: `request: Request` parameter + `enforce_picture_scope(server, request, pic_id)` at `_crud.py:1075`. The batch endpoint is the unscoped twin.

**Reachability.** The middleware (`auth.py:1542-1561`) blocks READ-scoped tokens from non-`READ_SAFE_POST_PATHS` POSTs, and this path is not on that allowlist, so a READ token is blocked. But a **WRITE-scoped resource token** (scoped to one picture set / character / project — `token_scope.scope == "WRITE"`) passes the method check and reaches the handler, which then leaks `character_likeness` scores and existence signals for **every picture id in the vault**, including pictures outside the token's grant. This is precisely the §16.1 hard-requirement violation ("an endpoint with neither [chokepoint nor documented exemption] is a bug").

**Per §16.1 the route is in neither acceptable state.** It does not call the chokepoint, and it is not a documented `READ_SAFE_POST_PATHS` / `READ_BLOCKED_GET_PATHS` exemption with reviewer sign-off.

**Fix.** Add `request: Request` to the signature and scope the candidate set before any DB read or return, exactly like the single-id sibling. Concretely, after de-duplicating, intersect `unique_ids` with the token's allowed picture ids:

```python
def get_pictures_character_likeness_batch(
    request: Request,
    payload: BatchCharacterLikenessRequest = Body(...),
):
    ...
    allowed = fetch_scope_allowed_picture_ids(server, request)  # None == unscoped/owner
    if allowed is not None:
        # Out-of-scope ids must be indistinguishable from missing ids: deny, do
        # not 403 per-id (per-id 403 would itself be an existence oracle).
        unique_ids = [pid for pid in unique_ids if pid in allowed]
```

then build `per_id` from the filtered set and have `requested_ids` ids that were filtered out fall through to `deny_result(pid)` (which already returns `{character_likeness: null, eligible: false, ready: true}` — indistinguishable from out-of-scope, good). Verify `fetch_scope_allowed_picture_ids` (in `utils/service/filter_helpers.py`) is the right helper — it is what the architecture doc names for the central design. Place the filter once, covering all return paths (the `classify`/`deny_result` fan-out already routes through `live_ids`, so filtering `unique_ids` upstream covers every branch).

**Also B1 (tests).** There is **no test for this endpoint at all** (grep of `tests/`), let alone the both-directions scope test §16.1 mandates. Add: (a) out-of-scope picture id under a resource-scoped token returns `eligible:false`/`null` and is indistinguishable from a missing id; (b) in-scope id still returns its real score (over-blocking is its own regression). Written ideally by someone other than the fix author per the review process.

> This must also go through `chief-security-officer` adversarial sign-off before merge (independent reviewer, reproduce the finding, hunt for sibling leaks) — that is the §16.1 / CLAUDE.md gate, not optional.

### B2 — FaceModelRefreshTask: claim released before async DB writes commit (redundant re-detection); silent swallow + dead code on commit failure (infinite reprocessing)
`pixlstash/tasks/face_model_refresh_task.py`.

Two coupled defects (full analysis in the model-pack sub-review):

- **C1 (`_run_task` ~line 248):** writes are dispatched via `self._db.submit_task(self._refresh_picture_faces, ...)` (fire-and-forget) and the returned Futures are never `.result()`-ed. `on_task_complete` releases the finder's claim and decrements inflight the instant `run()` returns — before the `model_pack`-updating writes commit. `MissingFaceModelRefreshFinder._fetch_stale_pack_pictures` then re-selects the same still-stale rows and re-runs full GPU detection on them. With `max_inflight_tasks()==1` it won't fan out unboundedly, but it does redundant GPU passes over the same batch. **Fix:** collect the submit Futures and block on them (`for f in futures: f.result()`) at the end of `_run_task`, or submit one batched write and `.result()` it, so the claim outlives the commit.
- **C2 (`_commit` ~line 393-415):** the generic `except Exception` rolls back, logs a `warning`, then runs dead code (`picture = session.get(Picture, picture_id); if picture is not None: _ = picture.id` — a no-op; no column is reset). On a persistent write failure the faces keep the **old** `model_pack`, so the finder re-selects the picture forever → genuine infinite reprocessing loop. **Fix:** remove the dead `session.get(...)` block; either retry with bounded attempts or stamp a failure marker, but never silently leave stale rows that re-trigger the finder.

The tests hide both: `_FakeDB.submit_task` in `test_insightface_model_pack.py` runs writes **synchronously**, masking the exact async behaviour that causes C1, and there is no test of `_commit`'s exception branch (C2) or of "finder does not re-select a just-refreshed picture." Add an end-to-end refresh test with a truly async DB shim.

---

## HIGH

### H1 — `find_query_params` malformed `min_score`/`max_score` now 500s instead of 400
`pixlstash/utils/query/predicate_filter.py` (`_int_or_none`) + `pixlstash/routes/pictures/_misc.py:835`.

The route calls `PredicateFilter.from_query_params(request)` **before** its own guarded score coercion (`_misc.py:860-864`). `from_query_params` runs bare `int(raw)` on `min_score`/`max_score`, so `?min_score=abc` raises an unhandled `ValueError` → HTTP 500. Pre-refactor this returned a clean 400. The comment at `_misc.py:834` ("scores keep their dedicated HTTPException-on-invalid coercion below") is defeated because `from_query_params` parses them first. **Fix:** make `_int_or_none` tolerant (`try: int(raw) except ValueError: return None`) and let the route's own coercion own the 400. Confirm `picture_sets.py:899` and `_search.py:99` are unaffected (they take typed `Query()` scores).

### H2 — Face-refresh detection is per-image, not size-batched
`face_model_refresh_task.py` `_detect_for_picture` (~line 135-227) loops one picture at a time and calls `detect_faces_in_images(app, [img])[0]` with a single-element list per image (and per video frame). The extraction path it mirrors (`face_extraction_task.py` `_extract_features`) deliberately batches detect + runs recognition as one ONNX call across all crops. For a full-library re-embedding sweep this is the hot path and is markedly slower. Violates the CLAUDE.md batching practice ("group by size"). **Fix:** reuse the extraction task's batched detect/recognize machinery over the picture batch.

---

## MEDIUM

### M1 — `sidebar_docked` has no boolean coercion
`pixlstash/utils/service/user_settings_utils.py:365-367`. `sidebar_docked` is in both allow-lists but has no dedicated branch, so it falls through to the generic `setattr(user, key, value)` with no `bool()` coercion — unlike every other boolean toggle in the function (`apply_tag_filter` at :261-265, `keep_models_in_memory` at :270-274). A client PATCHing `{"sidebar_docked": "false"}` persists the truthy string. **Fix:** add an explicit `bool()`-coercing branch mirroring `apply_tag_filter`.

### M2 — `picture.py` `find` / `semantic_search` apply the deleted/unimported clause twice
`pixlstash/db_models/picture.py:530-536` (semantic_search) and `:715-721` (find) re-add the inline `only_deleted`/`include_deleted`/`include_unimported` WHERE clauses (re-added in commit `48d126f0` when `import_excluded` was dropped) while **also** passing those same flags into the `PredicateFilter(...)` call (`:574-576`, `:771-773`). Result set is unchanged (idempotent ANDs), so **not a row-drift bug**, but it duplicates SQL and defeats the refactor's single-source-of-truth goal — a future edit to one copy silently diverges. **Fix:** pick one owner: drop the inline blocks and rely on PredicateFilter, or set `apply_deleted_filter=False` on those two PredicateFilter calls and keep the inline clauses.

### M3 — Two silent `except` swallows in startup ROCm/device path
`pixlstash/startup_checks.py:276-277` (`except Exception: continue` in `_has_onnxruntime_conflict` — `continue` is functionally `pass`, no log) and `:336-339` (`except Exception: providers = []` swallowing `ort.get_available_providers()` failure). Both sit in the modified ROCm/device-detection logic and violate CLAUDE.md's no-silent-failure rule. The second masks a broken ORT install as "no CUDA provider" on a CUDA box. **Fix:** log the exception with context before `continue`/defaulting.

### M4 — `PIXLSTASH_DEFAULT_DEVICE` accepts unvalidated values; no test for invalid input
`pixlstash/server.py:1856-1862`. The override is written straight into `default_device` with no allow-list check; `PIXLSTASH_DEFAULT_DEVICE=banana` silently falls through to CPU with no "you passed garbage" message. Tests cover `cpu`/`CUDA`/blank/unset but not an invalid value (the brief asked specifically). **Fix:** validate against the known set, `logger.warning` + ignore on mismatch, add a negative test.

### M5 — Refresh-finder migration-reference comment wrong (0052 vs 0053)
`pixlstash/tasks/missing_face_model_refresh_finder.py:81` says "the 0052 migration backfills...buffalo_l"; the backfill is in `0053_add_face_model_pack.py`. Same error in the test comment. Load-bearing provenance claim in a migration-sensitive area. **Fix:** correct both to `0053`.

### M6 — Stale-pack finder query is an unindexed full face-table scan each cycle
`missing_face_model_refresh_finder.py:78-94` runs `SELECT DISTINCT picture_id FROM face WHERE model_pack != :pack OR model_pack IS NULL` every WorkPlanner cycle; `Face.model_pack` has no index (`db_models/face.py`). In the steady state (packs match) this scans the whole `face` table to return empty, repeatedly. **Fix:** add an index on `model_pack`, or gate the finder so it doesn't scan when no pack change is pending.

---

## LOW

- **L1 — `PredicateFilter` uses default `extra="ignore"`** (`predicate_filter.py:82`): a future misspelled filter field (e.g. `tags_filer=`) is silently dropped, broadening results — a BOLA-shaped failure mode for scope/membership queries. Current call sites are clean. **Fix:** `model_config = ConfigDict(extra="forbid")`.
- **L2 — TOCTOU in `_check_port_bindable`** (`startup_checks.py:212-219`): probe binds with `SO_REUSEADDR` then closes; uvicorn binds later. Acknowledged in code and fails loud via `_serve_one`, so advisory only — follow-up, not a blocker. The probe's `SO_REUSEADDR` doesn't model uvicorn's bind, giving false confidence.
- **L3 — ROCm explicit-GPU failure message says "cuda"** (`startup_checks.py:330-331`): "ROCm is unavailable while default_device is set to cuda." Wording only.
- **L4 — `_detect_for_picture` video frame sampling may not match the extractor** (`face_model_refresh_task.py:194-196` vs `face_extraction_task.py`): if the sampled frame set differs, the `(frame_index, face_index)` match key misses for videos and manual `character_id` assignments on video faces can be dropped. Verify the two sampling expressions are identical or share a helper.
- **L5 — `sidebar_width` continuous-clamp vs `sidebar_thumbnail_size` enum-snap asymmetry** (`user_settings_utils.py:94-98`): intentional, no action.

---

## What is correct / good (verified, no action)

- **`import_excluded` fully removed and consistent.** No references remain in application code (only in historical migrations + the 0052 drop). The `Picture` model field is gone; `picture_stats`, `picture.py`, `picture_sets.py` are clean. The "72 deleted, empty grid" bug is fixed at the schema layer (0052 converts sentinel rows to `deleted_file_log` and deletes the rows), and the scrapheap-flush handler (`_crud.py:~1890`) now logs every deleted picture to the ledger and only protects the *file* on disk for `allow_delete_file=False` folders — file-protection correctly decoupled from row-visibility.
- **Migrations 0052–0055 follow CLAUDE.md.** Descriptive names; strictly increasing linear chain (0051→0052→0053→0054→0055); all `op.add_column` guarded with `sa.inspect`/existing-columns; `__all__` declared; 0054/0055 use `batch_alter_table` (correct for SQLite). 0052's data conversion (sentinel→ledger) is bounded, idempotent (`already_logged` check), correctly drops/recreates the grid indexes that reference `import_excluded`, and documents its non-reversibility honestly. 0053's `buffalo_l` backfill is a targeted NULL-fill (the reprocessing-adjacent pattern), not application logic.
- **`score_character_likeness` (the upload-scoring endpoint) is correctly scoped.** It scopes the *character* (`fetch_scope_allowed_character_ids`, `_crud.py:552`) and `require_user_id`; it takes uploaded images (no stored picture ids), so picture scope does not apply. Decode errors → 400 with `from exc`; detection timeout/failure → 503 with logged context; nothing is persisted. Tested for basic behaviour and 401-on-no-auth.
- **PredicateFilter refactor preserves WHERE semantics.** All 5 original builders' clauses migrated faithfully; the two intentional drifts (missing-parens OR-leak fix at `predicate_filter.py:257,264`; stats zero-threshold branch) are correct improvements. Stack-leader, set/character/project membership, and `apply_deleted_filter=False` composition all verified. Tests are genuinely thorough (28 cases, both directions via `matches()`), modulo the H1 malformed-score gap.
- **External-listener auth gate is fail-closed.** `_external_listener_password_ready` / `_build_electron_configs` refuse the `0.0.0.0` listener and log an error when the owner has no password; desktop seed-session grants only the loopback owner. Dual-listener concurrency (one event loop, external listener `lifespan="off"`, combined signal handler) is sound. Still route the external-exposure + non-Secure-cookie-over-TLS decisions through CSO for the independent sign-off CLAUDE.md requires.
- **user_settings / model columns.** `Face.model_pack` (nullable String), `User.sidebar_docked` (Boolean nullable), `User.sidebar_width` (Integer nullable, clamped 220–300) all match their migrations. No SQL injection (ORM attribute assignment); unknown keys raise a clean `ValueError`.
- **FaceModelRefreshTask BaseTask/finder conformance** is structurally correct: `queue_type=GPU` (serialised), registered in `tasks/__init__.py` / `task_type.py` / `work_planner.py`, `depends_on()==[FACE_EXTRACTION]`, download-failure dict guarded by a lock. The defects are the commit-vs-claim timing (B2), not the wiring.

---

## Summary

| Severity | Count |
|---|---|
| BLOCKER | 2 |
| HIGH | 2 |
| MEDIUM | 6 |
| LOW | 5 |

**Blockers:** B1 (batch character-likeness BOLA + zero tests — must also clear CSO adversarial sign-off), B2 (face-refresh claim-before-commit + silent-swallow infinite-reprocessing).
**ruff check pixlstash:** clean.
**Required before merge:** fix B1 and B2 with both-direction tests; B1's scope fix and tests are non-negotiable per CLAUDE.md §16.1 and must be independently signed off by the CSO. H1 (500→400) and the MEDIUM silent-`except` items should land in the same pass.

---

## Fixes applied (2026-06-13, senior-backend-developer)

All fixes verified with `ruff format` + `ruff check pixlstash` (clean) and the
relevant test modules (see test results at the end).

### B1 — batch character-likeness BOLA — FIXED
- `pixlstash/routes/pictures/_crud.py:1222` `get_pictures_character_likeness_batch` now takes `request: Request`.
- `_crud.py:1273` calls `fetch_scope_allowed_picture_ids(server, request)` (the fail-closed chokepoint helper) immediately after de-duplicating `unique_ids`, BEFORE any DB read. Out-of-scope ids are dropped from the queried set (`scoped_ids`), so they fall through `classify()` to `deny_result` — indistinguishable from a missing/deleted id (no existence or score leak). Unscoped/owner tokens (`None`) keep full access. The filter sits once, upstream of the branch fan-out, covering all return paths. Added the `fetch_scope_allowed_picture_ids` import at `_crud.py:61`.
- Endpoint confirmed NOT in `READ_SAFE_POST_PATHS` (so READ resource tokens are blocked at the middleware) — asserted by a test.
- Tests added in `tests/test_batch_character_likeness_scope.py` (4, all pass): endpoint-not-in-allowlist; READ resource token blocked at middleware (403); scoped token denies out-of-scope ids indistinguishably from missing ids while in-scope ids still classify as eligible (both directions); unscoped/owner token sees all ids.
- **Caveat for CSO sign-off:** the only API-creatable resource token is `scope=READ`, which the middleware already blocks from this POST. The fix brings the endpoint to parity with its guarded siblings (state 1 — calls the chokepoint helper). The genuinely reachable latent vector is the `ALL`+`resource_type` footgun (`token_scope` is `None`, so every BOLA guard — not just this one — is bypassed); that is the codebase-wide issue tracked in backend_architecture §16.2 item 4 and is out of scope for this merge. Flagged here for the independent adversarial sign-off.

### B2 — FaceModelRefreshTask correctness — FIXED
- `pixlstash/tasks/face_model_refresh_task.py` C1: `_run_task` now collects the `submit_task` Futures and blocks on `future.result()` (`:379`) before returning, so the finder's claim outlives the commit. A picture is only counted in `changed_ids` once its write has actually committed.
- C2: `_commit`'s generic `except Exception` no longer runs dead no-op code; it rolls back, logs at `error` with `exc_info`, and **re-raises** (`:553`) so the failure surfaces on the future. `_run_task` logs it and excludes the picture from `changed_ids`; the row stays stale so the finder re-selects it (genuine retry, not silent masking). Removed the now-unused `Picture` import.
- Tests added in `tests/test_insightface_model_pack.py` (3, all pass) using a new `_AsyncFakeDB` shim that runs writes on a background thread (real Future + commit delay): `test_run_task_blocks_until_writes_commit` (the write is committed by the time `run()` returns); `test_commit_failure_surfaces_and_excludes_picture` (a persistent failure → `changed_count==0`, row stays stale); `test_commit_reraises_on_unexpected_error` (unit: `_commit` re-raises).

### H1 — malformed min/max score 500 → 4xx — FIXED
- `pixlstash/utils/query/predicate_filter.py:359` `_int_or_none` now wraps `int()` and raises `HTTPException(status_code=422)` with a clear `"Invalid {name}: must be an integer"` message instead of letting a bare `ValueError` bubble up as a 500. This makes ALL callers fail-closed with a 4xx (better than returning `None`, which would silently broaden results). Added `from fastapi import HTTPException`.
- Test added in `tests/test_predicate_filter.py` (parametrized over `min_score`/`max_score`): malformed value raises `HTTPException` 422 with the field name in the detail.

### H2 / #4 — face-refresh detection now batched — FIXED (recognition batched; detection inherently per-image)
- `face_model_refresh_task.py` split into a load phase (`_load_detection_units`), a single batched detect over the whole picture batch (`_detect_batch:242`, bounded by `_DETECT_BATCH_IMAGES=64`), and a per-frame assembly (`_faces_from_detections` + `_assign_face_indices`). Previously `detect_faces_in_images([img])` was called once per image/frame.
- **Residual / scope note:** `BatchedFaceRunner` runs *detection* one image at a time regardless (RetinaFace is inherently batch=1, and source images are variable-sized); the batching win is the *recognition* (embedding) ONNX call, which it already collects across all aligned crops (normalised to a fixed recogniser input size, so mixed sizes batch cleanly). Feeding the whole batch's frames into one `detect_faces_in_images` call therefore replaces N single-image recognition calls with one batched call — the hot-path win for a full re-embedding sweep. Detection itself is not further batchable without changing the detector, so this is the safe, complete batching improvement for this merge; no per-image-detection residual remains to chase. Load/skip and no-faces-sentinel semantics preserved; a transient load/detect failure omits the picture (stays stale for retry) rather than wiping its rows.

### M1 — sidebar_docked boolean coercion — FIXED
- `pixlstash/utils/service/user_settings_utils.py:362` adds an explicit `sidebar_docked` branch mirroring `apply_tag_filter`/`keep_models_in_memory`: empty/None/"null" → `False`, else `bool(value)`. The value is now always persisted as a real `bool`, never a truthy string.

### M2 — doubled deleted/unimported clause — FIXED
- `pixlstash/db_models/picture.py` removed the redundant inline `only_deleted`/`include_deleted`/`include_unimported` WHERE blocks in `semantic_search` (`:534`) and `find` (`:717`); the `PredicateFilter(...)` call below each is now the single owner (it already received the same flags). Idempotent ANDs, so no row drift — confirmed by the still-passing predicate-filter / stream tests.

### M3 — silent except swallows in ROCm/device path — FIXED
- `pixlstash/startup_checks.py` `_has_onnxruntime_conflict` (`:276`) now logs a warning with the package name before `continue` instead of a bare swallow; the `ort.get_available_providers()` failure (`:336`) now logs a warning with context (explaining the "broken ORT looks like no-CUDA on a CUDA box" hazard) before defaulting to `[]`.

### M4 — PIXLSTASH_DEFAULT_DEVICE validation — FIXED
- `pixlstash/server.py:1864` validates the override against `{"cpu","cuda","gpu","auto"}` (the set `StartupChecks` accepts). A valid value is written through; an invalid value is `logger.warning`-ed and ignored, keeping the configured `default_device`. Tests added in `tests/test_default_device_override.py`: invalid value rejected + logged + config kept; `gpu` accepted.

### M5 — finder migration-reference comment (0052 → 0053) — FIXED
- `pixlstash/tasks/missing_face_model_refresh_finder.py:80` comment corrected to reference the 0053 backfill.

### Not addressed in this pass (out of brief)
- M6 (unindexed stale-pack finder scan), L1–L5 — not in the assigned fix list. M6 in particular is a real steady-state cost and is worth a follow-up (index on `Face.model_pack` or gate the finder when no pack change is pending).

### Test results
- `tests/test_batch_character_likeness_scope.py` — 4 passed
- `tests/test_insightface_model_pack.py` — 22 passed (incl. 3 new B2 tests)
- `tests/test_predicate_filter.py` — 30 passed (incl. new malformed-score test)
- `tests/test_default_device_override.py` — 7 passed (incl. 2 new M4 tests)
- `tests/test_rocm_device_check.py` — 9 passed
- `tests/test_pictures_stream.py` / `tests/test_stats_api.py` — pass (M2 regression check)
- Combined touched-module run: **65 passed**.
- Pre-existing unrelated failures: `tests/test_user_settings_tagger_settings.py` (4 serialize tests) fail on clean `develop` too (an `AttributeError` in serialize logic at `user_settings_utils.py:72`, unrelated to these fixes).
- `ruff format pixlstash` + `ruff check pixlstash`: clean.

# Branch Review: `frontend-fixes`

- **Base:** `main`
- **Reviewed:** 2026-05-27
- **Scope:** full working-tree diff vs `main` (7 commits + 1 uncommitted doc edit), 18 files, +793 / −296
- **Verdict:** ✅ Mergeable. No blockers. One MAJOR correctness item to verify before merge (stack-leader grid filter); the rest are MINOR/NIT.

This branch bundles three logically-separate efforts:

1. **Grid-fetch performance** — frontend (`useGridFetch.js`, `ImageGrid.vue`, `apiClient.js`) + backend (`_listing.py`, `picture.py`, migration `0047`). Loads visible thumbnails first, fixes a double-fetch, and adds stack-leader grid indexes.
2. **Likeness/face-search refactor** — extracts inline DB closures from the two search routes into the new `pixlstash/services/search_query_service.py` (vault-injection pattern), with the guardrail allowlist updated. Behaviour-preserving.
3. **Small frontend bug fixes** — sidebar response hardening, reactive-ordering fixes, search-key fix.

Reviewed against `docs/frontend_architecture.md`, `docs/backend_architecture.md`, `docs/integration_architecture.md`, and the CLAUDE.md alembic/import/class-ordering rules.

---

## Findings

### BLOCKER
None.

### MAJOR

**M1 — Grid stack-leader filter assumes every stack has a `stack_position == 0` member.** `pixlstash/db_models/picture.py:1344` (`find`) and the same filter now in `find_unassigned`:
```python
query = query.where(or_(Picture.stack_id.is_(None), Picture.stack_position == 0))
```
The canonical leader definition, `_get_stack_leader_ids` (`picture.py:697-705`), instead ranks each stack by `ROW_NUMBER() OVER (PARTITION BY stack_id ORDER BY COALESCE(stack_position, 999999) ASC, COALESCE(score,0) DESC, created_at DESC, id ASC)` and takes row 1 — so the true leader may have a **NULL or non-zero** position. The interactive editing paths (`routes/stacks.py:_compact_stack_positions_in_session:149`, `_ensure_stack_positions.update_positions:124`) both renumber `0..N-1`, so UI-edited stacks are fine. **But** the `COALESCE(stack_position, 999999)` in the canonical function is direct evidence the data model permits stacks with *no* position-0 row (e.g. auto-stacking on import, or migrated data). For any such stack, the `== 0` filter matches **zero** rows and the entire stack silently disappears from the grid.

- This filter already exists in `find` on `main`, so the branch does not introduce the risk for the standard grid — but it (a) **extends** the assumption to `find_unassigned` and (b) builds partial indexes (migration `0047`) around the `== 0` definition, cementing it.
- **Action before merge:** confirm whether stacks without a position-0 member are reachable (check the auto-stacking / import path, not just `routes/stacks.py`). If they are, either normalise position-0 on every stack-creating path, or align the grid filter with `_get_stack_leader_ids` (a `ROW_NUMBER() … = 1` subquery / correlated `NOT EXISTS` for "no lower-positioned sibling"). If they are provably not reachable, add a comment at `picture.py:1344` stating the invariant and where it's enforced.

### MINOR

**m1 — `useSearchStore` is missing `isSearchActive` (store ⇄ doc ⇄ component out of sync).** `frontend/src/components/panels/Toolbar.vue:333` reads `searchStore.isSearchActive`, but `frontend/src/stores/useSearchStore.js` returns `searchQuery` only (return object ~line 52) — never `isSearchActive` — so it is `undefined` and the search-toggle active highlight never lights up. `frontend_architecture.md` (≈line 158) lists `isSearchActive` as `useSearchStore` state, so the doc and store disagree. Not a regression (the prior binding was also `undefined`), but the touched line propagates a latent bug.
- **Fix:** add `const isSearchActive = computed(() => !!searchQuery.value?.trim())` to the store and return it (matches the doc), or change the binding to the local pattern already used in this file (lines 64/84/98): `searchStore.searchQuery && searchStore.searchQuery.trim()`.

**m2 — `find_unassigned` stack filter inherits M1.** `picture.py` (`find_unassigned`): newly uses the `stack_id IS NULL OR stack_position == 0` filter. Same root cause as **M1**; called out separately because this is the *new* surface the branch adds the assumption to. Note `find_unassigned` does not constrain `import_excluded`, so the new partial indexes from `0047` (which include `import_excluded = 0`) will **not** be used by this query path — acceptable, since `0047` explicitly targets the standard grid (`find`), but worth knowing for future index work.

### NIT

**n1 — Function-local SQLAlchemy imports in a brand-new file.** `pixlstash/services/search_query_service.py:124,165,194` each do `from sqlalchemy import select as sa_select` inside the function. These were copied verbatim from the original route closures, but per CLAUDE.md local imports are only for circular-dep/startup/optional cases; `sqlalchemy` is a core dependency. Hoist one `from sqlalchemy import select as sa_select` to the top alongside the existing `from sqlmodel import select` (line 12).

**n2 — Misleading early-return comment in migration `0047`.** `pixlstash/migrations/versions/0047_add_stack_leader_grid_indexes.py:72-74` ("Fresh install … Nothing to do") is dead on a real fresh install (the `0001_baseline` `create_all()` makes the `picture` table before `0047` runs, and the indexes are created *by this migration*, not the model). Copied from `0044`, so it is consistent existing convention — flagging only because it can mislead a future reader. No code change required.

**n3 — Debug telemetry shipping in `ImageGrid.vue`.** `frontend/src/components/views/ImageGrid.vue:~3550-3702` adds ~150 lines of telemetry (window globals `__PIXLSTASH_GRID_FETCH_TELEMETRY__`, per-fetch `console.debug`). Correctly bounded (Map cleared on completion, window array trimmed to 400, `typeof window` guarded) so no leak — but consider gating behind `import.meta.env.DEV` so it doesn't ship in production bundles.

**n4 — Off-screen margin prefetch relies on `requestAnimationFrame`.** `frontend/src/composables/useGridFetch.js:~858-872` schedules the deferred margin prefetch via rAF; a backgrounded tab won't fire rAF, delaying off-screen thumbnail requests until the next scroll. Acceptable degradation; a `setTimeout(…, 0)` fallback would make it robust. The stale-request guard inside the callback is correct.

**n5 — Dropped optional chaining in `apiClient.js`.** `frontend/src/utils/apiClient.js:30` changed `import.meta?.env?.VITE_BACKEND_URL` → `import.meta.env.VITE_BACKEND_URL`. Fine under Vite/Vitest; only relevant if a non-Vite tool ever imports this module.

---

## Integration review (`integration_architecture.md`)

- **`apiClient.js` dev-mode change** (`:43-44`) hardcodes the dev backend at `hostname:9537`. This *aligns* with §14 (backend default port 9537, Vite at :5173, CORS regex auto-permits localhost) and `VITE_BACKEND_URL` remains the documented override (§1/§3), so the single-origin contract for production is preserved (prod still derives from `window.location`). Acceptable; just note the hardcoded dev port assumption.
- **Grid-fetch contract coherence:** the frontend split (immediate visible-range fetch + deferred margin fetch) and the backend `grid_lite` listing change are consistent — no new event types, no URL-prefix or payload-shape changes, so no integration contract is touched. The double-fetch fix is dedup'd via `rangeCovers(...)` over disjoint ranges (verified, no duplicate thumbnail POSTs).
- No changes to event wire `type` strings, auth/share-token flow, settings field names, or build output path — no §8/§12/§14 contract impact.

## Conformance checks (pass)

- **Alembic (CLAUDE.md / backend §12):** `0047` chains correctly (`down_revision = 0046_tag_sentinel_rename`, the current head), strictly increasing; `__all__` exports the four revision identifiers; index creation and `downgrade()` drops are both conditional/idempotent (by-name `existing_indexes` check); no application logic. Index columns (`ix_picture_grid_leaders_{score,smart_score,imported_at,created_at}` + partial `WHERE deleted=0 AND import_excluded=0 AND (stack_id IS NULL OR stack_position=0)`) match `find`'s sort keys and predicate.
- **Service / vault-injection (backend §10, guardrail):** `search_query_service.py` takes `db` (`vault.db`) and delegates via `run_immediate_read_task`; both route files no longer match the route guardrail pattern, so they correctly stay off the route allowlist, and the service-allowlist addition at `tests/test_architecture_guardrails.py:115` is the correct, necessary form. (Confirmed this turn: the route guardrail flips red→green; ruff clean; `test_likeness_and_face_search.py` passes.)
- **Behaviour-preserving extraction:** all extracted functions are 1:1 with the removed closures — `deleted_only=False`, `HTTPException(400)` on bad `project_id`, embedding normalisation, and the `deleted/image_embedding/features` filters all preserved. No filters dropped.

## Positives

- **`find_unassigned` change incidentally fixes a real count/listing mismatch:** on `main`, with `stack_leaders_only=True` + `count_only=True`, the count returned *before* the Python-side dedup, over-reporting stacked pictures vs the deduped listing. Moving the filter into SQL (before the `count_only` return) makes count and listing agree.
- **`useGridFetch.js` cancellation is solid:** every network `await` is followed by a `lastRequestId !== requestId` guard (including inside the rAF callback and the background loop); the tail-batch promise has `.catch(() => null)`; telemetry is finalised in `finally` (no Map leak).
- **`_listing.py:98`** is a clean behaviour-identical micro-optimization (one `safe_model_dict(pic)` per picture instead of per-field).
- **Reactive-ordering fixes** in `StatsSidebar.vue` (refs moved above the `{ immediate: true }` watch — fixes a real TDZ `ReferenceError`) and `OverlayMetadataPanel.vue`, plus adding `applyTagFilter` to `buildGridFetchKey` (`useGridFetch.js:137`) are genuine, correct fixes.

---

## Recommendation

Merge after resolving **M1** (verify stack-without-position-0 reachability and either fix the filter or document the invariant). **m1** (`isSearchActive`) is a cheap, worthwhile fix that also re-syncs the store with the architecture doc. The NITs can be follow-ups.

# `docs` branch — open follow-ups to file as GitHub issues

Consolidated from the four review passes on this branch (the snapshot /
restore / API-docs / undo-removal work, `v1.4.1..HEAD`). Everything that was
a 1.5.0 **must-fix or should-fix is resolved** — see [Resolved](#resolved-do-not-re-file)
at the bottom so nothing here gets re-filed. The items below are the
**still-open, non-blocking** findings, written so each can be pasted into a
GitHub issue verbatim.

Verified open at `c00fd653` (1.5.0.dev0). Nothing here is a data-loss or
release-blocking defect.

Suggested labels in parentheses.

---

## Tests (highest-value gap)

### 1. Dependency-aware restore is tested for characters only `(tests, restore)`
`_find_missing_parent_ids` / `_restore_parent_rows` / `_collect_batch_candidate_parents`
([restore_service.py](../../pixlstash/services/restore_service.py)) handle
`picture_sets` and `projects`, but `test_restore.py` only exercises the
`characters` (`Face.character_id`) path. Add a missing-`set_id`
(`PictureSetMember`) case and a missing-`project_id` (`PictureProjectMember`)
case, for both `restore_resource` and `restore_batch` (un-confirmed → 409,
confirmed → parents restored first).

### 2. Missing-file ratio refusal (A3) has zero tests `(tests, restore)`
Nothing trips the `>50%` / `≥10`-picture mount-failure guard in
`restore_full` ([restore_service.py:439-452](../../pixlstash/services/restore_service.py)),
nor proves a small legitimate snapshot still cleans up missing-file rows. Add
both: (a) ≥10 pictures, >50% missing, no opt-in → refuses; (b) opt-in →
proceeds; (c) <10 pictures all missing → still cleans up.

### 3. `RESTORE_FAILED` / no-dangling-`RESTORE_STARTED` is unverified `(tests, restore)`
The lifecycle ordering is correct in source (STARTED emitted only after the
lock is held and the snapshot file is confirmed; every error path emits a
terminal FAILED) but no test asserts it. Add: a 404/409/412 emits **no**
`RESTORE_STARTED`; a mid-restore failure emits a terminal `RESTORE_FAILED`
across all three restore methods.

### 4. Restore-with-live-workers is never exercised `(tests, restore)`
All snapshot/restore tests run `disable_background_workers=True`. The
production path — swap while the `TaskRunner` is live — has no coverage.
Pairs with issue #6.

### 5. Snapshot-route scope test covers only one endpoint `(tests, auth)`
`test_picture_scoped_all_token_rejected_on_snapshot_routes`
([test_snapshots_auth.py](../../tests/test_snapshots_auth.py)) only checks
`GET /snapshots`. Parametrize across `list`, `status`,
`preview_full_restore`, `preview_resource_restore`.

### 6. `test_openapi_response_schemas.py` doesn't validate per-route shape `(tests, api)`
It smoke-checks that responses have *a* schema. A `response_model` swap from
`SnapshotResponse` to `dict` would still pass. Add per-route shape assertions
for the snapshot routes at minimum.

### 7. Daily-snapshot background task is untested `(tests, snapshot)`
No test that it runs, that it's idempotent within a day, or that it isolates
failures.

### 8. `_upgrade_snapshot_schema` is only tested `0048 → 0049`, one column `(tests, restore)`
[test_restore.py](../../tests/test_restore.py) exercises a single-column
upgrade; multi-step migrations and data-shape upgrades on restore of an older
snapshot are unverified.

---

## Restore / snapshot robustness `(restore, backlog)`

### 9. In-flight TaskRunner tasks aren't drained before the swap
The swap cancels only *pending* tasks
([restore_service.py:471](../../pixlstash/services/restore_service.py)); a task
already executing can enqueue a write that lands after the swap. Narrow window
(the single-threaded writer serializes the swap control-task), but real.

### 10. `compare_hashes` writes outside `_restore_lock`
A UI hash-compare (fired on context-menu hover) backfills `metadata_hash` in
the live DB via `run_task` and can rewrite pre-`metadata_hash` snapshot files
in place ([restore_service.py:1066-1133](../../pixlstash/services/restore_service.py)).
Benign today (writer queue serializes; new snapshots already have the column)
but it's a write on a read-shaped path that isn't under the restore lock.

### 11. Batch parent-restore and child-upsert run in separate transactions
`restore_batch` restores union-parents in one writer task then each item in
its own; a mid-batch item failure can leave restored parent rows with no
children. Per-item failures are already collected into `report.errors`, so
this is cosmetic-consistency, not data loss.

### 12. `restore_resource` leaks its `mkdtemp` dir
The `finally` removes only the `.sqlite`
([restore_service.py:687](../../pixlstash/services/restore_service.py),
[:1038](../../pixlstash/services/restore_service.py)), not the parent temp dir
— unlike `restore_batch` / `preview_*`, which `rmtree` correctly.

### 13. `snapshot_if_due` is dead code
Zero production callers (only referenced by a comment and tests). Either wire
it into a finder (so opportunistic/periodic snapshots actually happen) or
delete it so the next reader doesn't assume they do.

### 14. Migration 0049 unconditionally runs `UPDATE picture SET metadata_hash = NULL`
([0049_snapshots.py:66](../../pixlstash/migrations/versions/0049_snapshots.py))
runs even when the column was just created (all-NULL already) — a pointless
full-table write on every migrate. Gate it inside the `if not in existing_cols`
branch.

### 15. `_vacuum_into` runs while `session.connection()` is open
([snapshot_service.py](../../pixlstash/services/snapshot_service.py)) Works
today because the writer thread's session isn't in a transaction at the call
site, but a future `before_flush` hook could trip SQLite's "no VACUUM inside a
transaction" rule. Run VACUUM on a dedicated raw connection.

### 16. `Snapshot.created_at` is a naive `DateTime`
([snapshot.py:30-31](../../pixlstash/db_models/snapshot.py)) while the service
writes tz-aware UTC — a latent aware/naive arithmetic trap. Use
`DateTime(timezone=True)`.

### 17. `restore_service.py` is ~2,450 lines
Three preview methods, three restore methods, the swap path, the upgrade path,
the hash-compare path, the cleanup path, the dependency machinery, the
locking. Split into a package (`restore_service/{public, swap, upgrade,
preview, hash, deps}.py`) before the next significant change to this area.

### 18. (Optional) Strip more regenerable data from snapshots if size still hurts
1.5.0 strips Picture embeddings/scores and the likeness tables
(`picturelikeness` / `picturelikenessqueue` / `picturelikenessfrontier`) from
snapshots, with the live likeness queue + frontier captured and replayed
across a full restore. Deliberately **kept**: `Face.features`,
`tag_prediction`, `quality`. If snapshots are still too large, those are the
next dials — but each needs the same "regenerated after restore" verification
the embeddings got.

---

## API / OpenAPI `(api, backlog)`

### 19. `response_model` + `ConfigDict(extra="allow")` everywhere = "docs, not contract"
Every `*Response` allows extra keys, so the response schema can silently drift
from the handler (a dropped key still serialises; no validation 500). This is
a defensible deliberate choice — just make it consciously and note it once in
`backend_architecture.md`, or tighten the snapshot/restore responses that are
load-bearing for the UI.

### 20. `_install_custom_openapi` has no guard against an early `app.openapi()` cache
([server.py](../../pixlstash/server.py)) monkey-patches `app.openapi`; currently
safe by call order, but a defensive `self.api.openapi_schema = None` at the top
of `custom_openapi` would prevent a stale-schema cache if something calls
`app.openapi()` before all routers are mounted.

### 21. OpenAPI post-processors are untested
`_strip_query_param_defaults` / `_inject_response_examples` /
`_inject_path_param_examples` ([server.py](../../pixlstash/server.py)) silently
no-op if a future FastAPI version changes where `default` lives (e.g. moves it
into `anyOf`). One assertion per post-processor catches the drift.

### 22. `generate_openapi_docs.py` is not hermetic
`from pixlstash.server import Server` triggers
`pillow_heif.register_heif_opener()` and the full vault/tagger import graph.
Doc generation shouldn't need the ML stack imported.

---

## Frontend / UX `(frontend, backlog)`

### 23. `MissingDependenciesError` surfaces a scary "Restore failed" toast
The expected "needs confirmation" first pass (un-confirmed restore with
deleted parents) emits a terminal `RESTORE_FAILED`, which `onRestoreFailed`
turns into `error.value = "Restore failed: …"`
([useSnapshotsStore.js:159](../../frontend/src/stores/useSnapshotsStore.js)).
Functionally fine (it clears `activeJob`) but it reads as an error for what is
really a confirmation prompt. Suppress/relabel for the missing-dependencies
case.

### 24. `useSnapshotsStore` mixes error patterns
`createSnapshot` / `renameSnapshot` / `deleteSnapshot` throw; `fetchStatus` /
`fetchSnapshotSettings` swallow into `console.warn`. Pick one and document the
convention in `docs/frontend_architecture.md`.

### 25. WS handlers re-fetch instead of using their payload
`onSnapshotCreated(payload)`
([useSnapshotsStore.js:117](../../frontend/src/stores/useSnapshotsStore.js))
ignores the payload and re-fetches the whole list; `onRestoreStarted`
synthesises `activeJob` client-side (wrong when two clients restore at once).
Trust the server payload / `fetchStatus`.

### 26. Context-menu hash-compare fan-out on hover
`ImageGridContextMenu`
([ImageGridContextMenu.vue:427](../../frontend/src/components/widgets/ImageGridContextMenu.vue))
fires a hash-compare per recent snapshot on every submenu hover, even when the
selection isn't in any snapshot. Gate on "selection intersects a snapshot's
picture set", or cache. Also: `identicalSnapshotIds` isn't cleared when the
menu closes — watch `props.visible` and reset.

### 27. `RestoreConfirmDialog` dismisses on outside-click
`@click:outside="dialogOpen = false"` on a destructive-action dialog — a
misclick dismisses the preview. Use `persistent`.

### 28. `RestoreConfirmDialog` a11y
No `aria-label` on the X button; the destructive Restore button has no
`aria-describedby` pointing at the preview summary; focus doesn't land on
Cancel.

### 29. `SnapshotsSection.vue` polish
Uses `window.confirm()` for delete (inconsistent with the rest of the snapshot
UI, hostile on mobile); label save fires on both `@keydown.enter` and `@blur`
(defensive bail-out around a structural double-fire — use `@change`).

### 30. Duplicated `humanBytes` / `summaryLabel`
Re-implemented inline in `SnapshotsSection` and `RestoreConfirmDialog` despite
`utils/snapshots.js` existing precisely for shared formatters. Centralise.

---

## Cleanup / cosmetic `(cleanup)`

### 31. Function-local imports in `routes/config.py`
`import json as _json` ([config.py:304](../../pixlstash/routes/config.py)) and
`from io import BytesIO` ([config.py:435](../../pixlstash/routes/config.py)) —
hoist to module top per the imports convention.

### 32. `/api/v1/scalar` is auth-bypassed
`auth.py` adds `/scalar` to `AUTH_EXCLUDED_PATHS`; the matcher also strips the
`/api/v1` prefix, so `/api/v1/scalar` is excluded too. No route there today —
latent landmine if one is ever added. Tighten the matcher.

### 33. "Snapshot snapshot" docstring typos
Leftover from the Checkpoint→Snapshot sed-rename, in
`restore_service.py` and `routes/snapshots.py`. Proofread.

---

## Future: re-introducing undo/redo `(feature, undo)`

Undo/ChangeLog was removed from the backend for 1.5.0 (too fragile to ship).
When it comes back, **restart from a clean design** rather than reviving the
deleted code — it had real correctness gaps:

- **UPDATE-undo silently dropped BLOB columns** (`Face.features`,
  `Picture.text_embedding` / `image_embedding`). The serializer wrote
  `"sha256:…"` markers; on undo `should_set=False` → column omitted → the new
  (post-change) value stayed in place and undo reported success. DELETE-undo at
  least warned; UPDATE-undo was silent. Whatever replaces it must surface both
  and ideally enqueue reprocessing.
- **No ChangeLog atomicity test** — the audit-row-write-fails-→-roll-back-user-write
  contract was never pinned.
- **No undo-conflict test** — "row modified by an unrelated txn after the
  snapshot, then undo back to it" was unspecified.
- **Thin column-type matrix** — only TEXT + the BLOB warning were exercised;
  enum / FK / nullable→non-nullable / boolean had zero coverage.
- **UX wishlist:** CTRL-Z / CTRL-Y, undo+redo in context + selection menus,
  tooltips showing what will be undone/redone, a transient pill at the bottom
  of the grid announcing each undo/redo.

---

## Resolved — do not re-file

Shipped in 1.5.0; listed so these aren't re-raised:

- **A1** safety-snapshot failure now aborts with `SafetySnapshotFailedError`
  (→ 412) unless `allow_without_safety`.
- **A2** dependency-aware resource/batch restore — missing parents → 409
  `{"code":"missing_dependencies"}` un-confirmed, or restored-first when
  confirmed; FK-safe delete-then-insert children.
- **A3** transient-missing-files guard (mount-down refusal + >50%/≥10 ratio).
- **A4** `RESTORE_FAILED` event + no-dangling-`RESTORE_STARTED` ordering;
  frontend `onRestoreFailed` recovers without reload.
- **A5** full-vault restore gated behind an explicit acknowledgement checkbox.
- **B1** migration 0049 no longer creates the orphaned `changelog` table.
- **B2** GFS docs corrected (DAILY/OPPORTUNISTIC/MANUAL only; WEEKLY/MONTHLY
  disclaimed as reserved).
- **metadata_hash/Face** — face state folded into the digest; iterates
  `column_attrs` only (no relationship lazy-load).
- **Undo removal** — `UndoService` / `change_log` / flush hooks / `write_reason`
  / dead `undo_applied` handler all gone.
- **Non-atomic config writes** + **credential-reset persistence** — fixed via
  `utils/atomic_write.py` (staged write → fsync → `os.replace`), applied to
  `config.py` / `server.py` / `auth.py`.
- **Snapshot size** — embeddings/scores + likeness tables stripped from
  snapshots; live likeness queue/frontier preserved across restore. One-shot
  `scripts/strip_snapshot_blobs.py` reclaims existing snapshots.
- **Ownership lockdown** — all `/server-config/*` + snapshot routes require
  `require_unscoped_owner`; `PATCH /users/me/config` hardened with a
  field allowlist.
- **`restore_full` resilience** — planner stop/start in try/finally; bulk
  Core UPDATE in cleanup; `_swap_database` fsyncs staged file + parent dir.
- **Real concurrent-restore test** (two threads, one wins / one 409).
- **Migration-from-real-v1.4.1 test** with data-preservation assertions.
- **Scalar CDN** pinned to `@scalar/api-reference@1.32`.
- **Version** bumped to `1.5.0.dev0`.
- **Project-level per-resource restore** is intentionally excluded with a
  documented rationale (graph spans `ProjectAttachment` / `Character.project_id`
  / `PictureSet.project_id` / `PictureProjectMember`); not a "dead branch".

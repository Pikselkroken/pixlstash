# Code Review — PR #360: Snapshots / Restore / Undo

Branch: `docs` → `main`

## Overview

A large feature PR (~9,800 insertions, 81 files) adding a **snapshot → restore →
undo** system on top of the existing `ChangeLog` audit trail, plus substantial
API-documentation work (Scalar docs, screenshots, website). The architecture is
sound and conventions are mostly well-followed:

- New `snapshot` / `changelog` tables, migration `0049`, correctly classified as
  `CHANGE_LOG_EXCLUDED`.
- Three new services: `snapshot_service` (creation + GFS retention),
  `restore_service` (file-swap full restore + per-resource upsert restore),
  `undo_service` (ChangeLog-based reversal).
- A Pinia store + Vue UI with a dry-run-preview confirmation flow for the
  destructive restore.
- Daily-snapshot background task wired through `WorkPlanner`/`TaskRunner`.

The migration conventions, table classification, and the daily-snapshot
scheduling are **correct and verified**. The concerns below are concentrated in
restore/undo correctness, concurrency safety, and an access-control gap on the
new GET endpoints.

---

## 🔴 Critical

**C1 — Restore-preview GET endpoints leak the whole DB to scope-limited READ/share tokens**
(`routes/snapshots.py:300-369`, also `list_snapshots`/`status` at `175-207`)
`preview_full_restore`, `preview_resource_restore`, `list_snapshots`, and
`snapshots_status` never call `require_user_id` and perform **no scope
enforcement**. A READ-scoped share token (`?token=`) — meant to see a single
picture — can read a full-database diff (`changed_fields`, `dependent_counts`)
across every user's data. Every other read route honors scope
(`import_folders.py:105`, `filesystem.py:83`, `config.py:564`). Restore is an
owner-only system op; its previews must be too.
```python
if getattr(request.state, "token_scope", None) is not None:
    raise HTTPException(status_code=403, detail="Not allowed for scoped tokens")
```

**C2 — No mutual exclusion between concurrent full restores → live-DB corruption**
(`restore_service.py` `restore_full`, ~170-187)
`_active_job` is set/cleared but only *read* by the `/status` endpoint — it is
**not a guard**. Two concurrent `POST /snapshots/{id}/restore` calls both stop
the planner, take safety snapshots, and enqueue `_do_swap` +
`_post_restore_cleanup`. The two file swaps and cleanups interleave on the writer
thread, and one restore's cleanup can run against the other's swapped file —
deleting rows based on a stale `missing_ids` set. Add a non-blocking
`threading.Lock` at the top of `restore_full`/`restore_batch`/`restore_resource`;
reject with 409 if held.

**C3 — Engine dispose/recreate happens *inside* the worker's open `Session` block**
(`database.py` `_task_worker_loop` ~1050 + `restore_service.py` `_do_swap` ~1089-1108)
The worker runs each task within `with Session(self._engine) as session:`,
capturing the old engine; `_do_swap` then `dispose()`s and reassigns
`db._engine`. On block exit, `session.close()` / pool return — and the attached
`after_flush` ChangeLog hook if anything flushes — operate against the disposed
engine. `_do_swap` closing the session first mitigates the happy path, but this
is structurally fragile. The teardown/rebuild should happen as a dedicated
control op *outside* any open per-task session.

> Note: C2/C3 are derived from reading the `restore_service` internals —
> high-confidence but worth confirming against the actual swap implementation
> before merge, since they're the most consequential.

---

## 🟠 High

**H1 — `undo_to_snapshot` can silently produce a partial rewind**
(`undo_service.py` + `snapshot_service.py` GFS prune)
GFS retention truncates `ChangeLog.id < oldest_snapshot.max_changelog_id`.
Undoing to a target that isn't the oldest retained snapshot may find the needed
ChangeLog range already truncated; the `WHERE id > max_changelog_id` query then
reverses only the surviving subset and reports success. Before a ChangeLog undo,
verify `min(ChangeLog.id) <= target.max_changelog_id + 1`; otherwise refuse and
escalate to the file-based `restore_full`.

**H2 — DELETE-undo drops BLOB columns, reported as success**
(`undo_service.py` `_reverse_entry` / `_coerce_serialized_value`)
BLOBs are serialized as `"sha256:…"` markers. On undo of a DELETE,
`_coerce_serialized_value` returns `(False, None)`, so columns like
`Face.features`, `Picture.text_embedding/image_embedding` are omitted — the row
is re-created with NULL binaries while `reverted_row_count` increments. Silently
lossy. At minimum log per-column and surface in `UndoReport.errors`; ideally
trigger reprocessing for the affected rows.

**H3 — Per-resource restore doesn't remove live-only dependents / risks PK collisions**
(`restore_service.py` `_upsert_rows` ~1288-1352)
Tags are correctly delete-then-reinsert, but Faces and set/project members are
`merge`d by snapshot PK. Two problems: (1) faces present in *live but not the
snapshot* are never deleted, so the picture isn't actually reverted to snapshot
state; (2) `merge` by a snapshot `Face.id` can land on an unrelated live face
that reused that id. Mirror the Tag pattern (delete dependents for the restored
picture ids, then insert).

**H4 — Missing-file picture deletion may fail or orphan on non-cascading FKs**
(`restore_service.py` `_post_restore_cleanup` ~268)
`session.delete(pic)` for missing-file pictures assumes cascade to
Face/Tag/members. The code special-cases Tags elsewhere precisely because
cascades aren't uniform — so this delete may roll back the whole cleanup task,
silently leaving the missing-file rows. Verify cascade behavior or delete
dependents in FK-safe order first.

---

## 🟡 Medium

- **M1 — ChangeLog INSERT failure is swallowed with a warning** (`database.py`
  `_after_flush_handler`). The user's write commits but isn't logged, so undo
  silently skips it. Per CLAUDE.md "avoid silent failures," a failed audit write
  should fail the transaction, since the trail is load-bearing for undo
  correctness.
- **M2 — In-place-mutated JSON columns may record a wrong "before" state**
  (`database.py` `_cl_before_state_from_history`). Confirm all JSON/array columns
  use `MutableList`/`MutableDict` or are excluded; otherwise UPDATE-undo restores
  the current (wrong) value.
- **M3 — Snapshot-file backfill (`compare_hashes`/`_fill_snapshot_hashes_at`)
  mutates the snapshot file under no lock** (`restore_service.py`). Concurrent
  preview/compare calls, or a restore reading the file while a backfill rewrites
  it, race on disk. Add a per-path lock.
- **M4 — `hash_compare` lacks an `except Exception` branch**
  (`routes/snapshots.py:494-519`) — only catches `ValueError`, so other errors
  return an uncontextualized 500. Mirror the sibling routes' logging.
- **M5 (frontend) — `App.vue:1660` `fetchSnapshots()` runs unconditionally on
  mount**, including READ/share sessions that 403. Gate on `!isReadOnly`.

---

## Test coverage

The happy-path tests are real assertions, not smoke tests (ChangeLog payloads,
undo per op-type, full restore revert / missing-file drop / dry-run). But notable
gaps:

- **High — the dual-list guardrail test CLAUDE.md names
  (`test_change_log_dual_list_covers_all_tables`) does not exist.** The PR adds
  two tables and a whole `test_change_log.py` without the documented regression
  guard that would catch a future unclassified `table=True`. Add the dynamic scan
  over `SQLModel.metadata.tables`.
- **Medium — `_upgrade_snapshot_schema` (restoring an older-schema snapshot) is
  never tested** — every test snapshots at current head, so the
  alembic-upgrade-on-restore path is a no-op `0049→0049`. This is the most
  fragile part of the feature.
- **Medium — per-resource/batch restore of pictures *with* faces/tags/membership,
  and of `picture_set`/`project`/`character` resource types, has zero coverage** —
  exactly the FK-ordering logic flagged in H3.
- **Low** — `test_snapshots.py` docstring claims GFS-retention coverage that
  isn't present; no concurrent-restore-vs-writer test despite the real
  concurrency machinery.

---

## Frontend (well-structured; minor issues)

The destructive-restore guard is **adequate**: `RestoreConfirmDialog` always
fetches a server dry-run preview (red-flagging deletions/missing files) before
enabling an `error`-colored Restore button. Store/`<script setup>`/`apiClient`
conventions are clean. Worth fixing:

- **High — context menu stays open after launching restore**
  (`ImageGridContextMenu.vue:454-468`): `handleRestoreFromSnapshot`/
  `handleRestoreMore` never `emit("close")`, unlike every other action. Add the
  emit.
- **High — stale-closure race in the hash-compare watcher**
  (`ImageGridContextMenu.vue:423-446`): rapid submenu toggles fire overlapping
  batches that both write `identicalSnapshotIds`; late responses of a superseded
  run land after a reset, corrupting enable/disable state. Capture a run token
  and bail on stale apply (the `_createSuccessToken` pattern already used in
  `SnapshotsSection.vue:88`).
- **Low** — `relativeDate()`/`kindChipColor()` duplicated across two components;
  `ImageGridContextMenu.vue:230` blindly appends `"Z"` and yields `Invalid Date`
  if the backend sends an offset-suffixed timestamp.

---

## Recommendation

Solid, ambitious feature with a clean architecture and good happy-path tests.
**Address before merge:** C1 (scope leak — quick fix), and the restore
concurrency/safety items C2/C3/H4 since they risk live-DB corruption. H1/H2
(silent partial/lossy undo) and the missing guardrail + schema-upgrade tests
should also land before this ships, given that the whole point of the feature is
data-recovery correctness.

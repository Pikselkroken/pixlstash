# Review — `checkpoints` branch (snapshot / restore / undo)

**Scope:** `git diff main...checkpoints` — 36 files, ~6,800 insertions. Introduces a vault
snapshot system (`VACUUM INTO` + JSON manifest, GFS retention), a `ChangeLog` audit trail
captured via SQLAlchemy flush hooks, full/resource/batch restore with a live-DB file swap,
undo (last-txn and undo-to-snapshot), a `metadata_hash` identity column, REST routes, and a
Vue settings panel + restore-confirm dialog.

Reviewed: change-log machinery (`database.py`), `snapshot_service.py`, `undo_service.py`,
`restore_service.py` (DB swap + restore paths), `server.py` WS broadcast, frontend
(`App.vue`, `SnapshotsSection.vue`, `RestoreConfirmDialog.vue`, `useSnapshotsStore.js`,
`ImageGridContextMenu.vue`), migration `0049`, scripts, and tests.

## Verdict

Solid architecture — the change-log row is inserted inside the same flush/transaction as the
user write (atomic), full restore takes a safety snapshot first, `restore_resource` upserts via
the ORM so hooks fire, and migration `0049` is fully CLAUDE.md-compliant. The original blocking
`ctx`-crash on mount and the undo flush-hook bug are now fixed (undo reverse-applies through the
ORM, so it is change-logged and recomputes `metadata_hash`). The remaining should-fix correctness
issues cluster around WS delivery, the non-atomic file swap, and `undo_to_snapshot` atomicity. Not
ready to merge until those are addressed.

---

## Nice-to-have

- **`tests/test_architecture_guardrails.py`** — `search_query_service.py` is added to the allowlist
  twice (already present; harmless in a set). Drop the duplicate.
- **`restore_service.py:942, 1008`** — `PRAGMA wal_snapshot(TRUNCATE)` is not a real pragma (it's
  `wal_checkpoint`); SQLite silently ignores it. The following `PRAGMA journal_mode=DELETE` does the
  real WAL flush, so behaviour is fine, but the dead pragma is misleading.
- **`snapshot_service.py:335`** — `_vacuum_into` builds `f"VACUUM INTO '{abs_snapshot}'"`. VACUUM INTO
  can't use bound params, but a single quote in the vault path would break it. Path isn't user-controlled
  (low risk); escape by doubling single quotes for robustness.
- **`scripts/backfill_snapshot_hashes.py:41`** — passes `reset_all=True` while the docstring says it only
  fills pictures without a hash; reconcile. Also the `Vault` is never closed — wrap in `try/finally:
  vault.close()`.
- **Dead frontend code:** `wsSnapshotEvent` / `wsRestoreEvent` (`useWsStore.js`) are written but never
  read; `previewResourceRestore` (`useSnapshotsStore.js`) is exported but never called; the
  `restore-from-snapshot` emit in `ImageGridContextMenu.vue` has no listener in `ImageGrid.vue`.
- **URL-prefix inconsistency:** the store uses `/api/v1/snapshots…` while `ImageGridContextMenu.vue:442`
  uses bare `/snapshots/{id}/hash-compare`. Both work via the interceptor; pick one style.
- **`SnapshotsSection.vue`** — `humanBytes` caps at GB (multi-TB shows huge GB values); success-toast
  reset compares against the literal `"Snapshot created."` string (fragile — use a timer/token ref).
- **`database.py:336`** — `_compute_picture_metadata_hash` iterates `pic.__fields__` (Pydantic-v2
  deprecated alias; use `model_fields`) and could touch relationship attributes — verify only columns
  are hashed.
- **Missing test coverage:** `_upgrade_snapshot_schema` (restoring a pre-`metadata_hash` snapshot) and an
  undo-conflict case (same row modified post-snapshot by an unrelated txn) are untested. The happy paths,
  missing-file-drop, and invalid-id paths are well covered with real data-integrity assertions.
- **Actual UI support for undo:** support for CTRL-Z undo and CTRL-Y redo. Add Undo and Redo entries in the context menu and selection menu.
Undo should be available as long as there are changes since the last snapshot. Redo should be available if we've undone anything. The feature should be entirely linear so you have to go one step at a time backwards and forwards. A tooltip on the undo and redo saying what we will undo and redo would be good. Also a little transient and semi-transparent pill in primary color at the bottom of the image grid saying what we have just undone or redone.

---

## What's good

- Change-log rows are inserted inside the same flush as the user write
  (`database.py:_after_flush_handler`), so the audit trail is atomic with the data — and so is the
  `metadata_hash` Core-UPDATE in `_after_flush_hash_updater`.
- Per-task `contextvars.copy_context()` cleanly carries `write_reason`/`actor_user_id` from the caller
  into the single-threaded writer.
- Migration `0049_snapshots.py` is fully CLAUDE.md-compliant: conditional `add_column` via `sa.inspect`,
  guarded `create_table`, `__all__` export, correct `down_revision` chain off `0048`, conditional
  downgrade, and only the permitted NULL-reset (`metadata_hash`) — no app logic.
- Full restore takes a safety snapshot first, pauses the planner, and routes the swap through the writer
  queue; `restore_resource`/`restore_batch` upsert via the ORM so hooks fire correctly.
- The change-log dual-list guardrail is in place and `RestoreConfirmDialog.vue` follows frontend
  conventions (`open: Boolean`, `watch(() => props.open)`, kebab-case emits, destructive action gated
  behind a server dry-run preview and `!isReadOnly`).

# Review: `snapshot-compression`

Scope: the four snapshot commits since `origin/main` (`800148e544`):

- `56a7949` Keep newer snapshots in the index when restoring
- `11db4e7` Close snapshot confirmation dialog so we can see progress
- `cea1fd29` Perform compression of snapshots
- `26d6c462` Fix restore previews

(`df51685` "Add some snapshots to the test data" excluded — pure fixtures.)

Verified: `ruff check` clean on the changed backend files; `pytest tests/test_snapshot_compression.py tests/test_snapshots.py tests/test_restore.py` → 59 passed. All read/restore/preview paths correctly route through `_upgrade_snapshot_schema` → `materialize_snapshot` (decompress); the orphan-file scanner ignores the whole `snapshots/` dir so the new `.sqlite.zst` / `.hashes.json` are never flagged.

## Overall

Solid, well-tested work. `snapshot_compression.py` is a clean single source of truth for the on-disk format, compression/decompression stream (no full-buffer), the legacy `.sqlite` path is preserved and tested, the hash sidecar keeps the list endpoint lean, the snapshot-index re-insertion correctly preserves roll-forward, and the preview rewrite is a real precision improvement (the old preview counted *every* present picture as "to revert"). Docs were updated consistently. The items below are what I'd want resolved or consciously accepted before 1.5.0.

---

## High — address before 1.5.0

### 1. Scratch DBs (full, embedding-laden) are written to the system `/tmp`
`SnapshotService._create_and_record` (`pixlstash_snapshot_`) and `RestoreService._upgrade_snapshot_schema` (`pixlstash_restore_`) both call `tempfile.mkdtemp()` with no `dir=`, so the scratch `.sqlite` lands in `TMPDIR`/`/tmp`. Now that embeddings are **retained**, that scratch file is the full vault DB — potentially many GB on exactly the embedding-heavy vaults this feature targets. If `/tmp` is tmpfs (RAM) or a small root partition, snapshot creation and *every* restore/preview can fail with `ENOSPC` or balloon RAM, while the vault disk has plenty of room.

Fix: route both `mkdtemp` calls to a dir on the vault filesystem (e.g. under `image_root`, a `snapshots/.tmp`), or make it configurable. Cheap change, removes a real footgun.

### 2. Snapshot disk footprint grows substantially — make it a conscious decision
`snapshot_compression.py` itself notes float32 embeddings are "close to incompressible — the win is structural." So on embedding-dominated vaults, zstd barely shrinks the dominant bytes and each snapshot now stores ~the full DB, reversing the previous strip-on-create optimization. Multiplied by GFS retention (daily + weekly + monthly + manual), total snapshot disk can grow by an order of magnitude vs. the prior design.

This is a legitimate trade (disk vs. avoiding a full GPU re-embed on restore), but it should be an explicit 1.5.0 decision and documented (expected sizes; consider a config toggle to strip embeddings for users who prefer the old behaviour). It compounds #1.

---

## Medium

### 3. Preview / `compare_hashes` can under-report set & project membership changes
`metadata_hash` (`database.py:_compute_picture_metadata_hash`) covers Picture columns + tags + faces, but **not** `PictureSetMember` / `PictureProjectMember`. A full restore is a file swap, so it *does* revert memberships — but a picture whose only post-snapshot change is set/project membership hashes identically and is classified `unchanged`, so it never appears in the preview and isn't counted.

For a commit titled "Fix restore previews — now show the actual changes," under-reporting (telling the user nothing changes when the restore will move pictures between sets/projects) is the unsafe direction. Not a regression — the old preview didn't compare memberships either — but it's now formalized in the hash. Either fold membership into the hash, or add a known-limitation note. (Same blind spot affects the per-resource "identical" graying-out in the UI.)

### 4. Test-data / tracked snapshots dir hygiene
`test-data/images/snapshots/` is tracked (not gitignored). The running dev server actively creates `.sqlite.zst` + `.hashes.json` there and GFS-prunes/deletes the committed `.sqlite` fixtures — that's the `D` + `??` churn in `git status`. Some untracked snapshots currently in the tree are in a **stale intermediate format**: the manifest embeds `picture_hashes` inline with **no** `.hashes.json` sidecar — which the final code and a test (`"Manifest must not embed the hash map"`) explicitly reject. A compressed snapshot with no sidecar hits the degraded `compare_hashes` path ("no hash map → treat all as changed").

Before 1.5.0: decide the committed fixture set (regenerate cleanly in the final format, with sidecars) and gitignore the live snapshots dir so the dev server stops dirtying the tree and deleting tracked fixtures.

---

## Low / nits

5. **Resource restore leaks an empty temp dir.** `restore_resource` does `os.remove(upgraded_snapshot)` (file only), leaving the `mkdtemp` parent behind; `preview_resource` and the dry-run full restore correctly `shutil.rmtree(os.path.dirname(...))`. Pre-existing, trivially fixable for consistency.

6. **Embeddings persist across model changes.** Restore no longer NULL-resets embeddings and the WorkPlanner only regenerates NULLs, so restoring an old snapshot reinstates old-model embeddings that won't be refreshed even if CLIP/InsightFace has since changed. No model-version guard. Edge case — worth a note now that embeddings survive restore.

7. **Legacy full-preview is coarser.** A legacy (pre-sidecar) snapshot's `metadata_hash` is NULL after the alembic upgrade (column added, not populated) and `_compute_full_preview` doesn't backfill it, so every picture in a legacy snapshot lists as "changed." Conservative/safe, just less precise than the old column diff.

8. **`import shutil` inside `materialize_snapshot`.** Repo import policy prefers top-of-file for common stdlib. Trivial.

---

## Minor races / edge cases (acceptable, noted for completeness)

- `live_snapshots` and the likeness state in `_restore_full_steps` are captured *before* `planner.stop()`; a background snapshot created in that tiny window between capture and swap would be lost from the index without re-insertion. Window is milliseconds.
- Restoring an old snapshot can resurrect dangling `Snapshot` index rows for snapshots whose files were already GFS-pruned (re-insertion only adds, never removes resurrected-but-deleted rows). Pre-existing; restore on such a row fails gracefully (file-not-found).
- A failed `compress_snapshot` leaves an orphan partial `.zst` with no index row (manifest/sidecar/row are written after compress). Harmless; the snapshots dir is excluded from the orphan scanner so it won't be auto-cleaned either.

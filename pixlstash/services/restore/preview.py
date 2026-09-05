"""Restore previews and snapshot hash comparison.

Dry-run previews of full / per-resource / batch restores (diffing the snapshot
against the live DB via ``metadata_hash``), plus the on-demand and bulk
metadata-hash backfill that keeps hash comparison correct for legacy
snapshots.
"""

import os
import shutil
import sqlite3

from sqlmodel import Session, select
from sqlalchemy import (
    bindparam as sa_bindparam,
    update as sa_update,
)

from pixlstash.database import (
    _compute_picture_metadata_hashes,
)
from pixlstash.db_models import (
    Character,
    DeletedFileLog,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    Project,
    Tag,
)
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.snapshot_compression import is_compressed

from ._models import (
    ResourcePreview,
    RestorePreview,
    _SUPPORTED_RESOURCE_TYPES,
)
from .schema_upgrade import (
    _alembic_head_revisions,
    _snapshot_schema_is_current,
    _snapshot_schema_revision,
    snapshot_engine,
)

logger = get_logger(__name__)


class PreviewMixin:
    """Restore preview and hash-comparison behaviour.

    Mixed into :class:`~pixlstash.services.restore.RestoreService`.
    """

    def preview_full(self, snapshot_id: int) -> RestorePreview:
        """Compute a dry-run preview of a full restore without modifying the DB.

        Opens the snapshot read-only, diffs picture rows against the live DB,
        checks file presence on disk, and returns a ``RestorePreview``.

        Args:
            snapshot_id: ID of the snapshot to preview.

        Returns:
            A ``RestorePreview`` with summary, per-resource entries (capped at
            200), and warnings.

        Raises:
            ValueError: If the snapshot or snapshot file is not found.
        """
        vault_root = self._vault.image_root
        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = resolve_path_within(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

        preview = RestorePreview(
            snapshot_id=snapshot_id,
            snapshot_kind=cp.kind,
            snapshot_label=cp.label,
            snapshot_created_at=cp.created_at.isoformat(),
        )

        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            preview.warnings.append("Schema upgrade failed; preview unavailable.")
            return preview

        try:
            snap_engine = snapshot_engine(upgraded_snapshot)
            try:
                with Session(snap_engine) as snap_session:
                    self._compute_full_preview(
                        snap_session, preview, vault_root, snapshot_id
                    )
            except Exception as exc:
                logger.error(
                    "RestoreService: preview_full failed for snapshot %d: %s",
                    snapshot_id,
                    exc,
                    exc_info=True,
                )
                preview.warnings.append(f"Preview computation error: {exc}")
            finally:
                snap_engine.dispose()
        finally:
            try:
                shutil.rmtree(os.path.dirname(upgraded_snapshot), ignore_errors=True)
            except Exception:
                logger.warning(
                    "RestoreService: failed to remove temp upgraded snapshot dir: %s",
                    os.path.dirname(upgraded_snapshot),
                )

        return preview

    def preview_resource(
        self,
        snapshot_id: int,
        resource_type: str,
        resource_id: int,
    ) -> RestorePreview:
        """Compute a dry-run preview of a single-resource restore.

        Args:
            snapshot_id: ID of the snapshot.
            resource_type: One of ``'picture'``, ``'picture_set'``,
                ``'project'``, or ``'character'``.
            resource_id: Primary key of the resource.

        Returns:
            A ``RestorePreview`` for the targeted resource.

        Raises:
            ValueError: If the snapshot/snapshot is not found or
                ``resource_type`` is invalid.
        """
        if resource_type not in _SUPPORTED_RESOURCE_TYPES:
            raise ValueError(
                f"Unsupported resource_type '{resource_type}'. "
                f"Supported: {', '.join(_SUPPORTED_RESOURCE_TYPES)}. "
                "Use the full restore for project-level recovery."
            )

        vault_root = self._vault.image_root
        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = resolve_path_within(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

        preview = RestorePreview(
            snapshot_id=snapshot_id,
            snapshot_kind=cp.kind,
            snapshot_label=cp.label,
            snapshot_created_at=cp.created_at.isoformat(),
        )

        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            preview.warnings.append("Schema upgrade failed; preview unavailable.")
            return preview

        try:
            snap_engine = snapshot_engine(upgraded_snapshot)
            try:
                with Session(snap_engine) as snap_session:
                    self._compute_resource_preview(
                        snap_session,
                        preview,
                        resource_type,
                        resource_id,
                        vault_root,
                    )
            except Exception as exc:
                logger.error(
                    "RestoreService: preview_resource failed for snapshot %d (%s/%s): %s",
                    snapshot_id,
                    resource_type,
                    resource_id,
                    exc,
                    exc_info=True,
                )
                preview.warnings.append(f"Preview computation error: {exc}")
            finally:
                snap_engine.dispose()
        finally:
            try:
                shutil.rmtree(os.path.dirname(upgraded_snapshot), ignore_errors=True)
            except Exception:
                logger.warning(
                    "RestoreService: failed to remove temp upgraded snapshot dir: %s",
                    os.path.dirname(upgraded_snapshot),
                )

        return preview

    def preview_batch(
        self,
        snapshot_id: int,
        resources: list[dict],
    ) -> RestorePreview:
        """Compute a dry-run preview for a batch of resources.

        Args:
            snapshot_id: ID of the snapshot.
            resources: List of ``{"type": str, "id": int}`` dicts.

        Returns:
            A combined ``RestorePreview`` for all specified resources.

        Raises:
            ValueError: If the snapshot/snapshot is not found.
        """
        vault_root = self._vault.image_root
        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = resolve_path_within(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

        preview = RestorePreview(
            snapshot_id=snapshot_id,
            snapshot_kind=cp.kind,
            snapshot_label=cp.label,
            snapshot_created_at=cp.created_at.isoformat(),
        )

        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            preview.warnings.append("Schema upgrade failed; preview unavailable.")
            self._finalise_preview_summary(preview)
            return preview

        try:
            snap_engine = snapshot_engine(upgraded_snapshot)
            try:
                with Session(snap_engine) as snap_session:
                    for item in resources:
                        self._compute_resource_preview(
                            snap_session,
                            preview,
                            item.get("type", ""),
                            int(item.get("id", 0)),
                            vault_root,
                        )
            except Exception as exc:
                logger.error(
                    "RestoreService: preview_batch failed for snapshot %d: %s",
                    snapshot_id,
                    exc,
                    exc_info=True,
                )
                preview.warnings.append(f"Preview computation error: {exc}")
            finally:
                snap_engine.dispose()
        finally:
            try:
                shutil.rmtree(os.path.dirname(upgraded_snapshot), ignore_errors=True)
            except Exception:
                logger.warning(
                    "RestoreService: failed to remove temp upgraded snapshot dir: %s",
                    os.path.dirname(upgraded_snapshot),
                )

        self._finalise_preview_summary(preview)
        return preview

    def compare_hashes(self, snapshot_id: int, picture_ids: list[int]) -> dict:
        """Compare live ``metadata_hash`` values against a snapshot snapshot.

        Opens the snapshot file read-only and looks up the ``metadata_hash``
        column for the requested picture IDs in both the live DB and the
        snapshot.  A NULL hash on either side is treated conservatively as
        "potentially changed" so the snapshot stays enabled.

        Args:
            snapshot_id: ID of the snapshot to compare against.
            picture_ids: List of live picture IDs to check.

        Returns:
            ``{"identical_ids": [...], "changed_ids": [...]}`` where each
            input ID appears in exactly one list.

        Raises:
            ValueError: If the snapshot or its snapshot file cannot be found.
        """
        if not picture_ids:
            return {"identical_ids": [], "changed_ids": []}

        cp = self._get_snapshot_or_raise(snapshot_id)
        snapshot_path = os.path.join(self._vault.image_root, cp.relative_path)
        if not os.path.exists(snapshot_path):
            raise ValueError(f"Snapshot file not found for snapshot {snapshot_id}")

        # Fetch live hashes, computing and persisting any that are NULL so
        # existing pictures (pre-migration) can be compared correctly. NULL
        # rows are batched into a single bulk Core UPDATE rather than one
        # UPDATE per picture - the context-menu fans out N pictures × M recent
        # snapshots and the per-row execute path saturated the writer queue
        # with single-row updates.
        def _get_live_hashes(session: Session) -> dict[int, str | None]:
            rows = session.execute(
                select(Picture.id, Picture.metadata_hash).where(
                    Picture.id.in_(picture_ids)
                )
            ).all()
            hashes: dict[int, str | None] = {pid: h for pid, h in rows}
            to_persist: list[dict] = []
            missing = [pid for pid, h in hashes.items() if h is None]
            if missing:
                # Batched, not one call per NULL row: the per-picture helper is
                # a thin wrapper over this one, so a loop costs five queries per
                # picture on the interactive compare path the bulk UPDATE below
                # was already added to unblock. Ids with no picture row get no
                # entry, which is what the per-picture ``None`` meant here.
                for pid, computed in _compute_picture_metadata_hashes(
                    session, missing
                ).items():
                    hashes[pid] = computed
                    to_persist.append({"_pid": pid, "_hash": computed})
            if to_persist:
                # Bulk Core UPDATE: one prepared statement, N parameter sets.
                # Target ``Picture.__table__`` (Core ``Table``) rather than the
                # ORM ``Picture`` mapper so SQLAlchemy doesn't try to route this
                # through the ORM "bulk by primary key" path - that path
                # requires ``id`` in every row and clashes with our explicit
                # WHERE bindparam. Core DML also keeps the after_flush hash
                # hook from re-firing on this backfill write.
                stmt = (
                    sa_update(Picture.__table__)
                    .where(Picture.__table__.c.id == sa_bindparam("_pid"))
                    .values(metadata_hash=sa_bindparam("_hash"))
                )
                session.execute(stmt, to_persist)
                # ``run_task`` wraps the callable in ``with Session(...)``
                # which rolls back on close without an explicit commit; the
                # backfilled hashes would otherwise be discarded.
                session.commit()
            return hashes

        live_hashes: dict[int, str | None] = self._vault.db.run_task(_get_live_hashes)

        # Snapshot-side hashes. New snapshots ship a complete {id: hash} map in
        # an uncompressed sidecar, so an interactive compare never has to touch
        # - let alone decompress - the archive. The legacy file path only runs
        # for old uncompressed snapshots that predate the sidecar.
        sidecar_hashes = self._vault.snapshot_service.load_picture_hashes(snapshot_id)

        snap_hashes: dict[int, str | None] = {}
        if sidecar_hashes:
            # JSON object keys are strings; look up by str(pid).
            snap_hashes = {pid: sidecar_hashes.get(str(pid)) for pid in picture_ids}
        elif is_compressed(snapshot_path):
            # Compressed but no manifest map - should not happen for snapshots
            # created by this version. Don't try to open the archive as a DB;
            # report everything changed so the restore stays available.
            logger.warning(
                "RestoreService.compare_hashes: compressed snapshot %d has no "
                "manifest hash map; treating all pictures as changed.",
                snapshot_id,
            )
            return {"identical_ids": [], "changed_ids": list(picture_ids)}
        else:
            # ── Legacy uncompressed snapshot ────────────────────────────────
            # Schema-currency check → optional in-place upgrade + backfill →
            # read hashes, held under the per-path file lock so a concurrent
            # compare/preview/restore can't read a half-rewritten file. The
            # lock is reentrant, so the nested _backfill_snapshot call
            # re-enters safely.
            with self._snapshot_file_lock(snapshot_path):
                if not _snapshot_schema_is_current(snapshot_path):
                    # One-time fix: alembic-upgrade the file to head and write
                    # the hashes into it. The check is revision-based, not a
                    # single-column probe: the NULL-hash path below does a full
                    # ORM entity load, which selects *every* column of the
                    # current Picture model, so anything short of head fails.
                    self._backfill_snapshot(snapshot_path)

                # Read hashes from the (possibly just backfilled) file.
                _snap_engine = None
                try:
                    _snap_engine = snapshot_engine(snapshot_path)
                    with Session(_snap_engine) as snap_session:
                        snap_rows = snap_session.execute(
                            select(Picture.id, Picture.metadata_hash).where(
                                Picture.id.in_(picture_ids)
                            )
                        ).all()
                        missing_in_snapshot: list[int] = []
                        for pid, h in snap_rows:
                            if h is not None:
                                snap_hashes[pid] = h
                            else:
                                missing_in_snapshot.append(pid)
                        if missing_in_snapshot:
                            # Safety fallback, should not occur after backfill.
                            # Batched for the same reason as the live side above.
                            snap_hashes.update(
                                _compute_picture_metadata_hashes(
                                    snap_session, missing_in_snapshot
                                )
                            )
                except Exception as exc:
                    # Last-resort path only: an out-of-date snapshot is handled
                    # by the upgrade above, so anything landing here is a real
                    # failure. Log the full context needed to diagnose it.
                    logger.warning(
                        "RestoreService.compare_hashes: failed to read snapshot "
                        "%d at %s (schema revision %s, head %s) for %d picture "
                        "id(s): %s - reporting all as changed.",
                        snapshot_id,
                        snapshot_path,
                        _snapshot_schema_revision(snapshot_path),
                        sorted(_alembic_head_revisions()),
                        len(picture_ids),
                        exc,
                        exc_info=True,
                    )
                    # Treat all as changed on error (conservative / keep enabled)
                    return {"identical_ids": [], "changed_ids": list(picture_ids)}
                finally:
                    if _snap_engine is not None:
                        try:
                            _snap_engine.dispose()
                        except Exception:
                            logger.warning(
                                "RestoreService.compare_hashes: failed to dispose snapshot engine for snapshot %d",
                                snapshot_id,
                            )

        identical_ids: list[int] = []
        changed_ids: list[int] = []
        for pid in picture_ids:
            live_h = live_hashes.get(pid)
            snap_h = snap_hashes.get(pid)
            if live_h is not None and snap_h is not None and live_h == snap_h:
                identical_ids.append(pid)
            else:
                changed_ids.append(pid)

        return {"identical_ids": identical_ids, "changed_ids": changed_ids}

    def backfill_all_snapshot_hashes(self, reset_all: bool = False) -> None:
        """Permanently compute and save metadata_hash for all snapshot snapshot files.

        Per-snapshot errors are logged and skipped so a single corrupt file
        does not abort the sweep.

        Args:
            reset_all: When True, clear existing hashes before recomputing so
                that every picture gets a fresh hash (use this after the hash
                algorithm changes).  When False (default), only fill NULLs.
        """
        snapshots = self._vault.db.run_immediate_read_task(
            lambda session: session.exec(select(Snapshot)).all()
        )
        for cp in snapshots:
            abs_snapshot = os.path.join(self._vault.image_root, cp.relative_path)
            if not os.path.exists(abs_snapshot):
                continue
            if is_compressed(abs_snapshot):
                # Compressed snapshots carry their hash map in the manifest;
                # there is no in-file metadata_hash to backfill.
                continue
            try:
                self._backfill_snapshot(abs_snapshot, reset_all=reset_all)
            except Exception as exc:
                logger.warning(
                    "RestoreService.backfill_all_snapshot_hashes: failed for %s: %s",
                    abs_snapshot,
                    exc,
                    exc_info=True,
                )

    def _backfill_snapshot(self, abs_snapshot: str, reset_all: bool = False) -> None:
        """Compute and permanently write metadata_hash for pictures in *abs_snapshot*.

        If the snapshot's schema is behind the current Alembic head, the file
        is upgraded in-place via a temp copy that replaces the original.  Once
        the schema is current, all rows whose metadata_hash IS NULL are filled
        and committed directly to the snapshot file.

        The "is it current?" test compares the snapshot's stamped Alembic
        revision against head rather than probing for a specific column: an
        intermediate snapshot can have ``metadata_hash`` yet lack columns added
        by later migrations, and hashing loads the full Picture entity.

        Held under the per-path file lock so concurrent compare/preview/
        restore on the same snapshot can't read a half-rewritten file.

        Args:
            abs_snapshot: Absolute path to the snapshot .sqlite file to update.
            reset_all: When True, clear all existing hashes before recomputing.
        """
        if is_compressed(abs_snapshot):
            # In-place backfill is a legacy-only path: compressed snapshots
            # carry a complete hash map in their manifest, so there is nothing
            # to write into the (sealed) archive.
            logger.debug(
                "RestoreService._backfill_snapshot: skipping compressed snapshot %s",
                abs_snapshot,
            )
            return

        with self._snapshot_file_lock(abs_snapshot):
            if not _snapshot_schema_is_current(abs_snapshot):
                # Upgrade via a temp copy, then atomically replace the original.
                upgraded = self._upgrade_snapshot_schema(abs_snapshot)
                if upgraded is None:
                    logger.warning(
                        "RestoreService._backfill_snapshot: schema upgrade failed "
                        "for %s (schema revision %s, head %s)",
                        abs_snapshot,
                        _snapshot_schema_revision(abs_snapshot),
                        sorted(_alembic_head_revisions()),
                    )
                    return
                tmp_dir = os.path.dirname(upgraded)
                try:
                    self._fill_snapshot_hashes_at(upgraded, reset_all=reset_all)
                    shutil.copy2(upgraded, abs_snapshot)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return

            # Already at head - fill any NULL hashes on the original.
            self._fill_snapshot_hashes_at(abs_snapshot, reset_all=reset_all)

    def _fill_snapshot_hashes_at(self, db_path: str, reset_all: bool = False) -> None:
        """Compute and commit metadata_hash for Pictures in *db_path*.

        Opens a standalone SQLite session on *db_path* (independent of the
        vault DB), computes SHA-256 metadata hashes, and commits the results.
        This is the **only** restore path that writes to a snapshot file; it
        uses ``snapshot_engine`` (non-WAL, see there) and checkpoints/converts
        the file back to a rollback journal afterwards, because the caller may
        ``copy2`` the main file by name over the real snapshot.

        Args:
            db_path: Absolute path to a writable SQLite file.
            reset_all: When True, reset all existing hashes to NULL first so
                every picture is recomputed (use after algorithm changes).
        """
        engine = snapshot_engine(db_path)
        try:
            with Session(engine) as session:
                if reset_all:
                    session.execute(sa_update(Picture).values(metadata_hash=None))
                    session.commit()
                null_pids = (
                    session.execute(
                        select(Picture.id).where(Picture.metadata_hash.is_(None))
                    )
                    .scalars()
                    .all()
                )
                if not null_pids:
                    return
                # Whole-file backfill: one batched hash read plus one
                # executemany UPDATE instead of six statements per picture.
                # A legacy snapshot can hold the entire library, so the old
                # per-row loop was the dominant cost of a first-time compare.
                new_hashes = _compute_picture_metadata_hashes(session, null_pids)
                if new_hashes:
                    stmt = (
                        sa_update(Picture.__table__)
                        .where(Picture.__table__.c.id == sa_bindparam("_pid"))
                        .values(metadata_hash=sa_bindparam("_hash"))
                    )
                    session.execute(
                        stmt,
                        [
                            {"_pid": pid, "_hash": new_hash}
                            for pid, new_hash in new_hashes.items()
                        ],
                    )
                session.commit()
            # Flush WAL to main file for a clean single-file snapshot.
            # NB: ``sqlite3.Connection.__exit__`` commits but does NOT close the
            # connection, so a ``with sqlite3.connect(...)`` here would leak the
            # file handle and block deletion of the snapshot on Windows. Close
            # explicitly in a finally.
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.commit()
            finally:
                conn.close()
            logger.info(
                "RestoreService: filled %d metadata hashes in %s",
                len(null_pids),
                db_path,
            )
        finally:
            engine.dispose()

    def _compute_full_preview(
        self,
        snap_session: Session,
        preview: RestorePreview,
        vault_root: str,
        snapshot_id: int,
    ) -> None:
        """Populate *preview* with only the resources that actually change.

        Uses the ``metadata_hash`` (which covers columns + tags + face state)
        to classify every picture across the WHOLE vault as
        revert / recreate / delete / missing-file / unchanged, then lists only
        the changed ones (capped at ``MAX_RESOURCES``). Unchanged pictures are
        counted but never fill the resource table, so the cap is spent on the
        rows the user cares about. Only id/path/hash columns are scanned - the
        now-retained embeddings are never pulled into memory for the full set.

        Args:
            snap_session: Read session on the (upgraded) snapshot.
            preview: Preview object to mutate.
            vault_root: Vault root for file-existence checks.
            snapshot_id: Snapshot being previewed (for the hash sidecar).
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        MAX_RESOURCES = 200

        # Lightweight scan - id, file_path, metadata_hash only.
        snap_rows = snap_session.exec(
            select(Picture.id, Picture.file_path, Picture.metadata_hash)
        ).all()
        snap_ids = {r[0] for r in snap_rows}

        live_rows = self._vault.db.run_immediate_read_task(
            lambda s: s.exec(select(Picture.id, Picture.metadata_hash)).all()
        )
        live_ids = {r[0] for r in live_rows}
        live_hash = {pid: h for pid, h in live_rows}

        # Snapshot hashes: prefer the complete sidecar (legacy snapshots fall
        # back to the per-row metadata_hash scanned above).
        sidecar = self._vault.snapshot_service.load_picture_hashes(snapshot_id)

        def _snap_hash(pid: int, scanned_hash) -> "str | None":
            if sidecar:
                return sidecar.get(str(pid))
            return scanned_hash

        missing_files = 0
        pictures_to_revert = 0
        pictures_unchanged = 0
        pictures_to_recreate = 0
        pictures_to_delete = 0
        shown_reverts: list = []  # (ResourcePreview, picture_id) for tag diffing

        for pid, file_path, scanned_hash in snap_rows:
            # File presence - a picture whose file vanished is dropped on
            # restore, so it counts as a change regardless of its hash.
            file_ok = True
            if file_path:
                try:
                    resolved = ImageUtils.resolve_picture_path(vault_root, file_path)
                    file_ok = os.path.isfile(resolved)
                except Exception:
                    file_ok = False
            if not file_ok:
                missing_files += 1
                if len(preview.resources) < MAX_RESOURCES:
                    preview.resources.append(
                        ResourcePreview(
                            type="picture",
                            id=pid,
                            exists_in_live=pid in live_ids,
                            exists_in_snapshot=True,
                            file_on_disk=False,
                            changed_fields=[],
                            dependent_counts={},
                        )
                    )
                continue

            if pid not in live_ids:
                pictures_to_recreate += 1
                if len(preview.resources) < MAX_RESOURCES:
                    preview.resources.append(
                        ResourcePreview(
                            type="picture",
                            id=pid,
                            exists_in_live=False,
                            exists_in_snapshot=True,
                            file_on_disk=True,
                            changed_fields=["(new)"],
                            dependent_counts=self._picture_dependent_counts(
                                snap_session, pid
                            ),
                        )
                    )
                continue

            # Present in both - changed iff the metadata hash differs (a NULL
            # on either side is treated as "can't confirm identical" → changed).
            s_hash = _snap_hash(pid, scanned_hash)
            l_hash = live_hash.get(pid)
            if s_hash is not None and l_hash is not None and s_hash == l_hash:
                pictures_unchanged += 1
                continue

            pictures_to_revert += 1
            if len(preview.resources) < MAX_RESOURCES:
                snap_pic = snap_session.get(Picture, pid)
                rp = ResourcePreview(
                    type="picture",
                    id=pid,
                    exists_in_live=True,
                    exists_in_snapshot=True,
                    file_on_disk=True,
                    changed_fields=self._diff_picture(snap_pic, True),
                    dependent_counts=self._picture_dependent_counts(snap_session, pid),
                )
                preview.resources.append(rp)
                shown_reverts.append((rp, pid))

        # Pictures in live but not in snapshot (will be deleted by full restore).
        for live_id in live_ids - snap_ids:
            pictures_to_delete += 1
            if len(preview.resources) < MAX_RESOURCES:
                preview.resources.append(
                    ResourcePreview(
                        type="picture",
                        id=live_id,
                        exists_in_live=True,
                        exists_in_snapshot=False,
                        file_on_disk=True,
                        changed_fields=[],
                        dependent_counts={},
                    )
                )

        # Annotate shown reverts with a "tags" field where the tag sets differ,
        # and a "(modified)" placeholder when the hash changed but no column /
        # tag diff is visible (e.g. a face-only edit). Bounded to the shown set.
        self._annotate_shown_revert_tags(snap_session, shown_reverts)

        total_changed = (
            pictures_to_revert
            + pictures_to_recreate
            + pictures_to_delete
            + missing_files
        )
        shown = len(preview.resources)
        if total_changed > shown:
            preview.warnings.append(
                f"Showing {shown} of {total_changed} changed resources."
            )
        if missing_files:
            preview.warnings.append(
                f"{missing_files} picture file(s) missing on disk; "
                "those rows will be removed after restore."
            )

        # Permanent-deletion ledger: surface how many snapshot pictures will be
        # withheld because their content was permanently deleted (these also
        # appear under missing_files when their file is already gone from disk).
        path_shas, pixel_shas = self._load_deleted_file_index()
        permanently_deleted = len(
            self._match_deleted_picture_ids(snap_session, path_shas, pixel_shas)
        )
        if permanently_deleted:
            preview.warnings.append(
                f"{permanently_deleted} picture(s) in this snapshot were "
                "permanently deleted and will not be restored."
            )

        preview.summary = {
            "pictures_to_revert": pictures_to_revert,
            "pictures_to_recreate": pictures_to_recreate,
            "pictures_to_delete": pictures_to_delete,
            "pictures_unchanged": pictures_unchanged,
            "missing_files": missing_files,
            "permanently_deleted": permanently_deleted,
        }

    def _annotate_shown_revert_tags(self, snap_session: Session, shown_reverts: list):
        """Add a ``tags`` change marker (and a ``(modified)`` fallback) to the
        shown revert previews.

        ``_diff_picture`` only compares Picture columns; tag changes (and
        face-only changes, which still flip the metadata hash) wouldn't show.
        Resolved here for just the shown set so it stays bounded by the cap.

        Args:
            snap_session: Read session on the snapshot.
            shown_reverts: List of ``(ResourcePreview, picture_id)`` tuples.
        """
        if not shown_reverts:
            return
        shown_ids = [pid for _, pid in shown_reverts]

        snap_tag_sets: dict[int, set] = {}
        for _pid, _tag in snap_session.exec(
            select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(shown_ids))
        ).all():
            snap_tag_sets.setdefault(_pid, set()).add(_tag)

        def _load_live(session) -> dict:
            d: dict[int, set] = {}
            for _pid, _tag in session.exec(
                select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(shown_ids))
            ).all():
                d.setdefault(_pid, set()).add(_tag)
            return d

        live_tag_sets = self._vault.db.run_immediate_read_task(_load_live)

        for rp, pid in shown_reverts:
            if snap_tag_sets.get(pid, set()) != live_tag_sets.get(pid, set()):
                if "tags" not in rp.changed_fields:
                    rp.changed_fields.append("tags")
            if not rp.changed_fields:
                # Hash differs but nothing visible diffed (e.g. a face edit).
                rp.changed_fields.append("(modified)")

    def _compute_resource_preview(
        self,
        snap_session: Session,
        preview: RestorePreview,
        resource_type: str,
        resource_id: int,
        vault_root: str,
    ) -> None:
        """Populate *preview* for a single resource.

        For picture resources, diffs the snapshot row against the live DB.
        For set/project/character, adds a single summary entry.  Mutates
        *preview* in place; does NOT call ``_finalise_preview_summary`` (the
        caller does that for batch previews; single-resource callers should
        call it after this method).

        Args:
            snap_session: Read session on the snapshot.
            preview: Preview object to mutate.
            resource_type: Resource type string.
            resource_id: Primary key of the resource.
            vault_root: Vault root for file-existence checks.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        if resource_type == "picture":
            snap_pic = snap_session.get(Picture, resource_id)
            exists_in_snapshot = snap_pic is not None
            exists_in_live = False
            file_ok = True
            changed: list[str] = []
            dep_counts: dict = {}

            if snap_pic:
                if snap_pic.file_path:
                    try:
                        resolved = ImageUtils.resolve_picture_path(
                            vault_root, snap_pic.file_path
                        )
                        file_ok = os.path.isfile(resolved)
                    except Exception:
                        file_ok = False
                if not file_ok:
                    preview.warnings.append(
                        f"Picture id={resource_id} file missing on disk."
                    )
                path_shas, pixel_shas = self._load_deleted_file_index()
                if (
                    snap_pic.file_path
                    and DeletedFileLog.hash_path(snap_pic.file_path) in path_shas
                ) or (snap_pic.pixel_sha and snap_pic.pixel_sha in pixel_shas):
                    preview.warnings.append(
                        f"Picture id={resource_id} was permanently deleted and "
                        "will not be restored."
                    )
                exists_in_live = self._vault.db.run_immediate_read_task(
                    lambda session: session.get(Picture, resource_id) is not None
                )
                changed = self._diff_picture(snap_pic, exists_in_live)
                dep_counts = self._picture_dependent_counts(snap_session, resource_id)
                if exists_in_live:
                    changed.extend(
                        self._diff_picture_dependents(snap_session, resource_id)
                    )

            preview.resources.append(
                ResourcePreview(
                    type="picture",
                    id=resource_id,
                    exists_in_live=exists_in_live,
                    exists_in_snapshot=exists_in_snapshot,
                    file_on_disk=file_ok,
                    changed_fields=changed,
                    dependent_counts=dep_counts,
                )
            )

        elif resource_type == "picture_set":
            exists_snap = snap_session.get(PictureSet, resource_id) is not None
            exists_live = self._vault.db.run_immediate_read_task(
                lambda session: session.get(PictureSet, resource_id) is not None
            )
            members = snap_session.exec(
                select(PictureSetMember).where(PictureSetMember.set_id == resource_id)
            ).all()
            preview.resources.append(
                ResourcePreview(
                    type="picture_set",
                    id=resource_id,
                    exists_in_live=exists_live,
                    exists_in_snapshot=exists_snap,
                    file_on_disk=True,
                    changed_fields=[],
                    dependent_counts={"pictures": len(members)},
                )
            )

        elif resource_type == "project":
            from pixlstash.db_models.picture_project import (
                PictureProjectMember as PPM,
            )

            exists_snap = snap_session.get(Project, resource_id) is not None
            exists_live = self._vault.db.run_immediate_read_task(
                lambda session: session.get(Project, resource_id) is not None
            )
            ppm_rows = snap_session.exec(
                select(PPM).where(PPM.project_id == resource_id)
            ).all()
            preview.resources.append(
                ResourcePreview(
                    type="project",
                    id=resource_id,
                    exists_in_live=exists_live,
                    exists_in_snapshot=exists_snap,
                    file_on_disk=True,
                    changed_fields=[],
                    dependent_counts={"pictures": len(ppm_rows)},
                )
            )

        elif resource_type == "character":
            exists_snap = snap_session.get(Character, resource_id) is not None
            exists_live = self._vault.db.run_immediate_read_task(
                lambda session: session.get(Character, resource_id) is not None
            )
            preview.resources.append(
                ResourcePreview(
                    type="character",
                    id=resource_id,
                    exists_in_live=exists_live,
                    exists_in_snapshot=exists_snap,
                    file_on_disk=True,
                    changed_fields=[],
                    dependent_counts={},
                )
            )

        self._finalise_preview_summary(preview)

    def _diff_picture(self, snap_pic: Picture, exists_in_live: bool) -> list[str]:
        """Return list of column names that differ between snapshot and live.

        Args:
            snap_pic: Picture row from the snapshot.
            exists_in_live: Whether the picture exists in the live DB.

        Returns:
            List of field names that differ (or all fields if new).
        """
        if not exists_in_live:
            return ["(new)"]

        live_pic = self._vault.db.run_immediate_read_task(
            lambda session: session.get(Picture, snap_pic.id)
        )
        if live_pic is None:
            return ["(new)"]

        _SKIP = {
            "text_embedding",
            "image_embedding",
            "id",
            "file_path",
            "created_at",
            # Derived/regenerable scores and internal hash - not user-controlled
            # metadata, so they should not surface as differences in the preview.
            "aesthetic_score",
            "smart_score",
            "text_score",
            "metadata_hash",
        }
        changed: list[str] = []
        for col in type(snap_pic).model_fields:
            if col in _SKIP:
                continue
            snap_val = getattr(snap_pic, col, None)
            live_val = getattr(live_pic, col, None)
            if snap_val != live_val:
                changed.append(col)
        return changed

    def _picture_dependent_counts(self, snap_session: Session, picture_id: int) -> dict:
        """Return counts of dependent rows for a picture in the snapshot.

        Args:
            snap_session: Read session on the snapshot.
            picture_id: Picture primary key.

        Returns:
            Dict with 'faces' and 'tags' counts.
        """
        from sqlalchemy import func

        face_count = snap_session.exec(
            select(func.count(Face.id)).where(Face.picture_id == picture_id)
        ).one()
        tag_count = snap_session.exec(
            select(func.count(Tag.id)).where(Tag.picture_id == picture_id)
        ).one()
        return {"faces": face_count or 0, "tags": tag_count or 0}

    def _diff_picture_dependents(
        self, snap_session: Session, picture_id: int
    ) -> list[str]:
        """Return dependent types that differ between snapshot and live.

        Compares the snapshot tag set against the live tag set for the given
        picture.  Only ``"tags"`` is checked; face rows are system-derived and
        not included in the diff.

        Args:
            snap_session: Read session on the snapshot.
            picture_id: Picture primary key.

        Returns:
            List of changed dependent type names (e.g. ``["tags"]``).
        """
        snap_tags = frozenset(
            snap_session.exec(select(Tag.tag).where(Tag.picture_id == picture_id)).all()
        )

        def _get_live_tags(session) -> frozenset:
            return frozenset(
                session.exec(select(Tag.tag).where(Tag.picture_id == picture_id)).all()
            )

        live_tags = self._vault.db.run_immediate_read_task(_get_live_tags)
        if snap_tags != live_tags:
            return ["tags"]
        return []

    def _finalise_preview_summary(self, preview: RestorePreview) -> None:
        """Compute the summary dict on *preview* from its resources list.

        Idempotent - safe to call multiple times.

        Args:
            preview: Preview to update.
        """
        counts: dict = {
            "pictures_to_revert": 0,
            "pictures_to_recreate": 0,
            "pictures_to_delete": 0,
            "missing_files": 0,
            "picture_sets_to_revert": 0,
            "projects_to_revert": 0,
            "characters_to_revert": 0,
        }
        for r in preview.resources:
            if not r.file_on_disk:
                counts["missing_files"] += 1
                continue
            if r.type == "picture":
                if not r.exists_in_snapshot:
                    counts["pictures_to_delete"] += 1
                elif r.exists_in_live:
                    counts["pictures_to_revert"] += 1
                else:
                    counts["pictures_to_recreate"] += 1
            elif r.type == "picture_set":
                counts["picture_sets_to_revert"] += 1
            elif r.type == "project":
                counts["projects_to_revert"] += 1
            elif r.type == "character":
                counts["characters_to_revert"] += 1
        preview.summary = counts

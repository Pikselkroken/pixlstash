"""Service layer for restoring vault state from a snapshot snapshot.

Supports full-database restore and per-resource (picture, picture_set,
project, character) restore.  All restores run ``alembic upgrade head`` on
the snapshot file before any data work to handle cross-version snapshots.
"""

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sqlmodel import Session, create_engine, select
from sqlalchemy import update as sa_update

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    PictureProjectMember,
    Project,
    Tag,
)
from pixlstash.database import _compute_picture_metadata_hash
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# Alembic's EnvironmentContext is not thread-safe (uses module globals).
# Serialise all snapshot schema upgrades with this lock.
_ALEMBIC_UPGRADE_LOCK = threading.Lock()

# Columns on Picture that are regenerable and should be NULL-reset after any
# restore so the WorkPlanner reprocesses them automatically.
_PICTURE_DERIVED_COLUMNS: tuple[str, ...] = (
    "smart_score",
    "text_score",
    "text_embedding",
    "image_embedding",
)


@dataclass
class RestoreReport:
    """Summary of a completed restore operation.

    Attributes:
        snapshot_id: ID of the snapshot that was restored.
        resource_type: ``'full'``, ``'picture'``, ``'picture_set'``,
            ``'project'``, or ``'character'``.
        resource_id: Primary key of the specific resource (None for full
            restore).
        missing_files_count: Number of Picture rows skipped because their
            files were absent on disk.
        upserted_count: Number of rows upserted (per-resource restores only).
        errors: Non-fatal error messages accumulated during the restore.
    """

    snapshot_id: int
    resource_type: str
    resource_id: Optional[int] = None
    missing_files_count: int = 0
    upserted_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ResourcePreview:
    """Preview information for a single resource that would be affected by a restore.

    Attributes:
        type: Resource type string (``'picture'``, ``'picture_set'``, etc.).
        id: Primary key of the resource.
        exists_in_live: True if the resource exists in the live database.
        exists_in_snapshot: True if the resource exists in the snapshot.
        file_on_disk: True if the picture file exists on disk (always True
            for non-picture resources).
        changed_fields: List of column names that differ between live and
            snapshot (for picture resources).
        dependent_counts: Counts of dependent objects (e.g.
            ``{"faces": 2, "tags": 10}``).
    """

    type: str
    id: int
    exists_in_live: bool = True
    exists_in_snapshot: bool = True
    file_on_disk: bool = True
    changed_fields: list[str] = field(default_factory=list)
    dependent_counts: dict = field(default_factory=dict)


@dataclass
class RestorePreview:
    """Dry-run preview of a restore operation.

    Attributes:
        snapshot_id: ID of the snapshot to be restored.
        snapshot_kind: Kind of the snapshot.
        snapshot_label: Optional user label.
        snapshot_created_at: ISO timestamp string.
        resources: Per-resource preview entries (capped at 200).
        summary: High-level counts of what would change.
        warnings: Human-readable warning strings (e.g. missing files).
    """

    snapshot_id: int
    snapshot_kind: str
    snapshot_label: Optional[str]
    snapshot_created_at: str
    resources: list[ResourcePreview] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class RestoreService:
    """Restores vault metadata from a snapshot snapshot.

    Attributes:
        _vault: Back-reference to the owning Vault.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the service.

        Args:
            vault: The owning Vault instance.
        """
        self._vault = vault
        # Tracks the currently executing restore job for /snapshots/status.
        self._active_job: Optional[dict] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def restore_full(self, snapshot_id: int, dry_run: bool = False) -> RestoreReport:
        """Replace the live database with a snapshot snapshot.

        Steps:
        1. Take an OPPORTUNISTIC safety snapshot of the current state.
        2. Upgrade the snapshot schema to the current Alembic head.
        3. Scan snapshot Picture rows; collect missing-file IDs.
        4. Dispose the live engine, copy the snapshot over the live DB, and
           re-open it.
        5. Delete rows whose files are missing.
        6. NULL-reset derived columns so the WorkPlanner regenerates them.
        7. Resume the TaskRunner and emit ``RESTORE_COMPLETED``.

        Args:
            snapshot_id: ID of the snapshot to restore.
            dry_run: If True, perform all steps except the actual DB swap and
                return a report without modifying the live database.

        Returns:
            A ``RestoreReport`` summarising the operation.

        Raises:
            ValueError: If the snapshot is not found or the snapshot file is
                missing from disk.
        """
        vault_root = self._vault.image_root
        report = RestoreReport(
            snapshot_id=snapshot_id,
            resource_type="full",
        )

        from datetime import datetime, timezone

        self._active_job = {
            "kind": "RESTORE",
            "snapshot_id": snapshot_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "progress": 0.0,
        }
        try:
            return self._restore_full_inner(snapshot_id, dry_run, vault_root, report)
        finally:
            self._active_job = None

    def _restore_full_inner(
        self,
        snapshot_id: int,
        dry_run: bool,
        vault_root: str,
        report: "RestoreReport",
    ) -> "RestoreReport":
        """Inner implementation of full restore (called from restore_full).

        Args:
            snapshot_id: Snapshot ID.
            dry_run: If True skip DB swap.
            vault_root: Vault root path.
            report: Pre-constructed RestoreReport to populate.

        Returns:
            Populated RestoreReport.
        """
        db = self._vault.db

        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

        # 1. Safety snapshot of current state.
        try:
            self._vault.snapshot_service.create_snapshot("OPPORTUNISTIC")
        except Exception as exc:
            logger.warning(
                "RestoreService: safety snapshot failed (continuing): %s", exc
            )

        # 2. Upgrade snapshot schema to head.
        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            report.errors.append("Schema upgrade failed; aborting restore.")
            return report

        # 3. Find missing-file Picture IDs from the snapshot.
        missing_ids = self._find_missing_file_ids(upgraded_snapshot, vault_root)
        report.missing_files_count = len(missing_ids)
        if missing_ids:
            logger.info(
                "RestoreService: %d picture file(s) missing on disk; "
                "those rows will be dropped after restore.",
                len(missing_ids),
            )

        if dry_run:
            logger.info("RestoreService: dry_run=True — skipping DB swap.")
            shutil.rmtree(os.path.dirname(upgraded_snapshot), ignore_errors=True)
            return report

        # 4. Pause background work, then route the DB swap through the writer
        #    queue so it is serialised with all other DB operations and no
        #    competing connection can hold a lock during the file swap.
        live_db_path = db._db_path
        planner = self._vault._work_planner
        task_runner = self._vault._task_runner

        if planner is not None:
            planner.stop()
            logger.info("RestoreService: WorkPlanner stopped for full restore.")

        if task_runner is not None:
            cancelled = task_runner.cancel_pending_tasks()
            if cancelled:
                logger.info(
                    "RestoreService: cancelled %d pending background task(s).",
                    cancelled,
                )

        logger.info(
            "RestoreService: swapping live DB with snapshot (snapshot id=%d)",
            snapshot_id,
        )

        def _do_swap(session):
            # Close this task's session connection so no lock remains on the
            # live DB file when we dispose the engine and copy the snapshot.
            session.close()
            with db.write_reason(f"restore snapshot {snapshot_id}"):
                self._swap_database(live_db_path, upgraded_snapshot)

        db.run_task(_do_swap, priority=0)

        # 5 & 6. Drop missing-file rows and NULL-reset derived columns.
        def _post_restore_cleanup(session):
            if missing_ids:
                pictures = session.exec(
                    select(Picture).where(Picture.id.in_(missing_ids))
                ).all()
                for pic in pictures:
                    session.delete(pic)
                logger.info(
                    "RestoreService: deleted %d missing-file picture rows.",
                    len(pictures),
                )
            # NULL-reset derived columns.
            all_pictures = session.exec(select(Picture)).all()
            for pic in all_pictures:
                for col in _PICTURE_DERIVED_COLUMNS:
                    if hasattr(pic, col):
                        setattr(pic, col, None)
            session.commit()

        db.run_task(_post_restore_cleanup, priority=0)

        # 7. Restart the WorkPlanner and emit event.
        if planner is not None:
            planner.start()
            logger.info("RestoreService: WorkPlanner restarted after full restore.")

        try:
            from pixlstash.event_types import EventType

            self._vault.emit_event(
                EventType.RESTORE_COMPLETED,
                {
                    "snapshot_id": snapshot_id,
                    "resource_type": "full",
                    "missing_files_count": report.missing_files_count,
                },
            )
        except Exception as exc:
            logger.warning("RestoreService: failed to emit RESTORE_COMPLETED: %s", exc)

        logger.info(
            "RestoreService: full restore from snapshot %d completed "
            "(%d missing files).",
            snapshot_id,
            report.missing_files_count,
        )
        return report

    def restore_resource(
        self,
        snapshot_id: int,
        resource_type: str,
        resource_id: int,
    ) -> RestoreReport:
        """Restore a single resource from a snapshot snapshot.

        Supported *resource_type* values:
        - ``'picture'``  — restores the Picture row plus Face, Tag,
          PictureSetMember, and PictureProjectMember dependents.
        - ``'picture_set'`` — restores the PictureSet row and all member
          pictures (recursive picture restore).
        - ``'project'`` — restores the Project row plus all PictureSets,
          Characters, and Picture members.
        - ``'character'`` — restores the Character row.

        Args:
            snapshot_id: ID of the snapshot to restore from.
            resource_type: One of ``'picture'``, ``'picture_set'``,
                ``'project'``, or ``'character'``.
            resource_id: Primary key of the resource to restore.

        Returns:
            A ``RestoreReport`` summarising the operation.

        Raises:
            ValueError: If the snapshot is not found or ``resource_type`` is
                invalid.
        """
        vault_root = self._vault.image_root
        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

        if resource_type not in ("picture", "picture_set", "project", "character"):
            raise ValueError(
                f"Invalid resource_type '{resource_type}'. "
                "Must be one of: picture, picture_set, project, character."
            )

        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            report = RestoreReport(
                snapshot_id=snapshot_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            report.errors.append("Schema upgrade failed; aborting restore.")
            return report

        try:
            report = self._restore_resource_from_snapshot(
                upgraded_snapshot,
                snapshot_id,
                resource_type,
                resource_id,
                vault_root,
            )
        finally:
            try:
                os.remove(upgraded_snapshot)
            except Exception:
                pass

        try:
            from pixlstash.event_types import EventType

            self._vault.emit_event(
                EventType.RESTORE_COMPLETED,
                {
                    "snapshot_id": snapshot_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "missing_files_count": report.missing_files_count,
                    "upserted_count": report.upserted_count,
                },
            )
        except Exception as exc:
            logger.warning("RestoreService: failed to emit RESTORE_COMPLETED: %s", exc)

        return report

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
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
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
            snap_engine = create_engine(f"sqlite:///{upgraded_snapshot}", echo=False)
            try:
                with Session(snap_engine) as snap_session:
                    self._compute_full_preview(snap_session, preview, vault_root)
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
                pass

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
        if resource_type not in ("picture", "picture_set", "project", "character"):
            raise ValueError(
                f"Invalid resource_type '{resource_type}'. "
                "Must be one of: picture, picture_set, project, character."
            )

        vault_root = self._vault.image_root
        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
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
            snap_engine = create_engine(f"sqlite:///{upgraded_snapshot}", echo=False)
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
                pass

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
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
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
            snap_engine = create_engine(f"sqlite:///{upgraded_snapshot}", echo=False)
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
                pass

        self._finalise_preview_summary(preview)
        return preview

    def restore_batch(
        self,
        snapshot_id: int,
        resources: list[dict],
    ) -> RestoreReport:
        """Restore a batch of resources from a snapshot.

        Args:
            snapshot_id: ID of the snapshot.
            resources: List of ``{"type": str, "id": int}`` dicts.

        Returns:
            A ``RestoreReport`` with aggregate counts.

        Raises:
            ValueError: If the snapshot/snapshot is not found.
        """
        vault_root = self._vault.image_root
        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

        if not resources:
            return RestoreReport(
                snapshot_id=snapshot_id,
                resource_type="batch",
            )

        # Upgrade schema once for the whole batch.
        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            report = RestoreReport(snapshot_id=snapshot_id, resource_type="batch")
            report.errors.append("Schema upgrade failed; aborting batch restore.")
            return report

        total = RestoreReport(snapshot_id=snapshot_id, resource_type="batch")
        try:
            with self._vault.db.write_reason(
                f"restore snapshot {snapshot_id} batch of {len(resources)} resources"
            ):
                for item in resources:
                    rtype = item.get("type", "")
                    rid = int(item.get("id", 0))
                    if rtype not in ("picture", "picture_set", "project", "character"):
                        total.errors.append(f"Skipped unknown resource type '{rtype}'.")
                        continue
                    try:
                        sub = self._restore_resource_from_snapshot(
                            upgraded_snapshot,
                            snapshot_id,
                            rtype,
                            rid,
                            vault_root,
                        )
                        total.missing_files_count += sub.missing_files_count
                        total.upserted_count += sub.upserted_count
                        total.errors.extend(sub.errors)
                    except Exception as exc:
                        msg = f"{rtype}/{rid}: {exc}"
                        logger.error(
                            "RestoreService: batch item restore failed: %s",
                            msg,
                            exc_info=True,
                        )
                        total.errors.append(msg)
        finally:
            try:
                os.remove(upgraded_snapshot)
                shutil.rmtree(os.path.dirname(upgraded_snapshot), ignore_errors=True)
            except Exception:
                pass

        try:
            from pixlstash.event_types import EventType

            self._vault.emit_event(
                EventType.RESTORE_COMPLETED,
                {
                    "snapshot_id": snapshot_id,
                    "resource_type": "batch",
                    "upserted_count": total.upserted_count,
                    "missing_files_count": total.missing_files_count,
                },
            )
        except Exception as exc:
            logger.warning("RestoreService: failed to emit RESTORE_COMPLETED: %s", exc)

        return total

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
        # existing pictures (pre-migration) can be compared correctly.
        def _get_live_hashes(session: Session) -> dict[int, str | None]:
            rows = session.execute(
                select(Picture.id, Picture.metadata_hash).where(
                    Picture.id.in_(picture_ids)
                )
            ).all()
            hashes: dict[int, str | None] = {pid: h for pid, h in rows}
            for pid, h in list(hashes.items()):
                if h is None:
                    computed = _compute_picture_metadata_hash(session, pid)
                    if computed is not None:
                        # Persist via Core SQL so the after_flush hook is not
                        # re-triggered and the ChangeLog is not polluted.
                        session.execute(
                            sa_update(Picture)
                            .where(Picture.id == pid)
                            .values(metadata_hash=computed)
                        )
                        hashes[pid] = computed
            return hashes

        live_hashes: dict[int, str | None] = self._vault.db.run_task(_get_live_hashes)

        # Open snapshot directly — backfilling schema + hashes in-place for
        # old snapshots that predate the metadata_hash migration.
        from sqlalchemy import inspect as sa_inspect

        _probe_engine = None
        try:
            _probe_engine = create_engine(f"sqlite:///{snapshot_path}", echo=False)
            snap_has_col = "metadata_hash" in {
                c["name"] for c in sa_inspect(_probe_engine).get_columns("picture")
            }
        except Exception:
            snap_has_col = False
        finally:
            if _probe_engine is not None:
                try:
                    _probe_engine.dispose()
                except Exception:
                    pass

        if not snap_has_col:
            # One-time fix: write migration + hashes into the original snapshot.
            self._backfill_snapshot(snapshot_path)

        # Fetch snapshot hashes directly from the (possibly just backfilled) file.
        _snap_engine = None
        try:
            _snap_engine = create_engine(f"sqlite:///{snapshot_path}", echo=False)
            with Session(_snap_engine) as snap_session:
                snap_rows = snap_session.execute(
                    select(Picture.id, Picture.metadata_hash).where(
                        Picture.id.in_(picture_ids)
                    )
                ).all()
                snap_hashes: dict[int, str | None] = {}
                for pid, h in snap_rows:
                    if h is not None:
                        snap_hashes[pid] = h
                    else:
                        # Safety fallback — should not occur after backfill.
                        snap_hashes[pid] = _compute_picture_metadata_hash(
                            snap_session, pid
                        )
        except Exception as exc:
            logger.warning(
                "RestoreService.compare_hashes: failed to read snapshot %d: %s",
                snapshot_id,
                exc,
            )
            # Treat all as changed on error (conservative / keep enabled)
            return {"identical_ids": [], "changed_ids": list(picture_ids)}
        finally:
            if _snap_engine is not None:
                try:
                    _snap_engine.dispose()
                except Exception:
                    pass

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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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

        If the snapshot predates the metadata_hash migration (column missing),
        the file is upgraded in-place via a temp copy that replaces the
        original.  After the column exists, all rows whose metadata_hash IS
        NULL are filled and committed directly to the snapshot file.

        Args:
            abs_snapshot: Absolute path to the snapshot .sqlite file to update.
            reset_all: When True, clear all existing hashes before recomputing.
        """
        from sqlalchemy import inspect as sa_inspect

        _probe = None
        try:
            _probe = create_engine(f"sqlite:///{abs_snapshot}", echo=False)
            col_names = {
                col["name"] for col in sa_inspect(_probe).get_columns("picture")
            }
        finally:
            if _probe is not None:
                try:
                    _probe.dispose()
                except Exception:
                    pass

        if "metadata_hash" not in col_names:
            # Upgrade via a temp copy, then atomically replace the original.
            upgraded = self._upgrade_snapshot_schema(abs_snapshot)
            if upgraded is None:
                logger.warning(
                    "RestoreService._backfill_snapshot: schema upgrade failed for %s",
                    abs_snapshot,
                )
                return
            tmp_dir = os.path.dirname(upgraded)
            try:
                self._fill_snapshot_hashes_at(upgraded, reset_all=reset_all)
                shutil.copy2(upgraded, abs_snapshot)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        # Column already exists — fill in any NULL hashes directly on the original.
        self._fill_snapshot_hashes_at(abs_snapshot, reset_all=reset_all)

    def _fill_snapshot_hashes_at(self, db_path: str, reset_all: bool = False) -> None:
        """Compute and commit metadata_hash for Pictures in *db_path*.

        Opens a standalone SQLite session on *db_path* (independent of the
        vault DB), computes SHA-256 metadata hashes, and commits the results.
        A WAL snapshot is issued afterwards to keep the snapshot as a
        self-contained single file.

        Args:
            db_path: Absolute path to a writable SQLite file.
            reset_all: When True, reset all existing hashes to NULL first so
                every picture is recomputed (use after algorithm changes).
        """
        import sqlite3 as _sqlite3

        engine = create_engine(f"sqlite:///{db_path}", echo=False)
        try:
            with Session(engine) as session:
                if reset_all:
                    session.execute(sa_update(Picture).values(metadata_hash=None))
                    session.commit()
                null_pids = (
                    session.execute(
                        select(Picture.id).where(
                            Picture.metadata_hash == None  # noqa: E711
                        )
                    )
                    .scalars()
                    .all()
                )
                if not null_pids:
                    return
                for pid in null_pids:
                    new_hash = _compute_picture_metadata_hash(session, pid)
                    if new_hash is not None:
                        session.execute(
                            sa_update(Picture)
                            .where(Picture.id == pid)
                            .values(metadata_hash=new_hash)
                        )
                session.commit()
            # Flush WAL to main file for a clean single-file snapshot.
            with _sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA wal_snapshot(TRUNCATE)")
                conn.execute("PRAGMA journal_mode=DELETE")
            logger.info(
                "RestoreService: filled %d metadata hashes in %s",
                len(null_pids),
                db_path,
            )
        finally:
            engine.dispose()

    def _get_snapshot_or_raise(self, snapshot_id: int) -> Snapshot:
        cp = self._vault.snapshot_service.get_snapshot(snapshot_id)
        if cp is None:
            raise ValueError(f"Snapshot id={snapshot_id} not found.")
        return cp

    def _upgrade_snapshot_schema(self, abs_snapshot: str) -> Optional[str]:
        """Copy the snapshot to a temp file and run alembic upgrade head on it.

        Args:
            abs_snapshot: Absolute path to the read-only snapshot .sqlite file.

        Returns:
            Path to the upgraded temp file, or None if the upgrade failed.
        """
        tmp_dir = tempfile.mkdtemp(prefix="pixlstash_restore_")
        tmp_snapshot = os.path.join(tmp_dir, "snapshot.sqlite")
        try:
            shutil.copy2(abs_snapshot, tmp_snapshot)
        except Exception as exc:
            logger.error(
                "RestoreService: failed to copy snapshot to temp dir: %s",
                exc,
                exc_info=True,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

        try:
            from alembic import command
            from alembic.config import Config
            from pathlib import Path

            module_dir = Path(__file__).resolve().parent.parent
            repo_root = module_dir.parent
            for candidate_ini, candidate_migrations in (
                (repo_root / "alembic.ini", repo_root / "migrations"),
                (module_dir / "alembic.ini", module_dir / "migrations"),
            ):
                if candidate_ini.exists() and candidate_migrations.exists():
                    alembic_ini = candidate_ini
                    migrations_dir = candidate_migrations
                    break
            else:
                raise RuntimeError("Alembic config not found for snapshot upgrade.")

            config = Config(str(alembic_ini))
            config.set_main_option("script_location", str(migrations_dir))
            config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_snapshot}")
            with _ALEMBIC_UPGRADE_LOCK:
                command.upgrade(config, "head")
            # Snapshot and convert back to rollback journal so the
            # main file contains all data without a WAL sidecar.
            import sqlite3

            with sqlite3.connect(tmp_snapshot) as _conn:
                _conn.execute("PRAGMA wal_snapshot(TRUNCATE)")
                _conn.execute("PRAGMA journal_mode=DELETE")
            logger.info(
                "RestoreService: snapshot schema upgraded to head at %s",
                tmp_snapshot,
            )
            return tmp_snapshot
        except Exception as exc:
            logger.error(
                "RestoreService: snapshot schema upgrade failed: %s",
                exc,
                exc_info=True,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

    def _find_missing_file_ids(self, abs_snapshot: str, vault_root: str) -> list[int]:
        """Return Picture IDs from the snapshot whose files are absent on disk.

        Args:
            abs_snapshot: Absolute path to the (possibly upgraded) snapshot.
            vault_root: Root directory of the vault image files.

        Returns:
            List of Picture IDs with missing files.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        missing: list[int] = []
        try:
            engine = create_engine(f"sqlite:///{abs_snapshot}", echo=False)
            with Session(engine) as session:
                pictures = session.exec(select(Picture)).all()
                for pic in pictures:
                    if not pic.file_path:
                        continue
                    try:
                        resolved = ImageUtils.resolve_picture_path(
                            vault_root, pic.file_path
                        )
                        if not os.path.isfile(resolved):
                            missing.append(pic.id)
                    except Exception as exc:
                        logger.debug(
                            "RestoreService: could not resolve path for picture %s: %s",
                            pic.id,
                            exc,
                        )
            engine.dispose()
        except Exception as exc:
            logger.error(
                "RestoreService: failed to scan snapshot for missing files: %s",
                exc,
                exc_info=True,
            )
        return missing

    def _swap_database(self, live_db_path: str, new_db_path: str) -> None:
        """Replace the live SQLite file with *new_db_path*.

        Disposes the live engine, atomically swaps the new file into the live
        path, and re-creates the engine.

        The new DB is first copied to a sibling temp file on the same
        filesystem and then moved into place with ``os.replace`` (an atomic
        rename within a filesystem).  This guarantees the live file is always
        either the old or the new database — a crash mid-copy can never leave
        it truncated or partially written.

        Args:
            live_db_path: Absolute path to the live database file.
            new_db_path: Absolute path to the replacement database file.
        """
        db = self._vault.db
        staged_db_path = live_db_path + ".new"
        try:
            # Fence out immediate reads for the whole swap: this waits for any
            # in-flight run_immediate_read_task to finish and blocks new ones,
            # so none opens a session on the disposed engine or hits the file
            # while it is being replaced.
            with db.exclusive_engine_access():
                # Dispose engine and all pooled connections before touching the file.
                db._engine.dispose()
                # Remove stale WAL/SHM files so the new DB starts clean.
                for suffix in ("-wal", "-shm"):
                    stale = live_db_path + suffix
                    if os.path.exists(stale):
                        os.remove(stale)
                shutil.copy2(new_db_path, staged_db_path)
                os.replace(staged_db_path, live_db_path)
                # Recreate engine
                from sqlalchemy import event as sa_event
                from pixlstash.database import init_database

                db._engine = create_engine(
                    f"sqlite:///{live_db_path}",
                    echo=False,
                    connect_args={"timeout": 30},
                )
                sa_event.listen(db._engine, "connect", init_database)
            logger.info("RestoreService: DB swap complete, engine re-created.")
        except Exception as exc:
            logger.error("RestoreService: DB swap failed: %s", exc, exc_info=True)
            raise
        finally:
            try:
                if os.path.exists(staged_db_path):
                    os.remove(staged_db_path)
                os.remove(new_db_path)
                shutil.rmtree(os.path.dirname(new_db_path), ignore_errors=True)
            except Exception as exc:
                logger.warning(
                    "RestoreService: failed to clean up temp DB swap files: %s",
                    exc,
                )

    def _restore_resource_from_snapshot(
        self,
        abs_snapshot: str,
        snapshot_id: int,
        resource_type: str,
        resource_id: int,
        vault_root: str,
    ) -> RestoreReport:
        """Upsert resource rows from the snapshot into the live database.

        Args:
            abs_snapshot: Absolute path to the upgraded snapshot.
            snapshot_id: Original snapshot ID.
            resource_type: Resource type string.
            resource_id: Primary key.
            vault_root: Vault root directory for file existence checks.

        Returns:
            RestoreReport with counts.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        report = RestoreReport(
            snapshot_id=snapshot_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )

        snap_engine = create_engine(f"sqlite:///{abs_snapshot}", echo=False)
        try:
            with Session(snap_engine) as snap_session:
                if resource_type == "picture":
                    picture_ids = [resource_id]
                elif resource_type == "picture_set":
                    members = snap_session.exec(
                        select(PictureSetMember).where(
                            PictureSetMember.picture_set_id == resource_id
                        )
                    ).all()
                    picture_ids = [m.picture_id for m in members]
                elif resource_type == "project":
                    # Collect all picture_set_ids in the project.
                    from pixlstash.db_models.picture_project import (
                        PictureProjectMember as PPM,
                    )

                    ppm_rows = snap_session.exec(
                        select(PPM).where(PPM.project_id == resource_id)
                    ).all()
                    picture_ids = [p.picture_id for p in ppm_rows]
                elif resource_type == "character":
                    picture_ids = []
                else:
                    picture_ids = []

                # Filter to pictures whose files exist on disk.
                valid_picture_ids: list[int] = []
                for pid in picture_ids:
                    pic = snap_session.get(Picture, pid)
                    if pic is None:
                        continue
                    if pic.file_path:
                        try:
                            resolved = ImageUtils.resolve_picture_path(
                                vault_root, pic.file_path
                            )
                            if not os.path.isfile(resolved):
                                report.missing_files_count += 1
                                logger.info(
                                    "RestoreService: skipping picture id=%d "
                                    "(file missing: %s)",
                                    pid,
                                    pic.file_path,
                                )
                                continue
                        except Exception:
                            report.missing_files_count += 1
                            continue
                    valid_picture_ids.append(pid)

                # Collect all rows to upsert.
                snap_rows = self._collect_rows_for_upsert(
                    snap_session,
                    resource_type,
                    resource_id,
                    valid_picture_ids,
                )
        finally:
            snap_engine.dispose()

        # Upsert in the live DB.
        with self._vault.db.write_reason(
            f"restore snapshot {snapshot_id} {resource_type} {resource_id}"
        ):
            upserted = self._vault.db.run_task(
                lambda session: self._upsert_rows(
                    session, snap_rows, valid_picture_ids
                ),
                priority=0,
            )
        report.upserted_count = upserted
        return report

    def _collect_rows_for_upsert(
        self,
        snap_session: Session,
        resource_type: str,
        resource_id: int,
        valid_picture_ids: list[int],
    ) -> dict:
        """Collect rows from the snapshot for all tables we need to upsert.

        Args:
            snap_session: Read session on the snapshot.
            resource_type: The resource type being restored.
            resource_id: Primary key of the resource.
            valid_picture_ids: Filtered list of picture IDs to restore.

        Returns:
            Dict mapping table name → list of row dicts.
        """
        rows: dict = {
            "pictures": [],
            "faces": [],
            "tags": [],
            "picture_set_members": [],
            "picture_project_members": [],
            "character": None,
            "picture_set": None,
            "project": None,
        }

        # Pictures and their dependents.
        for pid in valid_picture_ids:
            pic = snap_session.get(Picture, pid)
            if pic:
                rows["pictures"].append(pic)
            faces = snap_session.exec(select(Face).where(Face.picture_id == pid)).all()
            rows["faces"].extend(faces)
            tags = snap_session.exec(select(Tag).where(Tag.picture_id == pid)).all()
            rows["tags"].extend(tags)
            psms = snap_session.exec(
                select(PictureSetMember).where(PictureSetMember.picture_id == pid)
            ).all()
            rows["picture_set_members"].extend(psms)
            ppms = snap_session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == pid
                )
            ).all()
            rows["picture_project_members"].extend(ppms)

        if resource_type == "picture_set":
            ps = snap_session.get(PictureSet, resource_id)
            rows["picture_set"] = ps
        elif resource_type == "project":
            proj = snap_session.get(Project, resource_id)
            rows["project"] = proj
        elif resource_type == "character":
            char = snap_session.get(Character, resource_id)
            rows["character"] = char

        return rows

    def _upsert_rows(
        self, session: Session, snap_rows: dict, valid_picture_ids: list[int]
    ) -> int:
        """Upsert all collected snapshot rows into the live session.

        Args:
            session: Live writer session.
            snap_rows: Collected rows from the snapshot.
            valid_picture_ids: IDs whose derived columns should be NULL-reset.

        Returns:
            Total number of objects upserted.
        """

        count = 0

        def _merge(obj):
            nonlocal count
            if obj is None:
                return
            # Detach from the snapshot session by expunging and merging.
            session.merge(obj)
            count += 1

        if snap_rows.get("project"):
            _merge(snap_rows["project"])
        if snap_rows.get("picture_set"):
            _merge(snap_rows["picture_set"])
        if snap_rows.get("character"):
            _merge(snap_rows["character"])

        for pic in snap_rows.get("pictures", []):
            _merge(pic)
        for face in snap_rows.get("faces", []):
            _merge(face)
        # Tags are deleted and re-inserted rather than merged to avoid UNIQUE
        # constraint violations when snapshot and live have different row IDs
        # for the same (picture_id, tag) pair.
        if valid_picture_ids:
            for existing_tag in session.exec(
                select(Tag).where(Tag.picture_id.in_(valid_picture_ids))
            ).all():
                session.delete(existing_tag)
            session.flush()
        for tag in snap_rows.get("tags", []):
            session.add(Tag(picture_id=tag.picture_id, tag=tag.tag))
            count += 1
        for psm in snap_rows.get("picture_set_members", []):
            _merge(psm)
        for ppm in snap_rows.get("picture_project_members", []):
            _merge(ppm)

        # NULL-reset derived columns for restored pictures.
        for pid in valid_picture_ids:
            pic = session.get(Picture, pid)
            if pic:
                for col in _PICTURE_DERIVED_COLUMNS:
                    if hasattr(pic, col):
                        setattr(pic, col, None)

        session.commit()
        return count

    # --- Preview helpers --------------------------------------------------

    def _compute_full_preview(
        self,
        snap_session: Session,
        preview: RestorePreview,
        vault_root: str,
    ) -> None:
        """Populate *preview* by diffing all pictures in snap vs live DB.

        Caps the per-resource list at 200 entries.  Summary and warnings are
        also populated here.

        Args:
            snap_session: Read session on the snapshot.
            preview: Preview object to mutate.
            vault_root: Vault root for file-existence checks.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        MAX_RESOURCES = 200
        snap_pictures = snap_session.exec(select(Picture)).all()
        snap_ids = {p.id for p in snap_pictures}

        live_ids_result = self._vault.db.run_immediate_read_task(
            lambda session: set(session.exec(select(Picture.id)).all())
        )

        # Bulk-load tag sets from snapshot and live for efficient comparison.
        _snap_tag_map: dict[int, set] = {}
        for _pid, _tag in snap_session.exec(select(Tag.picture_id, Tag.tag)).all():
            _snap_tag_map.setdefault(_pid, set()).add(_tag)
        _snap_tag_sets: dict[int, frozenset] = {
            k: frozenset(v) for k, v in _snap_tag_map.items()
        }
        _snap_ids_list = list({p.id for p in snap_pictures})

        def _load_live_tag_sets(session) -> dict:
            _live: dict[int, set] = {}
            for _pid, _tag in session.exec(
                select(Tag.picture_id, Tag.tag).where(
                    Tag.picture_id.in_(_snap_ids_list)
                )
            ).all():
                _live.setdefault(_pid, set()).add(_tag)
            return {k: frozenset(v) for k, v in _live.items()}

        _live_tag_sets: dict[int, frozenset] = self._vault.db.run_immediate_read_task(
            _load_live_tag_sets
        )

        missing_files = 0
        pictures_to_revert = 0
        pictures_to_recreate = 0
        pictures_to_delete = 0

        # Pictures in snapshot (to revert or recreate).
        for snap_pic in snap_pictures:
            file_ok = True
            if snap_pic.file_path:
                try:
                    resolved = ImageUtils.resolve_picture_path(
                        vault_root, snap_pic.file_path
                    )
                    file_ok = os.path.isfile(resolved)
                except Exception:
                    file_ok = False

            if not file_ok:
                missing_files += 1
                preview.warnings.append(
                    f"Picture id={snap_pic.id} file missing on disk; "
                    "row will be removed after restore."
                )
                continue

            exists_in_live = snap_pic.id in live_ids_result
            if exists_in_live:
                pictures_to_revert += 1
            else:
                pictures_to_recreate += 1

            if len(preview.resources) < MAX_RESOURCES:
                changed = self._diff_picture(snap_pic, exists_in_live)
                if exists_in_live:
                    snap_tags = _snap_tag_sets.get(snap_pic.id, frozenset())
                    live_tags = _live_tag_sets.get(snap_pic.id, frozenset())
                    if snap_tags != live_tags:
                        changed.append("tags")
                preview.resources.append(
                    ResourcePreview(
                        type="picture",
                        id=snap_pic.id,
                        exists_in_live=exists_in_live,
                        exists_in_snapshot=True,
                        file_on_disk=True,
                        changed_fields=changed,
                        dependent_counts=self._picture_dependent_counts(
                            snap_session, snap_pic.id
                        ),
                    )
                )

        # Pictures in live but not in snapshot (will be deleted by full restore).
        for live_id in live_ids_result - snap_ids:
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

        total_shown = len(preview.resources)
        total_affected = len(snap_pictures) + len(live_ids_result - snap_ids)
        if total_affected > total_shown:
            preview.warnings.append(
                f"Showing {total_shown} of {total_affected} affected resources."
            )

        if missing_files:
            preview.warnings.append(
                f"{missing_files} picture file(s) missing on disk; "
                "those rows will be removed after restore."
            )

        preview.summary = {
            "pictures_to_revert": pictures_to_revert,
            "pictures_to_recreate": pictures_to_recreate,
            "pictures_to_delete": pictures_to_delete,
            "missing_files": missing_files,
        }

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
                select(PictureSetMember).where(
                    PictureSetMember.picture_set_id == resource_id
                )
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
            # Derived/regenerable scores and internal hash — not user-controlled
            # metadata, so they should not surface as differences in the preview.
            "aesthetic_score",
            "smart_score",
            "text_score",
            "metadata_hash",
        }
        changed: list[str] = []
        for col in snap_pic.__fields__:
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

        Idempotent — safe to call multiple times.

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

"""Service layer for restoring vault state from a checkpoint snapshot.

Supports full-database restore and per-resource (picture, picture_set,
project, character) restore.  All restores run ``alembic upgrade head`` on
the snapshot file before any data work to handle cross-version snapshots.
"""

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sqlmodel import Session, create_engine, select, SQLModel

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    PictureProjectMember,
    PictureStack,
    Project,
    Tag,
)
from pixlstash.db_models.checkpoint import Checkpoint
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

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
        checkpoint_id: ID of the checkpoint that was restored.
        resource_type: ``'full'``, ``'picture'``, ``'picture_set'``,
            ``'project'``, or ``'character'``.
        resource_id: Primary key of the specific resource (None for full
            restore).
        missing_files_count: Number of Picture rows skipped because their
            files were absent on disk.
        upserted_count: Number of rows upserted (per-resource restores only).
        errors: Non-fatal error messages accumulated during the restore.
    """

    checkpoint_id: int
    resource_type: str
    resource_id: Optional[int] = None
    missing_files_count: int = 0
    upserted_count: int = 0
    errors: list[str] = field(default_factory=list)


class RestoreService:
    """Restores vault metadata from a checkpoint snapshot.

    Attributes:
        _vault: Back-reference to the owning Vault.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the service.

        Args:
            vault: The owning Vault instance.
        """
        self._vault = vault

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def restore_full(
        self, checkpoint_id: int, dry_run: bool = False
    ) -> RestoreReport:
        """Replace the live database with a checkpoint snapshot.

        Steps:
        1. Take an OPPORTUNISTIC safety checkpoint of the current state.
        2. Upgrade the snapshot schema to the current Alembic head.
        3. Scan snapshot Picture rows; collect missing-file IDs.
        4. Dispose the live engine, copy the snapshot over the live DB, and
           re-open it.
        5. Delete rows whose files are missing.
        6. NULL-reset derived columns so the WorkPlanner regenerates them.
        7. Resume the TaskRunner and emit ``RESTORE_COMPLETED``.

        Args:
            checkpoint_id: ID of the checkpoint to restore.
            dry_run: If True, perform all steps except the actual DB swap and
                return a report without modifying the live database.

        Returns:
            A ``RestoreReport`` summarising the operation.

        Raises:
            ValueError: If the checkpoint is not found or the snapshot file is
                missing from disk.
        """
        vault_root = self._vault.image_root
        db = self._vault.db

        cp = self._get_checkpoint_or_raise(checkpoint_id)
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(
                f"Snapshot file not found on disk: {abs_snapshot}"
            )

        report = RestoreReport(
            checkpoint_id=checkpoint_id,
            resource_type="full",
        )

        # 1. Safety checkpoint of current state.
        try:
            self._vault.checkpoint_service.create_checkpoint("OPPORTUNISTIC")
        except Exception as exc:
            logger.warning(
                "RestoreService: safety checkpoint failed (continuing): %s", exc
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
            "RestoreService: swapping live DB with snapshot (checkpoint id=%d)",
            checkpoint_id,
        )

        def _do_swap(session):
            # Close this task's session connection so no lock remains on the
            # live DB file when we dispose the engine and copy the snapshot.
            session.close()
            with db.write_reason(f"restore checkpoint {checkpoint_id}"):
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
                    "checkpoint_id": checkpoint_id,
                    "resource_type": "full",
                    "missing_files_count": report.missing_files_count,
                },
            )
        except Exception as exc:
            logger.warning(
                "RestoreService: failed to emit RESTORE_COMPLETED: %s", exc
            )

        logger.info(
            "RestoreService: full restore from checkpoint %d completed "
            "(%d missing files).",
            checkpoint_id,
            report.missing_files_count,
        )
        return report

    def restore_resource(
        self,
        checkpoint_id: int,
        resource_type: str,
        resource_id: int,
    ) -> RestoreReport:
        """Restore a single resource from a checkpoint snapshot.

        Supported *resource_type* values:
        - ``'picture'``  — restores the Picture row plus Face, Tag,
          PictureSetMember, and PictureProjectMember dependents.
        - ``'picture_set'`` — restores the PictureSet row and all member
          pictures (recursive picture restore).
        - ``'project'`` — restores the Project row plus all PictureSets,
          Characters, and Picture members.
        - ``'character'`` — restores the Character row.

        Args:
            checkpoint_id: ID of the checkpoint to restore from.
            resource_type: One of ``'picture'``, ``'picture_set'``,
                ``'project'``, or ``'character'``.
            resource_id: Primary key of the resource to restore.

        Returns:
            A ``RestoreReport`` summarising the operation.

        Raises:
            ValueError: If the checkpoint is not found or ``resource_type`` is
                invalid.
        """
        vault_root = self._vault.image_root
        cp = self._get_checkpoint_or_raise(checkpoint_id)
        abs_snapshot = os.path.join(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(
                f"Snapshot file not found on disk: {abs_snapshot}"
            )

        if resource_type not in ("picture", "picture_set", "project", "character"):
            raise ValueError(
                f"Invalid resource_type '{resource_type}'. "
                "Must be one of: picture, picture_set, project, character."
            )

        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            report = RestoreReport(
                checkpoint_id=checkpoint_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            report.errors.append("Schema upgrade failed; aborting restore.")
            return report

        try:
            report = self._restore_resource_from_snapshot(
                upgraded_snapshot,
                checkpoint_id,
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
                    "checkpoint_id": checkpoint_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "missing_files_count": report.missing_files_count,
                    "upserted_count": report.upserted_count,
                },
            )
        except Exception as exc:
            logger.warning(
                "RestoreService: failed to emit RESTORE_COMPLETED: %s", exc
            )

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_checkpoint_or_raise(self, checkpoint_id: int) -> Checkpoint:
        cp = self._vault.checkpoint_service.get_checkpoint(checkpoint_id)
        if cp is None:
            raise ValueError(f"Checkpoint id={checkpoint_id} not found.")
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
                "RestoreService: failed to copy snapshot to temp dir: %s", exc,
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
            command.upgrade(config, "head")
            # Checkpoint and convert back to rollback journal so the
            # main file contains all data without a WAL sidecar.
            import sqlite3
            with sqlite3.connect(tmp_snapshot) as _conn:
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                _conn.execute("PRAGMA journal_mode=DELETE")
            logger.info(
                "RestoreService: snapshot schema upgraded to head at %s",
                tmp_snapshot,
            )
            return tmp_snapshot
        except Exception as exc:
            logger.error(
                "RestoreService: snapshot schema upgrade failed: %s", exc,
                exc_info=True,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

    def _find_missing_file_ids(
        self, abs_snapshot: str, vault_root: str
    ) -> list[int]:
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

        Disposes the live engine, copies the new file over the live path, and
        re-creates the engine.

        Args:
            live_db_path: Absolute path to the live database file.
            new_db_path: Absolute path to the replacement database file.
        """
        db = self._vault.db
        try:
            # Dispose engine and all pooled connections before touching the file.
            db._engine.dispose()
            # Remove stale WAL/SHM files so the new DB starts clean.
            for suffix in ("-wal", "-shm"):
                stale = live_db_path + suffix
                if os.path.exists(stale):
                    os.remove(stale)
            shutil.copy2(new_db_path, live_db_path)
            # Recreate engine
            from sqlalchemy import event as sa_event
            from pixlstash.database import init_database
            db._engine = create_engine(f"sqlite:///{live_db_path}", echo=False, connect_args={"timeout": 30})
            sa_event.listen(db._engine, "connect", init_database)
            logger.info(
                "RestoreService: DB swap complete, engine re-created."
            )
        except Exception as exc:
            logger.error(
                "RestoreService: DB swap failed: %s", exc, exc_info=True
            )
            raise
        finally:
            try:
                os.remove(new_db_path)
                shutil.rmtree(os.path.dirname(new_db_path), ignore_errors=True)
            except Exception:
                pass

    def _restore_resource_from_snapshot(
        self,
        abs_snapshot: str,
        checkpoint_id: int,
        resource_type: str,
        resource_id: int,
        vault_root: str,
    ) -> RestoreReport:
        """Upsert resource rows from the snapshot into the live database.

        Args:
            abs_snapshot: Absolute path to the upgraded snapshot.
            checkpoint_id: Original checkpoint ID.
            resource_type: Resource type string.
            resource_id: Primary key.
            vault_root: Vault root directory for file existence checks.

        Returns:
            RestoreReport with counts.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        report = RestoreReport(
            checkpoint_id=checkpoint_id,
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
                    from pixlstash.db_models.picture_project import PictureProjectMember as PPM
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
            f"restore checkpoint {checkpoint_id} {resource_type} {resource_id}"
        ):
            upserted = self._vault.db.run_task(
                lambda session: self._upsert_rows(session, snap_rows, valid_picture_ids),
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
            faces = snap_session.exec(
                select(Face).where(Face.picture_id == pid)
            ).all()
            rows["faces"].extend(faces)
            tags = snap_session.exec(
                select(Tag).where(Tag.picture_id == pid)
            ).all()
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
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
        for tag in snap_rows.get("tags", []):
            _merge(tag)
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

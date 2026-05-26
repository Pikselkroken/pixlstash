"""Service layer for vault checkpoint creation, listing, and GFS-style retention.

The CheckpointService creates full SQLite snapshots via ``VACUUM INTO``, writes
a JSON manifest sidecar, records a ``Checkpoint`` row in the live DB, and prunes
old snapshots according to the GFS retention constants.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Character, Picture, PictureSet, Project
from pixlstash.db_models.change_log import ChangeLog
from pixlstash.db_models.checkpoint import Checkpoint
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# GFS retention constants (v1 — not user-configurable yet)
# ---------------------------------------------------------------------------
GFS_KEEP_DAILY: int = 7
GFS_KEEP_WEEKLY: int = 4  # most-recent Sunday of each of the last 4 weeks
GFS_KEEP_MONTHLY: int = 12  # first-of-month snapshot for the last 12 months

# Minimum hours between opportunistic checkpoints.
OPPORTUNISTIC_MIN_HOURS: float = 1.0


class CheckpointService:
    """Creates and manages vault-wide SQLite snapshots (checkpoints).

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

    def create_checkpoint(self, kind: str, label: Optional[str] = None) -> Checkpoint:
        """Create a full SQLite snapshot of the live vault database.

        The snapshot is written to
        ``<vault_root>/checkpoints/YYYY/MM/DD/<uuid>.sqlite`` via
        ``VACUUM INTO``.  A JSON manifest sidecar is written alongside it
        with resource counts, id-lists, and the ``max(ChangeLog.id)``
        covered by this snapshot.  The ``Checkpoint`` row is then inserted
        in the live DB, GFS retention is applied, and a
        ``CHECKPOINT_CREATED`` event is emitted.

        Args:
            kind: One of ``'DAILY'``, ``'WEEKLY'``, ``'MONTHLY'``,
                ``'MANUAL'``, or ``'OPPORTUNISTIC'``.
            label: Optional user label (only meaningful for MANUAL
                checkpoints).

        Returns:
            The newly created ``Checkpoint`` row.

        Raises:
            RuntimeError: If the vault database engine is unavailable.
        """
        vault_root = self._vault.image_root
        db = self._vault.db

        snapshot_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        date_dir = now.strftime("%Y/%m/%d")
        rel_dir = os.path.join("checkpoints", date_dir)
        abs_dir = os.path.join(vault_root, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)

        rel_snapshot = os.path.join(rel_dir, f"{snapshot_uuid}.sqlite")
        abs_snapshot = os.path.join(vault_root, rel_snapshot)
        rel_manifest = os.path.join(rel_dir, f"{snapshot_uuid}.manifest.json")
        abs_manifest = os.path.join(vault_root, rel_manifest)

        # --- VACUUM INTO snapshot -----------------------------------------
        logger.info(
            "CheckpointService: creating %s checkpoint → %s",
            kind,
            rel_snapshot,
        )
        db.run_task(
            lambda session: self._vacuum_into(session, abs_snapshot),
            priority=DBPriority.IMMEDIATE,
        )

        byte_size = os.path.getsize(abs_snapshot) if os.path.exists(abs_snapshot) else 0

        # --- Build manifest + insert Checkpoint row (one atomic writer task) ------
        # Running both in the same writer-thread task ensures max_changelog_id is
        # captured with no background writes interleaved between the read and the
        # Checkpoint INSERT.
        def _build_and_insert(session):
            manifest = self._build_manifest(session)
            manifest["snapshot_uuid"] = snapshot_uuid
            manifest["kind"] = kind
            manifest["created_at"] = now.isoformat()

            with open(abs_manifest, "w", encoding="utf-8") as _fh:
                json.dump(manifest, _fh, indent=2)

            _schema_version = manifest.get("schema_version", "")
            _picture_count = manifest.get("picture_count", 0)

            cp = Checkpoint(
                kind=kind,
                created_at=now,
                relative_path=rel_snapshot,
                manifest_relative_path=rel_manifest,
                byte_size=byte_size,
                picture_count=_picture_count,
                schema_version=_schema_version,
                label=label,
            )
            session.add(cp)
            session.commit()
            session.refresh(cp)
            return cp

        checkpoint = db.run_task(_build_and_insert, priority=DBPriority.IMMEDIATE)

        # --- GFS retention ------------------------------------------------
        self._apply_gfs_retention(now)

        # --- Emit event ---------------------------------------------------
        try:
            from pixlstash.event_types import EventType

            self._vault.emit_event(
                EventType.CHECKPOINT_CREATED, {"id": checkpoint.id, "kind": kind}
            )
        except Exception as exc:
            logger.warning(
                "CheckpointService: failed to emit CHECKPOINT_CREATED: %s", exc
            )

        logger.info(
            "CheckpointService: checkpoint %d created (%d bytes, %d pictures)",
            checkpoint.id,
            byte_size,
            checkpoint.picture_count,
        )
        return checkpoint

    def list_checkpoints(self) -> list[Checkpoint]:
        """Return all checkpoint rows ordered by creation time (newest first).

        Returns:
            List of Checkpoint rows.
        """
        return self._vault.db.run_immediate_read_task(
            lambda session: session.exec(
                select(Checkpoint).order_by(Checkpoint.created_at.desc())
            ).all()
        )

    def get_checkpoint(self, checkpoint_id: int) -> Optional[Checkpoint]:
        """Return the checkpoint with the given ID, or None if not found.

        Args:
            checkpoint_id: Primary key of the checkpoint.

        Returns:
            The Checkpoint row or None.
        """
        return self._vault.db.run_immediate_read_task(
            lambda session: session.get(Checkpoint, checkpoint_id)
        )

    def delete_checkpoint(self, checkpoint_id: int) -> bool:
        """Delete a checkpoint row and its snapshot file from disk.

        Args:
            checkpoint_id: Primary key of the checkpoint to delete.

        Returns:
            True if the checkpoint was found and deleted, False otherwise.
        """
        vault_root = self._vault.image_root

        def _delete(session):
            cp = session.get(Checkpoint, checkpoint_id)
            if cp is None:
                return False
            abs_snapshot = os.path.join(vault_root, cp.relative_path)
            abs_manifest = os.path.join(vault_root, cp.manifest_relative_path)
            session.delete(cp)
            session.commit()
            for path in (abs_snapshot, abs_manifest):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception as exc:
                    logger.warning(
                        "CheckpointService: could not remove %s: %s", path, exc
                    )
            return True

        deleted = self._vault.db.run_task(_delete, priority=DBPriority.IMMEDIATE)
        if deleted:
            try:
                from pixlstash.event_types import EventType

                self._vault.emit_event(
                    EventType.CHECKPOINT_DELETED, {"id": checkpoint_id}
                )
            except Exception as exc:
                logger.warning(
                    "CheckpointService: failed to emit CHECKPOINT_DELETED: %s", exc
                )
        return deleted

    def rename_checkpoint(
        self, checkpoint_id: int, label: Optional[str]
    ) -> Optional[Checkpoint]:
        """Update the label of an existing checkpoint.

        Args:
            checkpoint_id: Primary key of the checkpoint.
            label: New label string, or None to clear it.

        Returns:
            The updated Checkpoint row, or None if not found.
        """

        def _rename(session):
            cp = session.get(Checkpoint, checkpoint_id)
            if cp is None:
                return None
            cp.label = label
            session.add(cp)
            session.commit()
            session.refresh(cp)
            return cp

        return self._vault.db.run_task(_rename, priority=DBPriority.IMMEDIATE)

    def load_manifest(self, checkpoint_id: int) -> dict:
        """Load the JSON manifest sidecar for a checkpoint.

        Args:
            checkpoint_id: Primary key of the checkpoint.

        Returns:
            Parsed manifest dict, or empty dict if not found / unreadable.
        """
        cp = self.get_checkpoint(checkpoint_id)
        if cp is None:
            return {}
        abs_manifest = os.path.join(self._vault.image_root, cp.manifest_relative_path)
        try:
            with open(abs_manifest, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            logger.warning(
                "CheckpointService: could not read manifest for checkpoint %d: %s",
                checkpoint_id,
                exc,
            )
            return {}

    def get_live_schema_version(self) -> str:
        """Return the current alembic head revision of the live database.

        Returns:
            Schema version string, or empty string on failure.
        """
        try:
            from sqlalchemy import text

            return self._vault.db.run_immediate_read_task(
                lambda session: (
                    session.exec(
                        text("SELECT version_num FROM alembic_version LIMIT 1")
                    ).scalar()
                    or ""
                )
            )
        except Exception as exc:
            logger.warning(
                "CheckpointService: could not read live schema version: %s", exc
            )
            return ""

    def checkpoint_if_due(self, reason: str = "opportunistic") -> Optional[Checkpoint]:
        """Take an opportunistic checkpoint if more than OPPORTUNISTIC_MIN_HOURS have passed.

        Args:
            reason: Short label for logging.

        Returns:
            A new Checkpoint if one was created, or None if skipped.
        """
        checkpoints = self.list_checkpoints()
        if checkpoints:
            last = checkpoints[0]
            last_dt = last.created_at
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if age_hours < OPPORTUNISTIC_MIN_HOURS:
                logger.debug(
                    "CheckpointService: opportunistic checkpoint skipped "
                    "(last was %.1fh ago, minimum %.1fh)",
                    age_hours,
                    OPPORTUNISTIC_MIN_HOURS,
                )
                return None
        logger.info("CheckpointService: creating opportunistic checkpoint (%s)", reason)
        return self.create_checkpoint("OPPORTUNISTIC")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _vacuum_into(self, session, abs_snapshot: str) -> None:
        """Run VACUUM INTO via a raw SQLite connection in the writer session."""
        conn = session.connection()
        raw = conn.connection.driver_connection  # underlying sqlite3.Connection
        raw.execute(f"VACUUM INTO '{abs_snapshot}'")

    def _build_manifest(self, session) -> dict:
        """Build the manifest dict capturing resource counts and ChangeLog head.

        Args:
            session: An active read session.

        Returns:
            Dict suitable for JSON serialisation.
        """
        from sqlalchemy import func, text

        picture_ids: list[int] = list(session.exec(select(Picture.id)).all())
        picture_set_count = session.exec(select(func.count(PictureSet.id))).one()
        project_count = session.exec(select(func.count(Project.id))).one()
        character_count = session.exec(select(func.count(Character.id))).one()

        # schema_version from alembic_version table
        try:
            result = session.exec(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            schema_version = result.scalar() or ""
        except Exception:
            schema_version = ""

        # max ChangeLog id at snapshot time
        # session.exec() with a scalar aggregate returns the value directly (int
        # or None), not a Row.  Using [0] on an int raises TypeError that was
        # previously swallowed, always producing None.
        try:
            max_changelog_id = session.exec(
                select(func.max(ChangeLog.id))
            ).one_or_none()
        except Exception:
            max_changelog_id = None

        return {
            "picture_count": len(picture_ids),
            "picture_ids": picture_ids,
            "picture_set_count": picture_set_count,
            "project_count": project_count,
            "character_count": character_count,
            "schema_version": schema_version,
            "max_changelog_id": max_changelog_id,
        }

    def _apply_gfs_retention(self, now: datetime) -> None:
        """Prune snapshots beyond GFS retention limits.

        Keeps:
        - ``GFS_KEEP_DAILY`` most-recent DAILY checkpoints.
        - ``GFS_KEEP_WEEKLY`` most-recent WEEKLY checkpoints.
        - ``GFS_KEEP_MONTHLY`` most-recent MONTHLY checkpoints.
        - All MANUAL and OPPORTUNISTIC checkpoints (user-managed).

        Args:
            now: Current UTC datetime (used only for logging).
        """

        def _prune(session):
            for kind, keep in (
                ("DAILY", GFS_KEEP_DAILY),
                ("WEEKLY", GFS_KEEP_WEEKLY),
                ("MONTHLY", GFS_KEEP_MONTHLY),
            ):
                rows = session.exec(
                    select(Checkpoint)
                    .where(Checkpoint.kind == kind)
                    .order_by(Checkpoint.created_at.desc())
                ).all()
                to_delete = rows[keep:]
                for cp in to_delete:
                    vault_root = self._vault.image_root
                    for rel_path in (cp.relative_path, cp.manifest_relative_path):
                        abs_path = os.path.join(vault_root, rel_path)
                        try:
                            if os.path.exists(abs_path):
                                os.remove(abs_path)
                        except Exception as exc:
                            logger.warning(
                                "CheckpointService: GFS prune could not remove %s: %s",
                                abs_path,
                                exc,
                            )
                    session.delete(cp)
                    logger.info(
                        "CheckpointService: GFS pruned %s checkpoint id=%d",
                        kind,
                        cp.id,
                    )
            session.commit()

            # Truncate old ChangeLog rows beyond the oldest retained checkpoint.
            from pixlstash.db_models.change_log import ChangeLog

            oldest_cp = session.exec(
                select(Checkpoint).order_by(Checkpoint.created_at.asc())
            ).first()
            if oldest_cp is not None:
                oldest_manifest_path = os.path.join(
                    self._vault.image_root, oldest_cp.manifest_relative_path
                )
                try:
                    with open(oldest_manifest_path, encoding="utf-8") as fh:
                        manifest = json.load(fh)
                    min_cl_id = manifest.get("max_changelog_id")
                    if min_cl_id is not None:
                        from sqlmodel import delete as sm_delete

                        session.exec(
                            sm_delete(ChangeLog).where(ChangeLog.id < min_cl_id)
                        )
                        session.commit()
                        logger.info(
                            "CheckpointService: truncated ChangeLog entries with id < %d",
                            min_cl_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "CheckpointService: could not truncate ChangeLog: %s", exc
                    )

        self._vault.db.run_task(_prune, priority=DBPriority.IMMEDIATE)

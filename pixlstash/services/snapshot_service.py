"""Service layer for vault snapshot creation, listing, and GFS-style retention.

The SnapshotService creates full SQLite snapshots via ``VACUUM INTO``, writes
a JSON manifest sidecar, records a ``Snapshot`` row in the live DB, and prunes
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
from pixlstash.db_models.snapshot import Snapshot
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

# Minimum hours between opportunistic snapshots.
OPPORTUNISTIC_MIN_HOURS: float = 1.0


class SnapshotService:
    """Creates and manages vault-wide SQLite snapshots (snapshots).

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

    def create_snapshot(self, kind: str, label: Optional[str] = None) -> Snapshot:
        """Create a full SQLite snapshot of the live vault database.

        The snapshot is written to
        ``<vault_root>/snapshots/YYYY/MM/DD/<uuid>.sqlite`` via
        ``VACUUM INTO``.  A JSON manifest sidecar is written alongside it
        with resource counts, id-lists, and the ``max(ChangeLog.id)``
        covered by this snapshot.  The ``Snapshot`` row is then inserted
        in the live DB, GFS retention is applied, and a
        ``SNAPSHOT_CREATED`` event is emitted.

        Args:
            kind: One of ``'DAILY'``, ``'WEEKLY'``, ``'MONTHLY'``,
                ``'MANUAL'``, or ``'OPPORTUNISTIC'``.
            label: Optional user label (only meaningful for MANUAL
                snapshots).

        Returns:
            The newly created ``Snapshot`` row.

        Raises:
            RuntimeError: If the vault database engine is unavailable.
        """
        vault_root = self._vault.image_root
        db = self._vault.db

        snapshot_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        date_dir = now.strftime("%Y/%m/%d")
        rel_dir = os.path.join("snapshots", date_dir)
        abs_dir = os.path.join(vault_root, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)

        rel_snapshot = os.path.join(rel_dir, f"{snapshot_uuid}.sqlite")
        abs_snapshot = os.path.join(vault_root, rel_snapshot)
        rel_manifest = os.path.join(rel_dir, f"{snapshot_uuid}.manifest.json")
        abs_manifest = os.path.join(vault_root, rel_manifest)

        # --- VACUUM + manifest + Snapshot row (one atomic writer task) --------
        # The VACUUM INTO, the manifest read (counts + max_changelog_id), and the
        # Snapshot INSERT all run in a single writer-thread task so they describe
        # one consistent point-in-time: no other write can interleave between the
        # snapshot file and the manifest it is paired with.  VACUUM must run first
        # because SQLite forbids VACUUM inside an open transaction.
        logger.info(
            "SnapshotService: creating %s snapshot → %s",
            kind,
            rel_snapshot,
        )

        def _create_and_record(session):
            self._vacuum_into(session, abs_snapshot)

            byte_size = (
                os.path.getsize(abs_snapshot) if os.path.exists(abs_snapshot) else 0
            )

            manifest = self._build_manifest(session)
            manifest["snapshot_uuid"] = snapshot_uuid
            manifest["kind"] = kind
            manifest["created_at"] = now.isoformat()

            with open(abs_manifest, "w", encoding="utf-8") as _fh:
                json.dump(manifest, _fh, indent=2)

            cp = Snapshot(
                kind=kind,
                created_at=now,
                relative_path=rel_snapshot,
                manifest_relative_path=rel_manifest,
                byte_size=byte_size,
                picture_count=manifest.get("picture_count", 0),
                schema_version=manifest.get("schema_version", ""),
                label=label,
            )
            session.add(cp)
            session.commit()
            session.refresh(cp)
            return cp

        snapshot = db.run_task(_create_and_record, priority=DBPriority.IMMEDIATE)

        # --- GFS retention ------------------------------------------------
        self._apply_gfs_retention(now)

        # --- Emit event ---------------------------------------------------
        try:
            from pixlstash.event_types import EventType

            self._vault.emit_event(
                EventType.SNAPSHOT_CREATED, {"id": snapshot.id, "kind": kind}
            )
        except Exception as exc:
            logger.warning("SnapshotService: failed to emit SNAPSHOT_CREATED: %s", exc)

        logger.info(
            "SnapshotService: snapshot %d created (%d bytes, %d pictures)",
            snapshot.id,
            snapshot.byte_size,
            snapshot.picture_count,
        )
        return snapshot

    def list_snapshots(self) -> list[Snapshot]:
        """Return all snapshot rows ordered by creation time (newest first).

        Returns:
            List of Snapshot rows.
        """
        return self._vault.db.run_immediate_read_task(
            lambda session: session.exec(
                select(Snapshot).order_by(Snapshot.created_at.desc())
            ).all()
        )

    def get_snapshot(self, snapshot_id: int) -> Optional[Snapshot]:
        """Return the snapshot with the given ID, or None if not found.

        Args:
            snapshot_id: Primary key of the snapshot.

        Returns:
            The Snapshot row or None.
        """
        return self._vault.db.run_immediate_read_task(
            lambda session: session.get(Snapshot, snapshot_id)
        )

    def delete_snapshot(self, snapshot_id: int) -> bool:
        """Delete a snapshot row and its snapshot file from disk.

        Args:
            snapshot_id: Primary key of the snapshot to delete.

        Returns:
            True if the snapshot was found and deleted, False otherwise.
        """
        vault_root = self._vault.image_root

        def _delete(session):
            cp = session.get(Snapshot, snapshot_id)
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
                        "SnapshotService: could not remove %s: %s", path, exc
                    )
            return True

        deleted = self._vault.db.run_task(_delete, priority=DBPriority.IMMEDIATE)
        if deleted:
            try:
                from pixlstash.event_types import EventType

                self._vault.emit_event(EventType.SNAPSHOT_DELETED, {"id": snapshot_id})
            except Exception as exc:
                logger.warning(
                    "SnapshotService: failed to emit SNAPSHOT_DELETED: %s", exc
                )
        return deleted

    def rename_snapshot(
        self, snapshot_id: int, label: Optional[str]
    ) -> Optional[Snapshot]:
        """Update the label of an existing snapshot.

        Args:
            snapshot_id: Primary key of the snapshot.
            label: New label string, or None to clear it.

        Returns:
            The updated Snapshot row, or None if not found.
        """

        def _rename(session):
            cp = session.get(Snapshot, snapshot_id)
            if cp is None:
                return None
            cp.label = label
            session.add(cp)
            session.commit()
            session.refresh(cp)
            return cp

        return self._vault.db.run_task(_rename, priority=DBPriority.IMMEDIATE)

    def load_manifest(self, snapshot_id: int) -> dict:
        """Load the JSON manifest sidecar for a snapshot.

        Args:
            snapshot_id: Primary key of the snapshot.

        Returns:
            Parsed manifest dict, or empty dict if not found / unreadable.
        """
        cp = self.get_snapshot(snapshot_id)
        if cp is None:
            return {}
        abs_manifest = os.path.join(self._vault.image_root, cp.manifest_relative_path)
        try:
            with open(abs_manifest, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            logger.warning(
                "SnapshotService: could not read manifest for snapshot %d: %s",
                snapshot_id,
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
                "SnapshotService: could not read live schema version: %s", exc
            )
            return ""

    def snapshot_if_due(self, reason: str = "opportunistic") -> Optional[Snapshot]:
        """Take an opportunistic snapshot if more than OPPORTUNISTIC_MIN_HOURS have passed.

        Only automatic snapshots (DAILY, WEEKLY, MONTHLY, OPPORTUNISTIC) are
        considered when checking timing — MANUAL snapshots are user-curated
        archives and should not suppress the opportunistic schedule.

        Args:
            reason: Short label for logging.

        Returns:
            A new Snapshot if one was created, or None if skipped.
        """
        auto_kinds = {"DAILY", "WEEKLY", "MONTHLY", "OPPORTUNISTIC"}
        auto_snapshots = [s for s in self.list_snapshots() if s.kind in auto_kinds]
        if auto_snapshots:
            last = auto_snapshots[0]
            last_dt = last.created_at
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if age_hours < OPPORTUNISTIC_MIN_HOURS:
                logger.debug(
                    "SnapshotService: opportunistic snapshot skipped "
                    "(last was %.1fh ago, minimum %.1fh)",
                    age_hours,
                    OPPORTUNISTIC_MIN_HOURS,
                )
                return None
        logger.info("SnapshotService: creating opportunistic snapshot (%s)", reason)
        return self.create_snapshot("OPPORTUNISTIC")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _vacuum_into(self, session, abs_snapshot: str) -> None:
        """Run VACUUM INTO via a raw SQLite connection in the writer session."""
        conn = session.connection()
        raw = conn.connection.driver_connection  # underlying sqlite3.Connection
        escaped = abs_snapshot.replace("'", "''")
        raw.execute(f"VACUUM INTO '{escaped}'")

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
        except Exception as exc:
            logger.warning(
                "SnapshotService: failed to read schema_version for manifest: %s",
                exc,
            )
            schema_version = ""

        # max ChangeLog id at snapshot time
        # session.exec() with a scalar aggregate returns the value directly (int
        # or None), not a Row.  Using [0] on an int raises TypeError that was
        # previously swallowed, always producing None.
        try:
            max_changelog_id = session.exec(
                select(func.max(ChangeLog.id))
            ).one_or_none()
        except Exception as exc:
            logger.warning(
                "SnapshotService: failed to read max_changelog_id for manifest: %s",
                exc,
            )
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
        - ``GFS_KEEP_DAILY`` most-recent DAILY snapshots.
        - ``GFS_KEEP_WEEKLY`` most-recent WEEKLY snapshots.
        - ``GFS_KEEP_MONTHLY`` most-recent MONTHLY snapshots.
        - All MANUAL and OPPORTUNISTIC snapshots (user-managed).

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
                    select(Snapshot)
                    .where(Snapshot.kind == kind)
                    .order_by(Snapshot.created_at.desc())
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
                                "SnapshotService: GFS prune could not remove %s: %s",
                                abs_path,
                                exc,
                            )
                    session.delete(cp)
                    logger.info(
                        "SnapshotService: GFS pruned %s snapshot id=%d",
                        kind,
                        cp.id,
                    )
            session.commit()

            # Truncate old ChangeLog rows beyond the oldest retained snapshot.
            from pixlstash.db_models.change_log import ChangeLog

            oldest_cp = session.exec(
                select(Snapshot).order_by(Snapshot.created_at.asc())
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
                            "SnapshotService: truncated ChangeLog entries with id < %d",
                            min_cl_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "SnapshotService: could not truncate ChangeLog: %s", exc
                    )

        self._vault.db.run_task(_prune, priority=DBPriority.IMMEDIATE)

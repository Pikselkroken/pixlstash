"""Service layer for vault snapshot creation, listing, and GFS-style retention.

The SnapshotService creates full SQLite snapshots via ``VACUUM INTO``, writes
a JSON manifest sidecar, records a ``Snapshot`` row in the live DB, and prunes
old snapshots according to the GFS retention constants.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlmodel import select

from pixlstash.database import DBPriority, _compute_picture_metadata_hash
from pixlstash.db_models import Character, Picture, PictureSet, Project
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.snapshot_compression import (
    COMPRESSED_SUFFIX,
    compress_snapshot,
    snapshot_scratch_dir,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# GFS retention constants (v1 — not user-configurable yet)
# ---------------------------------------------------------------------------
GFS_KEEP_DAILY: int = 7
GFS_KEEP_WEEKLY: int = 4  # most-recent Sunday of each of the last 4 weeks
GFS_KEEP_MONTHLY: int = 12  # first-of-month snapshot for the last 12 months
# OPPORTUNISTIC snapshots accumulate from safety-snapshot-before-restore and
# from snapshot_if_due() — without a cap they grow without bound. MANUAL
# snapshots are intentionally not capped: they are user-curated archives.
GFS_KEEP_OPPORTUNISTIC: int = 5

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
        with resource counts and id-lists.  The ``Snapshot`` row is then
        inserted in the live DB, GFS retention is applied, and a
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

        rel_snapshot = os.path.join(rel_dir, f"{snapshot_uuid}{COMPRESSED_SUFFIX}")
        abs_snapshot = os.path.join(vault_root, rel_snapshot)
        rel_manifest = os.path.join(rel_dir, f"{snapshot_uuid}.manifest.json")
        abs_manifest = os.path.join(vault_root, rel_manifest)
        # The per-picture hash map lives in its own sidecar (not the manifest)
        # so the snapshot-list endpoint — which parses every manifest for its
        # small resource counts — never pays to read a multi-MB hash blob.
        rel_hashes = os.path.join(rel_dir, f"{snapshot_uuid}.hashes.json")
        abs_hashes = os.path.join(vault_root, rel_hashes)

        # --- VACUUM + manifest + Snapshot row (one atomic writer task) --------
        # The VACUUM INTO, the manifest read (resource counts), and the Snapshot
        # INSERT all run in a single writer-thread task so they describe one
        # consistent point-in-time: no other write can interleave between the
        # snapshot file and the manifest it is paired with.  ``_vacuum_into``
        # uses its own dedicated connection (see its docstring) but still runs
        # inside this writer task, so the serialisation guarantee holds.
        logger.info(
            "SnapshotService: creating %s snapshot → %s",
            kind,
            rel_snapshot,
        )

        def _create_and_record(session):
            # VACUUM the live DB into a scratch .sqlite, drop the live
            # pipeline-state tables, then compress the archive to .sqlite.zst.
            # The expensive GPU-regenerated blobs (CLIP image/text embeddings,
            # InsightFace face features) are deliberately KEPT now — zstd makes
            # carrying them affordable (~3x smaller) so a restore no longer
            # triggers a full re-embedding / re-detection pass. Only the .zst
            # is retained; the scratch file is always removed.
            tmp_dir = tempfile.mkdtemp(
                prefix="pixlstash_snapshot_", dir=snapshot_scratch_dir(vault_root)
            )
            tmp_sqlite = os.path.join(tmp_dir, "snapshot.sqlite")
            try:
                self._vacuum_into(tmp_sqlite)
                self._prepare_snapshot_for_archive(tmp_sqlite)
                byte_size = compress_snapshot(tmp_sqlite, abs_snapshot)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

            manifest, picture_hashes = self._build_manifest(session)
            manifest["snapshot_uuid"] = snapshot_uuid
            manifest["kind"] = kind
            manifest["created_at"] = now.isoformat()

            with open(abs_manifest, "w", encoding="utf-8") as _fh:
                json.dump(manifest, _fh, indent=2)

            # Hash-map sidecar (separate so the list endpoint's manifest reads
            # stay lean). compact JSON — no indent — to keep it small.
            with open(abs_hashes, "w", encoding="utf-8") as _fh:
                json.dump(picture_hashes, _fh, separators=(",", ":"))

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

    def snapshots_containing(self, picture_ids) -> list[dict]:
        """Return snapshots whose manifest still references any of *picture_ids*.

        Used after a permanent purge to tell the user which snapshots continue
        to hold metadata (tags, description, captions, …) for the pictures they
        just deleted. The snapshot archives are not scrubbed, so deleting those
        snapshots is currently the way to erase that retained metadata.

        Discovery reads only the JSON manifests (which list each snapshot's
        ``picture_ids``) — no snapshot database is opened or decompressed.

        Args:
            picture_ids: Live picture IDs that were just purged.

        Returns:
            List of ``{"id", "kind", "label", "created_at", "matched_count"}``
            dicts, one per snapshot containing at least one of the IDs, ordered
            newest first.
        """
        wanted = {int(p) for p in picture_ids if p is not None}
        if not wanted:
            return []
        result: list[dict] = []
        for cp in self.list_snapshots():
            manifest = self.load_manifest(cp.id)
            manifest_ids = manifest.get("picture_ids") or []
            matched = wanted.intersection(manifest_ids)
            if matched:
                result.append(
                    {
                        "id": cp.id,
                        "kind": cp.kind,
                        "label": cp.label,
                        "created_at": cp.created_at.isoformat(),
                        "matched_count": len(matched),
                    }
                )
        return result

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
            abs_hashes = os.path.join(
                vault_root, self._hashes_relative_path(cp.manifest_relative_path)
            )
            session.delete(cp)
            session.commit()
            for path in (abs_snapshot, abs_manifest, abs_hashes):
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

    @staticmethod
    def _hashes_relative_path(manifest_relative_path: str) -> str:
        """Derive the hash-sidecar path from a snapshot's manifest path.

        ``<uuid>.manifest.json`` → ``<uuid>.hashes.json`` in the same dir.

        Args:
            manifest_relative_path: The snapshot's ``manifest_relative_path``.

        Returns:
            The relative path of the per-picture hash sidecar.
        """
        if manifest_relative_path.endswith(".manifest.json"):
            return manifest_relative_path[: -len(".manifest.json")] + ".hashes.json"
        return manifest_relative_path + ".hashes.json"

    def load_picture_hashes(self, snapshot_id: int) -> dict:
        """Load the per-picture ``{str(id): metadata_hash}`` sidecar.

        Returns an empty dict for legacy snapshots that predate the sidecar
        (the caller then falls back to reading hashes from the snapshot file).

        Args:
            snapshot_id: Primary key of the snapshot.

        Returns:
            Parsed hash map, or empty dict if not found / unreadable.
        """
        cp = self.get_snapshot(snapshot_id)
        if cp is None:
            return {}
        abs_hashes = os.path.join(
            self._vault.image_root,
            self._hashes_relative_path(cp.manifest_relative_path),
        )
        if not os.path.exists(abs_hashes):
            return {}
        try:
            with open(abs_hashes, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            logger.warning(
                "SnapshotService: could not read hash sidecar for snapshot %d: %s",
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

    def _vacuum_into(self, abs_snapshot: str) -> None:
        """Write a clean copy of the live DB to *abs_snapshot* via ``VACUUM INTO``.

        Runs on a dedicated short-lived SQLite connection that is closed in a
        ``finally`` — NOT the writer session's pooled connection.  SQLite keeps
        an OS file handle on the VACUUM INTO *destination* alive on whichever
        connection issued the statement until that connection is closed.  The
        writer session's connection lives in the engine pool for the whole
        process lifetime, so issuing VACUUM INTO on it left the freshly-written
        snapshot file open indefinitely; Windows then refuses to delete it
        (``WinError 32``), which is why ``shutil.rmtree`` of the snapshots dir
        failed in CI.  POSIX allows unlinking an open file, so this only ever
        bit Windows.  A dedicated connection releases the handle immediately on
        ``close()``.

        The caller already runs this inside the single-threaded writer task, so
        reading the live DB on a second connection cannot race a concurrent
        write, and WAL mode lets that read observe all committed rows.

        Args:
            abs_snapshot: Absolute path of the snapshot file to create.
        """
        db_path = self._vault.db._db_path
        escaped = abs_snapshot.replace("'", "''")
        # isolation_level=None keeps the connection in autocommit so VACUUM INTO
        # is not wrapped in an implicit transaction (SQLite forbids VACUUM inside
        # an open transaction).
        conn = sqlite3.connect(db_path, isolation_level=None)
        try:
            conn.execute(f"VACUUM INTO '{escaped}'")
        finally:
            conn.close()

    # Picture columns NULLed before archiving a snapshot. Now EMPTY: the
    # CLIP image/text embeddings and the cheap derived scores used to be
    # stripped (and NULL-reset on restore) to save disk, but that forced a
    # full GPU re-embedding pass on every restore. They are now KEPT inside
    # the snapshot and the whole archive is zstd-compressed instead, so a
    # restore comes back fully populated. Left as a tuple so the stripping
    # loop below stays a no-op rather than special-casing the empty case.
    _STRIP_PICTURE_COLUMNS: tuple[str, ...] = ()

    # Tables whose entire contents are dropped from a snapshot. These are
    # all regenerable / pipeline-state tables — keeping the snapshot's
    # rows would either waste disk on values the workers will recompute
    # (``picturelikeness``) or stomp on the live pipeline's progress
    # tracking when the restore swaps the file in (``picturelikenessqueue``
    # / ``picturelikenessfrontier``).  The full-restore path captures the
    # live queue + frontier before the swap and reinserts a reconciled
    # set after.
    _STRIP_TABLES_DELETE_ALL: tuple[str, ...] = (
        "picturelikeness",
        "picturelikenessqueue",
        "picturelikenessfrontier",
    )

    @classmethod
    def _prepare_snapshot_for_archive(cls, abs_snapshot: str) -> None:
        """Drop live pipeline-state tables from *abs_snapshot* and re-VACUUM.

        Empties the ``picturelikeness`` / ``picturelikenessqueue`` /
        ``picturelikenessfrontier`` tables (live similarity progress that the
        restore path reconstructs from the live DB) and re-VACUUMs to reclaim
        the freed pages. The expensive per-picture blobs (embeddings, face
        features, scores) are intentionally retained — see
        ``_STRIP_PICTURE_COLUMNS``.

        Failure is logged and swallowed: an unprepared snapshot is still
        correct, just larger.  The file is opened with a dedicated connection
        so the writer thread's session isn't disturbed.

        Args:
            abs_snapshot: Absolute path to the freshly-VACUUMed scratch
                snapshot (before compression).
        """
        try:
            conn = sqlite3.connect(abs_snapshot)
        except Exception as exc:
            logger.warning(
                "SnapshotService: could not open snapshot %s for stripping: %s",
                abs_snapshot,
                exc,
            )
            return
        try:
            # Probe existing columns so we never UPDATE a column the
            # snapshot's schema doesn't have (older snapshots restored
            # via the alembic-upgrade path may pre-date some columns).
            cols_present = {
                row[1] for row in conn.execute("PRAGMA table_info(picture)").fetchall()
            }
            null_cols = [c for c in cls._STRIP_PICTURE_COLUMNS if c in cols_present]
            if null_cols:
                set_clause = ", ".join(f"{c} = NULL" for c in null_cols)
                conn.execute(f"UPDATE picture SET {set_clause}")

            # Drop regenerable / pipeline-state tables. Only those that
            # actually exist in this snapshot's schema — older snapshots
            # may pre-date some of them.
            existing_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for table_name in cls._STRIP_TABLES_DELETE_ALL:
                if table_name in existing_tables:
                    conn.execute(f"DELETE FROM {table_name}")

            conn.commit()
            # VACUUM reclaims the pages freed by the UPDATEs and DELETEs
            # above. SQLite requires VACUUM outside a transaction; the
            # explicit commit above plus isolation_level reset handle that.
            conn.isolation_level = None
            conn.execute("VACUUM")
        except Exception as exc:
            logger.warning(
                "SnapshotService: failed to strip regenerable blobs from %s: %s",
                abs_snapshot,
                exc,
                exc_info=True,
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _build_manifest(self, session) -> tuple[dict, dict]:
        """Build the manifest dict and the per-picture hash map.

        Args:
            session: An active read session.

        Returns:
            ``(manifest, picture_hashes)``. The manifest holds resource counts
            and the id list (written to the ``.manifest.json``); the hash map
            is ``{str(picture_id): metadata_hash}`` (written to the separate
            ``.hashes.json`` sidecar so the list endpoint's manifest reads stay
            lean).
        """
        from sqlalchemy import func, text

        # Pull id + metadata_hash together. The hash map lets the interactive
        # restore preview and hash-compare read hashes from the sidecar and
        # never decompress the snapshot archive. NULL hashes (legacy rows the
        # after_flush hook hasn't stamped yet) are computed on the spot so the
        # map is complete.
        hash_rows = session.exec(select(Picture.id, Picture.metadata_hash)).all()
        picture_ids: list[int] = [row[0] for row in hash_rows]
        picture_hashes: dict[str, str] = {}
        for pid, h in hash_rows:
            if h is None:
                h = _compute_picture_metadata_hash(session, pid)
            if h is not None:
                picture_hashes[str(pid)] = h
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

        manifest = {
            "picture_count": len(picture_ids),
            "picture_ids": picture_ids,
            "picture_set_count": picture_set_count,
            "project_count": project_count,
            "character_count": character_count,
            "schema_version": schema_version,
        }
        return manifest, picture_hashes

    def _apply_gfs_retention(self, now: datetime) -> None:
        """Prune snapshots beyond GFS retention limits.

        Keeps:
        - ``GFS_KEEP_DAILY`` most-recent DAILY snapshots.
        - ``GFS_KEEP_WEEKLY`` most-recent WEEKLY snapshots.
        - ``GFS_KEEP_MONTHLY`` most-recent MONTHLY snapshots.
        - ``GFS_KEEP_OPPORTUNISTIC`` most-recent OPPORTUNISTIC snapshots.
        - All MANUAL snapshots (user-curated; user must delete them manually).

        Args:
            now: Current UTC datetime (used only for logging).
        """

        def _prune(session):
            for kind, keep in (
                ("DAILY", GFS_KEEP_DAILY),
                ("WEEKLY", GFS_KEEP_WEEKLY),
                ("MONTHLY", GFS_KEEP_MONTHLY),
                ("OPPORTUNISTIC", GFS_KEEP_OPPORTUNISTIC),
            ):
                rows = session.exec(
                    select(Snapshot)
                    .where(Snapshot.kind == kind)
                    .order_by(Snapshot.created_at.desc())
                ).all()
                to_delete = rows[keep:]
                for cp in to_delete:
                    vault_root = self._vault.image_root
                    for rel_path in (
                        cp.relative_path,
                        cp.manifest_relative_path,
                        self._hashes_relative_path(cp.manifest_relative_path),
                    ):
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

        self._vault.db.run_task(_prune, priority=DBPriority.IMMEDIATE)

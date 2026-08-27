import os
from datetime import datetime, timezone

from sqlmodel import Session, select

from pixlstash.db_models import Snapshot
from pixlstash.pixl_logging import get_logger
from pixlstash.services.portable_identity import (
    PortableIdentityScrubError,
    sanitize_snapshot_archive,
)
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)


class SnapshotIdentityScrubTask(BaseTask):
    """Rewrite one legacy snapshot archive so it carries no owner identity.

    Snapshots taken before the multi-library hub contain the vault's old
    ``user`` / ``usertoken`` / ``guest_session`` / ``guest_score`` rows, because
    identity lived in the vault then and a snapshot is a full copy of it. Those
    rows are credentials: a password hash and live token hashes. A library
    folder is now a portable object (attach, move, back up), so an archive that
    still holds them carries credentials wherever the folder goes.

    Restoring is already safe without this task: every restore and preview path
    scrubs the materialized scratch database before anything opens it
    (``services/restore/schema_upgrade``). This task closes the *at-rest* copy,
    which is what a file-level reader (notably ``create_backup``, which packages
    ``snapshots/**`` verbatim) would otherwise pick up.

    One archive per task, at ``LOW`` priority: each one is decompressed,
    scrubbed, recompressed and independently verified, which is tens of seconds
    and hundreds of megabytes of I/O for a real snapshot. Completion is recorded
    per archive, so an interrupted pass resumes rather than restarting.
    """

    def __init__(self, database, snapshot_id: int, relative_path: str):
        """Initialise the task.

        Args:
            database: The application database instance.
            snapshot_id: Primary key of the ``snapshot`` row to scrub.
            relative_path: The archive's path relative to the vault root.
        """
        super().__init__(
            task_type="SnapshotIdentityScrubTask",
            params={"snapshot_id": snapshot_id, "relative_path": relative_path},
        )
        self._db = database
        self._snapshot_id = snapshot_id
        self._relative_path = relative_path

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        vault_root = self._db.image_root
        try:
            archive = resolve_path_within(vault_root, self._relative_path)
        except ValueError as exc:
            # A registered archive path outside the vault root is never a file
            # this task may touch. There is nothing legitimate to scrub there;
            # record it done so it stops being handed out, exactly like the
            # missing-archive branch below.
            logger.error(
                "SnapshotIdentityScrubTask: snapshot %s is registered at %s, "
                "which is outside the vault root - refusing to touch it and "
                "recording it as done: %s",
                self._snapshot_id,
                self._relative_path,
                exc,
            )
            self._db.run_task(self._record_scrubbed, None)
            return

        if not os.path.exists(archive):
            # An orphaned row: the snapshot was registered but its archive is
            # gone from disk. There are no bytes at rest, so there is nothing to
            # scrub and nothing to leak, and retrying can never succeed. Marked
            # done so it stops being handed out; the dangling registration is a
            # separate concern for snapshot retention, not for this task.
            logger.warning(
                "SnapshotIdentityScrubTask: snapshot %s is registered at %s but "
                "no archive exists there. Nothing to scrub (no bytes at rest); "
                "recording it as done. The snapshot row is dangling.",
                self._snapshot_id,
                self._relative_path,
            )
            self._db.run_task(self._record_scrubbed, None)
            return

        try:
            byte_size = sanitize_snapshot_archive(archive)
        except PortableIdentityScrubError as exc:
            # Left unmarked on purpose so a later run retries it. Not swallowed:
            # a permanently failing archive is a credential still sitting at
            # rest, so it must stay visible in the log rather than be dropped.
            # The finder stops handing this id out for the rest of the process,
            # which is what keeps a permanent failure from becoming a spin loop.
            logger.error(
                "SnapshotIdentityScrubTask: could not scrub snapshot %s at %s: %s",
                self._snapshot_id,
                archive,
                exc,
                exc_info=True,
            )
            raise

        self._db.run_task(self._record_scrubbed, byte_size)
        logger.info(
            "SnapshotIdentityScrubTask: scrubbed legacy snapshot %s (%s), %d bytes.",
            self._snapshot_id,
            self._relative_path,
            byte_size,
        )

    def _record_scrubbed(self, session: Session, byte_size: int | None) -> None:
        """Commit this archive's completion; the commit is the resume point.

        Args:
            session: The writer session.
            byte_size: The rewritten archive's size, or None when the archive
                was missing and only the marker is being recorded.
        """
        row = session.exec(
            select(Snapshot).where(Snapshot.id == self._snapshot_id)
        ).first()
        if row is None:
            logger.warning(
                "SnapshotIdentityScrubTask: snapshot %s vanished before its scrub "
                "could be recorded; the archive on disk was already rewritten.",
                self._snapshot_id,
            )
            return
        if byte_size is not None:
            row.byte_size = byte_size
        row.identity_scrubbed_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()

from sqlalchemy import not_
from sqlmodel import Session, select

from pixlstash.db_models import Snapshot
from pixlstash.pixl_logging import get_logger
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.snapshot_identity_scrub_task import SnapshotIdentityScrubTask

logger = get_logger(__name__)


class MissingSnapshotIdentityScrubFinder(BaseTaskFinder):
    """Find legacy snapshot archives that still carry portable owner identity.

    ``identity_scrubbed_at IS NULL`` means exactly one thing: an archive written
    before the multi-library hub, when identity lived in the vault and a
    snapshot copied it. Snapshots created since are stamped at creation, because
    ``SnapshotService`` scrubs the scratch database before compressing it.

    This work used to run inline during startup, ahead of the listening socket.
    On a real snapshot history that is minutes of dead air (22 archives / 5.7 GB
    measured at 5 min 47 s) with no port open and no output, which reads as a
    hang and invites the interrupt that makes it restart. It moved here because
    the live vault is scrubbed synchronously and every restore path scrubs what
    it materializes, so serving does not depend on the archives being done.

    One archive in flight at a time: each is a decompress, scrub, recompress and
    verify cycle, and this is background cleanup that must never compete with
    interactive work.
    """

    def __init__(self, database):
        """Initialise the finder.

        Args:
            database: The application database instance.
        """
        super().__init__()
        self._db = database
        self._exhausted = False
        self._failed: set[int] = set()

    def finder_name(self) -> str:
        return "MissingSnapshotIdentityScrubFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        # A vault whose legacy archives are all scrubbed never grows new ones:
        # every snapshot created from now on is stamped at creation. So once the
        # query comes back empty it stays empty, and re-running it every
        # planning cycle would be a pointless read for the life of the process.
        if self._exhausted:
            return None

        row = self._db.run_immediate_read_task(self._fetch_next, self._failed)
        if row is None:
            self._exhausted = True
            return None

        snapshot_id, relative_path = row
        return SnapshotIdentityScrubTask(
            database=self._db,
            snapshot_id=snapshot_id,
            relative_path=relative_path,
        )

    def on_task_complete(self, task, error):
        """Stop handing out an archive that just failed to scrub.

        A failed archive stays ``identity_scrubbed_at IS NULL`` on purpose, so
        the work is not silently forgotten. Without this the planner would pick
        the same row again immediately and spin: a single unscrubbable archive
        produced hundreds of failing tasks per minute in testing. Skipping it
        for the rest of the process keeps the remaining archives draining, and a
        restart retries it once more.

        A cancelled task never got that far. The claims are released and the
        archive stays eligible, so a planner stop or a queue drain does not skip
        it for the rest of the process.
        """
        super().on_task_complete(task, error)
        if error is None:
            return
        if isinstance(error, TaskCancelledError):
            logger.debug(
                "MissingSnapshotIdentityScrubFinder: scrub task cancelled before "
                "it ran (%s). The archive stays eligible.",
                error,
            )
            return
        snapshot_id = (getattr(task, "params", None) or {}).get("snapshot_id")
        if snapshot_id is None:
            return
        self._failed.add(snapshot_id)
        logger.warning(
            "MissingSnapshotIdentityScrubFinder: snapshot %s failed to scrub and "
            "will not be retried until restart. It still carries portable "
            "identity at rest; a backup of this library will retry it and "
            "refuse to publish if it fails again.",
            snapshot_id,
        )

    @staticmethod
    def _fetch_next(session: Session, skip: set[int]):
        statement = select(Snapshot.id, Snapshot.relative_path).where(
            Snapshot.identity_scrubbed_at.is_(None)
        )
        if skip:
            statement = statement.where(not_(Snapshot.id.in_(skip)))
        row = session.exec(statement.order_by(Snapshot.id).limit(1)).first()
        return (row[0], row[1]) if row else None

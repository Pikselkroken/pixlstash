import time

from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.services.workflow_ghost_service import DEFAULT_GHOST_RETENTION
from pixlstash.tasks.missing_file_purge_task import MissingFilePurgeTask

logger = get_logger(__name__)


class MissingFilePurgeFinder(BaseTaskFinder):
    """Periodically scan the picture table and purge records whose files are gone.

    The finder walks through all pictures in ID order, one batch per planning
    cycle.  When it reaches the end of the table it waits ``SCAN_COOLDOWN_S``
    before starting the next full pass.  This keeps disk I/O spread across
    many planning cycles rather than doing a large burst at once.
    """

    SCAN_COOLDOWN_S: float = 3600.0  # one full pass at most once per hour

    def __init__(
        self, database, hub=None, library_uuid=None, ghost_retention_source=None
    ):
        """Initialise the finder.

        Args:
            database: The application database instance.
            hub: The hub database, or ``None`` when the vault was opened without
                one. Handed to the task so it can run the covered-ghost cascade
                over the instance hashes it destroys (§B4).
            library_uuid: Which library's ghosts the cascade may reach.
            ghost_retention_source: Zero-argument callable returning the
                CURRENT ghost-retention position. A callable rather than a value
                because the setting is saved at runtime and this finder outlives
                the save; capturing the value at construction would leave the
                sweep acting on whatever position was in force at start-up.
        """
        super().__init__()
        self._db = database
        self._hub = hub
        self._library_uuid = library_uuid
        self._ghost_retention_source = ghost_retention_source
        self._cursor_id: int = 0
        self._cooldown_start: float = 0.0

    def finder_name(self) -> str:
        return "MissingFilePurgeFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        # If we completed a full pass, wait for the cooldown to expire.
        if self._cooldown_start > 0:
            if time.monotonic() - self._cooldown_start < self.SCAN_COOLDOWN_S:
                return None
            # Cooldown expired — start a new pass from the beginning.
            self._cursor_id = 0
            self._cooldown_start = 0.0

        pictures = self._db.run_immediate_read_task(
            self._fetch_batch, self._cursor_id, MissingFilePurgeTask.BATCH_SIZE
        )

        if not pictures:
            # Reached the end of the table — begin cooldown.
            self._cooldown_start = time.monotonic()
            self._cursor_id = 0
            return None

        self._cursor_id = max(p.id for p in pictures)
        return MissingFilePurgeTask(
            database=self._db,
            pictures=pictures,
            hub=self._hub,
            library_uuid=self._library_uuid,
            ghost_retention=(
                self._ghost_retention_source()
                if self._ghost_retention_source is not None
                else DEFAULT_GHOST_RETENTION
            ),
        )

    @staticmethod
    def _fetch_batch(session: Session, cursor_id: int, limit: int) -> list:
        return session.exec(
            select(Picture)
            .where(Picture.id > cursor_id)
            .order_by(Picture.id)
            .limit(limit)
        ).all()

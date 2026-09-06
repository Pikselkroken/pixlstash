import time

from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder
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

    def __init__(self, database, is_ready=None):
        """Initialise the finder.

        Args:
            database: The application database instance.
            is_ready: Optional zero-argument callable
                (``ReferenceFolderScanFinder.root_scan_complete``). Passing it
                says a library-root scan owns the root, which has two
                consequences. While it returns ``False`` no purge task is
                queued at all: nothing has looked at the root yet, so a
                vanished path is not yet known to be a deletion. And from then
                on the sweep skips root-owned rows entirely
                (``reference_folder_id IS NULL``), because the scan is what
                decides those. The scan pairs a vanished path with an arrived
                one by pixel hash and defers when it cannot; this sweep reads
                only the move journal, so on a rename it races the scan and
                deletes the row with ``file_removed=True`` - which
                ``full_restore`` reads as *never resurrect*. There is no
                ordering that makes both safe, so only one of them may delete.
        """
        super().__init__()
        self._db = database
        self._is_ready = is_ready
        self._cursor_id: int = 0
        self._cooldown_start: float = 0.0

    def finder_name(self) -> str:
        return "MissingFilePurgeFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        if self._is_ready is not None and not self._is_ready():
            return None
        # If we completed a full pass, wait for the cooldown to expire.
        if self._cooldown_start > 0:
            if time.monotonic() - self._cooldown_start < self.SCAN_COOLDOWN_S:
                return None
            # Cooldown expired - start a new pass from the beginning.
            self._cursor_id = 0
            self._cooldown_start = 0.0

        pictures = self._db.run_immediate_read_task(
            self._fetch_batch,
            self._cursor_id,
            MissingFilePurgeTask.BATCH_SIZE,
            self._is_ready is not None,
        )

        if not pictures:
            # Reached the end of the table - begin cooldown.
            self._cooldown_start = time.monotonic()
            self._cursor_id = 0
            return None

        self._cursor_id = max(p.id for p in pictures)
        return MissingFilePurgeTask(database=self._db, pictures=pictures)

    @staticmethod
    def _fetch_batch(
        session: Session, cursor_id: int, limit: int, skip_root_owned: bool = False
    ) -> list:
        query = select(Picture).where(Picture.id > cursor_id)
        if skip_root_owned:
            # Left to the library-root scan; see ``is_ready``.
            query = query.where(Picture.reference_folder_id.is_not(None))
        return session.exec(query.order_by(Picture.id).limit(limit)).all()

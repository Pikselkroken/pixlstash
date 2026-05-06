from sqlmodel import Session

from .base_task_finder import BaseTaskFinder
from .likeness_task import LikenessTask
from pixlstash.database import DBPriority
from pixlstash.utils.likeness.likeness_parameter_utils import LikenessParameterUtils
from pixlstash.utils.likeness.likeness_utils import LikenessUtils, BulkCandidateArrays
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class MissingLikenessFinder(BaseTaskFinder):
    """Find pending likeness work and create a LikenessTask.

    Caches the decoded bulk candidate arrays (``BulkCandidateArrays``) for the
    duration of each sweep.  With 28k pictures × 2 KB embeddings the cache
    eliminates ~57 MB of repeated SQLite reads and blob deserialization from
    every task cycle.  The cache is invalidated when the queue drains to zero
    (sweep complete) so a fresh DB state is used for the next sweep.
    """

    def __init__(self, database):
        super().__init__()
        self._db = database
        self._bulk_cache: BulkCandidateArrays | None = None

    def finder_name(self) -> str:
        return "MissingLikenessFinder"

    def find_task(self):
        # Block pairs until *all* findable parameter work is done.
        has_pending = self._db.run_immediate_read_task(
            LikenessParameterUtils.has_pending_work
        )
        if has_pending:
            return None

        queue_count, candidate_count, pair_count = self._db.run_immediate_read_task(
            self._likeness_state
        )
        logger.debug(
            "MissingLikenessFinder: queue=%d candidates=%d pairs=%d",
            int(queue_count or 0),
            int(candidate_count or 0),
            int(pair_count or 0),
        )

        has_work = int(queue_count or 0) > 0 or (
            int(candidate_count or 0) > 0 and int(pair_count or 0) == 0
        )
        if not has_work:
            # Sweep done — drop the cache so the next sweep gets fresh data.
            self._bulk_cache = None
            return None

        # Build (or reuse) the bulk candidate array cache.
        if self._bulk_cache is None:
            # Seed the queue once at the start of a new sweep.
            self._db.result_or_throw(
                self._db.submit_task(LikenessUtils.seed_queue, priority=DBPriority.LOW)
            )
            logger.info("MissingLikenessFinder: building bulk candidate cache…")
            self._bulk_cache = self._db.run_immediate_read_task(
                LikenessUtils.build_bulk_arrays
            )
            if self._bulk_cache is None:
                return None
            logger.info(
                "MissingLikenessFinder: cache built (%d candidates)", len(self._bulk_cache.ids)
            )

        return LikenessTask(database=self._db, bulk_arrays=self._bulk_cache)

    @staticmethod
    def _likeness_state(session: Session):
        return (
            LikenessTask.count_queue(session),
            LikenessTask.count_total_candidates(session),
            LikenessTask.count_total_pairs(session),
        )


from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_likeness import PictureLikeness, PictureLikenessQueue
from pixlstash.utils.likeness.likeness_utils import LikenessUtils, BulkCandidateArrays
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class LikenessTask(BaseTask):
    """Task that processes one likeness queue scoring cycle."""

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def __init__(self, database, bulk_arrays: BulkCandidateArrays | None = None):
        super().__init__(
            task_type="LikenessTask",
            params={},
        )
        self._db = database
        self._bulk_arrays = bulk_arrays

    def _run_task(self):
        helper = LikenessUtils(self._db)

        def submit_low(func, *args, **kwargs):
            return self._db.result_or_throw(
                self._db.submit_task(func, *args, priority=DBPriority.LOW, **kwargs)
            )

        work_items = submit_low(
            LikenessUtils.get_next_work_batch,
            helper.MAX_A_PER_CYCLE,
        )
        if not work_items:
            return {"changed_count": 0, "changed": [], "pairs_written": 0}

        queued_ids = [int(item[0]) for item in work_items]

        # Use the cached pre-decoded arrays when available (set by the finder),
        # falling back to a fresh fetch only if the task was created without them.
        bulk = self._bulk_arrays
        if bulk is None:
            bulk = self._db.run_immediate_read_task(LikenessUtils.build_bulk_arrays)

        if bulk is None:
            return {"changed_count": 0, "changed": [], "pairs_written": 0}

        logger.info(
            "LikenessTask: processing %d queued pictures against %d candidates",
            len(queued_ids),
            len(bulk.ids),
        )
        likeness_results = helper.compute_bulk_likeness(queued_ids, bulk)
        logger.info(
            "LikenessTask: computed %d likeness pairs from %d queued pictures",
            len(likeness_results),
            len(queued_ids),
        )

        if likeness_results:
            submit_low(
                LikenessUtils.write_results,
                likeness_results,
                helper.TOP_K,
            )

        changed = [(PictureLikenessQueue, pid, "queue", None) for pid in queued_ids]
        return {
            "changed_count": len(changed),
            "changed": changed,
            "pairs_written": len(likeness_results),
        }

    @staticmethod
    def count_queue(session: Session) -> int:
        result = session.exec(
            select(func.count()).select_from(PictureLikenessQueue)
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def count_total_candidates(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.likeness_parameters.is_not(None))
            .where(Picture.perceptual_hash.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def count_total_pairs(session: Session) -> int:
        result = session.exec(select(func.count()).select_from(PictureLikeness)).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

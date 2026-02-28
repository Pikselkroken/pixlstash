from sqlalchemy import func
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.picture_likeness import PictureLikeness, PictureLikenessQueue
from pixlvault.picture_likeness_utils import PictureLikenessUtils
from pixlvault.task_runner import BaseTask


class LikenessTask(BaseTask):
    """Task that processes one likeness queue scoring cycle."""

    def __init__(self, database):
        super().__init__(
            task_type="LikenessTask",
            params={},
        )
        self._db = database

    def _run_task(self):
        helper = PictureLikenessUtils(self._db)

        def submit_low(func, *args, **kwargs):
            return self._db.result_or_throw(
                self._db.submit_task(func, *args, priority=DBPriority.LOW, **kwargs)
            )

        submit_low(PictureLikenessUtils.seed_queue)
        param_thresholds = submit_low(
            PictureLikenessUtils.compute_param_gap_thresholds,
            helper.PARAM_GAP_PERCENTILE,
            helper.PARAM_THRESHOLD_SAMPLE_LIMIT,
        )
        date_span_seconds = submit_low(PictureLikenessUtils.compute_date_span_seconds)

        work_items = submit_low(
            PictureLikenessUtils.get_next_work_batch,
            helper.MAX_A_PER_CYCLE,
        )
        if not work_items:
            return {"changed_count": 0, "changed": [], "pairs_written": 0}

        queued_ids = [int(item[0]) for item in work_items]
        bulk_rows = submit_low(PictureLikenessUtils.fetch_bulk_candidate_data)
        likeness_results = helper.compute_bulk_likeness(
            queued_ids,
            bulk_rows,
            param_thresholds,
            date_span_seconds,
        )

        if likeness_results:
            submit_low(
                PictureLikenessUtils.write_results,
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

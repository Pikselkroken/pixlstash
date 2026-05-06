from pixlstash.database import DBPriority
from pixlstash.db_models.picture import (
    LikenessParameter,
    Picture,
)
from pixlstash.utils.likeness.likeness_parameter_utils import LikenessParameterUtils
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class LikenessParametersTask(BaseTask):
    """Write all pre-fetched likeness parameter values to the database in a single pass.

    The finder is responsible for discovering work and fetching all required
    data (quality metrics, picture metadata, size-bin indices).  This task
    computes the final blobs and writes them atomically: one DB update covers
    SIZE_BIN, quality params, and picture params for the entire batch.
    """

    BATCH_SIZE = 1024
    SCAN_LIMIT = 4096

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.MEDIUM

    def __init__(self, database, ids: list, payload: dict):
        """
        Args:
            database: Database instance.
            ids: Picture IDs in this batch.
            payload: Pre-fetched data dict with keys:
                - ``size_bin_by_id``: mapping of id → size_bin_index integer
                - ``quality_by_id``: mapping of id → quality field dict
                - ``picture_by_id``: mapping of id → picture param field dict
                - ``picture_updates``: mapping of id → metadata fields to write back
        """
        super().__init__(
            task_type="LikenessParametersTask",
            params={"picture_ids": ids},
        )
        self._db = database
        self._ids = ids
        self._payload = payload

    def _run_task(self):
        def submit_low(func, *args, **kwargs):
            return self._db.result_or_throw(
                self._db.submit_task(func, *args, priority=DBPriority.LOW, **kwargs)
            )

        ids = self._ids
        vector_length = len(LikenessParameter)

        logger.info(
            "LikenessParametersTask: writing all params for %d pictures",
            len(ids),
        )

        # Fetch existing blobs in a concurrent read session — outside the
        # serialised write queue so all worker threads can do this in parallel.
        blobs_by_id = self._db.run_immediate_read_task(
            LikenessParameterUtils.fetch_blobs_for_ids, ids
        )

        # Compute all parameter blobs in the worker thread (parallel CPU).
        updates = LikenessParameterUtils.compute_all_param_updates(
            ids,
            blobs_by_id,
            self._payload["size_bin_by_id"],
            self._payload["quality_by_id"],
            self._payload["picture_by_id"],
            vector_length,
        )

        picture_updates = self._payload.get("picture_updates", {})
        if picture_updates:
            submit_low(LikenessParameterUtils.update_picture_metadata, picture_updates)

        # Single write covering likeness_parameters + size_bin_index for all pictures.
        submit_low(LikenessParameterUtils.write_blob_updates, updates)
        submit_low(LikenessParameterUtils.reset_likeness_for_pictures, ids)

        return {
            "changed_count": len(ids),
            "changed": [(Picture, pid, "likeness_parameters", None) for pid in ids],
        }


from typing import Callable

from .base_task_finder import BaseTaskFinder
from .image_embedding_task import ImageEmbeddingTask
from pixlstash.worker_config import IMAGE_EMBEDDING_MAX_INFLIGHT

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class MissingImageEmbeddingFinder(BaseTaskFinder):
    """Find pending image embedding work and create an ImageEmbeddingTask."""

    def __init__(self, database, engine_getter: Callable):
        super().__init__()
        self._db = database
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingImageEmbeddingFinder"

    def max_inflight_tasks(self) -> int:
        return IMAGE_EMBEDDING_MAX_INFLIGHT

    def find_task(self):
        engine = self._engine_getter()
        if engine is None:
            return None

        batch_size = ImageEmbeddingTask.BATCH_SIZE
        try:
            batch_size = max(
                1, int(engine.clip_embedding_workflow.suggested_batch_size())
            )
        except Exception:
            logger.warning(
                "clip_embedding_workflow.suggested_batch_size() failed, using default batch size",
                exc_info=True,
            )

        # Fetch more than one task worth so _filter_and_claim can skip claimed IDs.
        # Exclude undecodable pictures (issue #585) at the query so they cannot
        # crowd the candidate window and stall real work.
        suppressed_ids = self._db.unprocessable_images.active_suppressed_ids()
        candidates = self._db.run_immediate_read_task(
            lambda session: ImageEmbeddingTask.fetch_work(
                session=session,
                limit=batch_size * IMAGE_EMBEDDING_MAX_INFLIGHT,
                suppressed_ids=suppressed_ids,
            )
        )
        if not candidates:
            return None

        selected = self._filter_and_claim(candidates, batch_size)
        if not selected:
            return None

        return ImageEmbeddingTask(
            database=self._db,
            clip_workflow=engine.clip_embedding_workflow,
            batch=selected,
        )

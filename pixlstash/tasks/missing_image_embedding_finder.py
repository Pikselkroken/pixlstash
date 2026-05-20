from typing import Callable

from .base_task_finder import BaseTaskFinder
from .image_embedding_task import ImageEmbeddingTask
from .task_type import TaskType
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

    def depends_on(self) -> list[TaskType]:
        return [TaskType.FACE_EXTRACTION, TaskType.TAGGER]

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
        candidates = self._db.run_immediate_read_task(
            lambda session: ImageEmbeddingTask.fetch_work(
                session=session, limit=batch_size * IMAGE_EMBEDDING_MAX_INFLIGHT
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

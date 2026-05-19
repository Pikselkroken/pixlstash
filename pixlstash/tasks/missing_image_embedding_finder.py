from typing import Callable

from .base_task_finder import BaseTaskFinder
from .image_embedding_task import ImageEmbeddingTask
from pixlstash.worker_config import IMAGE_EMBEDDING_MAX_INFLIGHT

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class MissingImageEmbeddingFinder(BaseTaskFinder):
    """Find pending image embedding work and create an ImageEmbeddingTask."""

    def __init__(self, database, picture_tagger_getter: Callable):
        super().__init__()
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingImageEmbeddingFinder"

    def max_inflight_tasks(self) -> int:
        return IMAGE_EMBEDDING_MAX_INFLIGHT

    def depends_on(self) -> list[str]:
        return ["MissingFaceExtractionFinder", "MissingTagFinder"]

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        batch_size = ImageEmbeddingTask.BATCH_SIZE
        suggest_fn = getattr(
            picture_tagger, "suggested_image_embedding_batch_size", None
        )
        if callable(suggest_fn):
            try:
                batch_size = max(1, int(suggest_fn()))
            except Exception:
                logger.warning(
                    "suggested_image_embedding_batch_size() failed, using default batch size",
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
            picture_tagger=picture_tagger,
            batch=selected,
        )

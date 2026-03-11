from typing import Callable

from sqlmodel import Session

from .base_task_finder import BaseTaskFinder
from .image_embedding_task import ImageEmbeddingTask


class MissingImageEmbeddingFinder(BaseTaskFinder):
    """Find pending image embedding work and create an ImageEmbeddingTask."""

    def __init__(self, database, picture_tagger_getter: Callable):
        super().__init__()
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingImageEmbeddingFinder"

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        missing = int(self._db.run_immediate_read_task(self._count_remaining) or 0)
        if missing <= 0:
            return None

        return ImageEmbeddingTask(
            database=self._db,
            picture_tagger=picture_tagger,
        )

    @staticmethod
    def _count_remaining(session: Session) -> int:
        return ImageEmbeddingTask.count_remaining(session)

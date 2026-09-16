"""Finder for pictures whose text has not been read."""

from typing import Callable

from .base_task_finder import SimpleMissingFinder
from .ocr_task import OcrTask
from .task_type import TaskType


class MissingOcrFinder(SimpleMissingFinder):
    """Find text-heavy pictures with no ``ocr_text`` and create OcrTasks.

    Waits for text scoring, which decides which pictures qualify, and for
    captioning, which shares the model. Idle while captioning is switched off.
    """

    def __init__(self, database, engine_getter: Callable):
        super().__init__(database)
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingOcrFinder"

    def depends_on(self) -> list[TaskType]:
        return [TaskType.TEXT_SCORE, TaskType.DESCRIPTION]

    def _guard(self) -> bool:
        # Reading shares Florence-2 with captioning; with captioning switched
        # off the owner has asked for no background model, so none is loaded.
        engine = self._engine_getter()
        if engine is None:
            return False
        tagger_settings = getattr(engine, "tagger_settings", None)
        return tagger_settings is None or bool(
            tagger_settings.get("active_description_plugin")
        )

    def _batch_size(self) -> int:
        return OcrTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        return OcrTask.find_unread_pictures(session, limit)

    def _create_task(self, pictures: list):
        return OcrTask(
            database=self._db, engine=self._engine_getter(), pictures=pictures
        )

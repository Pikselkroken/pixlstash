"""Finder for pictures whose text has not been read."""

from typing import Callable

from pixlstash.pixl_logging import get_logger
from pixlstash.task_runner import TaskCancelledError

from .base_task_finder import SimpleMissingFinder
from .ocr_task import OcrTask
from .task_type import TaskType

logger = get_logger(__name__)


class MissingOcrFinder(SimpleMissingFinder):
    """Find text-heavy pictures with no ``ocr_text`` and create OcrTasks.

    Waits for text scoring, which decides which pictures qualify, and for
    captioning, which shares the model. Idle while captioning is switched off.

    A task that fails defers its pictures for the rest of the session, the way
    ``MissingCheckpointHashFinder`` does: the pictures stay unread, and without
    this a reader that cannot load would be handed the same batch every planner
    cycle. Read again reaches a deferred picture without the finder.
    """

    def __init__(self, database, engine_getter: Callable):
        super().__init__(database)
        self._engine_getter = engine_getter
        self._deferred: set[int] = set()

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
        pictures = OcrTask.find_unread_pictures(session, limit + len(self._deferred))
        return [pic for pic in pictures if pic.id not in self._deferred]

    def on_task_complete(self, task, error) -> None:
        """Release the batch's claims, and defer its pictures if it failed.

        A cancelled task never ran, so its pictures stay eligible.
        """
        super().on_task_complete(task, error)
        if error is None or isinstance(error, TaskCancelledError):
            return
        ids = (getattr(task, "params", None) or {}).get("picture_ids") or []
        logger.warning(
            "Reading the text in pictures %s failed: %s. Deferring them for the "
            "rest of this session.",
            ids,
            error,
        )
        self._deferred.update(ids)

    def _create_task(self, pictures: list):
        return OcrTask(
            database=self._db, engine=self._engine_getter(), pictures=pictures
        )

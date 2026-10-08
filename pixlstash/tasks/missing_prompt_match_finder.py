"""Finder for pictures with a prompt whose prompt match is not scored yet."""

from typing import Callable

from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.task_runner import TaskCancelledError

from .base_task_finder import SimpleMissingFinder
from .prompt_match_task import PromptMatchTask

logger = get_logger(__name__)


class MissingPromptMatchFinder(SimpleMissingFinder):
    """Hand pictures with a prompt and an image embedding to PromptMatchTask.

    A picture without a prompt is never a candidate, so its score stays NULL;
    one still waiting for its image embedding is picked up once it has one.
    A task that fails (CLIP could not encode) defers its pictures for the rest
    of the session, as ``MissingOcrFinder`` does, so a broken encoder is not
    handed the same batch every planner cycle; a restart retries them.
    """

    def __init__(self, database, engine_getter: Callable):
        super().__init__(database)
        self._engine_getter = engine_getter
        self._deferred: set[int] = set()

    def finder_name(self) -> str:
        return "MissingPromptMatchFinder"

    def _guard(self) -> bool:
        return self._engine_getter() is not None

    def _batch_size(self) -> int:
        return PromptMatchTask.BATCH_SIZE

    def _fetch_candidates(self, session: Session, limit: int) -> list:
        pictures = session.exec(self.candidate_query(limit + len(self._deferred))).all()
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
            "Scoring the prompt match of pictures %s failed: %s. Deferring them "
            "for the rest of this session.",
            ids,
            error,
        )
        self._deferred.update(ids)

    @staticmethod
    def candidate_query(limit: int):
        """The probe. Served by ix_picture_prompt_match_missing, whose WHERE
        its first two terms repeat exactly so SQLite can use it."""
        return (
            select(Picture)
            .options(load_only(Picture.id))
            .where(Picture.prompt_match.is_(None))
            .where(Picture.comfyui_positive_prompt.is_not(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.image_embedding.is_not(None))
            .order_by(Picture.id)
            .limit(limit)
        )

    def _create_task(self, pictures: list):
        return PromptMatchTask(
            database=self._db,
            clip_service=self._engine_getter().clip_service,
            pictures=pictures,
        )

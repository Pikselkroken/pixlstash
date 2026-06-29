from typing import Callable

from sqlalchemy import and_
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
)
from pixlstash.pixl_logging import get_logger
from .base_task_finder import SimpleMissingFinder
from .tag_prediction_backfill_task import TagPredictionBackfillTask
from .task_type import TaskType


logger = get_logger(__name__)


class MissingTagPredictionFinder(SimpleMissingFinder):
    """Find pictures that have real tags but no tag predictions and back-fill them.

    Predictions are only ever written by the PixlStash tagger, in the same pass
    that applies tags. Pictures tagged before that became inline (or by another
    engine then) keep their tags but have zero ``tag_prediction`` rows, and
    nothing revisits them: ``MissingTagFinder`` only re-tags pictures carrying a
    retag sentinel. This finder closes that gap by running the tagger for raw
    scores only and writing predictions against the existing tags, without
    re-tagging (which would replace the curated tag set).
    """

    def __init__(self, database, engine_getter: Callable):
        super().__init__(database)
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingTagPredictionFinder"

    def depends_on(self) -> list[TaskType]:
        # Stay out of the way of live work: face extraction has GPU priority, and
        # new-import tagging should always run before this catch-up backfill.
        return [TaskType.FACE_EXTRACTION, TaskType.TAGGER]

    def _guard(self) -> bool:
        engine = self._engine_getter()
        if engine is None:
            return False
        try:
            # Only the PixlStash tagger produces the raw scores predictions are
            # built from, so this backfill only applies when it is the active tagger.
            return bool(engine.tagging_workflow.is_pixlstash_tagger_enabled)
        except Exception:
            logger.warning(
                "MissingTagPredictionFinder guard check failed; skipping cycle",
                exc_info=True,
            )
            return False

    def _batch_size(self) -> int:
        engine = self._engine_getter()
        if engine is None:
            return 8
        return max(1, int(engine.tagging_workflow.suggested_task_size()))

    def _create_task(self, pictures: list):
        engine = self._engine_getter()
        if engine is None:
            return None
        return TagPredictionBackfillTask(
            database=self._db,
            tagging_workflow=engine.tagging_workflow,
            pictures=pictures,
        )

    def _fetch_candidates(self, session: Session, limit: int) -> list:
        return self._fetch_missing_predictions(session, limit)

    @staticmethod
    def _fetch_missing_predictions(session: Session, limit: int) -> list:
        has_sentinel = Tag.tag.like(
            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
        )
        has_real_tag = Picture.tags.any(and_(Tag.tag.is_not(None), ~has_sentinel))
        no_prediction = ~Picture.tag_predictions.any()
        return session.exec(
            select(Picture)
            .where(
                has_real_tag,
                no_prediction,
                Picture.deleted.is_(False),
                Picture.file_path.is_not(None),
            )
            .options(load_only(Picture.id, Picture.file_path))
            .order_by(Picture.id)
            .limit(limit)
        ).all()

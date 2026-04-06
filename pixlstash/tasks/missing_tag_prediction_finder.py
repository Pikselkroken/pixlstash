from typing import Callable

from sqlalchemy import not_, or_
from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.db_models.tag import Tag, TAG_EMPTY_SENTINEL
from pixlstash.db_models.tag_prediction import TagPrediction

from .base_task_finder import BaseTaskFinder
from .tag_prediction_task import TagPredictionTask


class MissingTagPredictionFinder(BaseTaskFinder):
    """Find pictures that have not yet been scored by the current custom tagger epoch."""

    _BATCH_MULTIPLIER = 3

    def __init__(self, database, picture_tagger_getter: Callable):
        super().__init__()
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingTagPredictionFinder"

    def depends_on(self) -> list[str]:
        # Run after tagging so pictures have confirmed tags before predictions
        return ["MissingTagFinder"]

    def find_task(self):
        tagger = self._picture_tagger_getter()
        if tagger is None:
            return None
        if not getattr(tagger, "_use_custom_tagger", False):
            return None

        epoch = tagger.custom_tagger_version()
        model_version = f"v{epoch}"

        batch_limit = max(1, getattr(tagger, "_custom_tagger_batch", 16))
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing(
                session, model_version, batch_limit * self._BATCH_MULTIPLIER
            )
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, batch_limit)
        if not selected:
            return None

        return TagPredictionTask(
            database=self._db,
            picture_tagger=tagger,
            pictures=selected,
            model_version=model_version,
        )

    @staticmethod
    def _fetch_missing(session: Session, model_version: str, limit: int):
        """Fetch pictures that have no TagPrediction row for the current model version,
        or that have a confirmed tag with no corresponding TagPrediction row at all."""
        has_prediction_for_version = (
            select(TagPrediction.picture_id)
            .where(
                TagPrediction.picture_id == Picture.id,
                TagPrediction.model_version == model_version,
            )
            .correlate(Picture)
        )
        # Correlated: does this picture have a Tag row with no matching TagPrediction?
        has_tag_without_prediction = (
            select(Tag.id)
            .outerjoin(
                TagPrediction,
                (TagPrediction.picture_id == Tag.picture_id)
                & (TagPrediction.tag == Tag.tag),
            )
            .where(
                Tag.picture_id == Picture.id,
                Tag.tag != TAG_EMPTY_SENTINEL,
                TagPrediction.id.is_(None),
            )
            .correlate(Picture)
            .exists()
        )
        return session.exec(
            select(Picture)
            .where(
                Picture.deleted.is_(False),
                Picture.file_path.is_not(None),
                or_(
                    not_(has_prediction_for_version.exists()),
                    has_tag_without_prediction,
                ),
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()

from typing import Callable

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
                session, batch_limit * self._BATCH_MULTIPLIER
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
    def _fetch_missing(session: Session, limit: int):
        """Fetch pictures that have a tag with no corresponding TagPrediction row.

        Only considers pictures that have at least one real (non-sentinel) tag —
        i.e. pictures that have already been through the tagger.  This guards
        against the race where reset_tags clears a picture's tags and predictions
        and the prediction task races ahead of the tag task, scoring the picture
        while it has only the empty-sentinel tag and therefore writing all
        predictions as REJECTED.

        Version-driven rescoring (i.e. re-running all pictures after a model
        update) is NOT handled here.  To trigger a full rescore for a new model
        version, delete the relevant TagPrediction rows in an Alembic migration;
        the finder will then naturally queue those pictures for rescoring."""
        # Guard: the picture must have been tagged (at least one real tag exists).
        # This prevents scoring a picture that has just had its tags reset and
        # is waiting for MissingTagFinder to re-run the tagger on it.
        has_real_tag = (
            select(Tag.id)
            .where(
                Tag.picture_id == Picture.id,
                Tag.tag.is_not(None),
                Tag.tag != TAG_EMPTY_SENTINEL,
            )
            .correlate(Picture)
            .exists()
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
                has_real_tag,
                has_tag_without_prediction,
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()

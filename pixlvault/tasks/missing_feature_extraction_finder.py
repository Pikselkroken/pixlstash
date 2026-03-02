from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlvault.db_models import Picture

from .base_task_finder import BaseTaskFinder
from .feature_extraction_task import FeatureExtractionTask

# InsightFace processes images sequentially (one at a time), so the batch size
# here controls task granularity, not neural-net parallelism. Use a large cap
# so a single task drains the backlog instead of making the planner round-trip
# every max_concurrent_images pictures (which is tuned for the tagger, not
# for sequential face detection).
FEATURE_EXTRACTION_BATCH_LIMIT = 512


class MissingFeatureExtractionFinder(BaseTaskFinder):
    """Find pictures missing faces and create a feature extraction task."""

    def __init__(self, database, picture_tagger_getter: Callable):
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingFeatureExtractionFinder"

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_features(session)
        )
        if not pictures:
            return None

        return FeatureExtractionTask(
            database=self._db,
            picture_tagger=picture_tagger,
            pictures=pictures,
        )

    @staticmethod
    def _fetch_missing_features(session: Session):
        return session.exec(
            select(Picture)
            .where(~Picture.faces.any())
            .options(selectinload(Picture.faces))
            .order_by(Picture.id)
            .limit(FEATURE_EXTRACTION_BATCH_LIMIT)
        ).all()

from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlvault.db_models import Picture

from .base_task_finder import BaseTaskFinder
from .feature_extraction_task import FeatureExtractionTask


class MissingFeatureExtractionFinder(BaseTaskFinder):
    """Find pictures missing faces or hands and create a feature extraction task."""

    def __init__(self, database, picture_tagger_getter: Callable):
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingFeatureExtractionFinder"

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        batch_limit = max(1, int(picture_tagger.max_concurrent_images()))
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_features(session, batch_limit)
        )
        if not pictures:
            return None

        return FeatureExtractionTask(
            database=self._db,
            picture_tagger=picture_tagger,
            pictures=pictures,
        )

    @staticmethod
    def _fetch_missing_features(session: Session, limit: int):
        return session.exec(
            select(Picture)
            .where((~Picture.faces.any()) | (~Picture.hands.any()))
            .options(selectinload(Picture.faces), selectinload(Picture.hands))
            .order_by(Picture.id)
            .limit(limit)
        ).all()

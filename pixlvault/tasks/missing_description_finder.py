from typing import Callable

from sqlmodel import Session, select

from pixlvault.db_models import Picture

from .description_task import DescriptionTask

from .base_task_finder import BaseTaskFinder


class MissingDescriptionFinder(BaseTaskFinder):
    """Find a batch of pictures missing descriptions and create a DescriptionTask."""

    def __init__(
        self,
        database,
        picture_tagger_getter: Callable,
    ):
        super().__init__()
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingDescriptionFinder"

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        batch_limit = max(
            1,
            int(
                picture_tagger.description_batch_size()
                if hasattr(picture_tagger, "description_batch_size")
                else picture_tagger.max_concurrent_images()
            ),
        )

        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_descriptions(session, batch_limit * 3)
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, batch_limit)
        if not selected:
            return None

        return DescriptionTask(
            database=self._db,
            picture_tagger=picture_tagger,
            pictures=selected,
        )

    @staticmethod
    def _fetch_missing_descriptions(session: Session, limit: int):
        return session.exec(
            select(Picture)
            .where(Picture.description.is_(None))
            .order_by(Picture.id)
            .limit(limit)
        ).all()

from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlvault.db_models import Face, Hand, Picture

from .base_task_finder import BaseTaskFinder
from .tag_task import TagTask


class MissingTagsFinder(BaseTaskFinder):
    """Find a batch of pictures missing tags and create a TagTask."""

    def __init__(
        self,
        database,
        picture_tagger_getter: Callable,
    ):
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingTagsFinder"

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        batch_limit = max(1, int(picture_tagger.max_concurrent_images()))
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_tags(session, batch_limit)
        )
        if not pictures:
            return None

        return TagTask(
            database=self._db,
            picture_tagger=picture_tagger,
            pictures=pictures,
        )

    @staticmethod
    def _fetch_missing_tags(session: Session, limit: int):
        return session.exec(
            select(Picture)
            .where(
                (~Picture.tags.any())
                | Picture.faces.any((Face.face_index >= 0) & (~Face.tags.any()))
                | Picture.hands.any((Hand.hand_index >= 0) & (~Hand.tags.any()))
            )
            .options(
                selectinload(Picture.tags),
                selectinload(Picture.faces),
                selectinload(Picture.hands),
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()

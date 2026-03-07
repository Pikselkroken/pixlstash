from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlvault.db_models import Picture, Tag, TAG_EMPTY_SENTINEL

from .base_task_finder import BaseTaskFinder
from .tag_task import TagTask


class MissingTagFinder(BaseTaskFinder):
    """Find a batch of pictures missing tags and create a TagTask."""

    def __init__(
        self,
        database,
        picture_tagger_getter: Callable,
    ):
        super().__init__()
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingTagFinder"

    def max_inflight_tasks(self) -> int:
        return 2

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        batch_limit = max(
            1,
            int(
                picture_tagger.suggested_tag_task_size()
                if hasattr(picture_tagger, "suggested_tag_task_size")
                else picture_tagger.max_concurrent_images()
            ),
        )
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_tags(session, batch_limit * 3)
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, batch_limit)

        if not selected:
            return None

        return TagTask(
            database=self._db,
            picture_tagger=picture_tagger,
            pictures=selected,
        )

    @staticmethod
    def _fetch_missing_tags(session: Session, limit: int):
        has_real_tag = (Tag.tag.is_not(None)) & (Tag.tag != TAG_EMPTY_SENTINEL)
        return session.exec(
            select(Picture)
            .where(~Picture.tags.any(has_real_tag))
            .options(
                selectinload(Picture.tags),
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()

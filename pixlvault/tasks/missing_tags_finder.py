from typing import Callable
import threading

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlvault.db_models import Picture, Tag, TAG_EMPTY_SENTINEL

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
        self._claim_lock = threading.Lock()
        self._claimed_picture_ids: set[int] = set()

    def finder_name(self) -> str:
        return "MissingTagsFinder"

    def max_inflight_tasks(self) -> int:
        return 2

    def on_task_complete(self, task, error) -> None:
        picture_ids = []
        if getattr(task, "params", None):
            picture_ids = task.params.get("picture_ids") or []
        if not picture_ids:
            return
        with self._claim_lock:
            for picture_id in picture_ids:
                self._claimed_picture_ids.discard(picture_id)

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

        selected = []
        with self._claim_lock:
            for picture in pictures:
                picture_id = getattr(picture, "id", None)
                if picture_id is None or picture_id in self._claimed_picture_ids:
                    continue
                self._claimed_picture_ids.add(picture_id)
                selected.append(picture)
                if len(selected) >= batch_limit:
                    break

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

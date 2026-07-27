"""Finder for pictures whose thumbnail has not been generated.

Keyed on ``Picture.thumbnail_width IS NULL``. New imports populate the thumbnail
columns at import time, so this finder normally only picks up rows an upgrade
reset to NULL, driving a one-time regeneration of the whole-frame AR bitmap
through :class:`ThumbnailGenerationTask`.
"""

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlstash.db_models import Picture

from .base_task_finder import BaseTaskFinder
from .thumbnail_generation_task import ThumbnailGenerationTask


class MissingThumbnailFinder(BaseTaskFinder):
    """Find pictures missing a generated thumbnail and create a generation task."""

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingThumbnailFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        limit = ThumbnailGenerationTask.BATCH_SIZE * (
            max(1, self.max_inflight_tasks()) + 1
        )
        pictures = self._db.run_immediate_read_task(self._fetch_missing, limit)
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, ThumbnailGenerationTask.BATCH_SIZE)
        if not selected:
            return None

        return ThumbnailGenerationTask(database=self._db, pictures=selected)

    @staticmethod
    def _fetch_missing(session: Session, limit: int):
        return session.exec(
            select(Picture)
            .where(Picture.thumbnail_width.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
            .options(selectinload(Picture.faces))
            .order_by(Picture.id)
            .limit(limit)
        ).all()

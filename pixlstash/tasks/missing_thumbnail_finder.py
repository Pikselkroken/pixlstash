"""Finder for pictures whose thumbnail has not been generated.

Keyed on ``Picture.thumbnail_width IS NULL``. New imports populate the thumbnail
columns at import time, so this finder normally only picks up rows an upgrade
reset to NULL, driving a one-time regeneration of the whole-frame AR bitmap
through :class:`ThumbnailGenerationTask`.
"""

from sqlmodel import Session, select
from sqlalchemy.orm import load_only, selectinload

from pixlstash.db_models import Face, Picture

from .base_task_finder import BaseTaskFinder
from .thumbnail_generation_task import ThumbnailGenerationTask


class MissingThumbnailFinder(BaseTaskFinder):
    """Find pictures missing a generated thumbnail and create a generation task."""

    def __init__(self, database, notifier=None):
        super().__init__()
        self._db = database
        self._notifier = notifier

    def finder_name(self) -> str:
        return "MissingThumbnailFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        limit = ThumbnailGenerationTask.BATCH_SIZE * (
            max(1, self.max_inflight_tasks()) + 1
        )
        # Exclude undecodable pictures (issue #585) at the query so they cannot
        # crowd the candidate window and stall real work behind them.
        suppressed_ids = self._db.unprocessable_images.active_suppressed_ids()
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing(session, limit, suppressed_ids)
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, ThumbnailGenerationTask.BATCH_SIZE)
        if not selected:
            return None

        return ThumbnailGenerationTask(
            database=self._db, pictures=selected, notifier=self._notifier
        )

    @staticmethod
    def _fetch_missing(session: Session, limit: int, suppressed_ids=None):
        stmt = (
            select(Picture)
            .where(Picture.thumbnail_width.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        )
        if suppressed_ids:
            stmt = stmt.where(Picture.id.notin_(tuple(suppressed_ids)))
        return session.exec(
            stmt.options(
                # This probe runs on every planning sweep, so it must never drag
                # the LargeBinary columns (image_embedding / text_embedding /
                # likeness_parameters) or Face.features off disk. Narrow it to
                # exactly what ThumbnailGenerationTask reads from a DETACHED
                # picture: ``pic.id`` (task params + claim filter), ``pic.file_path``
                # (source resolution + thumbnail write path), and each face's
                # ``bbox`` (the square-crop weighting), which is a Python property
                # over the ``bbox_`` column. ``Face.picture_id`` is the selectin
                # correlation column and must not be deferred. Anything else read
                # downstream would raise DetachedInstanceError, because
                # ``run_immediate_read_task`` closes the session before the task runs.
                load_only(Picture.id, Picture.file_path),
                selectinload(Picture.faces).load_only(Face.bbox_, Face.picture_id),
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()

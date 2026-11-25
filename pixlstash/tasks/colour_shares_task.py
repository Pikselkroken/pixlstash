"""Compute ``Picture.colour_shares`` - the swatch summary of a picture's thumbnail.

The thumbnail is the stored WebP bitmap, so this reads a few hundred kilobytes
of pixels at most and never touches the original file. It is CPU work at LOW
priority; nothing waits on it and nothing is announced when it lands.

A thumbnail that cannot be found or read leaves the column NULL and the picture
in the unprocessable-image registry (#585), pinned to the file it failed on, so
the finder skips it until that file changes or the server restarts. Nothing
durable is written for a failure: a drive that is briefly away, or a thumbnail
caught mid-rewrite, must not stamp a picture "no colours" for good. Only rows
still NULL are written, so a value that landed meanwhile is never overwritten.
"""

import time

from sqlalchemy import update
from sqlmodel import Session

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.colour_shares import (
    shares_as_json,
    shares_from_bytes,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)


class ColourSharesTask(BaseTask):
    """Fill ``colour_shares`` for a batch of pictures that have a thumbnail."""

    BATCH_SIZE = 64

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def __init__(self, database, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="ColourSharesTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._pictures = pictures or []

    def _run_task(self):
        start = time.time()
        updates: list[tuple[int, str]] = []
        for pic in self._pictures:
            summary = self._summary_of(pic)
            if summary is not None:
                updates.append((int(pic.id), summary))

        if not updates:
            return {"changed_count": 0}
        written = self._db.run_task(
            ColourSharesTask._persist_summaries, updates, priority=DBPriority.LOW
        )
        logger.debug(
            "ColourSharesTask completed in %.2fs with %s update(s)",
            time.time() - start,
            written,
        )
        return {"changed_count": written}

    def _summary_of(self, pic) -> str | None:
        """The stored JSON for one picture, or ``None`` if it cannot be read now."""
        thumb_path = ImageUtils.find_thumbnail(self._db.image_root, pic.file_path)
        if not thumb_path:
            # Pinned to the source: a new source makes a new thumbnail.
            self._mark_unreadable(
                pic,
                ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path),
                "no thumbnail on disk for its colour summary",
            )
            return None
        try:
            with open(thumb_path, "rb") as handle:
                return shares_as_json(shares_from_bytes(handle.read()))
        except Exception as exc:
            self._mark_unreadable(
                pic,
                thumb_path,
                f"thumbnail unreadable for its colour summary ({type(exc).__name__}: {exc})",
            )
            return None

    def _mark_unreadable(self, pic, path, reason: str) -> None:
        """Skip the picture until *path* changes, and say why once."""
        registry = getattr(self._db, "unprocessable_images", None)
        if registry is not None and path:
            registry.mark_unprocessable(pic.id, str(path), reason=reason)
            return
        logger.warning(
            "Colour shares: picture %s (%s): %s; it will be tried again",
            pic.id,
            pic.file_path,
            reason,
        )

    @staticmethod
    def _persist_summaries(session: Session, updates: list[tuple[int, str]]) -> int:
        """Write each summary where the column is still NULL, in one transaction."""
        written = 0
        for picture_id, value in updates:
            result = session.exec(
                update(Picture)
                .where(Picture.id == picture_id, Picture.colour_shares.is_(None))
                .values(colour_shares=value)
            )
            written += result.rowcount or 0
        session.commit()
        return written

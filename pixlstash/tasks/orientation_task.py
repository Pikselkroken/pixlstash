"""Backfill ``Picture.orientation`` - the mirrored EXIF orientation tag.

The operation log records orientation as a reversible facet (§21), and it
captures that facet from the **column**, never from the file: capture runs for
every recorded operation over every affected picture, so opening two files per
picture would make a 2,700-row tag edit do 5,400 file opens on the single DB
writer thread.

A NULL column is therefore a picture whose rotate could not be recorded honestly
- the ``before_state`` would say ``null`` and undo would have nothing to write
back. Every row predating the column is NULL, so this task exists to fill them.
The rotate endpoint does not wait for it (it primes its own targets first); this
is what keeps the rest of the library ready.

The value written is exactly what ``read_orientation`` returns, i.e. what every
decoder in the stack assumes, including ``1`` for a file that carries no
orientation at all and for one whose tag is unreadable. That is deliberate: the
column has to describe what the renderers do, not what the container says.
"""

import time

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.orientation import read_orientation

logger = get_logger(__name__)


class OrientationTask(BaseTask):
    """Read the EXIF orientation of a batch of pictures that have none stored."""

    BATCH_SIZE = 128

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def __init__(self, database, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="OrientationTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._pictures = pictures or []

    def _run_task(self):
        start = time.time()
        updates: list[tuple[int, int]] = []
        for pic in self._pictures:
            file_path = ImageUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            if not file_path:
                continue
            # read_orientation never raises: an unreadable or orientation-less
            # file reports 1, which is what the decoders assume anyway. Writing
            # that is what makes the probe terminate instead of re-selecting the
            # same rows for ever.
            updates.append((int(pic.id), read_orientation(str(file_path))))

        if not updates:
            return {"changed_count": 0, "changed": []}

        changed = self._db.run_task(
            OrientationTask._persist_orientations, updates, priority=DBPriority.LOW
        )
        logger.debug(
            "OrientationTask completed in %.2fs with %s update(s)",
            time.time() - start,
            len(changed or []),
        )
        return {"changed_count": len(changed or []), "changed": changed or []}

    @staticmethod
    def _persist_orientations(session: Session, updates: list[tuple[int, int]]) -> list:
        """Write the orientations in one transaction.

        Only a row that is still NULL is written. A rotate that landed between
        this task's file reads and its write already stored the *new* value, and
        overwriting it with the value read before the rotate would leave the
        mirror describing a file that no longer exists.
        """
        changed = []
        for picture_id, orientation in updates:
            pic = session.get(Picture, picture_id)
            if pic is None or pic.orientation is not None:
                continue
            pic.orientation = orientation
            session.add(pic)
            changed.append((Picture, picture_id, "orientation", orientation))
        session.commit()
        return changed

    @staticmethod
    def find_pictures_missing_orientation(session: Session, limit: int) -> list:
        """Return non-deleted pictures with a file but no stored orientation."""
        return session.exec(
            select(Picture)
            .where(Picture.orientation.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
            .order_by(Picture.id)
            .limit(limit)
        ).all()

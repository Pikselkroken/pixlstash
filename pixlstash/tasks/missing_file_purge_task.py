import os
from datetime import datetime, timezone

from sqlmodel import Session, select

from pixlstash.db_models import DeletedFileLog, Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)


class MissingFilePurgeTask(BaseTask):
    """Low-priority task that removes picture records whose files no longer exist.

    For each picture in the batch whose file is absent from the filesystem the
    task:

    1. Inserts a row into ``deleted_file_log`` (if one does not already exist
       for that ``file_path``).
    2. Deletes the picture row from the ``picture`` table.

    The task operates at ``TaskPriority.LOW`` so it never starves active work.
    """

    BATCH_SIZE = 250

    def __init__(self, database, pictures: list):
        """Initialise the task.

        Args:
            database: The application database instance.
            pictures: Sequence of picture rows returned by the finder (must
                expose ``id``, ``file_path``, and ``pixel_sha`` attributes).
        """
        picture_ids = [p.id for p in (pictures or []) if getattr(p, "id", None)]
        super().__init__(
            task_type="MissingFilePurgeTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._pictures = pictures or []

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        image_root = self._db.image_root
        missing = []
        for pic in self._pictures:
            if not pic.file_path:
                continue
            try:
                resolved = ImageUtils.resolve_picture_path(image_root, pic.file_path)
                if not os.path.isfile(resolved):
                    missing.append(pic)
            except Exception as exc:
                logger.debug(
                    "MissingFilePurgeTask: could not resolve path for picture %s: %s",
                    pic.id,
                    exc,
                )

        if not missing:
            return {"purged": 0}

        logger.info(
            "MissingFilePurgeTask: found %s missing file(s) in batch of %s — purging.",
            len(missing),
            len(self._pictures),
        )

        purged = self._db.run_task(self._purge_pictures, missing)
        logger.info("MissingFilePurgeTask: purged %s picture record(s).", purged)
        return {"purged": purged}

    @staticmethod
    def _purge_pictures(session: Session, pictures: list) -> int:
        """Delete picture rows and log them to deleted_file_log."""
        purged = 0
        now = datetime.now(timezone.utc)

        for pic in pictures:
            # Log the deletion only if not already recorded.
            if pic.file_path:
                already_logged = session.exec(
                    select(DeletedFileLog).where(
                        DeletedFileLog.file_path == pic.file_path
                    )
                ).first()
                if not already_logged:
                    session.add(
                        DeletedFileLog(
                            file_path=pic.file_path,
                            pixel_sha=pic.pixel_sha,
                            deleted_at=now,
                        )
                    )

            db_pic = session.get(Picture, pic.id)
            if db_pic is not None:
                session.delete(db_pic)
                purged += 1

        session.commit()
        return purged

import os
from datetime import datetime, timezone

from sqlmodel import Session, select

from pixlstash.db_models import DeletedFileLog, Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.services.scrapheap_service import file_location_is_unreachable
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
        unreachable = 0
        for pic in self._pictures:
            if not pic.file_path:
                continue
            try:
                resolved = ImageUtils.resolve_picture_path(image_root, pic.file_path)
                if os.path.isfile(resolved):
                    continue
                if file_location_is_unreachable(resolved):
                    # The file is absent because its LOCATION is absent — an
                    # unmounted reference folder or a dropped network share —
                    # not because it was deleted. Purging here would delete a
                    # live picture's row AND write file_removed=True, so a later
                    # restore would refuse to bring it back: permanent loss of a
                    # file that never went anywhere. Skip until the volume is
                    # back; the next pass will re-examine it.
                    unreachable += 1
                    logger.warning(
                        "MissingFilePurgeTask: SKIPPING picture %s — its "
                        "location is unreachable (parent directory missing at "
                        "%s; unmounted reference folder or network vault?). Not "
                        "purging, because the file may still exist.",
                        pic.id,
                        resolved,
                    )
                    continue
                missing.append(pic)
            except Exception as exc:
                logger.debug(
                    "MissingFilePurgeTask: could not resolve path for picture %s: %s",
                    pic.id,
                    exc,
                )

        if unreachable:
            logger.warning(
                "MissingFilePurgeTask: %s picture(s) in this batch live at "
                "unreachable locations and were left untouched.",
                unreachable,
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
            # Log the deletion only if not already recorded. The path is stored
            # as an opaque hash (path_sha), never in cleartext.
            if pic.file_path:
                path_sha = DeletedFileLog.hash_path(pic.file_path)
                already_logged = session.exec(
                    select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
                ).first()
                if not already_logged:
                    session.add(
                        DeletedFileLog(
                            path_sha=path_sha,
                            pixel_sha=pic.pixel_sha,
                            deleted_at=now,
                            # The file is genuinely gone from disk (that is why
                            # it is being purged), so this is a real permanent
                            # deletion restore must never resurrect.
                            file_removed=True,
                        )
                    )
                elif not already_logged.file_removed:
                    # The path was first logged as file_removed=False (removed
                    # from the library but the file was deliberately kept). The
                    # file has now genuinely vanished, so upgrade the stale flag
                    # to True instead of skipping — the ledger must be truthful,
                    # not merely rely on restore's separate missing-file net.
                    # Only ever raise False -> True; never downgrade.
                    already_logged.file_removed = True
                    session.add(already_logged)

            db_pic = session.get(Picture, pic.id)
            if db_pic is not None:
                session.delete(db_pic)
                purged += 1

        session.commit()
        return purged

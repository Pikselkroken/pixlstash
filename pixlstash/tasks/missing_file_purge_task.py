import os
from datetime import datetime, timezone

from sqlmodel import Session, select

from pixlstash.db_models import DeletedFileLog, Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.services.scrapheap_service import file_location_is_unreachable
from pixlstash.services.workflow_ghost_service import (
    DEFAULT_GHOST_RETENTION,
    cascade_uncovered_ghosts,
    surviving_instance_hashes_in_session,
)
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
    3. Runs the **covered-ghost cascade** (§B4) over the instance hashes it just
       destroyed.

    Step 3 is here and not only in ``scrapheap_service`` because this is the one
    other path that removes a picture row for good. It can never *write* a
    ghost — the file it is purging is already gone from disk, so there is no
    thumbnail to keep — but it can remove the last picture covering an instance
    hash, and a ghost that quietly stops being covered is precisely the decay
    library plan §5 says must not be allowed to happen unobserved.

    The task operates at ``TaskPriority.LOW`` so it never starves active work.
    """

    BATCH_SIZE = 250

    def __init__(
        self,
        database,
        pictures: list,
        hub=None,
        library_uuid=None,
        ghost_retention: str = DEFAULT_GHOST_RETENTION,
    ):
        """Initialise the task.

        Args:
            database: The application database instance.
            pictures: Sequence of picture rows returned by the finder (must
                expose ``id``, ``file_path``, and ``pixel_sha`` attributes).
            hub: The hub database, or ``None`` for a vault opened without one
                (the CLI tools, most tests) — then there is no ghost store to
                cascade over.
            library_uuid: Which library's ghosts to cascade over. ``None``
                alongside a ``None`` hub; a ghost is scoped to the library whose
                vault holds its cover.
            ghost_retention: The user's ghost-retention position. Only
                ``covered`` cascades; see
                :func:`~pixlstash.services.workflow_ghost_service.cascade_uncovered_ghosts`.
        """
        picture_ids = [p.id for p in (pictures or []) if getattr(p, "id", None)]
        super().__init__(
            task_type="MissingFilePurgeTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._pictures = pictures or []
        self._hub = hub
        self._library_uuid = library_uuid
        self._ghost_retention = ghost_retention

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
            return {"purged": 0, "ghosts_cascaded": 0}

        logger.info(
            "MissingFilePurgeTask: found %s missing file(s) in batch of %s — purging.",
            len(missing),
            len(self._pictures),
        )

        purged, destroyed_hashes, surviving = self._db.run_task(
            self._purge_pictures, missing
        )
        logger.info("MissingFilePurgeTask: purged %s picture record(s).", purged)
        cascaded = cascade_uncovered_ghosts(
            self._hub,
            self._library_uuid,
            self._ghost_retention,
            destroyed_hashes,
            surviving,
        )
        return {"purged": purged, "ghosts_cascaded": cascaded}

    @staticmethod
    def _purge_pictures(
        session: Session, pictures: list
    ) -> tuple[int, set[str], set[str]]:
        """Delete picture rows, log them, and report what the cascade needs.

        Returns ``(purged, destroyed_instance_hashes, surviving_instance_hashes)``.
        The surviving set is read after the deletes, in this same submission, so
        no picture being destroyed can count as its own cover. (SQLAlchemy's
        autoflush would make the read see the pending deletes even before the
        commit; the commit is placed first because that is the order that stays
        correct if autoflush is ever turned off, not because it is what makes
        the read right today.)
        """
        purged = 0
        destroyed_hashes: set[str] = set()
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
                if db_pic.workflow_instance_hash:
                    destroyed_hashes.add(db_pic.workflow_instance_hash)
                session.delete(db_pic)
                purged += 1

        session.commit()
        surviving = surviving_instance_hashes_in_session(
            session, sorted(destroyed_hashes)
        )
        return purged, destroyed_hashes, surviving

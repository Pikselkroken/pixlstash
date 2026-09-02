import os
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from pixlstash.db_models import DeletedFileLog, Picture
from pixlstash.db_models.picture_move import RETENTION_S, PictureMove
from pixlstash.pixl_logging import get_logger
from pixlstash.services.scrapheap_service import file_location_is_unreachable
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.sql_chunking import chunked

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
                    # The file is absent because its LOCATION is absent - an
                    # unmounted reference folder or a dropped network share -
                    # not because it was deleted. Purging here would delete a
                    # live picture's row AND write file_removed=True, so a later
                    # restore would refuse to bring it back: permanent loss of a
                    # file that never went anywhere. Skip until the volume is
                    # back; the next pass will re-examine it.
                    unreachable += 1
                    logger.warning(
                        "MissingFilePurgeTask: SKIPPING picture %s - its "
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
            return {"purged": 0, "repaired": 0, "deferred": 0}

        repairs, deferred, missing = self._separate_our_own_moves(missing)

        repaired = 0
        if repairs:
            repaired = self._db.run_task(self._repair_moved_pictures, repairs)
            logger.info(
                "MissingFilePurgeTask: repaired %s picture(s) the layout engine "
                "had moved but not finished recording; none were purged.",
                repaired,
            )

        if not missing:
            return {"purged": 0, "repaired": repaired, "deferred": deferred}

        logger.info(
            "MissingFilePurgeTask: found %s missing file(s) in batch of %s - purging.",
            len(missing),
            len(self._pictures),
        )

        purged = self._db.run_task(self._purge_pictures, missing)
        logger.info("MissingFilePurgeTask: purged %s picture record(s).", purged)
        return {"purged": purged, "repaired": repaired, "deferred": deferred}

    def _separate_our_own_moves(self, candidates: list):
        """Split off pictures PixlStash moved itself but never finished recording.

        The move engine commits a ``picture_move`` row naming both paths
        **before** it renames anything, precisely so this sweep can tell "the
        owner deleted it" from "we moved it and the process died before the row
        was repointed". Purging the latter is unrecoverable: the picture row
        goes and its tags, sets, faces and score go with it, and the
        ``DeletedFileLog`` written alongside carries ``file_removed=True``,
        which :mod:`full_restore` reads as *never resurrect* - so not even a
        snapshot brings it back.

        **The journal is read in both directions**, because a move has two ends
        and a crash can strand a file at either. A row is a record that this
        file belongs at one of two paths: if the row's *other* end holds a file,
        that is where it is, whether the interrupted operation was the move
        (``old_path`` is the row's, the file is at ``new_path``) or the undo of
        one (``new_path`` is the row's, the file is back at ``old_path``).

        Three outcomes per candidate:

        * a journal row whose other end **is** a file on disk - repaired here,
          the row repointed, nothing lost;
        * a journal row whose other end is not - left alone for a later pass,
          because an operation still in flight must not be read as a deletion.
          The journal expires after ``RETENTION_S``, so this defers rather than
          exempts;
        * no journal row at all - a genuine deletion, purged as before.

        Returns ``(repairs, deferred_count, still_missing)``.
        """
        by_id: dict = {pic.id: pic for pic in candidates if pic.file_path}
        if not by_id:
            return [], 0, candidates

        rows = self._db.run_immediate_read_task(self._load_move_journal, list(by_id))
        if not rows:
            return [], 0, candidates

        # The far end of the newest move recorded for each picture. Matched on
        # the picture id *and* the path: rows outlive their move by
        # ``RETENTION_S``, and a later picture that reuses the path must not be
        # repointed at wherever the earlier one went.
        other_end: dict = {}
        stamped: dict = {}
        for row in rows:
            pic = by_id.get(row.picture_id)
            if pic is None:
                continue
            for here, there in (
                (row.old_path, row.new_path),
                (row.new_path, row.old_path),
            ):
                if here != pic.file_path:
                    continue
                if pic.id in stamped and stamped[pic.id] >= row.moved_at:
                    continue
                stamped[pic.id] = row.moved_at
                other_end[pic.id] = there

        image_root = self._db.image_root
        repairs: list = []
        deferred = 0
        still_missing: list = []
        for pic in candidates:
            landed_path = other_end.get(pic.id)
            if landed_path is None:
                still_missing.append(pic)
                continue
            try:
                landed = ImageUtils.resolve_picture_path(image_root, landed_path)
            except Exception as exc:
                logger.warning(
                    "MissingFilePurgeTask: picture %s at %s has a move journal "
                    "row pointing at %s, which could not be resolved (%s). "
                    "Leaving it alone rather than purging.",
                    pic.id,
                    pic.file_path,
                    landed_path,
                    exc,
                )
                deferred += 1
                continue
            if os.path.isfile(landed):
                repairs.append((pic.id, landed_path))
                continue
            deferred += 1
            logger.warning(
                "MissingFilePurgeTask: SKIPPING picture %s at %s - PixlStash "
                "recorded a move between that path and %s, and neither holds a "
                "file. Not purging: an operation still in flight must not be "
                "read as a deletion. The journal row expires on its own if it "
                "never completes.",
                pic.id,
                pic.file_path,
                landed_path,
            )
        return repairs, deferred, still_missing

    @staticmethod
    def _load_move_journal(session: Session, picture_ids: list) -> list:
        """Load unexpired journal rows for any of *picture_ids*."""
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=RETENTION_S
        )
        rows: list = []
        for chunk in chunked(picture_ids):
            rows.extend(
                session.exec(
                    select(PictureMove)
                    .where(PictureMove.picture_id.in_(list(chunk)))
                    .where(PictureMove.moved_at >= cutoff)
                ).all()
            )
        return rows

    @staticmethod
    def _repair_moved_pictures(session: Session, repairs: list) -> int:
        """Repoint each row at the path its file actually reached."""
        repaired = 0
        for picture_id, new_path in repairs:
            picture = session.get(Picture, picture_id)
            if picture is None:
                continue
            logger.info(
                "MissingFilePurgeTask: repointing picture %s from %s to %s - "
                "PixlStash moved this file and did not finish recording it.",
                picture_id,
                picture.file_path,
                new_path,
            )
            picture.file_path = new_path
            session.add(picture)
            repaired += 1
        session.commit()
        return repaired

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
                    # to True instead of skipping - the ledger must be truthful,
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

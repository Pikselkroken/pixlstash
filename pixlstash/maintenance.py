"""Startup maintenance jobs for the PixlStash server.

Extracted verbatim from ``pixlstash.server`` (Phase 2, §4.1 of the backend
refactor). ``MaintenanceMixin`` provides the two startup housekeeping jobs the
lifespan runs in a worker thread: regenerating missing thumbnails and pruning
picture rows whose source files have disappeared. ``Server`` inherits the mixin,
so the methods keep their original ``self.``-bound call sites unchanged.
"""

import os

from PIL import Image
from sqlmodel import select

from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)


class MaintenanceMixin:
    """Startup thumbnail generation and missing-picture cleanup for ``Server``."""

    def _generate_missing_thumbnails(self):
        def fetch_pictures(session):
            return session.exec(select(Picture.id, Picture.file_path)).all()

        rows = self.vault.db.run_immediate_read_task(fetch_pictures)
        if not rows:
            logger.info("No pictures found for thumbnail generation.")
            return

        missing = []
        for row in rows:
            pic_id, file_path = row
            if not file_path:
                continue
            # find_thumbnail moves a pre-#1164 bitmap into .pixlstash-thumbnails
            # as a side effect, so this pass is also the library's migration:
            # nothing is re-rendered for having lived beside its picture.
            if ImageUtils.find_thumbnail(self.vault.image_root, file_path):
                continue
            missing.append((pic_id, file_path))

        total = len(missing)
        if total == 0:
            logger.debug("All thumbnails already exist.")
            return

        logger.info("Generating %s missing thumbnails at startup.", total)
        generated = 0
        skipped = 0
        missing_source_count = 0
        for index, (pic_id, file_path) in enumerate(missing, start=1):
            resolved = ImageUtils.resolve_picture_path(self.vault.image_root, file_path)
            if not resolved or not os.path.exists(resolved):
                missing_source_count += 1
                skipped += 1
                logger.warning(
                    "Missing source file for thumbnail generation: %s", resolved
                )
                if (
                    missing_source_count == 1
                    and not self.DEFAULT_CLEANUP_MISSING_PICTURES
                ):
                    logger.info(
                        "Startup cleanup tip: run with '--cleanup-missing-pictures' "
                        "to remove stale picture records that point to missing files."
                    )
                continue
            img = ImageUtils.load_image_or_video(resolved)
            if img is None:
                skipped += 1
                logger.warning(
                    "Failed to load image for thumbnail generation: %s", resolved
                )
                continue
            if not isinstance(img, Image.Image):
                img = Image.fromarray(img)
            thumbnail_bytes = ImageUtils.generate_thumbnail_bytes(img)
            if not thumbnail_bytes:
                skipped += 1
                logger.warning(
                    "Failed to generate thumbnail bytes for picture %s", pic_id
                )
                continue
            saved = ImageUtils.write_thumbnail_bytes(
                self.vault.image_root, file_path, thumbnail_bytes
            )
            if saved:
                generated += 1
            else:
                skipped += 1
                logger.warning("Failed to persist thumbnail for picture %s", pic_id)
            if index % 250 == 0:
                logger.info("Thumbnail generation progress: %s/%s", index, total)

        logger.info(
            "Thumbnail generation completed: %s generated, %s skipped (%s missing source files).",
            generated,
            skipped,
            missing_source_count,
        )

    def _cleanup_missing_pictures(self):
        def fetch_pictures(session):
            return session.exec(select(Picture.id, Picture.file_path)).all()

        rows = self.vault.db.run_immediate_read_task(fetch_pictures)
        if not rows:
            logger.info("No pictures found for startup missing-file cleanup.")
            return

        missing_ids = []
        thumbnail_candidates = []
        for row in rows:
            pic_id, file_path = row
            resolved = None
            if file_path:
                resolved = ImageUtils.resolve_picture_path(
                    self.vault.image_root, file_path
                )
            if not resolved or not os.path.isfile(resolved):
                missing_ids.append(pic_id)
                if file_path:
                    thumbnail_candidates.append(file_path)

        if not missing_ids:
            logger.info("Startup missing-file cleanup found no stale picture records.")
            return

        logger.warning(
            "Startup missing-file cleanup removing %s stale picture records.",
            len(missing_ids),
        )

        def delete_rows(session, ids: list[int]):
            deleted_count = 0
            pictures = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
            for pic in pictures:
                session.delete(pic)
                deleted_count += 1
            session.commit()
            return deleted_count

        deleted_count = self.vault.db.run_task(delete_rows, missing_ids)

        thumbnails_removed = 0
        for rel_path in thumbnail_candidates:
            thumbnails_removed += ImageUtils.remove_thumbnail(
                self.vault.image_root, rel_path
            )

        logger.info(
            "Startup missing-file cleanup completed: %s records removed, %s orphan thumbnails removed.",
            deleted_count,
            thumbnails_removed,
        )

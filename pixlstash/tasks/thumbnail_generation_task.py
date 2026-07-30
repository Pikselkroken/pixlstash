"""Background generation of picture thumbnails.

A thumbnail is ONE aspect-ratio-preserving bitmap of the whole frame (short edge
``THUMBNAIL_SHORT_EDGE`` px, long edge capped), plus the face-weighted square-crop
rectangle within it. Generation is MODE-AGNOSTIC — the per-user
``thumbnail_mode`` is a display-only preference the frontend owns and is never
read here.

This task is data-driven off ``Picture.thumbnail_width IS NULL``. New imports
populate the columns at import time, so in normal operation the only rows this
task processes are those an upgrade reset to NULL (a one-time regeneration of the
whole-frame bitmap). Each processed picture is regenerated from its source: the
bitmap file is (re)written and every ``thumbnail_*`` / ``square_crop_*`` column is
set. Faces (when already present) weight the square-crop rectangle.
"""

import ast
import os

from PIL import Image
from sqlmodel import Session

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)


class ThumbnailGenerationTask(BaseTask):
    """Generate thumbnails for one batch of pictures (CPU queue)."""

    BATCH_SIZE = 64

    def __init__(self, database, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="ThumbnailGenerationTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._pictures = pictures or []

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _face_bboxes(pic) -> list:
        bboxes = []
        for face in getattr(pic, "faces", []) or []:
            bbox = getattr(face, "bbox", None)
            if isinstance(bbox, str):
                try:
                    bbox = ast.literal_eval(bbox)
                except Exception:
                    bbox = None
            if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                bboxes.append([float(v) for v in bbox])
        return bboxes

    def _resolve_columns(self, pic) -> dict | None:
        """Regenerate the whole-frame bitmap for one picture; return column values.

        Writes the thumbnail file as a side effect. Returns ``None`` when the
        source is missing/unreadable (the missing-file purge finder owns cleanup).
        """
        file_path = getattr(pic, "file_path", None)
        if not file_path:
            return None
        resolved = ImageUtils.resolve_picture_path(self._db.image_root, file_path)
        if not resolved or not os.path.exists(resolved):
            return None

        img = ImageUtils.load_image_or_video(resolved)
        if img is None:
            # The file exists but cannot be decoded. Returning None leaves
            # thumbnail_width NULL, which is exactly what MissingThumbnailFinder
            # selects on — without marking, the same corrupt picture is
            # re-selected on every sweep forever (#585). The registry logs one
            # warning per file version and _filter_and_claim skips it.
            registry = getattr(self._db, "unprocessable_images", None)
            if registry is not None:
                registry.mark_unprocessable(
                    getattr(pic, "id", None),
                    str(resolved),
                    reason="thumbnail source could not be decoded",
                )
            else:
                logger.warning(
                    "ThumbnailGenerationTask: failed to load source for picture %s (%s)",
                    getattr(pic, "id", None),
                    resolved,
                )
            return None
        if not isinstance(img, Image.Image):
            img = Image.fromarray(img)

        rendered = ImageUtils.render_thumbnail(img, face_bboxes=self._face_bboxes(pic))
        if rendered is None:
            return None
        thumbnail_bytes, bmp_w, bmp_h, crop = rendered

        saved = ImageUtils.write_thumbnail_bytes(
            self._db.image_root, file_path, thumbnail_bytes
        )
        if not saved:
            logger.warning(
                "ThumbnailGenerationTask: failed to persist thumbnail for picture %s",
                getattr(pic, "id", None),
            )
        return {
            "thumbnail_width": bmp_w,
            "thumbnail_height": bmp_h,
            "square_crop_x": crop["x"],
            "square_crop_y": crop["y"],
            "square_crop_side": crop["side"],
        }

    def _run_task(self):
        updates: dict[int, dict] = {}
        for pic in self._pictures:
            pic_id = getattr(pic, "id", None)
            if pic_id is None:
                continue
            try:
                columns = self._resolve_columns(pic)
            except Exception as exc:
                logger.warning(
                    "ThumbnailGenerationTask: error processing picture %s: %s",
                    pic_id,
                    exc,
                )
                continue
            if columns:
                updates[pic_id] = columns

        if not updates:
            return {"changed_count": 0}

        def _persist(session: Session, updates: dict[int, dict]):
            changed = 0
            for pic_id, columns in updates.items():
                db_pic = session.get(Picture, pic_id)
                if db_pic is None:
                    continue
                for key, value in columns.items():
                    setattr(db_pic, key, value)
                session.add(db_pic)
                changed += 1
            session.commit()
            return changed

        changed = self._db.run_task(_persist, updates, priority=DBPriority.LOW)
        logger.debug("ThumbnailGenerationTask updated %s pictures.", changed)
        return {"changed_count": changed or 0}

"""Quality calculation and persistence utilities."""

from typing import List, Tuple

import cv2
import numpy as np
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy import update as sa_update
from sqlmodel import delete, select

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.quality import Quality
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class QualityUtils:
    """Utility helper for picture and face quality calculation and persistence."""

    def __init__(self, database):
        self._db = database

    def group_pictures_by_format_and_size(self, pics: List[Picture]) -> dict:
        """Group pictures into a dict keyed by (format, width, height)."""
        groups = {}
        current_key = None
        current_group = []

        for pic in pics:
            if pic.format is None:
                raise ValueError(f"Picture id={pic.id} is missing format")
            if pic.width is None or pic.height is None:
                raise ValueError(f"Picture id={pic.id} is missing width/height")

            pic_format = pic.format.lower()
            key = (pic_format, pic.width, pic.height)

            if key != current_key:
                if current_key is not None:
                    groups[current_key] = current_group
                current_key = key
                current_group = [pic]
            else:
                current_group.append(pic)

        if current_group:
            groups[current_key] = current_group

        return groups

    def calculate_quality(
        self,
        pics: List[Picture],
        loaded_pics: List[np.ndarray] = None,
        max_side: int = None,
    ) -> List[Quality | None]:
        """
        Calculate quality metrics for a batch of pictures.

        Args:
            pics: List of Picture objects.
            loaded_pics: Pre-loaded numpy arrays (optional; loaded from disk if None).
            max_side: If set, downscale images so the longest side is at most this many
                pixels before computing quality.

        Returns:
            List of Quality objects (or None for pictures that failed to load).
        """
        try:
            all_qualities = []

            if loaded_pics is None:
                loaded_pics = []
                for pic in pics:
                    file_path = ImageUtils.resolve_picture_path(
                        self._db.image_root, pic.file_path
                    )
                    img = ImageUtils.load_image_or_video(file_path)
                    if img is None:
                        logger.warning(
                            "Could not load image for picture_id=%s, file_path=%s",
                            pic.id,
                            pic.file_path,
                        )
                    if img is not None and max_side:
                        img = self.downscale_image(img, max_side)
                    loaded_pics.append(img)
            elif max_side:
                loaded_pics = [
                    self.downscale_image(img, max_side) if img is not None else None
                    for img in loaded_pics
                ]

            valid_indices = [i for i, img in enumerate(loaded_pics) if img is not None]
            valid_pics = [img for img in loaded_pics if img is not None]
            if valid_pics:
                shapes = [img.shape for img in valid_pics]
                if len(set(shapes)) > 1:
                    logger.error(
                        "Shape mismatch in batch: %s", [str(s) for s in shapes]
                    )
                try:
                    batch_array = np.stack(valid_pics, axis=0)
                except Exception as stack_exc:
                    logger.error("np.stack failed: %s", stack_exc)
                    return [None] * len(pics)
                qualities = Quality.calculate_quality_batch(batch_array)
            else:
                qualities = []

            for i in range(len(pics)):
                if i in valid_indices:
                    q = qualities[valid_indices.index(i)]
                    all_qualities.append(q)
                else:
                    logger.warning(
                        "No quality calculated for picture_id=%s", pics[i].id
                    )
                    all_qualities.append(None)
            return all_qualities
        except Exception as exc:
            import traceback

            logger.error(
                "Failed to calculate quality for batch: %s\n%s",
                exc,
                traceback.format_exc(),
            )
            return [None] * len(pics)

    @staticmethod
    def downscale_image(img: np.ndarray, max_side: int) -> np.ndarray:
        """Downscale an image so its longest side is at most ``max_side`` pixels."""
        try:
            height, width = img.shape[:2]
            if max(height, width) <= max_side:
                return img
            scale = max_side / float(max(height, width))
            new_w = max(1, int(round(width * scale)))
            new_h = max(1, int(round(height * scale)))
            return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        except Exception as exc:
            logger.warning("Failed to downscale image: %s", exc)
            return img

    def update_quality(
        self, session, pics: List[Picture], qualities: List[Quality | None]
    ) -> List[Tuple[type, object, str, object]]:
        """Persist quality records for pictures and return a change list."""
        changed = []
        picture_ids = [getattr(pic, "id", None) for pic in pics]
        valid_ids = [pid for pid in picture_ids if pid is not None]
        if not valid_ids:
            return changed

        # One bulk query to find which pictures still exist and are not deleted.
        existing_id_set = set(
            session.exec(
                select(Picture.id)
                .where(Picture.id.in_(valid_ids))
                .where(Picture.deleted == False)  # noqa: E712
            ).all()
        )

        deleted_ids = set(valid_ids) - existing_id_set
        for pid in deleted_ids:
            logger.warning(
                "Skipping quality update for picture %s — picture was deleted.", pid
            )

        surviving_ids = list(existing_id_set)
        if not surviving_ids:
            return changed

        # Bulk-delete stale Quality rows and bulk-reset likeness_parameters in
        # two statements instead of N individual gets + adds.  Reset
        # size_bin_index alongside likeness_parameters: the likeness-parameters
        # finder treats size_bin_index IS NULL as the sole pending-work
        # indicator (see LikenessParameterUtils.find_next_work), so clearing the
        # blob without clearing the index would leave the picture invisible to
        # the finder and its likeness_parameters permanently NULL.
        session.exec(delete(Quality).where(Quality.picture_id.in_(surviving_ids)))
        session.exec(
            sa_update(Picture)
            .where(Picture.id.in_(surviving_ids))
            .values(likeness_parameters=None, size_bin_index=None)
        )

        for pic, quality in zip(pics, qualities):
            picture_id = getattr(pic, "id", None)
            if picture_id is None or picture_id not in existing_id_set:
                continue

            if quality is None:
                quality = Quality(
                    sharpness=-1.0,
                    edge_density=-1.0,
                    contrast=-1.0,
                    brightness=-1.0,
                    noise_level=-1.0,
                    colorfulness=-1.0,
                    luminance_entropy=-1.0,
                    dominant_hue=-1.0,
                )
            quality.picture_id = picture_id
            session.add(quality)
            changed.append((Picture, picture_id, "quality", quality))

        try:
            session.commit()
        except (IntegrityError, StaleDataError):
            session.rollback()
            logger.warning(
                "Quality update rolled back for batch of %s pictures — concurrent deletion likely.",
                len(pics),
            )
            return []

        return changed

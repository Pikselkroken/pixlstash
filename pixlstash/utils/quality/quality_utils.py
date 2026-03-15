"""Quality calculation and persistence utilities."""

from typing import List, Tuple

import cv2
import numpy as np
from sqlmodel import delete

from pixlstash.db_models.face import Face
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
        for pic, quality in zip(pics, qualities):
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
                    text_score=-1.0,
                    color_histogram=None,
                )
            session.exec(
                delete(Quality).where(
                    (Quality.picture_id == pic.id) & (Quality.face_id.is_(None))
                )
            )
            quality.picture_id = pic.id
            quality.face_id = None
            session.add(quality)
            pic.likeness_parameters = None
            session.add(pic)
            changed.append((Picture, pic.id, "quality", quality))
        session.commit()
        return changed

    def group_faces_by_format_and_size(
        self, faces: List[Tuple[Face, Picture]]
    ) -> Tuple[dict, List[Face]]:
        """
        Group face-picture pairs by (format, rounded_bbox_width, rounded_bbox_height).

        Returns a tuple of ``(groups_dict, invalid_faces_list)``.
        """
        groups = {}
        invalid_faces = []
        current_key = None
        current_group = []

        for face, pic in faces:
            if pic.format is None:
                raise ValueError(f"Picture id={pic.id} is missing format")

            pic_format = pic.format.lower()
            if face.bbox is None or len(face.bbox) != 4:
                logger.warning(
                    "Skipping face with missing/invalid bbox for picture_id=%s"
                    " face_id=%s",
                    pic.id,
                    face.id,
                )
                invalid_faces.append(face)
                continue

            x1, y1, x2, y2 = face.bbox
            bbox_width = int(round((x2 - x1) / 64.0) * 64)
            bbox_height = int(round((y2 - y1) / 64.0) * 64)
            key = (pic_format, bbox_width, bbox_height)

            if key != current_key:
                if current_key is not None:
                    groups[current_key] = current_group
                current_key = key
                current_group = [(pic, face)]
            else:
                current_group.append((pic, face))

        if current_group:
            groups[current_key] = current_group
        return groups, invalid_faces

    def calculate_face_quality(
        self, pics_and_faces: List[Tuple[Picture, Face]]
    ) -> List[Quality]:
        """
        Calculate quality metrics for a batch of face crops.

        Args:
            pics_and_faces: List of ``(Picture, Face)`` tuples.

        Returns:
            List of Quality objects (one per input pair).
        """
        try:
            all_qualities = []
            cropped_pics = []

            target_width = None
            target_height = None
            for pic, face in pics_and_faces:
                if face.bbox is not None and len(face.bbox) == 4:
                    x1, y1, x2, y2 = face.bbox
                    bbox_width = int(round((x2 - x1) / 64.0) * 64)
                    bbox_height = int(round((y2 - y1) / 64.0) * 64)
                    if bbox_width > 0 and bbox_height > 0:
                        target_width = bbox_width
                        target_height = bbox_height
                        break

            image_cache = {}
            for pic, face in pics_and_faces:
                if face.bbox is None or len(face.bbox) != 4:
                    logger.warning(
                        "Face bbox missing/invalid for picture_id=%s face_id=%s",
                        pic.id,
                        face.id,
                    )
                    cropped_pics.append(None)
                    continue

                x1, y1, x2, y2 = face.bbox
                if pic.id in image_cache:
                    img = image_cache[pic.id]
                else:
                    file_path = ImageUtils.resolve_picture_path(
                        self._db.image_root, pic.file_path
                    )
                    img = ImageUtils.load_image_or_video(file_path)
                    image_cache[pic.id] = img
                if img is None:
                    logger.warning(
                        "Could not load image for face quality:"
                        " picture_id=%s file_path=%s",
                        pic.id,
                        pic.file_path,
                    )
                    cropped_pics.append(None)
                    continue

                height, width = img.shape[:2]
                x1_clamped = max(0, min(width, int(round(x1))))
                x2_clamped = max(0, min(width, int(round(x2))))
                y1_clamped = max(0, min(height, int(round(y1))))
                y2_clamped = max(0, min(height, int(round(y2))))

                if x2_clamped <= x1_clamped or y2_clamped <= y1_clamped:
                    logger.warning(
                        "Invalid bbox after clamping for face quality:"
                        " file_path=%s bbox=%s clamped=%s",
                        pic.file_path,
                        face.bbox,
                        (x1_clamped, y1_clamped, x2_clamped, y2_clamped),
                    )
                    cropped_pics.append(None)
                    continue

                crop = img[y1_clamped:y2_clamped, x1_clamped:x2_clamped]
                if crop.size == 0:
                    logger.warning(
                        "Empty crop for face quality: file_path=%s bbox=%s"
                        " crop_shape=%s",
                        pic.file_path,
                        face.bbox,
                        getattr(crop, "shape", None),
                    )
                    cropped_pics.append(None)
                    continue

                if target_width and target_height:
                    try:
                        crop = cv2.resize(
                            crop,
                            (int(target_width), int(target_height)),
                            interpolation=cv2.INTER_AREA,
                        )
                    except Exception as resize_error:
                        logger.warning(
                            "OpenCV resize failed: file_path=%s bbox=%s"
                            " crop_shape=%s error=%s",
                            pic.file_path,
                            face.bbox,
                            getattr(crop, "shape", None),
                            resize_error,
                        )
                        cropped_pics.append(None)
                        continue

                if crop.ndim == 2:
                    crop = np.stack([crop] * 3, axis=-1)
                cropped_pics.append(crop)

            valid_indices = [i for i, img in enumerate(cropped_pics) if img is not None]
            valid_pics = [img for img in cropped_pics if img is not None]
            qualities = []
            if valid_pics:
                try:
                    batch_array = np.stack(valid_pics, axis=0)
                except Exception as stack_exc:
                    logger.error("np.stack failed: %s", stack_exc)
                    valid_indices = []
                    qualities = []
                else:
                    qualities = Quality.calculate_quality_batch(batch_array, False)

            for i in range(len(pics_and_faces)):
                if i in valid_indices:
                    q = qualities[valid_indices.index(i)]
                    all_qualities.append(q)
                else:
                    all_qualities.append(
                        Quality(
                            sharpness=-1.0,
                            edge_density=-1.0,
                            contrast=-1.0,
                            brightness=-1.0,
                            noise_level=-1.0,
                            color_histogram=None,
                        )
                    )
            return all_qualities
        except Exception as exc:
            import traceback

            logger.error(
                "Failed to calculate quality for batch: %s\n%s",
                exc,
                traceback.format_exc(),
            )
            return [
                Quality(
                    sharpness=-1.0,
                    edge_density=-1.0,
                    contrast=-1.0,
                    brightness=-1.0,
                    noise_level=-1.0,
                    color_histogram=None,
                )
                for _ in pics_and_faces
            ]

    def update_face_quality(
        self, session, faces: List[Face], qualities: List[Quality | None]
    ) -> List[Tuple[type, object, str, object]]:
        """Persist quality records for faces and return a change list."""
        changed = []
        for face, quality in zip(faces, qualities):
            if quality is None:
                quality = Quality(
                    sharpness=-1.0,
                    edge_density=-1.0,
                    contrast=-1.0,
                    brightness=-1.0,
                    noise_level=-1.0,
                    color_histogram=None,
                )
            quality.face_id = face.id
            quality.picture_id = face.picture_id
            session.add(quality)
            face.quality = quality
            session.add(face)
            changed.append((Face, face.id, "quality", quality))
        session.commit()
        return changed

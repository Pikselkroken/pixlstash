from typing import List, Tuple
import numpy as np
import time
import cv2

from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_utils import PictureUtils
from pixlvault.worker_registry import BaseWorker, WorkerType

from pixlvault.db_models.picture import Picture
from pixlvault.db_models.face import Face
from pixlvault.db_models.quality import Quality


logger = get_logger(__name__)


class QualityWorker(BaseWorker):
    def worker_type(self) -> WorkerType:
        return WorkerType.QUALITY

    def _run(self):
        logger.info("Starting QualityWorker...")
        BATCH_SIZE = 8
        time.sleep(0.75)
        while not self._stop.is_set():
            start = time.time()
            logger.debug("[QUALITY] Starting iteration...")
            quality_updates = 0
            try:
                # 1. Full image quality measures
                logger.debug(
                    "Searching for pictures needing full image quality calculation."
                )

                def find_pictures_missing_quality(session: Session):
                    result = session.exec(
                        select(Picture)
                        .outerjoin(Quality, Quality.picture_id == Picture.id)
                        .where(Quality.id.is_(None))
                        .order_by(Picture.format, Picture.width, Picture.height)
                    )
                    pics = result.all()
                    return pics

                pics_missing_quality = self._db.run_task(find_pictures_missing_quality)
                logger.debug(
                    f"Found {len(pics_missing_quality)} pictures missing quality metrics."
                )

                grouped_full = self._group_pictures_by_format_and_size(
                    pics_missing_quality
                )

                if self._stop.is_set():
                    break

                for group_key, group in grouped_full.items():
                    if self._stop.is_set():
                        break

                    batch = group[: min(len(group), BATCH_SIZE)]
                    # Determine expected shape from group key
                    expected_shape = (
                        group_key[2],
                        group_key[1],
                        3,
                    )  # (height, width, channels)
                    valid_batch = []
                    skipped = []
                    batch_shapes = []
                    for pic in batch:
                        if self._stop.is_set():
                            break

                        file_path = PictureUtils.resolve_picture_path(
                            self._db.image_root, pic.file_path
                        )
                        img = PictureUtils.load_image_or_video(file_path)
                        shape = img.shape if img is not None else None
                        batch_shapes.append(shape)
                        if shape == expected_shape:
                            valid_batch.append(pic)
                        else:
                            logger.warning(
                                f"Skipping image {pic.id}: expected shape {expected_shape}, got {shape}"
                            )
                            skipped.append(pic)
                    if len(valid_batch) > 0:
                        qualities = self._calculate_quality(valid_batch)
                        if qualities:
                            result = self._db.run_task(
                                self._update_quality,
                                valid_batch,
                                qualities,
                                priority=DBPriority.LOW,
                            )
                            self._notify_ids_processed(result)
                            quality_updates += len(result)
                        else:
                            logger.warning("[QUALITY] No quality updates calculated.")

            except Exception as e:
                import traceback

                logger.error(
                    "Quality Worker thread exiting due to error: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                break
            timing = time.time() - start
            if quality_updates > 0:
                logger.debug("QualityWorker: Done after %.2f seconds." % timing)
                self._notify_others(EventType.QUALITY_UPDATED)
            else:
                logger.debug(
                    "QualityWorker: Sleeping after %.2f seconds, no updates made."
                    % timing
                )
                self._wait()
        logger.info("QualityWorker: Stopped.")

    def _group_pictures_by_format_and_size(self, pics: List[Picture]):
        """
        Group pre-sorted pictures by (format, width, height).
        Returns: dict with (format, width, height) as keys and lists of pictures as values.
        """
        groups = {}
        current_key = None
        current_group = []

        for pic in pics:
            pic_format = pic.format.lower()
            width = pic.width
            height = pic.height
            key = (pic_format, width, height)

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

    def _calculate_quality(self, pics: List[Picture]) -> List["Quality"]:
        """
        Calculate quality metrics for a batch of images.
        Takes a list of pictures

        Returns:
          A list of qualities per picture with None-entries where the calculation failed.
        """
        try:
            all_qualities = []

            loaded_pics = []
            for pic in pics:
                file_path = PictureUtils.resolve_picture_path(
                    self._db.image_root, pic.file_path
                )
                img = PictureUtils.load_image_or_video(file_path)
                if img is None:
                    logger.warning(
                        f"Could not load image for picture_id={pic.id}, file_path={pic.file_path}"
                    )
                loaded_pics.append(img)

            # Remove None images for batch processing, keep index mapping
            valid_indices = [i for i, img in enumerate(loaded_pics) if img is not None]
            valid_pics = [img for img in loaded_pics if img is not None]
            if valid_pics:
                shapes = [img.shape for img in valid_pics]
                shape_set = set(shapes)
                if len(shape_set) > 1:
                    logger.error(f"Shape mismatch in batch: {[str(s) for s in shapes]}")
                try:
                    batch_array = np.stack(valid_pics, axis=0)
                except Exception as stack_exc:
                    logger.error(f"np.stack failed: {stack_exc}")
                    return [None] * len(pics)
                qualities = Quality.calculate_quality_batch(batch_array)
            else:
                qualities = []

            # Assign metrics
            for i in range(len(pics)):
                if i in valid_indices:
                    q = qualities[valid_indices.index(i)]
                    all_qualities.append(q)
                else:
                    logger.warning(f"No quality calculated for picture_id={pics[i].id}")
                    all_qualities.append(None)
            return all_qualities
        except Exception as e:
            import traceback

            logger.error(
                "Failed to calculate quality for batch: %s\n%s",
                e,
                traceback.format_exc(),
            )
            return [None] * len(pics)

    def _update_quality(
        self, session, pics: List[Picture], qualities: List["Quality"]
    ) -> List[Tuple[type, object, str]]:
        changed = []
        for pic, quality in zip(pics, qualities):
            session.add(quality)
            pic.quality = quality
            session.add(pic)
            changed.append((Picture, pic.id, "quality", quality))
        session.commit()
        return changed


class FaceQualityWorker(BaseWorker):
    def worker_type(self) -> WorkerType:
        return WorkerType.FACE_QUALITY

    def _run(self):
        logger.info("Starting FaceQualityWorker...")
        BATCH_SIZE = 8
        time.sleep(0.75)
        while not self._stop.is_set():
            start = time.time()
            logger.debug("[FACE QUALITY] Starting iteration...")
            quality_update_count = 0
            try:
                logger.debug(
                    "Searching for picture faces needing face quality calculation."
                )

                def find_faces_missing_quality(session: Session):
                    statement = (
                        select(Face, Picture)
                        .join(Picture, Face.picture_id == Picture.id)
                        .outerjoin(Quality, Quality.picture_id == Picture.id)
                        .where(Quality.id.is_(None))
                        .order_by(Picture.format, Picture.width, Picture.height)
                    )
                    result = session.exec(statement)
                    return result.all()

                faces_missing_quality = self._db.run_task(find_faces_missing_quality)

                logger.debug(
                    f"Found {len(faces_missing_quality)} faces missing face quality metrics."
                )

                grouped_faces = self._group_faces_by_format_and_size(
                    faces_missing_quality
                )

                for group in grouped_faces.values():
                    batch = group[:BATCH_SIZE]
                    if len(batch) > 0:
                        qualities = self._calculate_face_quality(batch)
                        if qualities:
                            faces = [face for _, face in batch]
                            result = self._db.run_task(
                                self._update_face_quality,
                                faces,
                                qualities,
                                priority=DBPriority.LOW,
                            )
                            self._notify_ids_processed(result)
                            quality_update_count += len(result)
                        else:
                            logger.warning(
                                "[FACE QUALITY] No quality updates calculated."
                            )
            except Exception as e:
                import traceback

                logger.error(
                    "FaceQualityWorker thread exiting due to error: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                break
            timing = time.time() - start
            if quality_update_count > 0:
                logger.debug("FaceQualityWorker: Done after %.2f seconds." % timing)
            else:
                logger.debug(
                    "FaceQualityWorker: Sleeping after %.2f seconds, no updates made."
                    % timing
                )
                self._wait()
        logger.info("FaceQualityWorker: Stopped.")

    def _group_faces_by_format_and_size(self, faces: List[Tuple[Face, Picture]]):
        """
        Group pre-sorted pictures by (format, bbox_width, bbox_height).
        Input must be sorted by format, bbox_width, bbox_height.
        Returns: dict with (format, bbox_width, bbox_height) as keys and lists of pictures as values.
        """
        groups = {}
        current_key = None
        current_group = []

        for face, pic in faces:
            pic_format = pic.format.lower()
            assert face.bbox is not None, "Face bbox is None."
            assert len(face.bbox) == 4, "Face bbox is invalid."

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
        return groups

    def _calculate_face_quality(
        self, pics_and_faces: List[Tuple[Picture, Face]]
    ) -> List["Quality"]:
        """
        Calculate quality metrics for a batch of faces.
        Takes a list of pictures.

        Returns:
          A list of qualities per face with None-entries where the calculation failed.
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
                file_path = PictureUtils.resolve_picture_path(
                    self._db.image_root, pic.file_path
                )
                img = PictureUtils.load_image_or_video(file_path)
                if img is None:
                    logger.warning(
                        "Could not load image for face quality: picture_id=%s file_path=%s",
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
                        "Invalid bbox after clamping for face quality: file_path=%s bbox=%s clamped=%s",
                        pic.file_path,
                        face.bbox,
                        (x1_clamped, y1_clamped, x2_clamped, y2_clamped),
                    )
                    cropped_pics.append(None)
                    continue

                crop = img[y1_clamped:y2_clamped, x1_clamped:x2_clamped]
                if crop.size == 0:
                    logger.warning(
                        "Empty crop for face quality: file_path=%s bbox=%s crop_shape=%s",
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
                            "OpenCV resize failed: file_path=%s bbox=%s crop_shape=%s error=%s",
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
        except Exception as e:
            import traceback

            logger.error(
                "Failed to calculate quality for batch: %s\n%s",
                e,
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

    def _update_face_quality(
        self, session, faces: List[Face], qualities: List["Quality"]
    ) -> List[Tuple[type, object, str, object]]:
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

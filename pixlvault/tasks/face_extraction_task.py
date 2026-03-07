import gc
import os
import platform
import threading
import time
import torch
from typing import List

import cv2
from insightface.app import FaceAnalysis
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import NO_VALUE
from sqlmodel import select

from pixlvault.database import DBPriority
from pixlvault.db_models.face import Face
from pixlvault.db_models.picture import Picture
from pixlvault.picture_tagger import PictureTagger
from pixlvault.utils.image_processing.image_utils import ImageUtils
from pixlvault.utils.image_processing.face_utils import FaceUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.tasks.base_task import BaseTask, TaskPriority


logger = get_logger(__name__)


CROP_EXPAND_SCALE = 1.25

# Inference-only VRAM scratch when InsightFace models are already resident.
# The cold-load cost is covered by estimate_face_extraction_vram_mb the first
# time; subsequent tasks only pay for activation memory during a forward pass.
INSIGHTFACE_INFERENCE_SCRATCH_MB = 150


class FaceExtractionTask(BaseTask):
    """Task that extracts and persists face/hand detections for a picture batch.

    Args:
        database: Vault database instance.
        picture_tagger: PictureTagger used for model settings.
        pictures: Pictures included in this extraction batch.
    """

    _global_insightface_app = None
    _global_cpu_insightface_app = None
    _cpu_insightface_lock = threading.Lock()

    def __init__(self, database, picture_tagger, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="FaceExtractionTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._picture_tagger = picture_tagger
        self._pictures = pictures or []
        self._insightface_app = None
        self._cpu_spillover_enabled = False

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": [], "picture_ids": []}

        changed = self._extract_features(self._pictures)
        picture_ids = sorted(
            {pic_id for _, pic_id, _, _ in (changed or []) if pic_id is not None}
        )

        if not self._should_keep_models_in_memory() and not self._cpu_spillover_enabled:
            # Only release the GPU app when not in CPU spillover mode; the CPU
            # app uses no GPU VRAM so there is nothing to free.
            self.release_detection_models()

        return {
            "changed_count": len(changed or []),
            "changed": changed or [],
            "picture_ids": picture_ids,
        }

    def _should_keep_models_in_memory(self) -> bool:
        return bool(getattr(self._picture_tagger, "keep_models_in_memory", True))

    def estimated_vram_mb(self) -> int:
        if FaceExtractionTask._global_insightface_app is not None:
            # InsightFace models are already resident in VRAM; only charge for
            # the inference activation scratch, not the cold model-load cost.
            return INSIGHTFACE_INFERENCE_SCRATCH_MB
        fn = getattr(self._picture_tagger, "estimate_face_extraction_vram_mb", None)
        if callable(fn):
            try:
                return max(0, int(fn()))
            except Exception:
                return 0
        return 0

    @classmethod
    def release_detection_models(cls):
        cls._global_insightface_app = None

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        cls._trim_process_memory()

    @staticmethod
    def _trim_process_memory():
        if not platform.system().lower().startswith("linux"):
            return
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6")
            trim = getattr(libc, "malloc_trim", None)
            if trim is not None:
                trim(0)
        except Exception:
            pass

    def _init_insightface_app(self):
        if self._insightface_app is not None:
            return

        if self._cpu_spillover_enabled:
            with FaceExtractionTask._cpu_insightface_lock:
                if FaceExtractionTask._global_cpu_insightface_app is None:
                    logger.debug("FaceExtractionTask: initialising CPU spillover InsightFace app (ctx_id=-1).")
                    app = FaceAnalysis(providers=["CPUExecutionProvider"])
                    app.prepare(ctx_id=-1, det_thresh=0.25, det_size=(480, 480))
                    FaceExtractionTask._global_cpu_insightface_app = app
                else:
                    logger.debug("FaceExtractionTask: reusing CPU spillover InsightFace app.")
                self._insightface_app = FaceExtractionTask._global_cpu_insightface_app
            return

        if FaceExtractionTask._global_insightface_app is not None:
            logger.debug("Reusing global InsightFace app")
            self._insightface_app = FaceExtractionTask._global_insightface_app
            return

        logger.debug("initialising InsightFace with GPU (ctx_id=0) or CPU (ctx_id=-1)")
        app = FaceAnalysis(providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        app.prepare(
            ctx_id=0 if not PictureTagger.FORCE_CPU else -1,
            det_thresh=0.25,
            det_size=(480, 480),
        )
        FaceExtractionTask._global_insightface_app = app
        self._insightface_app = app

    @staticmethod
    def _get_loaded_relationship(obj, name):
        try:
            state = sa_inspect(obj)
        except Exception:
            return False, None
        attr = state.attrs.get(name)
        if attr is None:
            return False, None
        loaded = attr.loaded_value
        if loaded is NO_VALUE:
            return False, None
        return True, loaded

    def _has_faces(self, picture_id: int) -> bool:
        def fetch(session):
            return (
                session.exec(
                    select(Face.id).where(Face.picture_id == picture_id)
                ).first()
                is not None
            )

        return bool(self._db.run_immediate_read_task(fetch))

    @staticmethod
    def _expand_bbox(bbox, frame_w, frame_h, scale):
        if bbox is None or len(bbox) != 4:
            return None
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        w = max(1.0, x2 - x1)
        h = max(1.0, y2 - y1)
        half_w = (w * scale) / 2.0
        half_h = (h * scale) / 2.0
        ex1 = int(max(0, min(frame_w - 1, round(cx - half_w))))
        ey1 = int(max(0, min(frame_h - 1, round(cy - half_h))))
        ex2 = int(max(0, min(frame_w, round(cx + half_w))))
        ey2 = int(max(0, min(frame_h, round(cy + half_h))))
        if ex2 <= ex1 or ey2 <= ey1:
            return None
        return [ex1, ey1, ex2, ey2]

    def _extract_features(self, pics) -> List[tuple]:
        profile_enabled = os.getenv("PIXLVAULT_FEATURE_TIMING", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        batch_start = time.time()
        self._init_insightface_app()

        updates = []
        precheck_s = 0.0
        image_load_s = 0.0
        inference_s = 0.0
        db_write_s = 0.0
        thumb_write_s = 0.0
        processed_images = 0
        detected_faces_total = 0

        for pic in pics:
            pic_start = time.time()
            if pic.id is None:
                logger.warning(
                    "Skipping feature extraction for %s: missing picture id",
                    getattr(pic, "file_path", "<unknown>"),
                )
                continue
            pic_face_ids = []
            check_start = time.time()
            faces_loaded, faces_value = self._get_loaded_relationship(pic, "faces")
            if faces_loaded:
                need_faces = not faces_value
            else:
                need_faces = not self._has_faces(pic.id)
            precheck_s += time.time() - check_start
            logger.debug("Looking for faces in picture %s %s", pic.id, pic.description)
            file_path = ImageUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            ext = os.path.splitext(file_path)[1].lower()
            face_objects = []
            thumbnail_bytes = None
            thumbnail_crop = None

            if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"]:
                read_start = time.time()
                img = cv2.imread(file_path)
                image_load_s += time.time() - read_start
                if img is not None:
                    if need_faces:
                        infer_start = time.time()
                        faces = self._insightface_app.get(img)
                        inference_s += time.time() - infer_start
                        detected_faces_total += len(faces)
                        logger.debug(
                            "Found %d faces in image %s", len(faces), file_path
                        )
                        face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
                        for face in faces:
                            expanded_bbox = Face.expand_face_bbox(
                                face.bbox,
                                img.shape[1],
                                img.shape[0],
                                face_expand_fraction,
                            )
                            features_bytes = None
                            if (
                                hasattr(face, "embedding")
                                and face.embedding is not None
                            ):
                                features_bytes = face.embedding.astype(
                                    "float32"
                                ).tobytes()
                            face_objects.append(
                                Face(
                                    picture_id=pic.id,
                                    face_index=-1,
                                    bbox=expanded_bbox,
                                    character_id=None,
                                    frame_index=0,
                                    features=features_bytes,
                                )
                            )
                        if face_objects:
                            bboxes = [f.bbox for f in face_objects if f.bbox]
                            (
                                thumbnail_bytes,
                                thumbnail_crop,
                            ) = FaceUtils.generate_face_weighted_thumbnail_with_crop(
                                img,
                                bboxes,
                                min_side=256,
                                output_size=(256, 256),
                            )

            elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
                if need_faces:
                    read_start = time.time()
                    cap = cv2.VideoCapture(file_path)
                    image_load_s += time.time() - read_start
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if frame_count < 1:
                        logger.warning("No frames found in video: %s", file_path)
                        cap.release()
                    else:
                        first_frame = None
                        first_bboxes = []
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            first_frame = frame
                            if need_faces:
                                infer_start = time.time()
                                frame_faces = self._insightface_app.get(frame)
                                inference_s += time.time() - infer_start
                                detected_faces_total += len(frame_faces)
                                face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
                                for face in frame_faces:
                                    expanded_bbox = Face.expand_face_bbox(
                                        face.bbox,
                                        frame.shape[1],
                                        frame.shape[0],
                                        face_expand_fraction,
                                    )
                                    features_bytes = None
                                    if (
                                        hasattr(face, "embedding")
                                        and face.embedding is not None
                                    ):
                                        features_bytes = face.embedding.astype(
                                            "float32"
                                        ).tobytes()
                                    else:
                                        logger.warning(
                                            "Face embedding missing for face in video %s, frame 0",
                                            file_path,
                                        )
                                    first_bboxes.append(expanded_bbox)
                                    face_objects.append(
                                        Face(
                                            picture_id=pic.id,
                                            face_index=-1,
                                            bbox=expanded_bbox,
                                            character_id=None,
                                            frame_index=0,
                                            features=features_bytes,
                                        )
                                    )
                        step = max(1, frame_count // 3)
                        for frame_index in range(step, frame_count, step):
                            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                            ret, frame = cap.read()
                            if not ret or frame is None:
                                logger.warning(
                                    "Could not read frame %s from video: %s",
                                    frame_index,
                                    file_path,
                                )
                                continue
                            if need_faces:
                                infer_start = time.time()
                                frame_faces = self._insightface_app.get(frame)
                                inference_s += time.time() - infer_start
                                detected_faces_total += len(frame_faces)
                                face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
                                for face in frame_faces:
                                    expanded_bbox = Face.expand_face_bbox(
                                        face.bbox,
                                        frame.shape[1],
                                        frame.shape[0],
                                        face_expand_fraction,
                                    )
                                    features_bytes = None
                                    if (
                                        hasattr(face, "embedding")
                                        and face.embedding is not None
                                    ):
                                        features_bytes = face.embedding.astype(
                                            "float32"
                                        ).tobytes()
                                    else:
                                        logger.warning(
                                            "Face embedding missing for face in video %s, frame %s",
                                            file_path,
                                            frame_index,
                                        )
                                    face_objects.append(
                                        Face(
                                            picture_id=pic.id,
                                            face_index=-1,
                                            bbox=expanded_bbox,
                                            character_id=None,
                                            frame_index=frame_index,
                                            features=features_bytes,
                                        )
                                    )
                    cap.release()
                    if need_faces and first_frame is not None and first_bboxes:
                        (
                            thumbnail_bytes,
                            thumbnail_crop,
                        ) = FaceUtils.generate_face_weighted_thumbnail_with_crop(
                            first_frame,
                            first_bboxes,
                            min_side=256,
                            output_size=(256, 256),
                        )
            else:
                logger.warning(
                    "Unsupported file extension for feature extraction: %s",
                    file_path,
                )

            face_objects.sort(
                key=lambda f: (f.bbox[1], f.bbox[0], f.bbox[3], f.bbox[2])
            )
            for idx, face in enumerate(face_objects):
                face.face_index = idx

            if need_faces and not face_objects:
                logger.warning(
                    "No face found in %s for picture %s. Inserting sentinel record.",
                    file_path,
                    pic.id,
                )

                def insert_sentinel(session):
                    face = Face(
                        picture_id=pic.id,
                        face_index=-1,
                        character_id=None,
                        bbox=None,
                    )
                    session.add(face)
                    session.commit()
                    session.refresh(face)
                    return face.id

                db_start = time.time()
                face_id = self._db.run_task(insert_sentinel, priority=DBPriority.HIGH)
                db_write_s += time.time() - db_start
                pic_face_ids.append(face_id)
            elif need_faces:

                def insert_faces(session, faces_to_insert):
                    face_ids = []
                    for face in faces_to_insert:
                        session.add(face)
                    session.commit()
                    for face in faces_to_insert:
                        session.refresh(face)
                        face_ids.append(face.id)
                    return face_ids

                db_start = time.time()
                face_ids = self._db.run_task(
                    insert_faces, face_objects, priority=DBPriority.HIGH
                )
                db_write_s += time.time() - db_start
                pic_face_ids.extend(face_ids)

            if need_faces and thumbnail_bytes:
                thumb_start = time.time()
                saved_thumb = ImageUtils.write_thumbnail_bytes(
                    self._db.image_root,
                    pic.file_path,
                    thumbnail_bytes,
                )
                thumb_write_s += time.time() - thumb_start
                if not saved_thumb:
                    logger.warning(
                        "Failed to persist thumbnail for picture %s",
                        getattr(pic, "file_path", pic.id),
                    )

                def update_thumbnail_crop(session, picture_id, crop):
                    picture = session.get(Picture, picture_id)
                    if picture is None:
                        return None
                    if crop:
                        picture.thumbnail_left = crop.get("left")
                        picture.thumbnail_top = crop.get("top")
                        picture.thumbnail_side = crop.get("side")
                    session.add(picture)
                    session.commit()
                    return picture.id

                if thumbnail_crop:
                    db_start = time.time()
                    self._db.run_task(
                        update_thumbnail_crop,
                        pic.id,
                        thumbnail_crop,
                        priority=DBPriority.HIGH,
                    )
                    db_write_s += time.time() - db_start

            if need_faces:
                updates.append((Picture, pic.id, "faces", pic_face_ids))

            processed_images += 1
            if profile_enabled and (time.time() - pic_start) > 0.75:
                logger.info(
                    "[FEATURE_TIMING] Slow image id=%s path=%s elapsed=%.3fs need_faces=%s faces=%s",
                    pic.id,
                    pic.file_path,
                    time.time() - pic_start,
                    need_faces,
                    len(pic_face_ids),
                )

        if profile_enabled:
            elapsed = time.time() - batch_start
            logger.info(
                "[FEATURE_TIMING] batch=%s processed=%s updates=%s faces=%s elapsed=%.3fs precheck=%.3fs load=%.3fs infer=%.3fs db=%.3fs thumb=%.3fs",
                len(pics),
                processed_images,
                len(updates),
                detected_faces_total,
                elapsed,
                precheck_s,
                image_load_s,
                inference_s,
                db_write_s,
                thumb_write_s,
            )

        return updates

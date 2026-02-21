from typing import List
import cv2
import os
import urllib.request
from insightface.app import FaceAnalysis

from sqlmodel import select
from sqlalchemy import func, inspect as sa_inspect
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import NO_VALUE
from pixlvault.database import DBPriority
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.face import Face
from pixlvault.db_models.hand import Hand
from pixlvault.db_models.picture import Picture
from pixlvault.picture_tagger import PictureTagger, MODEL_DIR
from pixlvault.picture_utils import PictureUtils

logger = get_logger(__name__)


HAND_MODEL_NAME = "yolov8n-hand.pt"
HAND_MODEL_URL = "https://huggingface.co/Bingsu/adetailer/resolve/main/hand_yolov8n.pt"
HAND_MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", MODEL_DIR, HAND_MODEL_NAME
)

HAND_DETECT_IMGSZ = 256
HAND_DETECT_CONF = 0.3
HAND_DETECT_MAX_DET = 4
HAND_DETECT_VIDEO_FRAMES = 1
CROP_EXPAND_SCALE = 1.5


class FeatureExtractionWorker(BaseWorker):
    INSIGHTFACE_CLEANUP_TIMEOUT = 6000  # seconds
    _global_insightface_app = None
    _hand_model = None

    def worker_type(self) -> WorkerType:
        return WorkerType.FACE

    def __init__(self, database, picture_tagger, event_callback):
        super().__init__(database, picture_tagger, event_callback)
        self._init_insightface_app()

    def _run(self):
        logger.info("FeatureExtractionWorker: Worker thread started and running.")

        while not self._stop.is_set():
            try:
                logger.debug("FeatureExtractionWorker: Starting iteration...")
                pics_needing_face_bboxes = self._find_pics_needing_feature_extraction()
                total_pics = self._db.run_immediate_read_task(
                    self._count_total_pictures
                )
                pending = len(pics_needing_face_bboxes)
                total = max(int(total_pics or 0), 0)
                current = max(total - pending, 0)
                self._set_progress(
                    label="features_extracted",
                    current=current,
                    total=total,
                )
                logger.debug(
                    "FeatureExtractionWorker: Found %d pictures needing face bboxes: %s",
                    len(pics_needing_face_bboxes),
                    [
                        getattr(pic, "file_path", pic.id)
                        for pic in pics_needing_face_bboxes
                    ],
                )
                if not pics_needing_face_bboxes:
                    self._wait()
                    continue
                updates = self._extract_features(pics_needing_face_bboxes)
                if updates:
                    self._notify_ids_processed(updates)
                    picture_ids = sorted(
                        {
                            pic_id
                            for _, pic_id, _, payload in updates
                            if pic_id is not None
                        }
                    )
                    self._notify_others(EventType.CHANGED_FACES, picture_ids)
                    logger.debug(
                        "FeatureExtractionWorker: Done with iteration having processed %d pictures.",
                        len(updates),
                    )
                else:
                    logger.debug(
                        "FeatureExtractionWorker: Done with iteration but no pictures were processed."
                    )
                    self._wait()
            except Exception as e:
                import traceback

                logger.error(
                    "Worker thread exiting due to error: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                break
        logger.info("FeatureExtractionWorker: Worker thread exiting.")

    def _find_pics_needing_feature_extraction(self):
        return self._db.run_task(
            lambda session: session.exec(
                select(Picture)
                .where((~Picture.faces.any()) | (~Picture.hands.any()))
                .options(selectinload(Picture.faces), selectinload(Picture.hands))
            ).all()
        )

    @staticmethod
    def _count_total_pictures(session):
        result = session.exec(select(func.count()).select_from(Picture)).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    def _ensure_hand_detector(self):
        if FeatureExtractionWorker._hand_model is not None:
            return FeatureExtractionWorker._hand_model
        try:
            from ultralytics import YOLO
        except Exception as exc:
            logger.warning("Ultralytics not available for hand detection: %s", exc)
            return None

        model_path = HAND_MODEL_PATH
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        if not os.path.exists(model_path):
            try:
                logger.info("Downloading hand model to %s", model_path)
                urllib.request.urlretrieve(HAND_MODEL_URL, model_path)
            except Exception as exc:
                logger.warning("Failed to download hand model: %s", exc)
                return None
        try:
            FeatureExtractionWorker._hand_model = YOLO(model_path)
        except Exception as exc:
            logger.warning("Failed to load hand model: %s", exc)
            FeatureExtractionWorker._hand_model = None
        return FeatureExtractionWorker._hand_model

    def _init_insightface_app(self):
        if not hasattr(self, "_insightface_app"):
            if FeatureExtractionWorker._global_insightface_app is not None:
                logger.debug("Reusing global InsightFace app")
                self._insightface_app = FeatureExtractionWorker._global_insightface_app
                return

            logger.debug("initialising InsightFace with CPU only (ctx_id=-1)")
            app = FaceAnalysis(
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
            app.prepare(
                ctx_id=0 if not PictureTagger.FORCE_CPU else -1,
                det_thresh=0.25,
                det_size=(480, 480),
            )
            FeatureExtractionWorker._global_insightface_app = app
            self._insightface_app = app

    def _predict_hands(self, hand_model, image, file_path):
        try:
            results = hand_model.predict(
                image,
                imgsz=HAND_DETECT_IMGSZ,
                conf=HAND_DETECT_CONF,
                max_det=HAND_DETECT_MAX_DET,
                verbose=False,
            )
            return list(results[0].boxes.xyxy.cpu().numpy())
        except Exception as exc:
            logger.warning("Hand detection failed for %s: %s", file_path, exc)
            return []

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

    def _has_hands(self, picture_id: int) -> bool:
        def fetch(session):
            return (
                session.exec(
                    select(Hand.id).where(Hand.picture_id == picture_id)
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

    def close(self):
        """
        Clean up resources held by the worker.
        """
        if hasattr(self, "_insightface_app"):
            # With singleton pattern, we just unlink the instance ref.
            # We do NOT delete the global app so it can be reused.
            self._insightface_app = None

        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            # torch is an optional dependency; skip GPU cache cleanup if unavailable.
            pass

    def _extract_features(self, pics) -> List[tuple]:
        self._init_insightface_app()

        updates = []
        hand_model = self._ensure_hand_detector()

        for pic in pics:
            if pic.id is None:
                logger.warning(
                    "Skipping feature extraction for %s: missing picture id",
                    getattr(pic, "file_path", "<unknown>"),
                )
                continue
            pic_face_ids = []
            pic_hand_ids = []
            faces_loaded, faces_value = self._get_loaded_relationship(pic, "faces")
            hands_loaded, hands_value = self._get_loaded_relationship(pic, "hands")
            if faces_loaded:
                need_faces = not faces_value
            else:
                need_faces = not self._has_faces(pic.id)
            if hands_loaded:
                need_hands = not hands_value
            else:
                need_hands = not self._has_hands(pic.id)
            logger.debug("Looking for faces in picture %s %s", pic.id, pic.description)
            file_path = PictureUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            ext = os.path.splitext(file_path)[1].lower()
            face_objects = []
            hand_objects = []
            thumbnail_bytes = None
            thumbnail_crop = None
            if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"]:
                img = cv2.imread(file_path)
                if img is not None:
                    if need_faces:
                        faces = self._insightface_app.get(img)
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
                                    face_index=-1,  # will set after sorting
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
                            ) = PictureUtils.generate_face_weighted_thumbnail_with_crop(
                                img,
                                bboxes,
                                min_side=256,
                                output_size=(256, 256),
                            )

                    if need_hands and hand_model is not None:
                        boxes = self._predict_hands(hand_model, img, file_path)
                        for box in boxes:
                            expanded = self._expand_bbox(
                                box,
                                img.shape[1],
                                img.shape[0],
                                CROP_EXPAND_SCALE,
                            )
                            if expanded is None:
                                continue
                            hand_objects.append(
                                Hand(
                                    picture_id=pic.id,
                                    hand_index=-1,
                                    bbox=expanded,
                                    frame_index=0,
                                )
                            )
            elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
                if need_faces or need_hands:
                    cap = cv2.VideoCapture(file_path)
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    hand_frames_used = 0
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
                                frame_faces = self._insightface_app.get(frame)
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
                                            face_index=-1,  # will set after sorting
                                            bbox=expanded_bbox,
                                            character_id=None,
                                            frame_index=0,
                                            features=features_bytes,
                                        )
                                    )
                            if need_hands and hand_model is not None:
                                if hand_frames_used < HAND_DETECT_VIDEO_FRAMES:
                                    boxes = self._predict_hands(
                                        hand_model, frame, file_path
                                    )
                                    hand_frames_used += 1
                                    for box in boxes:
                                        expanded = self._expand_bbox(
                                            box,
                                            frame.shape[1],
                                            frame.shape[0],
                                            CROP_EXPAND_SCALE,
                                        )
                                        if expanded is None:
                                            continue
                                        hand_objects.append(
                                            Hand(
                                                picture_id=pic.id,
                                                hand_index=-1,
                                                bbox=expanded,
                                                frame_index=0,
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
                                frame_faces = self._insightface_app.get(frame)
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
                                            face_index=-1,  # will set after sorting
                                            bbox=expanded_bbox,
                                            character_id=None,
                                            frame_index=frame_index,
                                            features=features_bytes,
                                        )
                                    )
                            if need_hands and hand_model is not None:
                                if hand_frames_used < HAND_DETECT_VIDEO_FRAMES:
                                    boxes = self._predict_hands(
                                        hand_model, frame, file_path
                                    )
                                    hand_frames_used += 1
                                    for box in boxes:
                                        expanded = self._expand_bbox(
                                            box,
                                            frame.shape[1],
                                            frame.shape[0],
                                            CROP_EXPAND_SCALE,
                                        )
                                        if expanded is None:
                                            continue
                                        hand_objects.append(
                                            Hand(
                                                picture_id=pic.id,
                                                hand_index=-1,
                                                bbox=expanded,
                                                frame_index=frame_index,
                                            )
                                        )
                    cap.release()
                    if need_faces and first_frame is not None and first_bboxes:
                        (
                            thumbnail_bytes,
                            thumbnail_crop,
                        ) = PictureUtils.generate_face_weighted_thumbnail_with_crop(
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

            # Sort faces by bbox: (y0, x0, y1, x1)
            face_objects.sort(
                key=lambda f: (f.bbox[1], f.bbox[0], f.bbox[3], f.bbox[2])
            )
            # Assign face_index after sorting
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

                face_id = self._db.run_task(insert_sentinel, priority=DBPriority.HIGH)
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

                face_ids = self._db.run_task(
                    insert_faces, face_objects, priority=DBPriority.HIGH
                )
                pic_face_ids.extend(face_ids)

            if need_faces and thumbnail_bytes:
                saved_thumb = PictureUtils.write_thumbnail_bytes(
                    self._db.image_root, pic.file_path, thumbnail_bytes
                )
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
                    self._db.run_task(
                        update_thumbnail_crop,
                        pic.id,
                        thumbnail_crop,
                        priority=DBPriority.HIGH,
                    )

            if need_faces:
                updates.append((Picture, pic.id, "faces", pic_face_ids))

            if need_hands:
                for hand in hand_objects:
                    if hand.picture_id is None:
                        hand.picture_id = pic.id

                hand_objects.sort(
                    key=lambda h: (h.bbox[1], h.bbox[0], h.bbox[3], h.bbox[2])
                )
                for idx, hand in enumerate(hand_objects):
                    hand.hand_index = idx

                if not hand_objects:
                    logger.debug(
                        "No hands found in %s for picture %s. Inserting sentinel record.",
                        file_path,
                        pic.id,
                    )

                    def insert_hand_sentinel(session):
                        hand = Hand(
                            picture_id=pic.id,
                            hand_index=-1,
                            frame_index=0,
                            bbox=None,
                        )
                        session.add(hand)
                        session.commit()
                        session.refresh(hand)
                        return hand.id

                    hand_id = self._db.run_task(
                        insert_hand_sentinel, priority=DBPriority.HIGH
                    )
                    pic_hand_ids.append(hand_id)
                else:

                    def insert_hands(session, hands_to_insert, picture_id):
                        hand_ids = []
                        for hand in hands_to_insert:
                            if hand.picture_id is None:
                                hand.picture_id = picture_id
                            if hand.picture_id is None:
                                logger.warning(
                                    "Skipping hand insert for %s: missing picture_id",
                                    file_path,
                                )
                                continue
                            session.add(hand)
                        session.commit()
                        for hand in hands_to_insert:
                            if hand.picture_id is None:
                                continue
                            session.refresh(hand)
                            hand_ids.append(hand.id)
                        return hand_ids

                    hand_ids = self._db.run_task(
                        insert_hands, hand_objects, pic.id, priority=DBPriority.HIGH
                    )
                    pic_hand_ids.extend(hand_ids)

                updates.append((Picture, pic.id, "hands", pic_hand_ids))

        return updates

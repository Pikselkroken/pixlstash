from typing import List
import cv2
import os
from insightface.app import FaceAnalysis

from sqlmodel import select
from pixlvault.database import DBPriority
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.face import Face
from pixlvault.db_models.picture import Picture
from pixlvault.picture_tagger import PictureTagger
from pixlvault.picture_utils import PictureUtils

logger = get_logger(__name__)


class FaceExtractionWorker(BaseWorker):
    INSIGHTFACE_CLEANUP_TIMEOUT = 6000  # seconds
    _global_insightface_app = None

    def worker_type(self) -> WorkerType:
        return WorkerType.FACE

    def __init__(self, database, picture_tagger, event_callback):
        super().__init__(database, picture_tagger, event_callback)
        self._init_insightface_app()

    def _run(self):
        logger.info("FaceExtractionWorker: Worker thread started and running.")

        while not self._stop.is_set():
            try:
                logger.debug("FaceExtractionWorker: Starting iteration...")
                pics_needing_face_bboxes = self._find_pics_needing_face_extraction()
                logger.debug(
                    f"FaceExtractionWorker: Found {len(pics_needing_face_bboxes)} pictures needing face bboxes: {[getattr(pic, 'file_path', pic.id) for pic in pics_needing_face_bboxes]}"
                )
                if not pics_needing_face_bboxes:
                    self._wait()
                    continue
                updates = self._extract_faces(pics_needing_face_bboxes)
                if updates:
                    self._notify_ids_processed(updates)
                    self._notify_others(EventType.CHANGED_FACES)
                    logger.debug(
                        "FaceExtractionWorker: Done with iteration having processed %d pictures.",
                        len(updates),
                    )
                else:
                    logger.debug(
                        "FaceExtractionWorker: Done with iteration but no pictures were processed."
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
        logger.info("FaceExtractionWorker: Worker thread exiting.")

    def _find_pics_needing_face_extraction(self):
        return self._db.run_task(
            lambda session: session.exec(
                select(Picture).where(~Picture.faces.any())
            ).all()
        )

    def _init_insightface_app(self):
        if not hasattr(self, "_insightface_app"):
            if FaceExtractionWorker._global_insightface_app is not None:
                logger.debug("Reusing global InsightFace app")
                self._insightface_app = FaceExtractionWorker._global_insightface_app
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
            FaceExtractionWorker._global_insightface_app = app
            self._insightface_app = app

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

    def _extract_faces(self, pics) -> List[tuple]:
        self._init_insightface_app()

        updates = []
        all_face_ids = []

        for pic in pics:
            pic_face_ids = []
            logger.debug("Looking for faces in picture %s %s", pic.id, pic.description)
            file_path = PictureUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            ext = os.path.splitext(file_path)[1].lower()
            face_objects = []
            thumbnail_bytes = None
            thumbnail_crop = None
            if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"]:
                img = cv2.imread(file_path)
                if img is not None:
                    faces = self._insightface_app.get(img)
                    logger.debug("Found %d faces in image %s", len(faces), file_path)
                    for face in faces:
                        expanded_bbox = Face.expand_face_bbox(
                            face.bbox, img.shape[1], img.shape[0], 0.1
                        )
                        features_bytes = None
                        if hasattr(face, "embedding") and face.embedding is not None:
                            features_bytes = face.embedding.astype("float32").tobytes()
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
            elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
                cap = cv2.VideoCapture(file_path)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if frame_count < 1:
                    logger.warning(f"No frames found in video: {file_path}")
                    cap.release()
                else:
                    first_frame = None
                    first_bboxes = []
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        first_frame = frame
                        frame_faces = self._insightface_app.get(frame)
                        for face in frame_faces:
                            expanded_bbox = Face.expand_face_bbox(
                                face.bbox, frame.shape[1], frame.shape[0], 0.1
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
                                    f"Face embedding missing for face in video {file_path}, frame 0"
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
                    step = max(1, frame_count // 3)
                    for frame_index in range(step, frame_count, step):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                        ret, frame = cap.read()
                        if not ret or frame is None:
                            logger.warning(
                                f"Could not read frame {frame_index} from video: {file_path}"
                            )
                            continue
                        frame_faces = self._insightface_app.get(frame)
                        frame_bboxes = []
                        for face in frame_faces:
                            expanded_bbox = Face.expand_face_bbox(
                                face.bbox, frame.shape[1], frame.shape[0], 0.1
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
                                    f"Face embedding missing for face in video {file_path}, frame {frame_index}"
                                )
                            frame_bboxes.append(expanded_bbox)
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
                cap.release()
                if first_frame is not None and first_bboxes:
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
                    f"Unsupported file extension for face extraction: {file_path}"
                )

            # Sort faces by bbox: (y0, x0, y1, x1)
            face_objects.sort(
                key=lambda f: (f.bbox[1], f.bbox[0], f.bbox[3], f.bbox[2])
            )
            # Assign face_index after sorting
            for idx, face in enumerate(face_objects):
                face.face_index = idx

            if not face_objects:
                logger.warning(
                    f"No face found in {file_path} for picture {pic.id}. Inserting sentinel record."
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
            else:

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

            if thumbnail_bytes:

                def update_thumbnail(session, picture_id, thumbnail, crop):
                    picture = session.get(Picture, picture_id)
                    if picture is None:
                        return None
                    picture.thumbnail = thumbnail
                    if crop:
                        picture.thumbnail_left = crop.get("left")
                        picture.thumbnail_top = crop.get("top")
                        picture.thumbnail_side = crop.get("side")
                    session.add(picture)
                    session.commit()
                    return picture.id

                self._db.run_task(
                    update_thumbnail,
                    pic.id,
                    thumbnail_bytes,
                    thumbnail_crop,
                    priority=DBPriority.HIGH,
                )

            all_face_ids.extend(pic_face_ids)
            updates.append((Picture, pic.id, "faces", pic_face_ids))

        return updates

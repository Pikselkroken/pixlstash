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

logger = get_logger(__name__)


class FaceExtractionWorker(BaseWorker):
    INSIGHTFACE_CLEANUP_TIMEOUT = 6000  # seconds

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
            logger.debug("initialising InsightFace with CPU only (ctx_id=-1)")
            self._insightface_app = FaceAnalysis(
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
            self._insightface_app.prepare(
                ctx_id=0 if not PictureTagger.FORCE_CPU else -1,
                det_thresh=0.25,
                det_size=(480, 480),
            )

    def _extract_faces(self, pics) -> List[tuple]:
        self._init_insightface_app()

        updates = []
        all_face_ids = []

        for pic in pics:
            pic_face_ids = []
            logger.debug("Looking for faces in picture %s %s", pic.id, pic.description)
            file_path = pic.file_path
            ext = os.path.splitext(file_path)[1].lower()
            face_objects = []
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
            elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
                cap = cv2.VideoCapture(file_path)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if frame_count < 1:
                    logger.warning(f"No frames found in video: {file_path}")
                    cap.release()
                else:
                    step = max(1, frame_count // 3)
                    for frame_index in range(0, frame_count, step):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                        ret, frame = cap.read()
                        if not ret or frame is None:
                            logger.warning(
                                f"Could not read frame {frame_index} from video: {file_path}"
                            )
                            continue
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
                                    f"Face embedding missing for face in video {file_path}, frame {frame_index}"
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
                cap.release()
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

                face_id = self._db.run_task(insert_sentinel, priority=DBPriority.LOW)
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
                    insert_faces, face_objects, priority=DBPriority.LOW
                )
                pic_face_ids.extend(face_ids)

            all_face_ids.extend(pic_face_ids)
            updates.append((Picture, pic.id, "faces", pic_face_ids))

        return updates

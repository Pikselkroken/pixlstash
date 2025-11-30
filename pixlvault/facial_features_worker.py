import gc
import cv2
import os
import time

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlvault.database import DBPriority
from pixlvault.picture_tagger import PictureTagger
from pixlvault.logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType

from pixlvault.db_models.face import Face
from pixlvault.db_models.picture import Picture


logger = get_logger(__name__)


class FacialFeaturesWorker(BaseWorker):
    INSIGHTFACE_CLEANUP_TIMEOUT = 20  # seconds

    def __init__(self, db_connection, picture_tagger: PictureTagger):
        super().__init__(db_connection, picture_tagger)
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None

    def worker_type(self) -> WorkerType:
        return WorkerType.FACIAL_FEATURES

    def _run(self):
        logger.info("FacialFeaturesWorker: Worker thread started and running.")
        time.sleep(1.25)  # Stagger start times for multiple workers
        while not self._stop.is_set():
            try:
                data_updated = False

                start = time.time()
                # 1. Calculate face bboxes
                logger.debug("FacialFeaturesWorker: Starting iteration...")

                pics_needing_face_bboxes = self._find_pics_needing_face_extraction()
                logger.debug(
                    f"FacialFeaturesWorker: Found {len(pics_needing_face_bboxes)} pictures needing face bboxes: {[getattr(pic, 'file_path', pic.id) for pic in pics_needing_face_bboxes]}"
                )
                time_after_fetch = time.time()

                logger.debug(
                    "FacialFeaturesWorker: It took %.2f seconds to fetch %d pictures needing face bboxes."
                    % (time_after_fetch - start, len(pics_needing_face_bboxes))
                )
                logger.debug(
                    f"Found {len(pics_needing_face_bboxes)} pictures needing face bboxes. Doing {self._picture_tagger.max_concurrent_images()} at a time."
                )
                insightface_ok, bboxes_updated = self._extract_faces(
                    pics_needing_face_bboxes[
                        : self._picture_tagger.max_concurrent_images()
                    ]
                )
                time_after_calculate = time.time()
                logger.debug(
                    "FacialFeaturesWorker: It took %.2f seconds to calculate face bboxes."
                    % (time_after_calculate - time_after_fetch)
                )
                if not insightface_ok:
                    logger.warning(
                        "InsightFace model not available, skipping facial feature generation."
                    )
                    break

                data_updated |= bboxes_updated > 0

                if self._stop.is_set():
                    break

                # 2. Generate facial features for pictures missing them
                faces_missing_features = self._fetch_faces_missing_features()
                pictures_missing_facial_features = (
                    self._fetch_pics_missing_facial_features(faces_missing_features)
                )

                logger.debug(
                    "FacialFeaturesWorker: It took %.2f seconds to fetch %d pictures needing facial features."
                    % (
                        time.time() - time_after_calculate,
                        len(pictures_missing_facial_features),
                    )
                )
                if pictures_missing_facial_features:
                    logger.debug(
                        f"Generating facial features for {len(pictures_missing_facial_features)} pictures."
                    )
                    features_updated = self._generate_facial_features(
                        pictures_missing_facial_features
                    )
                    logger.debug(
                        f"Updated facial features for {len(pictures_missing_facial_features)} pictures."
                    )
                    data_updated |= features_updated > 0

                timing = time.time() - start

                if timing > 0.5:
                    logger.debug(
                        "FacialFeaturesWorker: Done after %.2f seconds." % timing
                    )
                if not data_updated:
                    if (
                        not pics_needing_face_bboxes
                        and not pictures_missing_facial_features
                    ):
                        logger.debug(
                            "FacialFeaturesWorker: Sleeping after %.2f seconds. No work needed."
                            % timing
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
        logger.info("FacialFeaturesWorker: Worker thread exiting.")

    def _find_pics_needing_face_extraction(self):
        """
        Find pictures that do not have any face records in picture_faces (including sentinel records).
        Only select pictures that have no entry at all in picture_faces.
        """
        return self._db.run_task(
            lambda session: session.exec(
                select(Picture).where(~Picture.faces.any())
            ).all()
        )

    def _extract_faces(self, pics) -> int:
        """Extract faces with bounding boxes for all faces in each picture or video"""

        bboxes_updated = 0
        if not pics:
            if self._last_time_insightface_was_needed is not None:
                elapsed = time.time() - self._last_time_insightface_was_needed
                if elapsed > FacialFeaturesWorker.INSIGHTFACE_CLEANUP_TIMEOUT:
                    if hasattr(self, "_insightface_app"):
                        del self._insightface_app
                        gc.collect()
                        logger.warning("Unloaded InsightFace app due to inactivity.")
                    self._last_time_insightface_was_needed = None
            return True, bboxes_updated

        logger.debug(f"Have {len(pics)} pictures needing facial features.")
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            logger.error(
                "InsightFace is not installed. Skipping facial features extraction."
            )
            return False, bboxes_updated

        if not hasattr(self, "_insightface_app"):
            logger.debug("initialising InsightFace with CPU only (ctx_id=-1)")
            self._insightface_app = FaceAnalysis()
            self._insightface_app.prepare(ctx_id=-1, det_thresh=0.25)

        self._last_time_insightface_was_needed = time.time()

        for pic in pics:
            logger.debug("Looking for faces in picture %s %s", pic.id, pic.description)
            self._skip_pictures.add(pic.id)

            if self._stop.is_set():
                logger.debug("Stopping facial features extraction as requested.")
                return False, bboxes_updated

            file_path = pic.file_path
            ext = os.path.splitext(file_path)[1].lower()
            face_objects = []
            if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"]:
                img = cv2.imread(file_path)
                if img is not None:
                    faces = self._insightface_app.get(img)
                    logger.debug("Found %d faces in image %s", len(faces), file_path)
                    for i, face in enumerate(faces):
                        expanded_bbox = Face.expand_face_bbox(
                            face.bbox, img.shape[1], img.shape[0], 0.1
                        )
                        face_objects.append(
                            Face(
                                picture_id=pic.id,
                                face_index=i,
                                bbox=expanded_bbox,
                                character_id=None,
                                frame_index=0,
                            )
                        )

            elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
                cap = cv2.VideoCapture(file_path)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if frame_count < 1:
                    logger.warning(f"No frames found in video: {file_path}")
                    cap.release()
                else:
                    step = max(1, frame_count // 10)
                    for frame_index in range(0, frame_count, step):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                        ret, frame = cap.read()
                        if not ret or frame is None:
                            logger.warning(
                                f"Could not read frame {frame_index} from video: {file_path}"
                            )
                            continue
                        frame_faces = self._insightface_app.get(frame)
                        for i, face in enumerate(frame_faces):
                            expanded_bbox = Face.expand_face_bbox(
                                face.bbox, frame.shape[1], frame.shape[0], 0.1
                            )
                            face_objects.append(
                                Face(
                                    picture_id=pic.id,
                                    face_index=i,
                                    bbox=expanded_bbox,
                                    character_id=None,
                                    frame_index=frame_index,
                                )
                            )
                cap.release()

            else:
                logger.warning(
                    f"Unsupported file extension for facial features: {file_path}"
                )

            assert isinstance(face_objects, list), (
                f"face_objects is not a list: {type(face_objects)}"
            )
            if not face_objects:
                logger.warning(
                    f"No face found in {file_path} for picture {pic.id}. Inserting sentinel record."
                )

                # Insert sentinel record to indicate no faces found
                def insert_sentinel(session):
                    session.add(
                        Face(
                            picture_id=pic.id,
                            face_index=-1,
                            character_id=None,
                            bbox=None,
                        )
                    )
                    session.commit()

                self._db.run_task(insert_sentinel, priority=DBPriority.LOW)
            else:
                # Assign primary_character_id to largest face if set
                if (
                    getattr(pic, "primary_character_id", None) is not None
                    and len(face_objects) > 0
                ):

                    def face_area(face):
                        bbox = face.bbox
                        if bbox and len(bbox) == 4:
                            return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                        return 0

                    largest_face = max(face_objects, key=face_area)
                    largest_face.character_id = pic.primary_character_id

                def insert_faces(session, faces_to_insert):
                    changed = []
                    for face in faces_to_insert:
                        session.add(face)
                        changed.append((Face, face.id))
                    session.commit()
                    return changed

                self._db.run_task(insert_faces, face_objects, priority=DBPriority.LOW)
            # Processed the picture even if no faces found
            self._notify_ids_processed([(Picture, pic.id, "faces")])

        logger.debug(f"Stored {bboxes_updated} updated face bboxes")
        return True, bboxes_updated

    def _fetch_faces_missing_features(self):
        """
        Return a list of faces needing features (features IS NULL and index != -1)
        """

        def find_faces(session: Session):
            column = getattr(Face, "features")

            return session.exec(
                select(Face)
                .where((Face.face_index >= 0) & (column.is_(None)))
                .options(selectinload(Face.picture))
            ).all()

        return self._db.run_task(find_faces, priority=DBPriority.LOW)

    def _fetch_pics_missing_facial_features(
        self, faces_missing_features: list[Face]
    ) -> dict[Picture, list[Face]]:
        """
        Return a dict of pictures and their corresponding faces needing facial features (features IS NULL and index != -1)
        """
        pictures_dict = {}
        for face in faces_missing_features:
            pic = face.picture
            pictures_dict.setdefault(pic.id, (pic, []))[1].append(face)
        return pictures_dict

    def _generate_facial_features(
        self, pics_missing_facial_features: dict[Picture, list[Face]]
    ) -> int:
        """
        Generate facial features for a batch of face records using PictureTagger.
        Each item in missing_facial_features is a dict with keys: picture_id, face_index, bbox, file_path, etc.
        Groups by picture, calls tagger once per picture, and updates each face.
        Returns the number of faces updated.
        """

        updates = 0

        for picture_id, (picture, faces) in pics_missing_facial_features.items():
            assert picture_id == picture.id, "Picture ID mismatch"
            if self._stop.is_set():
                logger.debug("Stopping facial features generation as requested.")
                return updates
            logger.debug(
                f"Generating facial features for picture {picture_id} with {len(faces)} faces."
            )
            # Collect bboxes for all faces in this picture
            bboxes = [f.bbox for f in faces]
            # Convert bbox from string if needed
            import ast

            bboxes = [ast.literal_eval(b) if isinstance(b, str) else b for b in bboxes]
            frame_indices = [f.frame_index for f in faces]
            try:
                features_list = self._picture_tagger.generate_facial_features(
                    picture, bboxes
                )
                for face, features, frame_index in zip(
                    faces, features_list, frame_indices
                ):
                    if features is not None:
                        logger.debug(
                            f"Got facial features for picture {face.picture_id} face_index {face.face_index} frame_index {frame_index}"
                        )
                        # Convert numpy array to bytes for DB storage
                        features_bytes = (
                            features.tobytes()
                            if hasattr(features, "tobytes")
                            else features
                        )
                        face.features = features_bytes
                    else:
                        logger.warning(
                            f"No facial features for picture {face.picture_id} face_index {face.face_index} frame_index {frame_index}"
                        )

                def update_faces(session, faces_to_update):
                    changed = []
                    for face in faces_to_update:
                        session.add(face)
                        changed.append((Face, face.id, "features"))
                    session.commit()
                    return changed

                faces_updated = self._db.run_task(
                    update_faces, faces, priority=DBPriority.LOW
                )
                self._notify_ids_processed(faces_updated)
                updates += len(faces_updated)
            except Exception as e:
                logger.error(
                    f"Failed to generate facial features for picture {face.picture_id}: {e}"
                )
        logger.debug("Generated facial features for %d faces", updates)
        return updates

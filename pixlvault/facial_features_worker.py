import time

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlvault.database import DBPriority
from pixlvault.picture_tagger import PictureTagger
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType

from pixlvault.db_models.face import Face
from pixlvault.db_models.picture import Picture


logger = get_logger(__name__)


class FacialFeaturesWorker(BaseWorker):
    INSIGHTFACE_CLEANUP_TIMEOUT = 20  # seconds

    def __init__(
        self, db_connection, picture_tagger: PictureTagger, event_callback: callable
    ):
        super().__init__(db_connection, picture_tagger, event_callback=event_callback)
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None

    def worker_type(self) -> WorkerType:
        return WorkerType.FACIAL_FEATURES

    def _run(self):
        logger.info("FacialFeaturesWorker: Worker thread started and running.")
        time.sleep(1.25)  # Stagger start times for multiple workers
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("FacialFeaturesWorker: Starting iteration...")
                # Only process faces that already exist and need features
                faces_missing_features = self._fetch_faces_missing_features()
                pictures_missing_facial_features = (
                    self._fetch_pics_missing_facial_features(faces_missing_features)
                )
                logger.debug(
                    "FacialFeaturesWorker: It took %.2f seconds to fetch %d pictures needing facial features."
                    % (
                        time.time() - start,
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
                        f"Updated facial features for {features_updated} pictures."
                    )
                else:
                    logger.debug(
                        "FacialFeaturesWorker: No pictures need facial features. Sleeping."
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

        import ast

        updates = 0

        for picture_id, (picture, faces) in pics_missing_facial_features.items():
            assert picture_id == picture.id, "Picture ID mismatch"
            if self._stop.is_set():
                logger.debug("Stopping facial features generation as requested.")
                return updates
            logger.debug(
                f"Generating facial features for picture {picture.description} with {len(faces)} faces."
            )
            # Collect bboxes for all faces in this picture
            bboxes = [f.bbox for f in faces]
            bboxes = [ast.literal_eval(b) if isinstance(b, str) else b for b in bboxes]
            frame_indices = [f.frame_index for f in faces]

            features_list = self._picture_tagger.generate_facial_features(
                picture, bboxes
            )
            if len(features_list) != len(faces):
                logger.error(
                    f"Number of features returned ({len(features_list)}) does not match number of faces ({len(faces)}) for picture {picture.description}."
                )
                continue

            for idx, (face, features, frame_index, bbox) in enumerate(
                zip(faces, features_list, frame_indices, bboxes)
            ):
                if features is not None:
                    # Convert numpy array to bytes for DB storage
                    features_bytes = (
                        features.tobytes() if hasattr(features, "tobytes") else features
                    )
                    face.features = features_bytes
                else:
                    logger.warning(
                        f"No facial features for picture {picture.description} face_index {face.face_index} frame_index {frame_index}"
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

        logger.debug("Generated facial features for %d faces", updates)
        return updates

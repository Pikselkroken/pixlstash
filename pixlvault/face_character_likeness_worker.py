import numpy as np
import time
from sqlmodel import select

from pixlvault.database import DBPriority
from pixlvault.db_models.character import Character
from pixlvault.db_models.face_character_likeness import FaceCharacterLikeness
from pixlvault.db_models.picture_set import PictureSet, PictureSetMember
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.face import Face
from pixlvault.db_models.face_likeness import FaceLikenessFrontier
from pixlvault.picture_utils import PictureUtils

logger = get_logger(__name__)


class FaceCharacterLikenessWorker(BaseWorker):
    """
    Worker to compute likeness between detected Faces and the faces in Character reference sets.
    The Face-Character Likeness is a softmax weighted average of a particular face vs the faces in the Character reference set.
    """

    BATCH_SIZE = 1000

    def worker_type(self) -> WorkerType:
        return WorkerType.FACE_CHARACTER_LIKENESS

    def _run(self):
        logger.info(
            "FaceCharacterLikenessWorker: Face-Character likeness worker started."
        )

        self._db.run_task(FaceLikenessFrontier.ensure_all)

        while not self._stop.is_set():
            start = time.time()

            # 1. Get a list of all characters with reference sets
            # 2. For each character, get the reference face IDs
            # 3. For each reference face ID, get the likeness to all faces

            def fetch_characters(session):
                characters = session.exec(select(Character)).all()
                return characters

            def get_character_reference_faces(session, character_id):
                # Need to get pictures in the reference set for this character
                character = Character.find(session, id=character_id)
                reference_set = session.get(
                    PictureSet, character[0].reference_picture_set_id
                )
                if not reference_set:
                    return []
                members = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == reference_set.id
                    )
                ).all()
                picture_ids = [m.picture_id for m in members]
                if not picture_ids:
                    logger.warning(
                        f"No pictures in reference set id={reference_set.id} for character id={character_id}"
                    )
                    return []
                faces = Face.find(session, picture_id=picture_ids)
                return faces

            processed_notify_ids = []

            for character in self._db.run_task(fetch_characters):
                if self._stop.is_set():
                    break
                character_id = character.id
                reference_faces = self._db.run_task(
                    get_character_reference_faces, character_id
                )

                logger.info(
                    "Got {} reference faces for character id={}".format(
                        len(reference_faces), character_id
                    )
                )

                if not reference_faces:
                    continue

                ref_arrs = []
                for ref_face in reference_faces:
                    if ref_face.features is not None:
                        ref_arrs.append(
                            np.frombuffer(ref_face.features, dtype=np.float32)
                        )
                if not ref_arrs:
                    continue

                faces_without_likeness = self._db.run_task(
                    Face.find_faces_without_character_likeness, character_id
                )

                logger.info(
                    "Found {} faces without likeness for character id={}".format(
                        len(faces_without_likeness), character_id
                    )
                )
                likeness_results = []

                for face in faces_without_likeness:
                    if self._stop.is_set():
                        break
                    if face.features is None:
                        continue
                    arr_face = np.frombuffer(face.features, dtype=np.float32)
                    sims = PictureUtils.cosine_similarity_batch(
                        [arr_face] * len(ref_arrs), ref_arrs
                    )
                    softmax_likeness = PictureUtils.softmax_weighted_average(
                        sims, alpha=2.0
                    )

                    likeness_result = FaceCharacterLikeness(
                        face_id=face.id,
                        character_id=character_id,
                        likeness=float(softmax_likeness),
                        metric="softmax_weighted_cosine",
                    )
                    likeness_results.append(likeness_result)
                    processed_notify_ids.append(
                        (
                            FaceCharacterLikeness,
                            (character_id, face.id),
                            "pair",
                            float(softmax_likeness),
                        )
                    )
                    if len(likeness_results) >= self.BATCH_SIZE:
                        break  # Process in batches

                logger.info(
                    "Computed likeness for {} faces for character id={}".format(
                        len(likeness_results), character_id
                    )
                )
                self._db.run_task(
                    FaceCharacterLikeness.bulk_insert_ignore,
                    likeness_results,
                    priority=DBPriority.LOW,
                )

            if self._stop.is_set():
                break

            elapsed = time.time() - start
            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)
                logger.info(
                    f"FaceCharacterLikenessWorker: Processed {len(processed_notify_ids)} Face-Character pairs in {elapsed:.2f}"
                )
            else:
                logger.info(
                    f"FaceCharacterLikenessWorker: No valid Face-Character pairs processed in {elapsed:.2f} seconds. Sleeping..."
                )
                self._wait()

        logger.info(
            "FaceCharacterLikenessWorker: Face-Character likeness worker stopped."
        )

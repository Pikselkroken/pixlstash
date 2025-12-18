import numpy as np
import time
from sqlmodel import select, Session, text
from typing import List, Optional, Tuple

from pixlvault.database import DBPriority
from pixlvault.db_models.character import Character
from pixlvault.db_models.face_character_likeness import FaceCharacterLikeness
from pixlvault.db_models.picture_set import PictureSet, PictureSetMember
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.face import Face
from pixlvault.db_models.face_likeness import FaceLikeness, FaceLikenessFrontier
from pixlvault.picture_utils import PictureUtils

logger = get_logger(__name__)


class FaceLikenessWorker(BaseWorker):
    BATCH_SIZE = 50000
    MIN_THRESHOLD = 0.5

    def worker_type(self) -> WorkerType:
        return WorkerType.FACE_LIKENESS

    @classmethod
    def get_next_batch(cls, session: Session) -> Optional[Tuple[int, List[int]]]:
        """
        Return the next work chunk as (a, bs), where:
        - a is the next face_id_a with remaining work and quality ready,
        - bs is a contiguous list of b ids (a < b) with quality ready,
        - len(bs) <= batch_size and starts at the current frontier start_b.
        Returns None if nothing to do.
        """

        max_id = FaceLikenessFrontier.max_face_id(session)
        if not max_id:
            return None, None

        a = FaceLikenessFrontier.smallest_a_with_work(session, max_id=max_id)
        if a is None:
            return None, None

        rng = FaceLikenessFrontier.range_to_compare(
            session, a, max_id=max_id, batch_limit=cls.BATCH_SIZE
        )
        if not rng:
            return None, None  # frontier already at max or race

        start_b, end_b = rng
        bs = list(range(start_b, end_b + 1))
        return a, bs

    def _run(self):
        logger.info("FaceLikenessWorker: Face likeness worker started.")

        self._db.run_task(FaceLikenessFrontier.ensure_all)

        while not self._stop.is_set():
            start = time.time()

            a, bs = self._db.run_immediate_read_task(FaceLikenessWorker.get_next_batch)

            if not a or not bs:
                logger.info("FaceLikenessWorker: No pending pairs. Sleeping...")
                self._wait()
                continue

            logger.debug(f"FaceLikenessWorker: Processing {len(bs)} pairs.")

            face_ids_needed = set()
            for b in bs:
                face_ids_needed.add(a)
                face_ids_needed.add(b)

            def fetch_faces(session, ids):
                faces = session.exec(select(Face).where(Face.id.in_(ids))).all()
                return {face.id: face for face in faces}

            face_dict = self._db.run_task(
                fetch_faces, list(face_ids_needed), priority=DBPriority.LOW
            )

            likeness_results = []
            processed_notify_ids = []
            arr_a_list = []
            arr_b_list = []
            pair_ids = []
            for b in bs:
                face_a = face_dict.get(a)
                face_b = face_dict.get(b)
                if (
                    not face_a
                    or not face_b
                    or face_a.features is None
                    or face_b.features is None
                ):
                    continue
                arr_a_list.append(np.frombuffer(face_a.features, dtype=np.float32))
                arr_b_list.append(np.frombuffer(face_b.features, dtype=np.float32))
                pair_ids.append((a, b))

            logger.debug(
                f"FaceLikenessWorker: Computing cosine similarities for batch. Lists lengths: arr_a_list={len(arr_a_list)}, arr_b_list={len(arr_b_list)}"
            )
            if arr_a_list and arr_b_list:
                sims = PictureUtils.cosine_similarity_batch(arr_a_list, arr_b_list)
                for (a, b), likeness in zip(pair_ids, sims):
                    if likeness >= self.MIN_THRESHOLD:
                        likeness_results.append(
                            FaceLikeness(
                                face_id_a=a,
                                face_id_b=b,
                                likeness=float(likeness),
                                metric="cosine_similarity",
                            )
                        )
                    processed_notify_ids.append(
                        (
                            FaceLikeness,
                            (a, b),
                            "pair",
                            likeness if likeness >= self.MIN_THRESHOLD else None,
                        )
                    )

            def insert_likeness_and_update_frontier(
                session, likeness_results, a, max_b
            ):
                try:
                    session.execute(text("BEGIN IMMEDIATE"))
                    FaceLikeness.bulk_insert_ignore(session, likeness_results)
                    FaceLikenessFrontier.update(session, a, max_b)
                    session.commit()
                except Exception as e:
                    logger.error(f"Error during insert and update frontier: {e}")
                    session.rollback()

            self._db.run_task(
                insert_likeness_and_update_frontier,
                likeness_results,
                a,
                max(bs),
                priority=DBPriority.LOW,
            )

            elapsed = time.time() - start
            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)
                logger.debug(
                    f"FaceLikenessWorker: Processed {len(processed_notify_ids)} pairs in {elapsed:.2f} seconds."
                )
            else:
                logger.debug(
                    f"FaceLikenessWorker: No valid pairs processed in {elapsed:.2f} seconds. Sleeping..."
                )
                self._wait()

        logger.info("FaceLikenessWorker: Face likeness worker stopped.")


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
                characters = session.exec(
                    select(Character).where(
                        Character.reference_picture_set_id.is_not(None)
                    )
                ).all()
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

            for character in self._db.run_task(fetch_characters):
                character_id = character.id
                reference_faces = self._db.run_task(
                    get_character_reference_faces, character_id
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

                logger.info(
                    "Got {} reference faces for character id={}".format(
                        len(ref_arrs), character_id
                    )
                )

                faces_without_likeness = self._db.run_task(
                    Face.find_faces_without_character_likeness, character_id
                )

                logger.info(
                    "Found {} faces without likeness for character id={}".format(
                        len(faces_without_likeness), character_id
                    )
                )
                likeness_results = []
                processed_notify_ids = []

                for face in faces_without_likeness:
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
                for likeness_result in likeness_results:
                    logger.info(
                        f"FaceCharacterLikenessWorker: Character ID {likeness_result.character_id}, Face ID {likeness_result.face_id} = Likeness {likeness_result.likeness}"
                    )
                self._db.run_task(
                    FaceCharacterLikeness.bulk_insert_ignore,
                    likeness_results,
                    priority=DBPriority.LOW,
                )

                elapsed = time.time() - start
                if processed_notify_ids:
                    for pair in processed_notify_ids:
                        logger.info(
                            f"FaceCharacterLikenessWorker: Notifying processed Face-Character pair: {pair}"
                        )
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

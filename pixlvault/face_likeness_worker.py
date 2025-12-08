import numpy as np
import time
from sqlmodel import select

from pixlvault.logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.face import Face
from pixlvault.db_models.face_likeness_work_queue import FaceLikenessWorkQueue
from pixlvault.db_models.face_likeness import FaceLikeness

logger = get_logger(__name__)


class FaceLikenessWorker(BaseWorker):
    BATCH_SIZE = 5000
    NUM_THREADS = 4
    INTERVAL = 5

    def worker_type(self) -> WorkerType:
        return WorkerType.FACE_LIKENESS

    def _run(self):
        logger.info("FaceLikenessWorker: Face likeness worker started.")
        while not self._stop.is_set():
            start = time.time()

            # 1. Fetch pending pairs from the work queue
            def fetch_pending_pairs(session):
                pairs = session.exec(
                    select(FaceLikenessWorkQueue).limit(self.BATCH_SIZE)
                ).all()
                face_ids = set()
                for pair in pairs:
                    face_ids.add(pair.face_id_a)
                    face_ids.add(pair.face_id_b)
                faces = session.exec(select(Face).where(Face.id.in_(face_ids))).all()
                # Filter out sentinel records (face_index == -1)
                valid_faces = [face for face in faces if face.face_index != -1]
                face_dict = {face.id: face for face in valid_faces}
                return pairs, face_dict

            pending_pairs, face_dict = self._db.run_task(fetch_pending_pairs)
            if not pending_pairs:
                logger.debug("FaceLikenessWorker: No pending pairs. Sleeping...")
                self._stop.wait(self.INTERVAL)
                continue

            logger.debug(f"FaceLikenessWorker: Processing {len(pending_pairs)} pairs.")

            # 2. Do likeness computation outside the session
            likeness_results = []
            to_remove = []
            processed_notify_ids = []
            for pair in pending_pairs:
                face_a = face_dict.get(pair.face_id_a)
                face_b = face_dict.get(pair.face_id_b)
                # Skip pairs where either face is missing or is a sentinel (already filtered in face_dict, but double check)
                if not face_a or not face_b:
                    logger.warning(
                        f"Skipping pair ({pair.face_id_a}, {pair.face_id_b}): missing or sentinel face(s). Removing from queue."
                    )
                    to_remove.append(pair)
                    continue
                features_a = face_a.features
                features_b = face_b.features
                if features_a is None or features_b is None:
                    logger.warning(
                        f"Skipping pair ({pair.face_id_a}, {pair.face_id_b}): missing facial features. Removing from queue."
                    )
                    to_remove.append(pair)
                    continue

                # Fetch and log picture descriptions for both faces before similarity
                def get_picture_desc(session, pic_id):
                    from pixlvault.db_models.picture import Picture

                    pics = Picture.find(session, id=pic_id)
                    if pics and hasattr(pics[0], "description"):
                        return pics[0].description
                    return str(pic_id)

                desc_a = self._db.run_task(get_picture_desc, face_a.picture_id)
                desc_b = self._db.run_task(get_picture_desc, face_b.picture_id)
                logger.debug(
                    f"Comparing faces from pictures: A='{desc_a}', B='{desc_b}'"
                )
                likeness = self._cosine_similarity(features_a, features_b)
                likeness_results.append(
                    FaceLikeness(
                        face_id_a=pair.face_id_a,
                        face_id_b=pair.face_id_b,
                        likeness=likeness,
                        metric="cosine_similarity",
                    )
                )
                to_remove.append(pair)
                processed_notify_ids.append(
                    (FaceLikeness, (pair.face_id_a, pair.face_id_b), "pair")
                )

            # 3. Write results and remove processed pairs in a new DB task
            def write_results(session):
                if likeness_results:
                    session.add_all(likeness_results)
                    logger.debug(
                        f"Inserted {len(likeness_results)} face likeness scores."
                    )
                if to_remove:
                    for pair in to_remove:
                        session.delete(pair)
                    logger.debug(
                        f"Removed {len(to_remove)} processed pairs from work queue."
                    )
                session.commit()

            self._db.run_task(write_results)

            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)

            elapsed = time.time() - start
            if elapsed < self.INTERVAL:
                self._stop.wait(self.INTERVAL - elapsed)
        logger.info("FaceLikenessWorker: Face likeness worker stopped.")

    def queue_pair(self, face_id_a: int, face_id_b: int):
        """
        Public method to queue a pair for likeness computation.
        """
        self._db.run_task(FaceLikenessWorker._add_pair_to_queue, face_id_a, face_id_b)

    @staticmethod
    def _add_pair_to_queue(session, face_id_a: int, face_id_b: int):
        """
        Add a pair to the likeness work queue, ensuring uniqueness and order (a < b).
        """
        if face_id_a == face_id_b:
            return  # Don't add self-pairs
        a, b = sorted([face_id_a, face_id_b])
        # Check if already exists in queue or results

        exists = session.exec(
            select(FaceLikenessWorkQueue).where(
                (FaceLikenessWorkQueue.face_id_a == a)
                & (FaceLikenessWorkQueue.face_id_b == b)
            )
        ).first()
        if exists:
            return
        exists = session.exec(
            select(FaceLikeness).where(
                (FaceLikeness.face_id_a == a) & (FaceLikeness.face_id_b == b)
            )
        ).first()
        if exists:
            return
        session.add(FaceLikenessWorkQueue(face_id_a=a, face_id_b=b))
        session.commit()

    def _cosine_similarity(self, features_a, features_b):
        # Assume features are bytes representing np.ndarray
        arr_a = np.frombuffer(features_a, dtype=np.float32)
        arr_b = np.frombuffer(features_b, dtype=np.float32)
        # Print more of the feature vectors for diagnostics
        norm_a = np.linalg.norm(arr_a)
        norm_b = np.linalg.norm(arr_b)
        if norm_a == 0 or norm_b == 0:
            logger.warning(
                "One of the feature vectors has zero norm; returning 0.0 similarity."
            )
            return 0.0
        similarity = float(np.dot(arr_a, arr_b) / (norm_a * norm_b))
        return similarity

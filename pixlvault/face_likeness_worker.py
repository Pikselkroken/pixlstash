import numpy as np
import time
from sqlmodel import select

from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.face import Face
from pixlvault.db_models.face_likeness import FaceLikeness

logger = get_logger(__name__)


class FaceLikenessWorker(BaseWorker):
    BATCH_SIZE = 50000
    CHUNKS = 500

    def worker_type(self) -> WorkerType:
        return WorkerType.FACE_LIKENESS

    def _run(self):
        logger.info("FaceLikenessWorker: Face likeness worker started.")
        while not self._stop.is_set():
            start = time.time()

            # 1. Fetch a batch of missing (a, b) pairs (a < b) not in FaceLikeness
            def fetch_missing_pairs(session):
                from sqlalchemy import and_
                from sqlalchemy.orm import aliased
                from sqlmodel import select
                Face2 = aliased(Face)
                from sqlalchemy import and_
                stmt = (
                    select(Face.id.label('a_id'), Face2.id.label('b_id'))
                    .select_from(Face)
                    .join(Face2, and_(Face.face_index != -1, Face2.face_index != -1, Face.id < Face2.id))
                    .outerjoin(
                        FaceLikeness,
                        and_(
                            FaceLikeness.face_id_a == Face.id,
                            FaceLikeness.face_id_b == Face2.id,
                        )
                    )
                    .where(FaceLikeness.face_id_a == None)
                    .limit(self.BATCH_SIZE)
                )
                result = session.exec(stmt)
                batch = [(row.a_id, row.b_id) for row in result]
                return batch, None

            pending_pairs, remaining = self._db.run_task(fetch_missing_pairs, priority=DBPriority.LOW)
            if not pending_pairs:
                logger.info("FaceLikenessWorker: No pending pairs. Sleeping...")
                self._wait()
                continue

            logger.info(f"FaceLikenessWorker: Processing {len(pending_pairs)} pairs. Remaining: {remaining}.")

            face_ids_needed = set()
            for a, b in pending_pairs:
                face_ids_needed.add(a)
                face_ids_needed.add(b)
            def fetch_faces(session, ids):
                faces = session.exec(select(Face).where(Face.id.in_(ids))).all()
                return {face.id: face for face in faces}
            face_dict = self._db.run_task(fetch_faces, list(face_ids_needed), priority=DBPriority.LOW)

            likeness_results = []
            processed_notify_ids = []
            arr_a_list = []
            arr_b_list = []
            pair_ids = []
            for a, b in pending_pairs:
                face_a = face_dict.get(a)
                face_b = face_dict.get(b)
                if not face_a or not face_b or face_a.features is None or face_b.features is None:
                    continue
                arr_a_list.append(np.frombuffer(face_a.features, dtype=np.float32))
                arr_b_list.append(np.frombuffer(face_b.features, dtype=np.float32))
                pair_ids.append((a, b))

            if arr_a_list and arr_b_list:
                sims = self._cosine_similarity_batch(arr_a_list, arr_b_list)
                for (a, b), likeness in zip(pair_ids, sims):
                    likeness_results.append(
                        FaceLikeness(
                            face_id_a=a,
                            face_id_b=b,
                            likeness=float(likeness),
                            metric="cosine_similarity",
                        )
                    )
                    processed_notify_ids.append((FaceLikeness, (a, b), "pair", float(likeness)))

                futures = []
                def write_results(session, batch):
                    if batch:
                        session.add_all(batch)
                    session.commit()
            
                # Split likeness_results into chunks for parallel writing
                chunk_size = max(1, len(likeness_results) // self.CHUNKS)
                for i in range(0, len(likeness_results), chunk_size):
                    chunk = likeness_results[i:i+chunk_size]
                    futures.append(self._db.submit_task(write_results, chunk, priority=DBPriority.LOW))

                for future in futures:
                    future.result()
            elapsed = time.time() - start
            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)
                logger.info(f"FaceLikenessWorker: Processed {len(processed_notify_ids)} pairs in {elapsed:.2f} seconds.")
            else:
                logger.info(f"FaceLikenessWorker: No valid pairs processed in {elapsed:.2f} seconds. Sleeping...")
                self._wait()
    logger.info("FaceLikenessWorker: Face likeness worker stopped.")



    def _cosine_similarity_batch(self, arr_a_list, arr_b_list):
        """
        Compute cosine similarity for two lists of np.ndarray feature vectors in batch.
        Returns a 1D np.ndarray of similarities scaled to [0, 1].
        """
        arr_a = np.stack(arr_a_list)
        arr_b = np.stack(arr_b_list)
        # Normalize
        arr_a_norm = arr_a / np.linalg.norm(arr_a, axis=1, keepdims=True)
        arr_b_norm = arr_b / np.linalg.norm(arr_b, axis=1, keepdims=True)
        # Compute dot products
        sims = np.sum(arr_a_norm * arr_b_norm, axis=1)
        sims = 0.5 * (sims + 1.0)  # Scale to [0, 1]
        return sims


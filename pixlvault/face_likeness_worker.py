import numpy as np
import time
from sqlmodel import select, Session, text
from typing import List, Optional, Tuple

from pixlvault.database import DBPriority
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
                if self._stop.is_set():
                    break
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
                if self._stop.is_set():
                    break
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

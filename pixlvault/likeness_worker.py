from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import cv2
import numpy as np
import os
import time

from sqlmodel import select, Session
from typing import List, Optional, Tuple

from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.face import Face
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.picture_likeness import (
    PictureLikeness,
    PictureLikenessFrontier,
)

logger = get_logger(__name__)


class LikenessWorker(BaseWorker):
    BATCH_SIZE = int(os.getenv("PIXL_LIKENESS_BATCH_SIZE", "5000"))
    TOP_K = 200
    FACE_COUNT_MAX_DIFF = 1
    FACE_WEIGHT = 0.5
    IMAGE_WEIGHT = 0.5
    PHASH_BITS = 64
    PHASH_STRONG_MATCH = 0.95
    PHASH_MIN_SIM = 0.92

    def worker_type(self) -> WorkerType:
        return WorkerType.LIKENESS

    @classmethod
    def get_next_batch(
        cls, session: Session
    ) -> Optional[Tuple[int, List[int], int, int]]:
        """
        Return the next work chunk as (a, bs), where:
        - a is the next picture_id_a with remaining work and embedding ready,
        - bs is a contiguous list of b ids (a < b) with embedding ready,
        - len(bs) <= batch_size and starts at the current frontier start_b.
        Returns None if nothing to do.
        """

        a = PictureLikenessFrontier.get_next_a_candidate(
            session, quality_ready=cls._embedding_ready
        )
        if a is None:
            return None, None, None, None

        max_id = PictureLikenessFrontier.max_picture_id(session)
        # Ask the model to compute the next contiguous [start_b, end_b] window
        rng = PictureLikenessFrontier.range_to_compare(
            session, a, max_id=max_id, batch_limit=cls.BATCH_SIZE
        )
        if not rng:
            return None, None, None, None  # frontier already at max or race

        start_b, end_b = rng

        # Filter window to b with embedding ready on both sides
        # Query all b rows in one go for efficiency
        b_rows = session.exec(
            select(Picture.id)
            .where(
                (Picture.id >= start_b)
                & (Picture.id <= end_b)
                & (Picture.image_embedding.is_not(None))
            )
            .order_by(Picture.id)
        ).all()
        eligible_bs_all = [int(pid) for pid in b_rows]

        # Allow sparse ranges: take any eligible b values within the window
        if not eligible_bs_all:
            return None, None, None, None  # No eligible b in the window

        return a, eligible_bs_all[: cls.BATCH_SIZE], start_b, end_b

    def _run(self):
        logger.info("LikenessWorker: Likeness worker started.")

        self._db.run_task(PictureLikenessFrontier.ensure_all)

        logger.info("LikenessWorker: PictureLikenessFrontier initialized.")

        while not self._stop.is_set():
            a, bs, start_b, end_b = self._db.run_immediate_read_task(
                LikenessWorker.get_next_batch
            )

            if not a or not bs:
                logger.info("LikenessWorker: No pending pairs. Sleeping...")
                self._wait()
                continue

            logger.info(
                "LikenessWorker: Processing %s pairs (a=%s, window=%s-%s).",
                len(bs),
                a,
                start_b,
                end_b,
            )
            batch_start = time.time()

            pids_needed = set()
            for b in bs:
                pids_needed.add(a)
                pids_needed.add(b)

            def fetch_phashes(session, ids):
                rows = session.exec(
                    select(Picture.id, Picture.perceptual_hash).where(
                        Picture.id.in_(ids)
                    )
                ).all()
                return {pid: phash for pid, phash in rows}

            phash_dict = self._db.run_task(
                fetch_phashes, list(pids_needed), priority=DBPriority.LOW
            )

            phash_a = phash_dict.get(a)
            candidate_bs = []
            skipped_by_phash = 0
            for b in bs:
                phash_b = phash_dict.get(b)
                if not phash_a or not phash_b:
                    skipped_by_phash += 1
                    continue
                phash_sim = self._phash_similarity(phash_a, phash_b)
                if phash_sim is None or phash_sim < self.PHASH_MIN_SIM:
                    skipped_by_phash += 1
                    continue
                candidate_bs.append(b)

            if not candidate_bs:

                def advance_frontier_only(session, a, max_b):
                    PictureLikenessFrontier.update(session, a, max_b)
                    session.commit()

                self._db.run_task(
                    advance_frontier_only,
                    a,
                    max(bs),
                    priority=DBPriority.LOW,
                )
                elapsed = time.time() - batch_start
                logger.info(
                    "LikenessWorker: Batch done (a=%s) elapsed=%.2fs total=%s scored=0 skipped_phash=%s missing_emb=0 invalid_emb=0",
                    a,
                    elapsed,
                    len(bs),
                    skipped_by_phash,
                )
                continue

            pids_needed = {a, *candidate_bs}

            def fetch_embeddings_and_faces(session, ids):
                embeddings = session.exec(
                    select(
                        Picture.id, Picture.image_embedding, Picture.perceptual_hash
                    ).where(Picture.id.in_(ids))
                ).all()
                embedding_dict = {}
                phash_dict = {}
                for pid, emb, phash in embeddings:
                    if isinstance(emb, (memoryview, bytearray)):
                        emb = bytes(emb)
                    embedding_dict[pid] = emb
                    phash_dict[pid] = phash

                face_rows = session.exec(
                    select(Face.picture_id, Face.features).where(
                        Face.picture_id.in_(ids)
                        & (Face.face_index != -1)
                        & (Face.features.is_not(None))
                    )
                ).all()
                face_dict = defaultdict(list)
                for pic_id, features in face_rows:
                    if isinstance(features, (memoryview, bytearray)):
                        features = bytes(features)
                    face_dict[pic_id].append(features)

                return embedding_dict, face_dict, phash_dict

            embedding_dict, face_dict, phash_dict = self._db.run_task(
                fetch_embeddings_and_faces, list(pids_needed), priority=DBPriority.LOW
            )

            likeness_results = []
            processed_notify_ids = []
            missing_embeddings = 0
            invalid_embeddings = 0
            for b in candidate_bs:
                if self._stop.is_set():
                    break
                logger.debug(f"LikenessWorker: Processing pair (a={a}, b={b})")
                embedding_a = embedding_dict.get(a)
                embedding_b = embedding_dict.get(b)
                if embedding_a is None or embedding_b is None:
                    missing_embeddings += 1
                    logger.warning(
                        "LikenessWorker: Missing embeddings for pair (a=%s, b=%s)",
                        a,
                        b,
                    )
                    continue
                emb_a = self._decode_embedding(embedding_a)
                emb_b = self._decode_embedding(embedding_b)
                if emb_a is None or emb_b is None:
                    invalid_embeddings += 1
                    logger.warning(
                        "LikenessWorker: Invalid embeddings for pair (a=%s, b=%s)",
                        a,
                        b,
                    )
                    continue

                face_features_a = face_dict.get(a, [])
                face_features_b = face_dict.get(b, [])
                likeness = self._embedding_likeness(
                    emb_a, emb_b, face_features_a, face_features_b
                )
                if likeness is None:
                    continue

                likeness_results.append(
                    PictureLikeness(
                        picture_id_a=a,
                        picture_id_b=b,
                        likeness=likeness,
                        metric="image_face_embedding",
                    )
                )
                processed_notify_ids.append((PictureLikeness, (a, b), "pair", likeness))
                if self._stop.is_set():
                    break

            logger.debug("LikenessWorker: Writing likeness scores to database...")

            def insert_likeness_and_update_frontier(
                session, likeness_results, a, max_b
            ):
                PictureLikeness.bulk_insert_ignore(session, likeness_results)
                PictureLikenessFrontier.update(session, a, max_b)
                PictureLikeness.prune_below_top_k(session, a, self.TOP_K)
                session.commit()

            self._db.run_task(
                insert_likeness_and_update_frontier,
                likeness_results,
                a,
                max(bs),
                priority=DBPriority.LOW,
            )

            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)
                # Update completed_tasks to track all b's for each a
                logger.debug(
                    f"LikenessWorker: Processed {len(processed_notify_ids)} likeness scores."
                )
            else:
                logger.debug("LikenessWorker: No likeness scores computed. Sleeping...")
                self._wait()
            elapsed = time.time() - batch_start
            logger.info(
                "LikenessWorker: Batch done (a=%s) elapsed=%.2fs total=%s scored=%s skipped_phash=%s missing_emb=%s invalid_emb=%s",
                a,
                elapsed,
                len(bs),
                len(likeness_results),
                skipped_by_phash,
                missing_embeddings,
                invalid_embeddings,
            )
        logger.info("LikenessWorker: Likeness worker stopped.")

    @staticmethod
    def _embedding_ready(session: Session, picture_id: int) -> bool:
        return (
            session.exec(
                select(Picture.id).where(
                    (Picture.id == picture_id) & (Picture.image_embedding.is_not(None))
                )
            ).first()
            is not None
        )

    @staticmethod
    def _decode_embedding(blob) -> Optional[np.ndarray]:
        if blob is None:
            return None
        if isinstance(blob, (memoryview, bytearray)):
            blob = bytes(blob)
        if isinstance(blob, np.ndarray):
            arr = np.asarray(blob, dtype=np.float32)
            return arr if arr.size else None
        if not isinstance(blob, (bytes, bytearray)):
            try:
                blob = bytes(blob)
            except Exception:
                return None
        try:
            arr = np.frombuffer(blob, dtype=np.float32)
            if arr.size == 0:
                return None
            return arr.copy()
        except Exception:
            return None

    @classmethod
    def _phash_similarity(cls, hash_a: str, hash_b: str) -> Optional[float]:
        if not hash_a or not hash_b:
            return None
        try:
            int_a = int(hash_a, 16)
            int_b = int(hash_b, 16)
        except Exception:
            return None
        distance = (int_a ^ int_b).bit_count()
        return 1.0 - (distance / float(cls.PHASH_BITS))

    @staticmethod
    def _cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> Optional[float]:
        if vec_a is None or vec_b is None:
            return None
        if vec_a.shape != vec_b.shape or vec_a.size == 0:
            return None
        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)
        if norm_a == 0 or norm_b == 0:
            return None
        sim = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
        sim = float(np.clip(sim, -1.0, 1.0))
        return 0.5 * (sim + 1.0)

    def _decode_face_vectors(self, features_list: list[bytes]) -> list[np.ndarray]:
        vectors = []
        for blob in features_list:
            arr = self._decode_embedding(blob)
            if arr is None:
                continue
            if arr.ndim != 1:
                arr = arr.ravel()
            if arr.size == 0:
                continue
            vectors.append(arr)
        if not vectors:
            return []
        target_size = vectors[0].size
        return [vec for vec in vectors if vec.size == target_size]

    @staticmethod
    def _filter_common_face_vectors(
        vecs_a: list[np.ndarray], vecs_b: list[np.ndarray]
    ) -> tuple[list[np.ndarray], list[np.ndarray]]:
        if not vecs_a or not vecs_b:
            return [], []
        sizes_a = [vec.size for vec in vecs_a]
        sizes_b = [vec.size for vec in vecs_b]
        common_sizes = set(sizes_a) & set(sizes_b)
        if not common_sizes:
            return [], []
        common_size = max(
            common_sizes, key=lambda s: sizes_a.count(s) + sizes_b.count(s)
        )
        return (
            [vec for vec in vecs_a if vec.size == common_size],
            [vec for vec in vecs_b if vec.size == common_size],
        )

    @staticmethod
    def _max_face_similarity(
        vecs_a: list[np.ndarray], vecs_b: list[np.ndarray]
    ) -> Optional[float]:
        if not vecs_a or not vecs_b:
            return None
        a = np.stack(vecs_a)
        b = np.stack(vecs_b)
        if a.ndim == 1:
            a = a.reshape(1, -1)
        if b.ndim == 1:
            b = b.reshape(1, -1)
        if a.shape[1] != b.shape[1]:
            return None
        a_norm = a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-8)
        b_norm = b / np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-8)
        sims = a_norm @ b_norm.T
        sim = float(np.max(sims))
        sim = float(np.clip(sim, -1.0, 1.0))
        return 0.5 * (sim + 1.0)

    def _embedding_likeness(
        self,
        emb_a: np.ndarray,
        emb_b: np.ndarray,
        face_features_a: list[bytes],
        face_features_b: list[bytes],
    ) -> Optional[float]:
        image_sim = self._cosine_similarity(emb_a, emb_b)
        if image_sim is None:
            return None

        vecs_a = self._decode_face_vectors(face_features_a)
        vecs_b = self._decode_face_vectors(face_features_b)
        if vecs_a or vecs_b:
            if not vecs_a or not vecs_b:
                return 0.0
            vecs_a, vecs_b = self._filter_common_face_vectors(vecs_a, vecs_b)
            if not vecs_a or not vecs_b:
                return 0.0
            if abs(len(vecs_a) - len(vecs_b)) > self.FACE_COUNT_MAX_DIFF:
                return 0.0
            face_sim = self._max_face_similarity(vecs_a, vecs_b)
            if face_sim is None:
                return 0.0
            likeness = (self.IMAGE_WEIGHT * image_sim) + (self.FACE_WEIGHT * face_sim)
            return float(np.clip(likeness, 0.0, 1.0))

        return float(np.clip(image_sim, 0.0, 1.0))

    def _color_histogram_likeness(self, img_a, img_b, bins=32):
        if img_a is None or img_b is None:
            return 0.0

        def get_hist(img):
            chans = cv2.split(img)
            hist = [
                cv2.calcHist([c], [0], None, [bins], [0, 256]).flatten() for c in chans
            ]
            hist = np.concatenate(hist)
            hist = hist / (np.sum(hist) + 1e-8)
            return hist

        hist_a = (
            img_a if hasattr(img_a, "ndim") and img_a.ndim == 1 else get_hist(img_a)
        )
        hist_b = (
            img_b if hasattr(img_b, "ndim") and img_b.ndim == 1 else get_hist(img_b)
        )
        l1 = np.sum(np.abs(hist_a - hist_b))
        likeness = 1.0 - (l1 / 2.0)
        return float(np.clip(likeness, 0.0, 1.0))

    def _process_batches_for_color_histogram_likeness(self, pending_pairs, bins=32):
        """
        Batch process color histogram likeness for all pending pairs.
        Returns (likeness_scores, processed_pairs, processed_total)
        """
        batches = [
            pending_pairs[i * self.BATCH_SIZE : (i + 1) * self.BATCH_SIZE]
            for i in range(
                min(
                    self.CHUNKS,
                    (len(pending_pairs) + self.BATCH_SIZE - 1) // self.BATCH_SIZE,
                )
            )
        ]

        def process_batch(batch):
            likeness_scores = []
            queue_pairs = []
            for item in batch:
                pic_a_id, pic_b_id, pic_a, pic_b = item
                # Assume PictureModel has .image_data or .get_image() returning np.ndarray (H,W,3)
                try:
                    img_a = (
                        pic_a.get_image()
                        if hasattr(pic_a, "get_image")
                        else pic_a.image_data
                    )
                    img_b = (
                        pic_b.get_image()
                        if hasattr(pic_b, "get_image")
                        else pic_b.image_data
                    )
                    if img_a is None or img_b is None:
                        continue
                    likeness = self._color_histogram_likeness(img_a, img_b, bins)
                    likeness_scores.append(
                        (pic_a_id, pic_b_id, float(likeness), "color_hist")
                    )
                    queue_pairs.append((pic_a_id, pic_b_id))
                except Exception as e:
                    logger.warning(
                        f"Color histogram likeness failed for pair ({pic_a_id}, {pic_b_id}): {e}"
                    )
            return likeness_scores, queue_pairs

        processed_total = 0
        all_likeness_scores = []
        all_processed_pairs = []
        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            futures = [
                executor.submit(process_batch, batch) for batch in batches if batch
            ]
            for future in as_completed(futures):
                batch_scores, processed_pairs = future.result()
                all_likeness_scores.extend(batch_scores)
                all_processed_pairs.extend(processed_pairs)
                processed_total += len(batch_scores)
        return all_likeness_scores, all_processed_pairs, processed_total

    def _color_histogram_likeness_batch(self, img_a, imgs_b, bins=32):
        """
        Compute color histogram likeness between img_a and a list of imgs_b efficiently.
        Returns a list of likeness scores.
        """

        def get_hist(img):
            chans = cv2.split(img)
            hist = [
                cv2.calcHist([c], [0], None, [bins], [0, 256]).flatten() for c in chans
            ]
            hist = np.concatenate(hist)
            hist = hist / (np.sum(hist) + 1e-8)
            return hist

        hist_a = get_hist(img_a)
        hists_b = [get_hist(img) for img in imgs_b]
        if not hists_b:
            return []
        hists_b = np.stack(hists_b, axis=0)
        l1 = np.sum(np.abs(hists_b - hist_a), axis=1)
        likeness = 1.0 - (l1 / 2.0)
        return np.clip(likeness, 0.0, 1.0).tolist()

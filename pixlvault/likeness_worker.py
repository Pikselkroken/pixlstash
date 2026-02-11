from __future__ import annotations

import time
from typing import List, Optional, Tuple

import numpy as np
from sqlalchemy import func
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.picture_likeness import (
    PictureLikeness,
    PictureLikenessFrontier,
)

logger = get_logger(__name__)


class LikenessWorker(BaseWorker):
    """
    Speed-focused likeness worker for stacking near-identical images.
    Uses aggressive pruning to avoid N^2 behavior.
    """

    BATCH_CANDIDATES = 256
    MAX_A_PER_CYCLE = 8
    YIELD_SLEEP_SECONDS = 0.05

    PHASH_PREFIX_LEN = 4
    PHASH_MIN_SIM = 0.92
    EMBEDDING_MIN_SIM = 0.96

    MAX_DIM_RATIO_DIFF = 0.2
    MAX_ASPECT_RATIO_DIFF = 0.1
    MAX_SIZE_RATIO_DIFF = 0.3

    TOP_K = 200
    PHASH_BITS = 64

    def worker_type(self) -> WorkerType:
        return WorkerType.LIKENESS

    def _run(self):
        logger.info("LikenessWorker: started.")

        def submit_low(func, *args, **kwargs):
            return self._db.result_or_throw(
                self._db.submit_task(func, *args, priority=DBPriority.LOW, **kwargs)
            )

        submit_low(PictureLikenessFrontier.ensure_all)
        logger.info("LikenessWorker: frontier initialized.")

        while not self._stop.is_set():
            work_items = submit_low(
                LikenessWorker._get_next_work_batch,
                self.MAX_A_PER_CYCLE,
            )

            if not work_items:
                logger.info("LikenessWorker: No pending pairs. Sleeping...")
                self._wait()
                continue

            likeness_results = []
            processed_notify_ids = []
            frontier_updates = []

            for item in work_items:
                if self._stop.is_set():
                    break

                a, start_b, max_id, phash_a, width_a, height_a, size_a = item
                if not phash_a:
                    frontier_updates.append((a, max_id))
                    logger.info(
                        "LikenessWorker: Skipping a=%s (missing phash); advanced frontier to %s.",
                        a,
                        max_id,
                    )
                    continue

                emb_a_blob = submit_low(LikenessWorker._fetch_embedding, a)
                emb_a = self._decode_embedding(emb_a_blob)
                if emb_a is None:
                    frontier_updates.append((a, max_id))
                    logger.warning(
                        "LikenessWorker: Missing embedding for a=%s; advanced frontier to %s.",
                        a,
                        max_id,
                    )
                    continue

                candidates = submit_low(
                    LikenessWorker._fetch_candidates,
                    a,
                    start_b,
                    max_id,
                    phash_a[: self.PHASH_PREFIX_LEN],
                    width_a,
                    height_a,
                    size_a,
                    self.BATCH_CANDIDATES,
                )

                if not candidates:
                    frontier_updates.append((a, max_id))
                    logger.info(
                        "LikenessWorker: Skipping a=%s due to prune filters; advanced frontier to %s.",
                        a,
                        max_id,
                    )
                    continue

                valid_bs = []
                emb_b_list = []
                for b_id, b_phash, b_width, b_height, b_size, b_emb in candidates:
                    if not b_phash:
                        continue
                    if (
                        self._phash_similarity(phash_a, b_phash)
                        < self.PHASH_MIN_SIM
                    ):
                        continue
                    if not self._passes_metadata_filter(
                        width_a,
                        height_a,
                        size_a,
                        b_width,
                        b_height,
                        b_size,
                    ):
                        continue
                    emb_b = self._decode_embedding(b_emb)
                    if emb_b is None or emb_b.shape != emb_a.shape:
                        continue
                    emb_b_list.append(emb_b)
                    valid_bs.append(b_id)

                if not valid_bs:
                    frontier_updates.append((a, max_id))
                    continue

                emb_b_stack = np.stack(emb_b_list, axis=0)
                norm_a = np.linalg.norm(emb_a)
                if norm_a == 0:
                    frontier_updates.append((a, max_id))
                    continue
                emb_a_norm = emb_a / norm_a
                emb_b_norm = emb_b_stack / np.maximum(
                    np.linalg.norm(emb_b_stack, axis=1, keepdims=True), 1e-8
                )
                sims = emb_b_norm @ emb_a_norm
                sims = np.clip(sims, -1.0, 1.0)
                image_sims = 0.5 * (sims + 1.0)

                scored = 0
                for idx, b_id in enumerate(valid_bs):
                    if image_sims[idx] < self.EMBEDDING_MIN_SIM:
                        continue
                    likeness = float(image_sims[idx])
                    likeness_results.append(
                        PictureLikeness(
                            picture_id_a=a,
                            picture_id_b=b_id,
                            likeness=likeness,
                            metric="image_embedding",
                        )
                    )
                    processed_notify_ids.append(
                        (PictureLikeness, (a, b_id), "pair", likeness)
                    )
                    scored += 1

                frontier_updates.append((a, max_id))
                logger.info(
                    "LikenessWorker: Batch done (a=%s) scored=%s candidates=%s.",
                    a,
                    scored,
                    len(valid_bs),
                )

            if likeness_results or frontier_updates:
                submit_low(
                    LikenessWorker._write_results,
                    likeness_results,
                    frontier_updates,
                    self.TOP_K,
                )

            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)

            if self.YIELD_SLEEP_SECONDS > 0 and not self._stop.is_set():
                time.sleep(self.YIELD_SLEEP_SECONDS)

        logger.info("LikenessWorker: stopped.")

    @staticmethod
    def _get_next_work_batch(
        session: Session, max_a: int
    ) -> List[Tuple[int, int, int, Optional[str], Optional[int], Optional[int], Optional[int]]]:
        max_id = PictureLikenessFrontier.max_picture_id(session)
        if not max_id:
            return []

        rows = session.exec(
            select(PictureLikenessFrontier)
            .where(PictureLikenessFrontier.j_max < max_id)
            .order_by(PictureLikenessFrontier.picture_id_a)
        ).all()

        batch = []
        for pf in rows:
            if len(batch) >= max_a:
                break
            a = int(pf.picture_id_a)
            if not LikenessWorker._embedding_ready(session, a):
                continue
            start_b = max(pf.j_max + 1, a + 1)
            if start_b > max_id:
                continue
            row = session.exec(
                select(
                    Picture.perceptual_hash,
                    Picture.width,
                    Picture.height,
                    Picture.size_bytes,
                ).where(Picture.id == a)
            ).first()
            if not row:
                batch.append((a, start_b, max_id, None, None, None, None))
                continue
            phash_a, width_a, height_a, size_a = row
            batch.append((a, start_b, max_id, phash_a, width_a, height_a, size_a))

        return batch

    @staticmethod
    def _fetch_embedding(session: Session, picture_id: int) -> Optional[bytes]:
        return session.exec(
            select(Picture.image_embedding).where(Picture.id == picture_id)
        ).first()

    @staticmethod
    def _fetch_candidates(
        session: Session,
        a_id: int,
        start_b: int,
        max_id: int,
        phash_prefix: str,
        width_a: Optional[int],
        height_a: Optional[int],
        size_a: Optional[int],
        limit: int,
    ) -> List[Tuple[int, Optional[str], Optional[int], Optional[int], Optional[int], Optional[bytes]]]:
        if not phash_prefix:
            return []
        query = select(
            Picture.id,
            Picture.perceptual_hash,
            Picture.width,
            Picture.height,
            Picture.size_bytes,
            Picture.image_embedding,
        ).where(
            (Picture.id >= start_b)
            & (Picture.id <= max_id)
            & (Picture.image_embedding.is_not(None))
        )
        query = query.where(
            Picture.perceptual_hash.is_not(None)
            & (
                func.substr(Picture.perceptual_hash, 1, len(phash_prefix))
                == phash_prefix
            )
        )
        if (
            isinstance(width_a, int)
            and isinstance(height_a, int)
            and width_a > 0
            and height_a > 0
        ):
            min_w, max_w = LikenessWorker._range_with_ratio(
                width_a, LikenessWorker.MAX_DIM_RATIO_DIFF
            )
            min_h, max_h = LikenessWorker._range_with_ratio(
                height_a, LikenessWorker.MAX_DIM_RATIO_DIFF
            )
            query = query.where(
                (Picture.width >= min_w)
                & (Picture.width <= max_w)
                & (Picture.height >= min_h)
                & (Picture.height <= max_h)
            )
        if isinstance(size_a, int) and size_a > 0:
            min_s, max_s = LikenessWorker._range_with_ratio(
                size_a, LikenessWorker.MAX_SIZE_RATIO_DIFF
            )
            query = query.where(
                (Picture.size_bytes >= min_s) & (Picture.size_bytes <= max_s)
            )
        return session.exec(query.order_by(Picture.id).limit(limit)).all()

    @staticmethod
    def _write_results(
        session: Session,
        likeness_results: List[PictureLikeness],
        updates: List[Tuple[int, int]],
        top_k: int,
    ) -> None:
        PictureLikeness.bulk_insert_ignore(session, likeness_results)
        for update_a, update_b in updates:
            pf = session.get(PictureLikenessFrontier, update_a)
            if pf is None:
                session.add(
                    PictureLikenessFrontier(
                        picture_id_a=update_a,
                        j_max=max(update_a, update_b),
                    )
                )
            else:
                pf.j_max = max(update_a, update_b)
                session.add(pf)
            PictureLikeness.prune_below_top_k(session, update_a, top_k)
        session.commit()

    @staticmethod
    def _range_with_ratio(value: int, ratio: float) -> Tuple[int, int]:
        delta = max(1, int(round(value * ratio)))
        return max(1, value - delta), value + delta

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
    def _phash_similarity(cls, hash_a: str, hash_b: str) -> float:
        try:
            int_a = int(hash_a, 16)
            int_b = int(hash_b, 16)
        except Exception:
            return 0.0
        distance = (int_a ^ int_b).bit_count()
        return 1.0 - (distance / float(cls.PHASH_BITS))

    @classmethod
    def _passes_metadata_filter(
        cls,
        width_a: Optional[int],
        height_a: Optional[int],
        size_a: Optional[int],
        width_b: Optional[int],
        height_b: Optional[int],
        size_b: Optional[int],
    ) -> bool:
        if (
            isinstance(width_a, int)
            and isinstance(height_a, int)
            and isinstance(width_b, int)
            and isinstance(height_b, int)
            and width_a > 0
            and height_a > 0
            and width_b > 0
            and height_b > 0
        ):
            width_ratio = abs(width_a - width_b) / max(width_a, width_b)
            height_ratio = abs(height_a - height_b) / max(height_a, height_b)
            if width_ratio > cls.MAX_DIM_RATIO_DIFF:
                return False
            if height_ratio > cls.MAX_DIM_RATIO_DIFF:
                return False
            aspect_a = width_a / float(height_a)
            aspect_b = width_b / float(height_b)
            aspect_ratio = abs(aspect_a - aspect_b) / max(aspect_a, aspect_b)
            if aspect_ratio > cls.MAX_ASPECT_RATIO_DIFF:
                return False

        if (
            isinstance(size_a, int)
            and isinstance(size_b, int)
            and size_a > 0
            and size_b > 0
        ):
            size_ratio = abs(size_a - size_b) / max(size_a, size_b)
            if size_ratio > cls.MAX_SIZE_RATIO_DIFF:
                return False

        return True

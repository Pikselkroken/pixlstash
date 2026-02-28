from __future__ import annotations

import math
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import func
from sqlmodel import Session, delete, select

from pixlvault.pixl_logging import get_logger
from pixlvault.db_models.picture import (
    LIKENESS_PARAMETER_SENTINEL,
    LikenessParameter,
    Picture,
)
from pixlvault.db_models.picture_likeness import (
    PictureLikeness,
    PictureLikenessQueue,
)

logger = get_logger(__name__)


class PictureLikenessUtils:
    """Speed-focused likeness utilities for stacking near-identical images."""

    BATCH_CANDIDATES = 1024
    MAX_A_PER_CYCLE = 256
    YIELD_SLEEP_SECONDS = 0.0

    PHASH_PREFIX_LEN = 3
    PHASH_MIN_SIM = 0.45
    EMBEDDING_MIN_SIM = 0.9
    LIKENESS_GAMMA = 2.0

    PARAM_GAP_PERCENTILE = 80
    PARAM_THRESHOLD_SAMPLE_LIMIT = 5000
    MIN_PARAM_OVERLAP = 1
    DATE_WINDOW_FRACTION = 0.004
    DATE_MAX_NEIGHBORS = 30
    BULK_MAX_WINDOW_SIZE = 60
    BULK_GAP_PERCENTILE = 60

    MAX_DIM_RATIO_DIFF = 0.2
    MAX_ASPECT_RATIO_DIFF = 0.1
    MAX_SIZE_RATIO_DIFF = 0.3

    TOP_K = 200
    PHASH_BITS = 64

    GATING_PARAMS = tuple(
        param
        for param in LikenessParameter
        if param
        not in {
            LikenessParameter.SIZE_BIN,
            LikenessParameter.PHASH_PREFIX,
            LikenessParameter.DATE,
        }
    )

    def __init__(self, database):
        self._db = database

    @staticmethod
    def get_next_work_batch(
        session: Session, max_a: int
    ) -> List[
        Tuple[
            int,
            int,
            int,
            Optional[str],
            Optional[int],
            Optional[int],
            Optional[int],
            Optional[bytes],
            Optional[object],
            Optional[bytes],
        ]
    ]:
        rows = session.exec(
            select(
                PictureLikenessQueue.picture_id,
                Picture.perceptual_hash,
                Picture.width,
                Picture.height,
                Picture.size_bytes,
                Picture.likeness_parameters,
                Picture.created_at,
                Picture.image_embedding,
            )
            .join(Picture, Picture.id == PictureLikenessQueue.picture_id)
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.likeness_parameters.is_not(None))
            .where(Picture.perceptual_hash.is_not(None))
            .order_by(PictureLikenessQueue.queued_at)
            .limit(max_a)
        ).all()
        if not rows:
            return []

        queued_ids = [int(row[0]) for row in rows]
        if queued_ids:
            session.exec(
                delete(PictureLikenessQueue).where(
                    PictureLikenessQueue.picture_id.in_(queued_ids)
                )
            )
            session.commit()

        batch = []
        for row in rows:
            (
                pic_id,
                phash_a,
                width_a,
                height_a,
                size_a,
                params_blob,
                created_at,
                emb_blob,
            ) = row
            batch.append(
                (
                    int(pic_id),
                    None,
                    None,
                    phash_a,
                    width_a,
                    height_a,
                    size_a,
                    params_blob,
                    created_at,
                    emb_blob,
                )
            )

        return batch

    @staticmethod
    def fetch_bulk_candidate_data(session: Session) -> List[Tuple]:
        return session.exec(
            select(
                Picture.id,
                Picture.likeness_parameters,
                Picture.image_embedding,
                Picture.perceptual_hash,
                Picture.created_at,
                Picture.width,
                Picture.height,
                Picture.size_bytes,
            )
            .where(Picture.deleted.is_(False))
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.likeness_parameters.is_not(None))
            .where(Picture.perceptual_hash.is_not(None))
        ).all()

    def compute_bulk_likeness(
        self,
        queued_ids: List[int],
        rows: List[Tuple],
        param_thresholds: Optional[Dict[LikenessParameter, float]],
        date_span_seconds: Optional[float],
    ) -> List[PictureLikeness]:
        if not rows or not queued_ids:
            return []
        queued_set = set(int(pid) for pid in queued_ids)
        ids = [int(row[0]) for row in rows]
        vectors = [
            self._decode_likeness_parameters(row[1], len(LikenessParameter))
            for row in rows
        ]
        embeddings = {int(row[0]): self._decode_embedding(row[2]) for row in rows}
        phash_by_id = {int(row[0]): str(row[3]) for row in rows if row[3]}

        n = len(ids)
        candidate_counts: Dict[Tuple[int, int], int] = {}
        params = [
            param
            for param in self.GATING_PARAMS
            if param not in {LikenessParameter.PHASH_PREFIX, LikenessParameter.DATE}
        ]

        for param in params:
            param_index = int(param)
            values = [
                vec[param_index] if vec is not None else LIKENESS_PARAMETER_SENTINEL
                for vec in vectors
            ]
            sorted_indices = sorted(
                range(n),
                key=lambda i: (not math.isfinite(values[i]), values[i]),
            )
            values_sorted = [values[i] for i in sorted_indices]
            diffs = []
            for idx in range(1, n):
                prev_val = values_sorted[idx - 1]
                curr_val = values_sorted[idx]
                if not math.isfinite(prev_val) or not math.isfinite(curr_val):
                    continue
                diff = curr_val - prev_val
                if diff >= 0:
                    diffs.append(diff)
            gap_threshold = (
                float(np.percentile(diffs, self.BULK_GAP_PERCENTILE)) if diffs else 0.0
            )
            for position, i in enumerate(sorted_indices):
                value_a = values_sorted[position]
                if not math.isfinite(value_a):
                    continue
                upper = min(position + self.BULK_MAX_WINDOW_SIZE, n - 1)
                id_a = ids[i]
                for neighbor_pos in range(position + 1, upper + 1):
                    value_b = values_sorted[neighbor_pos]
                    if not math.isfinite(value_b):
                        break
                    if (value_b - value_a) > gap_threshold:
                        break
                    j = sorted_indices[neighbor_pos]
                    id_b = ids[j]
                    if id_a not in queued_set and id_b not in queued_set:
                        continue
                    a_id, b_id = PictureLikeness.canon_pair(id_a, id_b)
                    candidate_counts[(a_id, b_id)] = (
                        candidate_counts.get((a_id, b_id), 0) + 1
                    )

        if date_span_seconds:
            date_values = [
                vec[int(LikenessParameter.DATE)] if vec is not None else -1.0
                for vec in vectors
            ]
            finite_dates = [val for val in date_values if math.isfinite(val)]
            if finite_dates:
                min_date = min(finite_dates)
                max_date = max(finite_dates)
                date_span = max_date - min_date
                max_gap = date_span * self.DATE_WINDOW_FRACTION
                if max_gap > 0:
                    date_sorted_indices = sorted(
                        range(n),
                        key=lambda i: (
                            not math.isfinite(date_values[i]),
                            date_values[i],
                        ),
                    )
                    for position, i in enumerate(date_sorted_indices):
                        date_a = date_values[i]
                        if not math.isfinite(date_a):
                            continue
                        id_a = ids[i]
                        upper = min(position + self.DATE_MAX_NEIGHBORS, n - 1)
                        for neighbor_pos in range(position + 1, upper + 1):
                            j = date_sorted_indices[neighbor_pos]
                            date_b = date_values[j]
                            if not math.isfinite(date_b):
                                break
                            if (date_b - date_a) > max_gap:
                                break
                            id_b = ids[j]
                            if id_a not in queued_set and id_b not in queued_set:
                                continue
                            a_id, b_id = PictureLikeness.canon_pair(id_a, id_b)
                            candidate_counts[(a_id, b_id)] = (
                                candidate_counts.get((a_id, b_id), 0) + 1
                            )

        candidate_pairs = {
            pair
            for pair, count in candidate_counts.items()
            if count >= self.MIN_PARAM_OVERLAP
        }

        results: List[PictureLikeness] = []
        for a_id, b_id in candidate_pairs:
            if a_id not in queued_set and b_id not in queued_set:
                continue
            phash_a = phash_by_id.get(a_id)
            phash_b = phash_by_id.get(b_id)
            if not phash_a or not phash_b:
                continue
            if self._phash_similarity(phash_a, phash_b) < self.PHASH_MIN_SIM:
                continue
            emb_a = embeddings.get(a_id)
            emb_b = embeddings.get(b_id)
            if emb_a is None or emb_b is None or emb_a.shape != emb_b.shape:
                continue
            norm_a = np.linalg.norm(emb_a)
            norm_b = np.linalg.norm(emb_b)
            if norm_a == 0 or norm_b == 0:
                continue
            sim = float((emb_a / norm_a) @ (emb_b / norm_b))
            sim = float(np.clip(sim, -1.0, 1.0))
            likeness = 0.5 * (sim + 1.0)
            if self.LIKENESS_GAMMA != 1.0:
                likeness = float(pow(max(likeness, 0.0), self.LIKENESS_GAMMA))
            if likeness > 1.0:
                likeness = 1.0
            elif likeness < 0.0:
                likeness = 0.0
            if likeness < self.EMBEDDING_MIN_SIM:
                continue
            results.append(
                PictureLikeness(
                    picture_id_a=a_id,
                    picture_id_b=b_id,
                    likeness=likeness,
                    metric="image_embedding",
                )
            )

        return results

    @staticmethod
    def write_results(
        session: Session,
        likeness_results: List[PictureLikeness],
        top_k: int,
    ) -> None:
        PictureLikeness.bulk_insert_ignore(session, likeness_results)
        processed_as = {pl.picture_id_a for pl in likeness_results}
        for a_id in processed_as:
            PictureLikeness.prune_below_top_k(session, a_id, top_k)
        session.commit()

    @staticmethod
    def seed_queue(session: Session) -> None:
        queued_count = session.exec(
            select(func.count()).select_from(PictureLikenessQueue)
        ).one()
        if queued_count and int(queued_count) > 0:
            return
        likeness_count = session.exec(
            select(func.count()).select_from(PictureLikeness)
        ).one()
        if likeness_count and int(likeness_count) > 0:
            return
        rows = session.exec(select(Picture.id)).all()
        ids = [
            int(row[0]) if isinstance(row, (tuple, list)) else int(row) for row in rows
        ]
        PictureLikenessQueue.enqueue(session, ids)
        session.commit()

    @classmethod
    def compute_param_gap_thresholds(
        cls, session: Session, percentile: int, sample_limit: int
    ) -> Dict[LikenessParameter, float]:
        rows = session.exec(
            select(Picture.likeness_parameters)
            .where(Picture.likeness_parameters.is_not(None))
            .limit(sample_limit)
        ).all()
        if not rows:
            return {}
        values_by_param: Dict[LikenessParameter, List[float]] = {
            param: [] for param in cls.GATING_PARAMS
        }
        for row in rows:
            blob = row[0] if isinstance(row, (tuple, list)) else row
            vec = cls._decode_likeness_parameters(blob, len(LikenessParameter))
            if vec is None:
                continue
            for param in cls.GATING_PARAMS:
                value = float(vec[int(param)])
                if value == LIKENESS_PARAMETER_SENTINEL or not math.isfinite(value):
                    continue
                values_by_param[param].append(value)

        thresholds: Dict[LikenessParameter, float] = {}
        for param, values in values_by_param.items():
            if len(values) < 2:
                continue
            values.sort()
            diffs = []
            prev = values[0]
            for value in values[1:]:
                diff = value - prev
                if diff >= 0:
                    diffs.append(diff)
                prev = value
            if diffs:
                thresholds[param] = float(np.percentile(diffs, percentile))
        return thresholds

    @staticmethod
    def compute_date_span_seconds(session: Session) -> Optional[float]:
        row = session.exec(
            select(func.min(Picture.created_at), func.max(Picture.created_at))
        ).first()
        if not row:
            return None
        min_date, max_date = row
        if min_date is None or max_date is None:
            return None
        span = max_date - min_date
        return float(span.total_seconds())

    @staticmethod
    def decode_embedding(blob) -> Optional[np.ndarray]:
        return PictureLikenessUtils._decode_embedding(blob)

    @staticmethod
    def decode_likeness_parameters(
        blob: Optional[object], length: int
    ) -> Optional[np.ndarray]:
        return PictureLikenessUtils._decode_likeness_parameters(blob, length)

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

    @staticmethod
    def _decode_likeness_parameters(
        blob: Optional[object], length: int
    ) -> Optional[np.ndarray]:
        if blob is None:
            return None
        if isinstance(blob, np.ndarray):
            if blob.size == length:
                return blob.astype(np.float32, copy=False)
            return None
        if isinstance(blob, (bytes, bytearray, memoryview)):
            data = np.frombuffer(blob, dtype=np.float32)
            if data.size == length:
                return data.copy()
            return None
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

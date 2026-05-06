"""Likeness computation utilities for stacking near-identical images."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import func
from sqlmodel import Session, delete, select

from pixlstash.pixl_logging import get_logger
from pixlstash.db_models.picture import (
    LIKENESS_PARAMETER_SENTINEL,
    LikenessParameter,
    Picture,
)
from pixlstash.db_models.picture_likeness import (
    PictureLikeness,
    PictureLikenessQueue,
)

logger = get_logger(__name__)


@dataclass
class BulkCandidateArrays:
    """Pre-decoded, cached candidate data passed to every LikenessTask in a sweep.

    Built once per sweep from ``build_bulk_arrays``; all arrays are read-only
    and can be shared safely across concurrent tasks.
    """

    ids: np.ndarray            # (N,) int64 — picture IDs
    param_matrix: np.ndarray   # (N, num_params) float32
    emb_norm: np.ndarray       # (N, emb_dim) float32 — L2-normalised
    phash_vec: np.ndarray      # (N,) uint64 — integer phash values for vectorised XOR
    id_to_idx: Dict[int, int]  # id → row index in the arrays


class LikenessUtils:
    """Speed-focused likeness utilities for stacking near-identical images."""

    BATCH_CANDIDATES = 1024
    MAX_A_PER_CYCLE = 2048
    YIELD_SLEEP_SECONDS = 0.0

    PHASH_PREFIX_LEN = 3
    PHASH_MIN_SIM = 0.45
    EMBEDDING_MIN_SIM = 0.82
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
    def get_next_work_batch(session: Session, max_a: int) -> List[Tuple]:
        """Fetch the next batch of pictures from the likeness queue."""
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
        """Fetch all candidate pictures for bulk likeness computation."""
        return session.exec(
            select(
                Picture.id,
                Picture.likeness_parameters,
                Picture.image_embedding,
                Picture.perceptual_hash,
            )
            .where(Picture.deleted.is_(False))
            .where(Picture.image_embedding.is_not(None))
            .where(Picture.likeness_parameters.is_not(None))
            .where(Picture.perceptual_hash.is_not(None))
        ).all()

    @staticmethod
    def build_bulk_arrays(session: Session) -> Optional["BulkCandidateArrays"]:
        """Fetch and decode all candidates into pre-computed numpy arrays.

        Called once per sweep by the finder and cached.  Decoding the 28k
        embedding blobs (2 KB each) takes ~200-500 ms; caching removes this
        cost from every subsequent task in the same sweep.
        """
        rows = LikenessUtils.fetch_bulk_candidate_data(session)
        return LikenessUtils._decode_bulk_rows(rows)

    @staticmethod
    def _decode_bulk_rows(rows: List[Tuple]) -> Optional["BulkCandidateArrays"]:
        """Decode raw DB rows into a ``BulkCandidateArrays`` struct."""
        if not rows:
            return None
        num_params = len(LikenessParameter)
        ids_list: List[int] = []
        param_rows: List[np.ndarray] = []
        emb_rows: List[np.ndarray] = []
        phash_list: List[int] = []

        for row in rows:
            pic_id = int(row[0])
            vec = LikenessUtils._decode_likeness_parameters(row[1], num_params)
            emb = LikenessUtils._decode_embedding(row[2])
            phash_str = str(row[3]) if row[3] else None
            if vec is None or emb is None or not phash_str:
                continue
            try:
                phash_val = int(phash_str, 16)
            except ValueError:
                continue
            ids_list.append(pic_id)
            param_rows.append(vec)
            emb_rows.append(emb)
            phash_list.append(phash_val)

        if not ids_list:
            return None

        ids_arr = np.array(ids_list, dtype=np.int64)
        param_matrix = np.stack(param_rows)                      # (N, num_params)
        emb_matrix = np.stack(emb_rows)                          # (N, emb_dim)
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        safe_norms = np.where(norms > 0, norms, 1.0)
        emb_norm = emb_matrix / safe_norms
        emb_norm[norms.ravel() == 0] = 0.0
        phash_vec = np.array(phash_list, dtype=np.uint64)
        id_to_idx = {pid: i for i, pid in enumerate(ids_list)}

        return BulkCandidateArrays(
            ids=ids_arr,
            param_matrix=param_matrix,
            emb_norm=emb_norm,
            phash_vec=phash_vec,
            id_to_idx=id_to_idx,
        )

    def compute_bulk_likeness(
        self,
        queued_ids: List[int],
        bulk: "BulkCandidateArrays",
    ) -> List[PictureLikeness]:
        """Compute bulk likeness relationships using vectorised numpy operations.

        Takes pre-decoded ``BulkCandidateArrays`` (built once per sweep by the
        finder) so blobs are never re-decoded per task.  The sliding-window
        pair-finding uses numpy offset subtraction rather than Python loops.
        """
        if bulk is None or not queued_ids:
            return []

        ids_arr = bulk.ids
        param_matrix = bulk.param_matrix
        emb_norm = bulk.emb_norm
        phash_vec = bulk.phash_vec
        id_to_idx = bulk.id_to_idx

        queued_ids_arr = np.array(list(queued_ids), dtype=np.int64)
        queued_mask = np.isin(ids_arr, queued_ids_arr)   # (N,) bool — no Python loop
        n = len(ids_arr)

        # ------------------------------------------------------------------ #
        # Sliding-window pair accumulation — fully numpy, no Python dict.     #
        #                                                                      #
        # Each (param, k) combination yields a small numpy array of valid     #
        # pair int64-encodings: (canon_a << 32) | canon_b.  All arrays are    #
        # concatenated into one, then np.unique counts occurrences in C ─     #
        # replacing the Python dict O(M) update loop with a single numpy sort.
        # ------------------------------------------------------------------ #
        pair_chunks: List[np.ndarray] = []   # each chunk: 1-D int64 encoded pairs

        params_to_scan = [
            param
            for param in self.GATING_PARAMS
            if param not in {LikenessParameter.PHASH_PREFIX, LikenessParameter.DATE}
        ]

        for param in params_to_scan:
            values = param_matrix[:, int(param)]   # (N,) view
            finite_mask = np.isfinite(values)
            finite_vals = values[finite_mask]
            if len(finite_vals) < 2:
                continue

            sorted_fv = np.sort(finite_vals)
            pos_diffs = np.diff(sorted_fv)
            pos_diffs = pos_diffs[pos_diffs >= 0]
            gap_threshold = (
                float(np.percentile(pos_diffs, self.BULK_GAP_PERCENTILE))
                if len(pos_diffs)
                else 0.0
            )

            sort_key = np.where(finite_mask, values, np.inf)
            order = np.argsort(sort_key, kind="stable")
            sv = values[order]
            si = ids_arr[order]
            sq = queued_mask[order]
            sf = np.isfinite(sv)

            for k in range(1, min(self.BULK_MAX_WINDOW_SIZE + 1, n)):
                diffs_k = sv[k:] - sv[:-k]
                valid = (
                    sf[:-k] & sf[k:]
                    & (diffs_k >= 0)
                    & (diffs_k <= gap_threshold)
                    & (sq[:-k] | sq[k:])
                )
                pos_i = np.nonzero(valid)[0]
                if pos_i.size == 0:
                    continue
                a_ids = si[pos_i]
                b_ids = si[pos_i + k]
                # Encode as single int64: low 32 bits = min(a,b), high 32 bits = max(a,b)
                lo = np.minimum(a_ids, b_ids).astype(np.int64)
                hi = np.maximum(a_ids, b_ids).astype(np.int64)
                pair_chunks.append((hi << 32) | lo)

        # Date window.
        date_vals = param_matrix[:, int(LikenessParameter.DATE)]
        finite_date = np.isfinite(date_vals)
        finite_date_vals = date_vals[finite_date]
        if len(finite_date_vals) >= 2:
            date_span = float(np.max(finite_date_vals) - np.min(finite_date_vals))
            max_gap = date_span * self.DATE_WINDOW_FRACTION
            if max_gap > 0:
                sort_key_d = np.where(finite_date, date_vals, np.inf)
                order_d = np.argsort(sort_key_d, kind="stable")
                sd = date_vals[order_d]
                si_d = ids_arr[order_d]
                sq_d = queued_mask[order_d]
                sf_d = np.isfinite(sd)

                for k in range(1, min(self.DATE_MAX_NEIGHBORS + 1, n)):
                    diffs_k = sd[k:] - sd[:-k]
                    in_window = sf_d[:-k] & sf_d[k:] & (diffs_k <= max_gap)
                    if not np.any(in_window):
                        break
                    valid = in_window & (sq_d[:-k] | sq_d[k:])
                    pos_i = np.nonzero(valid)[0]
                    if pos_i.size == 0:
                        continue
                    a_ids = si_d[pos_i]
                    b_ids = si_d[pos_i + k]
                    lo = np.minimum(a_ids, b_ids).astype(np.int64)
                    hi = np.maximum(a_ids, b_ids).astype(np.int64)
                    pair_chunks.append((hi << 32) | lo)

        if not pair_chunks:
            return []

        # Count occurrences entirely in numpy — no Python dict.
        all_encoded = np.concatenate(pair_chunks)      # 1-D int64
        unique_encoded, counts = np.unique(all_encoded, return_counts=True)
        keep = unique_encoded[counts >= self.MIN_PARAM_OVERLAP]
        if keep.size == 0:
            return []

        # Decode back to (a, b) pairs — both are in id_to_idx by construction.
        cand_a = keep & np.int64(0xFFFFFFFF)           # low 32 bits
        cand_b = (keep >> 32) & np.int64(0xFFFFFFFF)   # high 32 bits

        # ------------------------------------------------------------------ #
        # Vectorised phash filter: XOR int64s, popcount, threshold.          #
        # ------------------------------------------------------------------ #
        a_idx = np.array([id_to_idx[int(a)] for a in cand_a.tolist()], dtype=np.int64)
        b_idx = np.array([id_to_idx[int(b)] for b in cand_b.tolist()], dtype=np.int64)

        xor = phash_vec[a_idx] ^ phash_vec[b_idx]       # (K,) uint64
        # Vectorised Hamming weight (SWAR popcount).
        v = xor.copy()
        v = v - ((v >> np.uint64(1)) & np.uint64(0x5555555555555555))
        v = (v & np.uint64(0x3333333333333333)) + ((v >> np.uint64(2)) & np.uint64(0x3333333333333333))
        v = (v + (v >> np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
        hamming = ((v * np.uint64(0x0101010101010101)) >> np.uint64(56)).astype(np.float32)
        phash_sim = 1.0 - hamming / float(self.PHASH_BITS)
        phash_keep = phash_sim >= self.PHASH_MIN_SIM

        if not np.any(phash_keep):
            return []

        # ------------------------------------------------------------------ #
        # Batch embedding similarity.                                         #
        # ------------------------------------------------------------------ #
        a_idx_f = a_idx[phash_keep]
        b_idx_f = b_idx[phash_keep]
        sims = np.sum(emb_norm[a_idx_f] * emb_norm[b_idx_f], axis=1)
        sims = np.clip(sims, -1.0, 1.0)
        likeness_vals = 0.5 * (sims + 1.0)
        if self.LIKENESS_GAMMA != 1.0:
            likeness_vals = np.power(np.maximum(likeness_vals, 0.0), self.LIKENESS_GAMMA)
        likeness_vals = np.clip(likeness_vals, 0.0, 1.0)

        cand_a_f = cand_a[phash_keep]
        cand_b_f = cand_b[phash_keep]
        likeness_filter = likeness_vals >= self.EMBEDDING_MIN_SIM

        results: List[PictureLikeness] = [
            PictureLikeness(
                picture_id_a=int(a),
                picture_id_b=int(b),
                likeness=float(lv),
                metric="image_embedding",
            )
            for a, b, lv in zip(
                cand_a_f[likeness_filter].tolist(),
                cand_b_f[likeness_filter].tolist(),
                likeness_vals[likeness_filter].tolist(),
            )
        ]
        return results

    @staticmethod
    def write_results(
        session: Session,
        likeness_results: List[PictureLikeness],
        top_k: int,
    ) -> None:
        """Persist likeness results and prune below top-k in a single batched SQL."""
        logger.debug(
            "LikenessTask: writing %d candidate pairs (top_k=%d, unique_a=%d)",
            len(likeness_results),
            top_k,
            len({pl.picture_id_a for pl in likeness_results}),
        )
        PictureLikeness.bulk_insert_ignore(session, likeness_results)
        processed_as = list({pl.picture_id_a for pl in likeness_results})
        if processed_as:
            PictureLikeness.bulk_prune_below_top_k(session, processed_as, top_k)
        session.commit()

    @staticmethod
    def seed_queue(session: Session) -> None:
        """Seed the likeness queue with all pictures if it is empty."""
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
        logger.debug("LikenessTask: seeding queue with %d pictures", len(ids))
        PictureLikenessQueue.enqueue(session, ids)
        session.commit()

    @classmethod
    def compute_param_gap_thresholds(
        cls, session: Session, percentile: int, sample_limit: int
    ) -> Dict[LikenessParameter, float]:
        """Compute per-parameter gap thresholds from a sample of the database."""
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
        """Return the total date span (in seconds) across all pictures, or None."""
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
        """Decode a raw embedding blob to a numpy array."""
        return LikenessUtils._decode_embedding(blob)

    @staticmethod
    def decode_likeness_parameters(
        blob: Optional[object], length: int
    ) -> Optional[np.ndarray]:
        """Decode a raw likeness parameter blob to a numpy array."""
        return LikenessUtils._decode_likeness_parameters(blob, length)

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
        """Return the normalised perceptual hash similarity between two hex hashes."""
        try:
            int_a = int(hash_a, 16)
            int_b = int(hash_b, 16)
        except Exception:
            return 0.0
        distance = (int_a ^ int_b).bit_count()
        return 1.0 - (distance / float(cls.PHASH_BITS))

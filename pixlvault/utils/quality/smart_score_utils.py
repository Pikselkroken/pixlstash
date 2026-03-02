"""Smart score calculation utilities."""

from typing import List, Optional

import numpy as np

from pixlvault.pixl_logging import get_logger

logger = get_logger(__name__)


class SmartScoreUtils:
    """Utility methods for computing smart scores over picture candidates."""

    @staticmethod
    def calculate_smart_score_batch_numpy(
        candidates: List[dict],
        good_anchors: List[dict],
        bad_anchors: List[dict],
        config: Optional[dict] = None,
    ) -> np.ndarray:
        """
        Calculate smart scores for a batch of candidates using numpy.

        Args:
            candidates: List of dicts with 'id', 'embedding' (numpy array), 'aesthetic_score'.
            good_anchors: List of dicts with 'embedding', 'score'.
            bad_anchors: List of dicts with 'embedding', 'score'.
            config: Config dict overrides.

        Returns:
            np.ndarray: Array of floating point scores corresponding to candidates.
        """
        cfg = {
            "w_good": 0.60,
            "w_bad": 0.20,
            "w_aest": 0.2,
            "w_resolution": 0.06,
            "w_noise": 0.08,
            "w_edge": 0.05,
            "w_tags": 0.08,
            "w_penalised_tag": 0.40,
            "penalised_tag_cap": 3.5,
            "tag_bonus_cap": 10,
            "topk": 3,
            "minSim": 0.75,
            "minBadSim": 0.88,
            "aest_min": 2.0,
            "aest_max": 7.0,
            "res_min_mpx": 0.2,
            "res_max_mpx": 4.0,
            "res_use_log": True,
        }
        if config:
            cfg.update(config)

        if not candidates:
            return np.array([])

        target_shape = None
        for c in candidates:
            emb = c.get("embedding")
            if isinstance(emb, np.ndarray) and emb.ndim == 1 and emb.size > 0:
                target_shape = emb.shape
                break

        if target_shape is None:
            return np.zeros(len(candidates))

        invalid_embeddings = 0
        cand_vecs = []
        for c in candidates:
            emb = c.get("embedding")
            if (
                isinstance(emb, np.ndarray)
                and emb.ndim == 1
                and emb.shape == target_shape
            ):
                cand_vecs.append(emb.astype(np.float32, copy=False))
            else:
                cand_vecs.append(np.zeros(target_shape, dtype=np.float32))
                invalid_embeddings += 1

        if invalid_embeddings:
            logger.warning(
                "[SMART SCORE] %s candidates had invalid/mismatched embeddings; using zeros.",
                invalid_embeddings,
            )

        raw_aest = np.array(
            [float(c.get("aesthetic_score") or 5.0) for c in candidates]
        )

        a_min = cfg.get("aest_min", 3.0)
        a_max = cfg.get("aest_max", 7.0)
        denom = max(0.1, a_max - a_min)
        cand_aest = np.clip((raw_aest - a_min) / denom, 0.0, 1.0)

        M_cand = np.stack(cand_vecs)
        scores = np.zeros(len(candidates))

        def norm_weight(s):
            effective = s if s > 0 else 2.5
            return max(0, min(1, (effective - 1) / 4.0))

        def sim01_batch(A, B):
            return 0.5 * (1 + np.dot(A, B.T))

        # Good anchors
        good_component = np.zeros(len(candidates))
        M_good = None
        if good_anchors:
            good_pairs = [
                (a["embedding"], a.get("score", 0))
                for a in good_anchors
                if isinstance(a.get("embedding"), np.ndarray)
                and a["embedding"].ndim == 1
                and a["embedding"].shape == target_shape
            ]
            if good_pairs:
                good_vecs = [p[0] for p in good_pairs]
                good_weights = np.array([norm_weight(p[1]) for p in good_pairs])
                M_good = np.stack(good_vecs)

        if good_anchors and M_good is not None:
            sims = sim01_batch(M_cand, M_good)
            max_raw = np.max(sims, axis=1)
            mask_good = max_raw >= cfg["minSim"]
            weighted = sims * good_weights
            K = min(cfg["topk"], weighted.shape[1])
            if K > 0:
                if K < weighted.shape[1]:
                    topk = -np.partition(-weighted, K - 1, axis=1)[:, :K]
                else:
                    topk = weighted
                avg_good = np.mean(np.abs(topk), axis=1)
                good_component = cfg["w_good"] * (avg_good * mask_good)
                scores += good_component

        # Bad anchors
        bad_component = np.zeros(len(candidates))
        M_bad = None
        if bad_anchors:
            bad_pairs = [
                (a["embedding"], a.get("score", 0))
                for a in bad_anchors
                if isinstance(a.get("embedding"), np.ndarray)
                and a["embedding"].ndim == 1
                and a["embedding"].shape == target_shape
            ]
            if bad_pairs:
                bad_vecs = [p[0] for p in bad_pairs]
                bad_weights = np.array([1.0 - norm_weight(p[1]) for p in bad_pairs])
                M_bad = np.stack(bad_vecs)

        if bad_anchors and M_bad is not None:
            sims = sim01_batch(M_cand, M_bad)
            max_raw_bad = np.max(sims, axis=1)
            mask_bad = max_raw_bad >= cfg["minBadSim"]
            weighted = sims * bad_weights
            K = min(cfg["topk"], weighted.shape[1])
            if K > 0:
                if K < weighted.shape[1]:
                    topk = -np.partition(-weighted, K - 1, axis=1)[:, :K]
                else:
                    topk = weighted
                avg_bad = np.mean(np.abs(topk), axis=1)
                bad_component = cfg["w_bad"] * (avg_bad * mask_bad)
                scores -= bad_component

        # Aesthetic
        aest_component = cfg["w_aest"] * cand_aest
        scores += aest_component

        # Resolution (megapixels)
        widths = np.array([c.get("width") or 0 for c in candidates], dtype=np.float32)
        heights = np.array([c.get("height") or 0 for c in candidates], dtype=np.float32)
        mpx = (widths * heights) / 1_000_000.0
        res_min = float(cfg.get("res_min_mpx", 0.2) or 0.2)
        res_max = float(cfg.get("res_max_mpx", 4.0) or 4.0)
        res_min = max(0.01, res_min)
        res_max = max(res_min + 0.01, res_max)
        if cfg.get("res_use_log", True):
            res_vals = np.log10(np.clip(mpx, 1e-6, None))
            res_min_val = np.log10(res_min)
            res_max_val = np.log10(res_max)
        else:
            res_vals = mpx
            res_min_val = res_min
            res_max_val = res_max
        denom_res = max(0.001, res_max_val - res_min_val)
        res_norm = np.clip((res_vals - res_min_val) / denom_res, 0.0, 1.0)
        res_component = cfg["w_resolution"] * res_norm
        scores += res_component

        # Noise
        noise_vals = np.array(
            [c.get("noise_level") for c in candidates], dtype=np.float32
        )
        noise_vals = np.where(np.isfinite(noise_vals), noise_vals, np.nan)
        noise_vals = np.where(noise_vals < 0, np.nan, noise_vals)
        noise_vals = np.where(np.isnan(noise_vals), 0.5, noise_vals)
        noise_vals = np.clip(noise_vals, 0.0, 1.0)
        noise_component = cfg["w_noise"] * (1.0 - noise_vals)
        scores += noise_component

        # Edge density
        edge_vals = np.array(
            [c.get("edge_density") for c in candidates], dtype=np.float32
        )
        edge_vals = np.where(np.isfinite(edge_vals), edge_vals, np.nan)
        edge_vals = np.where(edge_vals < 0, np.nan, edge_vals)
        edge_vals = np.where(np.isnan(edge_vals), 0.5, edge_vals)
        edge_vals = np.clip(edge_vals, 0.0, 1.0)
        edge_component = cfg["w_edge"] * edge_vals
        scores += edge_component

        # Tag richness bonus
        tag_counts = np.array(
            [float(c.get("tag_count") or 0) for c in candidates], dtype=np.float32
        )
        tag_cap = max(1.0, float(cfg.get("tag_bonus_cap", 10) or 10))
        tag_norm = np.log1p(np.clip(tag_counts, 0.0, tag_cap)) / np.log1p(tag_cap)
        tag_component = cfg["w_tags"] * np.clip(tag_norm, 0.0, 1.0)
        scores += tag_component

        # Penalised tags
        penalised_counts = np.array(
            [float(c.get("penalised_tag_count") or 0) for c in candidates]
        )
        penalised_equivalent = np.clip(
            penalised_counts / 5.0, 0.0, float(cfg["penalised_tag_cap"])
        )
        penalised_component = cfg["w_penalised_tag"] * penalised_equivalent
        scores -= penalised_component

        # Rescale [0, 1] to [1, 5]
        clipped = np.clip(scores, 0.0, 1.0)
        final_scores = 1.0 + (clipped * 4.0)

        try:
            for idx, candidate in enumerate(candidates):
                penalised_count = float(penalised_counts[idx])
                final_score = float(final_scores[idx])
                if penalised_count <= 0 and final_score <= 1.5:
                    logger.info(
                        "[SMART SCORE][MIN] id=%s raw=%.4f clipped=%.4f final=%.4f "
                        "good=%.4f bad=%.4f aest=%.4f res=%.4f noise=%.4f "
                        "edge=%.4f tags=%.4f penalised=%.4f mpx=%.4f w=%s h=%s",
                        candidate.get("id"),
                        float(scores[idx]),
                        float(clipped[idx]),
                        final_score,
                        float(good_component[idx]),
                        float(bad_component[idx]),
                        float(aest_component[idx]),
                        float(res_component[idx]),
                        float(noise_component[idx]),
                        float(edge_component[idx]),
                        float(tag_component[idx]),
                        float(penalised_component[idx]),
                        float(mpx[idx]),
                        candidate.get("width"),
                        candidate.get("height"),
                    )
        except Exception as exc:
            logger.info("[SMART SCORE][MIN] logging failed: %s", exc)

        return final_scores

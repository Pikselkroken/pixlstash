"""Smart score calculation utilities."""

import json
from typing import List, Optional

import numpy as np

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def _smart_score_penalised_tags(
    value,
    fallback=None,
    allow_empty: bool = False,
    default_weight: int = 3,
):
    """Parse and normalise a smart-score penalised-tags value.

    Args:
        value: Raw value (JSON string, list, or dict).
        fallback: Returned when value is None or unparseable.
        allow_empty: If True, return an empty dict instead of fallback when the
            parsed result is empty.
        default_weight: Weight assigned to tags given as a plain list.

    Returns:
        A dict mapping lowercase tag name to int weight (1-5), or fallback.
    """
    if value is None:
        return fallback

    tags = None
    if isinstance(value, str):
        try:
            tags = json.loads(value)
        except Exception:
            return fallback
    else:
        tags = value

    if isinstance(tags, list):
        d = {}
        for tag in tags:
            if tag is None:
                continue
            clean = str(tag).strip().lower()
            if not clean:
                continue
            d[clean] = default_weight
    elif isinstance(tags, dict):
        d = {}
        for tag, weight in tags.items():
            if tag is None:
                continue
            clean = str(tag).strip().lower()
            if not clean:
                continue
            try:
                weight_value = int(float(weight))
            except (TypeError, ValueError):
                weight_value = default_weight
            weight_value = max(1, min(5, weight_value))
            existing = d.get(clean)
            if existing is None or weight_value > existing:
                d[clean] = weight_value
    else:
        return fallback

    if d:
        return d
    return {} if allow_empty else fallback


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
            "w_good": 0.13,
            "w_bad": 0.07,
            "w_aest": 0.35,
            "w_sharpness": 0.35,
            "w_resolution": 0.10,
            "w_detail": 0.16,
            "w_focus_presence": 0.16,
            "w_text_clutter": 0.30,
            "w_penalised_tag": 0.40,
            "penalised_tag_cap": 3.5,
            "topk": 3,
            "sim_knee": 0.3,
            "sim_power": 1.5,
            "bad_sim_knee": 0.3,
            "bad_sim_power": 1.5,
            "aest_min": 2.0,
            "aest_max": 7.0,
            "res_min_mpx": 0.2,
            "res_max_mpx": 4.0,
            "res_use_log": True,
            # Reward compositionally rich images using two objective cues:
            # 1) edge density and 2) luminance entropy.
            "edge_density_min": 0.015,
            "edge_density_max": 0.18,
            "entropy_min": 0.35,
            "entropy_max": 0.90,
            "focus_knee": 0.33,
            "focus_power": 1.4,
            # Penalise heavy text overlays while ignoring tiny/occasional text.
            "text_knee": 0.22,
            "text_power": 1.6,
            # Keep scores deterministic from per-image parameters alone.
            # Batch normalization can shift a picture's score based on what
            # other candidates are present in the same request.
            "batch_normalize": False,
            "batch_normalize_lo_pct": 5.0,
            "batch_normalize_hi_pct": 95.0,
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
            knee = float(cfg["sim_knee"])
            power = float(cfg["sim_power"])
            smooth = np.clip((sims - knee) / max(1e-6, 1.0 - knee), 0.0, 1.0) ** power
            weighted = smooth * good_weights
            K = min(cfg["topk"], weighted.shape[1])
            if K > 0:
                if K < weighted.shape[1]:
                    topk = -np.partition(-weighted, K - 1, axis=1)[:, :K]
                else:
                    topk = weighted
                avg_good = np.mean(topk, axis=1)
                good_component = cfg["w_good"] * avg_good
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
            bad_knee = float(cfg["bad_sim_knee"])
            bad_power = float(cfg["bad_sim_power"])
            smooth_bad = (
                np.clip((sims - bad_knee) / max(1e-6, 1.0 - bad_knee), 0.0, 1.0)
                ** bad_power
            )
            weighted = smooth_bad * bad_weights
            K = min(cfg["topk"], weighted.shape[1])
            if K > 0:
                if K < weighted.shape[1]:
                    topk = -np.partition(-weighted, K - 1, axis=1)[:, :K]
                else:
                    topk = weighted
                avg_bad = np.mean(topk, axis=1)
                bad_component = cfg["w_bad"] * avg_bad
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

        # Subject sharpness (high if at least one region is in focus)
        sharpness_vals = np.array(
            [c.get("sharpness") for c in candidates], dtype=np.float32
        )
        sharpness_vals = np.where(np.isfinite(sharpness_vals), sharpness_vals, np.nan)
        sharpness_vals = np.where(sharpness_vals < 0, np.nan, sharpness_vals)
        sharpness_vals = np.where(np.isnan(sharpness_vals), 0.5, sharpness_vals)
        sharpness_vals = np.clip(sharpness_vals, 0.0, 1.0)
        sharpness_component = cfg["w_sharpness"] * sharpness_vals
        scores += sharpness_component

        # Explicit in-focus presence: reward candidates with at least one clearly
        # sharp region using a thresholded smooth ramp on subject sharpness.
        focus_knee = float(cfg.get("focus_knee", 0.33))
        focus_knee = max(0.0, min(0.95, focus_knee))
        focus_power = max(0.1, float(cfg.get("focus_power", 1.4)))
        focus_signal = (
            np.clip(
                (sharpness_vals - focus_knee) / max(1e-6, 1.0 - focus_knee),
                0.0,
                1.0,
            )
            ** focus_power
        )
        focus_presence_component = cfg["w_focus_presence"] * focus_signal
        scores += focus_presence_component

        # Detail richness (objective): combine edge density and luminance entropy.
        edge_vals = np.array(
            [c.get("edge_density") for c in candidates], dtype=np.float32
        )
        edge_vals = np.where(np.isfinite(edge_vals), edge_vals, np.nan)
        ent_vals = np.array(
            [c.get("luminance_entropy") for c in candidates], dtype=np.float32
        )
        ent_vals = np.where(np.isfinite(ent_vals), ent_vals, np.nan)

        edge_min = float(cfg.get("edge_density_min", 0.015))
        edge_max = float(cfg.get("edge_density_max", 0.18))
        edge_max = max(edge_min + 1e-6, edge_max)
        edge_mid = edge_min + 0.5 * (edge_max - edge_min)
        edge_vals = np.where(np.isnan(edge_vals), edge_mid, edge_vals)
        edge_norm = np.clip((edge_vals - edge_min) / (edge_max - edge_min), 0.0, 1.0)

        ent_min = float(cfg.get("entropy_min", 0.35))
        ent_max = float(cfg.get("entropy_max", 0.90))
        ent_max = max(ent_min + 1e-6, ent_max)
        ent_mid = ent_min + 0.5 * (ent_max - ent_min)
        ent_vals = np.where(np.isnan(ent_vals), ent_mid, ent_vals)
        ent_norm = np.clip((ent_vals - ent_min) / (ent_max - ent_min), 0.0, 1.0)

        detail_signal = np.sqrt(edge_norm * ent_norm)
        detail_component = cfg["w_detail"] * detail_signal
        scores += detail_component

        # Text clutter penalty (objective): apply only above knee, then grow smoothly.
        text_vals = np.array(
            [c.get("text_score") for c in candidates], dtype=np.float32
        )
        text_vals = np.where(np.isfinite(text_vals), text_vals, np.nan)
        text_vals = np.where(np.isnan(text_vals), 0.0, text_vals)
        text_vals = np.clip(text_vals, 0.0, 1.0)
        text_knee = float(cfg.get("text_knee", 0.22))
        text_knee = max(0.0, min(0.95, text_knee))
        text_power = max(0.1, float(cfg.get("text_power", 1.6)))
        text_excess = (
            np.clip((text_vals - text_knee) / max(1e-6, 1.0 - text_knee), 0.0, 1.0)
            ** text_power
        )
        text_component = cfg["w_text_clutter"] * text_excess
        scores -= text_component

        # Penalised tags
        penalised_counts = np.array(
            [float(c.get("penalised_tag_count") or 0) for c in candidates]
        )
        penalised_equivalent = np.clip(
            penalised_counts / 5.0, 0.0, float(cfg["penalised_tag_cap"])
        )
        penalised_component = cfg["w_penalised_tag"] * penalised_equivalent
        scores -= penalised_component

        # Soft batch normalization: stretch scores so the distribution fills [0,1]
        # using robust percentiles so individual outliers don't dominate.
        if cfg.get("batch_normalize", False) and len(scores) >= 4:
            lo_pct = float(cfg.get("batch_normalize_lo_pct", 5.0))
            hi_pct = float(cfg.get("batch_normalize_hi_pct", 95.0))
            p_lo = np.percentile(scores, lo_pct)
            p_hi = np.percentile(scores, hi_pct)
            span = p_hi - p_lo
            if span > 0.01:
                scores = (scores - p_lo) / span

        # Rescale [0, 1] to [1, 5]
        clipped = np.clip(scores, 0.0, 1.0)
        final_scores = 1.0 + (clipped * 4.0)

        try:
            for idx, candidate in enumerate(candidates):
                penalised_count = float(penalised_counts[idx])
                final_score = float(final_scores[idx])
                if penalised_count <= 0 and final_score <= 1.5:
                    logger.debug(
                        "[SMART SCORE][MIN] id=%s raw=%.4f clipped=%.4f final=%.4f "
                        "good=%.4f bad=%.4f aest=%.4f sharpness=%.4f focus=%.4f "
                        "res=%.4f detail=%.4f text=%.4f penalised=%.4f mpx=%.4f "
                        "w=%s h=%s",
                        candidate.get("id"),
                        float(scores[idx]),
                        float(clipped[idx]),
                        final_score,
                        float(good_component[idx]),
                        float(bad_component[idx]),
                        float(aest_component[idx]),
                        float(sharpness_component[idx]),
                        float(focus_presence_component[idx]),
                        float(res_component[idx]),
                        float(detail_component[idx]),
                        float(text_component[idx]),
                        float(penalised_component[idx]),
                        float(mpx[idx]),
                        candidate.get("width"),
                        candidate.get("height"),
                    )
        except Exception as exc:
            logger.warning("[SMART SCORE][MIN] logging failed: %s", exc)

        return final_scores

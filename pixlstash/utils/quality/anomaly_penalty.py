"""Calibrated, precision-aware anomaly penalty for smart scoring.

Replaces the old binary "sum integer weights of present penalised tags" rule. Each
anomaly tag contributes by its *calibrated probability* (the tagger's stored sigmoid
confidence) discounted by the tag's measured *precision*, so a flaky classifier and a
borderline detection both penalise less than a confident, reliable one. Correlated
defects are grouped into families and combined with noisy-OR, so a single bad render
tagged "bad anatomy" + "malformed hand" + "malformed foot" is not triple-counted;
independent families (watermark, noise, ...) add up.

Punishment scales super-linearly with the tagger's confidence (:data:`CONF_POWER`), so a
near-certain catastrophic defect ("bad anatomy") drives the score to the floor while a
borderline, possibly-false detection stays gentle. The *severity* of each defect is derived
from the per-tag weights in ``DEFAULT_SMART_SCORE_PENALIZED_TAGS`` (one editable source of
truth) and amplified by :data:`SEVERITY_GAIN`; how *reliable* the detector is stays a
separate axis (the precision discount). "malformed hand" is as severe as "bad anatomy" but
harder to predict, so it carries the same severity yet a lower precision and thus punishes
somewhat less per detection.

See ``docs/reviews/2026-06-smart-score-calibrated-anomaly-plan.md`` for the rationale.
"""

from pixlstash.db_models.tag import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_TAG_MERGES,
)

# --- Precision policy -------------------------------------------------------
# A tag only pushes the score down if its measured precision clears this floor.
# Below it, false positives are frequent enough that down-scoring good images is worse
# than ignoring the tag; such tags are surfaced in the review queue instead, and a human
# confirmation (label_state POS) lets them back in as certain.
PRECISION_FLOOR = 0.70
# Precision assumed for a tag when no evaluated TaggerRun reports one. The new full-image
# tags and the upgraded anomaly tags ship at ~0.90.
DEFAULT_TAG_PRECISION = 0.90

# --- Severity & confidence shaping ------------------------------------------
# Confidence shaping: per-image evidence = confidence**CONF_POWER. CONF_POWER > 1 makes
# punishment rise super-linearly with the tagger's confidence — a 0.95 detection is
# punished much harder than a 0.6 one, while a borderline (possibly false) detection stays
# gentle. This is the "depending on the confidence the tagger gave it" knob.
CONF_POWER = 1.5
# Severity gain applied on top of the per-tag weights (weight/5) so the worst defects can
# floor a confident picture. Raise to punish harder across the board; the most severe tags
# ("bad anatomy" at weight 5) gain the most in absolute terms.
SEVERITY_GAIN = 1.5

# --- Objective corroboration ------------------------------------------------
# Objective OpenCV metrics (already stored on Quality) that independently support a
# defect. When the metric disagrees, the tag's contribution is damped toward
# CORRO_FLOOR; full agreement leaves it untouched. Bounded so a noisy objective metric
# can never dominate the model. The normalisation ranges are seeds, tunable on real data.
CORRO_FLOOR = 0.5
NOISE_LEVEL_LO, NOISE_LEVEL_HI = 0.02, 0.15
COLORFULNESS_LO, COLORFULNESS_HI = 0.40, 0.90

# --- Families ---------------------------------------------------------------
# A family groups defects that are manifestations of one underlying problem, so they
# combine by noisy-OR (not addition). ``corroborate`` names the objective metric, if any,
# that backs the family. Severity is *not* hard-coded here — it is derived from the per-tag
# weights below so there is a single editable source of truth.
ANOMALY_FAMILIES = (
    {
        "name": "anatomy",
        "tags": (
            "bad anatomy",
            "malformed hand",
            "malformed foot",
            "malformed teeth",
            "malformed nipples",
            "missing nipples",
            "incorrect reflection",
        ),
    },
    {
        "name": "skin",
        "tags": ("waxy skin", "silicone breasts", "flux chin"),
    },
    {
        "name": "compression",
        "tags": ("compression artifacts", "jpeg artifacts", "blocky"),
    },
    {
        "name": "noise",
        "corroborate": "noise",
        "tags": ("noise", "film grain"),
    },
    {
        "name": "watermark",
        "tags": ("watermark",),
    },
)

# Summed-family cap before the smart-score weight is applied. Limits how far stacked
# defects can compound; a single family never approaches it.
DEFAULT_PENALTY_CAP = 3.5

_PENALISED_WEIGHTS = {
    str(tag).strip().lower(): float(weight)
    for tag, weight in DEFAULT_SMART_SCORE_PENALIZED_TAGS.items()
}


def _family_severity(family: dict) -> float:
    """Family severity = (max member weight / 5) × SEVERITY_GAIN.

    Reads the per-tag weights from ``DEFAULT_SMART_SCORE_PENALIZED_TAGS`` (the single
    editable source of truth, also where the new IQA tags are registered) so there is no
    second severity table to keep in sync. Members absent from that dict (merge children,
    ``jpeg artifacts``, ``film grain``) do not raise the family ceiling.
    """
    weights = [_PENALISED_WEIGHTS[t] for t in family["tags"] if t in _PENALISED_WEIGHTS]
    base = (max(weights) / 5.0) if weights else 0.0
    return base * SEVERITY_GAIN


def _build_tag_to_family() -> dict[str, str]:
    """Index each anomaly tag (and merge children) to its family name."""
    index: dict[str, str] = {}
    for fam in ANOMALY_FAMILIES:
        for tag in fam["tags"]:
            index[tag] = fam["name"]
    # A child detection (e.g. "extra digit") belongs to its parent's family so it
    # combines under the same noisy-OR rather than counting separately.
    for child, parent in DEFAULT_TAG_MERGES.items():
        family = index.get(parent)
        if family is not None:
            index[child] = family
    return index


_TAG_TO_FAMILY = _build_tag_to_family()
_FAMILY_BY_NAME = {fam["name"]: fam for fam in ANOMALY_FAMILIES}
# Public {family name: severity}, derived from the per-tag weights × SEVERITY_GAIN.
FAMILY_SEVERITY = {fam["name"]: _family_severity(fam) for fam in ANOMALY_FAMILIES}

# The full anomaly vocabulary the penalty looks at (lowercased). Callers query
# TagPrediction for exactly these tags.
ANOMALY_PENALTY_TAGS = frozenset(_TAG_TO_FAMILY)


def _clip01(value) -> float:
    """Clamp to [0, 1]; ``None`` maps to 0."""
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _agreement(metric_name: str, metrics: dict) -> float | None:
    """Objective support in [0, 1] for a defect, or ``None`` if the metric is absent."""
    if metric_name == "noise":
        noise = metrics.get("noise_level")
        if noise is None:
            return None
        noise_norm = _clip01(
            (noise - NOISE_LEVEL_LO) / (NOISE_LEVEL_HI - NOISE_LEVEL_LO)
        )
        # noise_level (mean |Laplacian|) is confounded with edge detail; only trust it as
        # noise when the image is not dominated by a sharp subject.
        return noise_norm * (1.0 - _clip01(metrics.get("sharpness")))
    return None


def _corroboration_factor(metric_name: str, metrics: dict) -> float:
    """Multiplier in [CORRO_FLOOR, 1.0] from objective agreement (1.0 if no metric)."""
    agree = _agreement(metric_name, metrics)
    if agree is None:
        return 1.0
    return CORRO_FLOOR + (1.0 - CORRO_FLOOR) * agree


def anomaly_penalty(
    anomaly_probs: dict,
    *,
    tag_precisions: dict | None = None,
    human_tags=None,
    metrics: dict | None = None,
    cap: float = DEFAULT_PENALTY_CAP,
) -> float:
    """Severity-weighted, noisy-OR anomaly penalty for one picture (``>= 0``).

    Args:
        anomaly_probs: ``{tag: probability}`` from TagPrediction (the caller has already
            folded human POS/NEG to 1.0/0.0).
        tag_precisions: ``{tag: precision}`` from the latest evaluated TaggerRun; tags
            not present fall back to :data:`DEFAULT_TAG_PRECISION`.
        human_tags: set of tags a human verified — these bypass the precision floor and
            count as certain (a human said it is there, regardless of model precision).
        metrics: ``{sharpness, noise_level, colorfulness}`` for objective corroboration.
        cap: maximum summed family penalty before the smart-score weight is applied.

    Returns:
        Penalty in ``[0, cap]``; subtract it (after weighting) from the raw score.
    """
    if not anomaly_probs:
        return 0.0
    tag_precisions = tag_precisions or {}
    human_tags = human_tags or frozenset()
    metrics = metrics or {}

    # Per-family complement product, for noisy-OR = 1 - prod(1 - e_t).
    fam_complement = {fam["name"]: 1.0 for fam in ANOMALY_FAMILIES}
    corro_cache: dict[str, float] = {}

    for tag, prob in anomaly_probs.items():
        family_name = _TAG_TO_FAMILY.get(tag)
        if family_name is None:
            continue
        p = _clip01(prob)
        if p <= 0.0:
            continue

        is_human = tag in human_tags
        if is_human:
            precision = 1.0
        else:
            precision = tag_precisions.get(tag, DEFAULT_TAG_PRECISION)
            if precision < PRECISION_FLOOR:
                # Too unreliable to down-score; handled via the review queue instead.
                continue

        # Super-linear in confidence: a near-certain defect is punished much harder than a
        # borderline one. Precision is a separate (per-classifier) reliability discount.
        evidence = (p**CONF_POWER) * precision

        family = _FAMILY_BY_NAME[family_name]
        metric_name = family.get("corroborate")
        if metric_name is not None and not is_human:
            factor = corro_cache.get(metric_name)
            if factor is None:
                factor = _corroboration_factor(metric_name, metrics)
                corro_cache[metric_name] = factor
            evidence *= factor

        fam_complement[family_name] *= 1.0 - _clip01(evidence)

    total = 0.0
    for fam in ANOMALY_FAMILIES:
        noisy_or = 1.0 - fam_complement[fam["name"]]
        total += FAMILY_SEVERITY[fam["name"]] * noisy_or
    return min(total, cap)

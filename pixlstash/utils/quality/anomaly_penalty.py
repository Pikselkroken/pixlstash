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
borderline, possibly-false detection stays gentle. Confidence is read *relative to the
tag's own acceptance threshold* rather than in absolute terms: the scorer only ever sees
predictions that already cleared that gate, so the usable range for a tag is
``[threshold, 1.0]`` and the threshold varies per label (0.5-0.85 on the shipped model
once the user's ``threshold_offset`` is applied). Normalising to ``u = (p - t) / (1 - t)``
before the exponent makes "just over the line" mean the same thing for a tag gated at 0.50
as for one gated at 0.85 - with a bare exponent on ``p`` it does not, because ``0.86**k``
stays close to 1 for any usable ``k`` while ``0.51**k`` collapses. A just-accepted
detection is charged :data:`EVIDENCE_FLOOR` of full severity (it is a *visible* tag, so it
is not free), rising to full severity at ``p = 1``. The *severity* of each defect is derived
from the caller-supplied per-tag weight table - in the app that is the user's edited
``User.smart_score_penalised_tags``, with ``DEFAULT_SMART_SCORE_PENALIZED_TAGS`` acting only
as the seed/fallback. A tag the user removed from that table is not penalised at all. How
*reliable* the detector is stays a separate axis (the precision discount).

Aggregation is two-stage, because "is this the same defect seen twice?" and "how many
distinct things are wrong?" are different questions:

1. **Noisy-OR over true duplicates only.** ``extra digit`` and ``malformed hand`` are the
   same underlying defect (the former merges into the latter via ``DEFAULT_TAG_MERGES``),
   so their evidence combines by noisy-OR into a single *canonical* tag. This is the job
   noisy-OR is actually good at and the only place it is used.
2. **Rank-decayed accumulation across distinct defects.** Each distinct canonical tag in a
   family contributes ``severity × evidence``; contributions are sorted descending and
   summed with a geometric :data:`RANK_DECAY` (``1, 0.4, 0.16, …``). The total therefore
   keeps *rising* with the number of distinct defects - with diminishing returns, since
   correlated defects in one family are partly redundant - instead of saturating.

The previous implementation ran noisy-OR across *all* members of a family and multiplied by
the family's maximum member weight. Both were wrong: with the tagger's near-binary
confidences the noisy-OR pinned at ~0.9/0.99/0.999 for 1/2/3 defects, so defect *count*
barely moved the score and roughly a fifth of the library tied at the clamp floor; and
taking the family max made ``incorrect reflection`` (weight 3) as punishing as
``bad anatomy`` (weight 5). Severity is now per-tag, via :func:`_tag_severity`.

Severity maps weight into a band rather than scaling linearly from zero
(:data:`SEVERITY_BASE` + :data:`SEVERITY_WEIGHT_SPAN` × ``weight/5``): any confirmed defect
costs a meaningful baseline, and the per-tag weight modulates on top of it. A purely linear
weight→severity map spans too wide a range to keep every weight class inside the calibrated
count bands.

Constants are calibrated so that, on an otherwise-good picture (raw base score ≈ 0.65,
measured as the median of demo-vault pictures with no anomaly predictions), one anatomy
defect lands in 1.5–2.2 on the [1, 5] scale, two land in 1.0–1.5, and three or more sit at
the floor band. See ``docs/reviews/2026-06-smart-score-calibrated-anomaly-plan.md`` for the
original rationale.
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
# Confidence shaping: per-image evidence rises from EVIDENCE_FLOOR at the tag's acceptance
# threshold to 1.0 at confidence 1.0, as
#   EVIDENCE_FLOOR + (1 - EVIDENCE_FLOOR) * u**CONF_POWER,  u = (p - t) / (1 - t)
# CONF_POWER > 1 makes punishment rise super-linearly across that range, so a detection
# barely over its gate reads as mild while a near-certain one carries full severity. This
# is the "depending on the confidence the tagger gave it" knob.
#
# Raised from 1.5 to 3.0 together with the switch to threshold-relative normalisation. The
# reason is measurable: the tagger's accepted confidences are near-binary (median 0.999 on
# the demo vault, p25 >= 0.87 for every anatomy tag), so a bare exponent on the raw
# probability is close to a no-op - it can only discount the handful of predictions that
# are numerically low, not the ones that are low *for their own gate*. Normalising first
# and then applying a steeper exponent is what actually separates a 0.86 "malformed hand"
# (gate 0.85, u = 0.07) from a 0.999 one.
CONF_POWER = 3.0
# Evidence charged to a detection sitting exactly on its acceptance threshold, as a
# fraction of full severity. It is deliberately non-zero: a prediction that cleared the
# gate became a *visible* tag, so it must cost something - this is a mildness knob, not a
# second gate. Only applies when the caller supplies the tag's threshold; without one
# there is no "just accepted" point to anchor to and the raw confidence is used instead
# (see :func:`_confidence_evidence`).
EVIDENCE_FLOOR = 0.20
# Upper clamp on a supplied acceptance threshold. Thresholds arrive as the model's
# calibrated value plus the user's threshold_offset, which is unbounded above, and the
# normalisation divides by (1 - t). Clamping keeps that division finite; a tag gated this
# high is effectively "human confirmation only" anyway.
MAX_APPLY_THRESHOLD = 0.99
# Severity gain applied on top of the per-tag severity factor. Raise to punish harder
# across the board. Calibrated (with RANK_DECAY below) against the demo vault so a single
# confident anatomy defect on an otherwise-good picture lands in 1.5-2.2 on the [1, 5]
# scale; see the module docstring.
SEVERITY_GAIN = 1.12
# Weight -> severity factor is affine, not proportional: factor = SEVERITY_BASE +
# SEVERITY_WEIGHT_SPAN * (weight / 5). Every confirmed defect costs at least SEVERITY_BASE
# of the gain; the per-tag weight modulates the remaining SEVERITY_WEIGHT_SPAN. A purely
# proportional map (factor = weight / 5) spans 0.6-1.0 of the range, which is too wide to
# hold every weight class inside the calibrated per-count bands.
SEVERITY_BASE = 0.60
SEVERITY_WEIGHT_SPAN = 0.40
# Diminishing-returns factor for each *additional* distinct defect within one family. The
# n-th most severe distinct defect contributes RANK_DECAY**(n-1) of its severity, so the
# family total keeps rising with defect count (1, 1.4, 1.56, 1.62, ... times the worst
# single defect) while acknowledging that co-occurring defects in one family are partly
# redundant. Set to 0.0 to count only the single worst defect per family; 1.0 to sum them
# all with no discount.
RANK_DECAY = 0.40

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
# that backs the family. Severity is *not* hard-coded here - it is derived from the per-tag
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


def normalise_tag_weights(weights: dict | None) -> dict[str, float]:
    """Lowercase/normalise a ``{tag: weight}`` table; ``None`` yields the shipped defaults.

    ``DEFAULT_SMART_SCORE_PENALIZED_TAGS`` is only the *seed* for a new user's editable
    table (it is the ``default_factory`` of ``User.smart_score_penalised_tags``). It is
    used here purely as the fallback for callers that have no user config to offer -
    every scoring path is expected to pass the resolved user table instead.
    """
    source = DEFAULT_SMART_SCORE_PENALIZED_TAGS if weights is None else weights
    normalised: dict[str, float] = {}
    for tag, weight in source.items():
        if tag is None:
            continue
        clean = str(tag).strip().lower()
        if not clean:
            continue
        normalised[clean] = float(weight)
    return normalised


_PENALISED_WEIGHTS = normalise_tag_weights(None)


def _family_max_weights(weights: dict[str, float]) -> dict[str, float]:
    """Per-family ceiling: the largest weight any of its members carries in *weights*."""
    return {
        fam["name"]: max([weights[t] for t in fam["tags"] if t in weights] or [0.0])
        for fam in ANOMALY_FAMILIES
    }


_FAMILY_MAX_WEIGHT = _family_max_weights(_PENALISED_WEIGHTS)


def _tag_weight(
    tag: str,
    family_name: str,
    weights: dict[str, float],
    family_max: dict[str, float],
) -> float:
    """Weight for one canonical tag, from the effective per-tag weight table.

    Tags present in *weights* use their own weight. Family members that are *absent*
    (``jpeg artifacts``, ``film grain``, ``compression artifacts``) are unweighted aliases
    of a sibling, so they inherit the family's maximum present weight.

    Absence therefore means "not penalised" at the family level: if no member of a family
    appears in the user's table, ``family_max`` is 0 and every member - alias or not -
    contributes nothing. That is the semantic the settings UI implies, since it can only
    express weights 1-5 and removing a row is the only way to say "stop charging this".
    """
    if tag in weights:
        return weights[tag]
    return family_max.get(family_name, 0.0)


def _tag_severity(
    tag: str,
    family_name: str,
    weights: dict[str, float],
    family_max: dict[str, float],
) -> float:
    """Per-tag severity: ``SEVERITY_GAIN × (BASE + SPAN × weight/5)``.

    Replaces the old per-*family* severity, which took the family's maximum member weight
    and so punished ``incorrect reflection`` (weight 3) exactly as hard as ``bad anatomy``
    (weight 5). A weight of 0 - a tag absent from the effective table whose family is also
    empty, or a legacy explicit 0 such as the default ``silicone breasts`` - yields 0
    severity and is skipped by :func:`anomaly_penalty`.
    """
    weight = _tag_weight(tag, family_name, weights, family_max)
    if weight <= 0.0:
        return 0.0
    return SEVERITY_GAIN * (SEVERITY_BASE + SEVERITY_WEIGHT_SPAN * (weight / 5.0))


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
# Merge children are the *same* defect as their parent, so they collapse onto the parent's
# canonical tag and combine with it by noisy-OR instead of counting as a second defect.
_CANONICAL_TAG = {tag: DEFAULT_TAG_MERGES.get(tag, tag) for tag in _TAG_TO_FAMILY}


def family_severity(weights: dict | None = None) -> dict[str, float]:
    """{family name: severity ceiling} under *weights* (defaults when ``None``).

    The ceiling is the severity of the family's most severe single tag. Note this is not
    the family's maximum *penalty*: several distinct defects in one family accumulate past
    it (up to ``1 / (1 - RANK_DECAY)`` times it). Provided for introspection and tests.
    """
    effective = normalise_tag_weights(weights)
    family_max = _family_max_weights(effective)
    return {
        fam["name"]: max(
            [_tag_severity(t, fam["name"], effective, family_max) for t in fam["tags"]]
            or [0.0],
        )
        for fam in ANOMALY_FAMILIES
    }


# Severity ceilings under the shipped default weights.
FAMILY_SEVERITY = family_severity()

# The full anomaly vocabulary the penalty looks at (lowercased). Callers query
# TagPrediction for exactly these tags.
ANOMALY_PENALTY_TAGS = frozenset(_TAG_TO_FAMILY)


def _clip01(value) -> float:
    """Clamp to [0, 1]; ``None`` maps to 0."""
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _confidence_evidence(prob: float, threshold: float | None) -> float:
    """Shape a calibrated probability into evidence in ``[0, 1]``.

    With a known acceptance *threshold* the probability is first normalised onto the range
    the tag can actually occupy - ``u = (p - t) / (1 - t)``, since anything below ``t`` was
    dropped by the gate - and evidence runs from :data:`EVIDENCE_FLOOR` at ``u = 0`` to
    ``1.0`` at ``u = 1`` with exponent :data:`CONF_POWER`. That makes "barely accepted"
    equally mild for every tag no matter where its gate sits, and leaves full-confidence
    detections untouched at ``1.0``, which is what keeps the calibrated defect-count bands
    where they were.

    Without a threshold (``None`` or ``<= 0``) there is no "just accepted" point to anchor
    the floor to - charging a floor to a 0.05 prediction would be plainly wrong - so the
    raw probability is shaped directly by :data:`CONF_POWER`. That is the path unit tests
    and any caller with no gate resolved take.

    Args:
        prob: Calibrated probability, already clamped to ``[0, 1]``.
        threshold: The tag's acceptance threshold, or ``None`` when unknown.

    Returns:
        Evidence in ``[0, 1]``, monotonically increasing in *prob*.
    """
    if threshold is None or threshold <= 0.0:
        return prob**CONF_POWER
    t = min(float(threshold), MAX_APPLY_THRESHOLD)
    u = _clip01((prob - t) / (1.0 - t))
    return EVIDENCE_FLOOR + (1.0 - EVIDENCE_FLOOR) * (u**CONF_POWER)


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
    tag_weights: dict | None = None,
    tag_thresholds: dict | None = None,
    human_tags=None,
    metrics: dict | None = None,
    cap: float = DEFAULT_PENALTY_CAP,
) -> float:
    """Severity-weighted, count-escalating anomaly penalty for one picture (``>= 0``).

    Evidence for merge-child aliases is folded into their canonical parent tag by noisy-OR
    (true duplicates), then the distinct canonical defects within each family are summed
    with a geometric :data:`RANK_DECAY` on rank so the penalty keeps rising with defect
    count. Families are independent and add. See the module docstring.

    Args:
        anomaly_probs: ``{tag: probability}`` from TagPrediction (the caller has already
            folded human POS/NEG to 1.0/0.0).
        tag_precisions: ``{tag: precision}`` from the latest evaluated TaggerRun; tags
            not present fall back to :data:`DEFAULT_TAG_PRECISION`.
        tag_weights: the effective ``{tag: weight}`` table - normally the resolved
            ``User.smart_score_penalised_tags``. A tag absent from it is **not**
            penalised (subject to the family-alias rule in :func:`_tag_weight`).
            ``None`` falls back to ``DEFAULT_SMART_SCORE_PENALIZED_TAGS``, which is only
            correct for callers that genuinely have no user config.
        tag_thresholds: ``{tag: acceptance threshold}`` - the same gate the caller used to
            drop sub-threshold predictions, from
            :func:`~pixlstash.utils.service.anomaly_thresholds.resolve_anomaly_apply_thresholds`.
            Confidence is graded relative to it (see :func:`_confidence_evidence`); tags
            missing from it fall back to grading the raw probability.
        human_tags: set of tags a human verified - these bypass the precision floor and
            count as certain (a human said it is there, regardless of model precision).
        metrics: ``{sharpness, noise_level, colorfulness}`` for objective corroboration.
        cap: maximum summed family penalty before the smart-score weight is applied.

    Returns:
        Penalty in ``[0, cap]``; subtract it (after weighting) from the raw score.
    """
    if not anomaly_probs:
        return 0.0
    tag_precisions = tag_precisions or {}
    tag_thresholds = tag_thresholds or {}
    human_tags = human_tags or frozenset()
    metrics = metrics or {}
    weights = normalise_tag_weights(tag_weights)
    family_max = _family_max_weights(weights)

    # Stage 1: per *canonical* tag complement product, for noisy-OR = 1 - prod(1 - e_t).
    # Only true duplicates (a merge child and its parent) share a key here.
    canonical_complement: dict[tuple[str, str], float] = {}
    corro_cache: dict[str, float] = {}

    for tag, prob in anomaly_probs.items():
        family_name = _TAG_TO_FAMILY.get(tag)
        if family_name is None:
            continue
        p = _clip01(prob)
        if p <= 0.0:
            continue

        canonical = _CANONICAL_TAG.get(tag, tag)
        if _tag_severity(canonical, family_name, weights, family_max) <= 0.0:
            # Zero weight means "absent from the user's table (and from its family), or
            # explicitly de-penalised" - either way, not charged.
            continue

        is_human = tag in human_tags
        if is_human:
            precision = 1.0
        else:
            precision = tag_precisions.get(tag, DEFAULT_TAG_PRECISION)
            if precision < PRECISION_FLOOR:
                # Too unreliable to down-score; handled via the review queue instead.
                continue

        # Super-linear in confidence *relative to the tag's own gate*: a near-certain
        # defect is punished much harder than one that barely cleared the threshold.
        # Precision is a separate (per-classifier) reliability discount. A human POS
        # arrives as p = 1.0 and so always carries full evidence regardless of the gate.
        evidence = _confidence_evidence(p, tag_thresholds.get(tag)) * precision

        family = _FAMILY_BY_NAME[family_name]
        metric_name = family.get("corroborate")
        if metric_name is not None and not is_human:
            factor = corro_cache.get(metric_name)
            if factor is None:
                factor = _corroboration_factor(metric_name, metrics)
                corro_cache[metric_name] = factor
            evidence *= factor

        key = (family_name, canonical)
        canonical_complement[key] = canonical_complement.get(key, 1.0) * (
            1.0 - _clip01(evidence)
        )

    # Stage 2: within each family, accumulate the distinct canonical defects with a
    # geometric decay on rank so more defects always mean a strictly larger penalty.
    contributions: dict[str, list[float]] = {}
    for (family_name, canonical), complement in canonical_complement.items():
        combined_evidence = 1.0 - complement
        severity = _tag_severity(canonical, family_name, weights, family_max)
        contributions.setdefault(family_name, []).append(severity * combined_evidence)

    total = 0.0
    for family_contributions in contributions.values():
        family_contributions.sort(reverse=True)
        for rank, contribution in enumerate(family_contributions):
            total += contribution * (RANK_DECAY**rank)
    return min(total, cap)

"""Frozen eval slice: freeze action + the 4-tier threshold/metric procedure.

Wave C of the tag-review takeover design
(``docs/reviews/tag-review-tagger-takeover-design.md`` §1), hard-blocked on
Wave B (:mod:`pixlstash.services.picture_split_service`) for the train/eval
leakage guard this module reuses at freeze time.

**Freeze** (:func:`freeze_eval_slice_in_session`) snapshots a tag's current
EVAL-side human-verified labels into a new :class:`~pixlstash.db_models.tag_eval_slice.TagEvalSlice`.
The ground-truth ``label_state`` is copied, never live-joined, so a later
correction to the live ``TagPrediction`` row cannot retroactively change what
a past freeze's metrics were computed against.

**Scoring** (:func:`compute_eval_metrics_in_session`) deliberately does NOT
freeze the model's prediction: it joins the frozen membership against live
``TagPrediction.confidence`` for a requested ``model_version``, implementing
the design doc's tiered procedure:

1. No live predictions at all for the slice's items at this model_version ->
   ``eval_metric_kind = "none"``.
2. Live predictions exist but ``n_pos < MIN_EVAL_N_POS`` -> ``"insufficient_data"``.
3. ``n_pos >= MIN_EVAL_N_POS`` and a calibrated threshold IS available (tier
   3a "calibrated"/"carried_forward", or tier 3b "rederived_disjoint_val") ->
   standard P/R/F1 at that threshold, ``eval_metric_kind = "F1"``.
4. Otherwise (no calibrated threshold) -> Average Precision (threshold-free),
   ``eval_metric_kind = "AP"`` -- this is the *default* uncalibrated state, not
   a fallback. A 95% picture-level bootstrap CI is added once ``n_pos >= 25``.
5. A caller can explicitly opt into a last-resort fixed-0.5-threshold F1
   triple instead of AP (``allow_uncalibrated_f1=True``) when it specifically
   needs an F1-shaped number and tiers 3a/3b came up empty --
   ``eval_threshold_source = "uncalibrated_fallback"``. This is intentionally
   never the default: AP is preferred whenever no real threshold exists.

See the Wave C implementation report for two judgment calls this module makes
that the design doc left underspecified: (a) how "carried forward from a
previous generation" resolves given PixlStash keeps only one on-disk tagger
meta.json (the currently active generation's, not a historical archive) --
see :func:`_find_calibrated_threshold`'s docstring; (b) the tier-2(AP)-vs-
tier-5(fixed-0.5-F1) relationship, implemented here as an explicit opt-in
flag rather than an implicit override.
"""

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import numpy as np
from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import (
    EVAL_SLICE_ACTIVE,
    EVAL_SLICE_SUPERSEDED,
    Picture,
    TagEvalSlice,
    TagEvalSliceItem,
)
from pixlstash.db_models.picture_split import PictureSplit, SplitValue
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_prediction_service
from pixlstash.services.picture_split_service import has_train_side_conflict
from pixlstash.services.tag_health_service import _current_model_version
from pixlstash.utils.service.caption_utils import sanitise_tag
from pixlstash.utils.service.label_ledger import NEG, POS

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# Shared across the freeze floor, the AP-vs-F1 gate, and the disjoint-val-slice
# rederivation gate -- deliberately one magic number across the whole design,
# not three unrelated ones (see the design doc).
MIN_EVAL_N_POS = 10
# Below this, AP is a point estimate only ("CI unavailable -- n too small").
AP_CI_MIN_N_POS = 25
BOOTSTRAP_ITERATIONS = 2000
# If more than this share of bootstrap resamples are degenerate (zero
# positives), the CI itself is untrustworthy -- collapse to no-CI regardless
# of how many non-degenerate samples remain.
BOOTSTRAP_DEGENERATE_COLLAPSE_RATIO = 0.10
UNCALIBRATED_FALLBACK_THRESHOLD = 0.5

METRIC_KIND_NONE = "none"
METRIC_KIND_INSUFFICIENT = "insufficient_data"
METRIC_KIND_AP = "AP"
METRIC_KIND_F1 = "F1"

THRESHOLD_SOURCE_NONE = "none"
THRESHOLD_SOURCE_CALIBRATED = "calibrated"
THRESHOLD_SOURCE_CARRIED_FORWARD = "carried_forward"
THRESHOLD_SOURCE_REDERIVED = "rederived_disjoint_val"
THRESHOLD_SOURCE_UNCALIBRATED = "uncalibrated_fallback"


# --------------------------------------------------------------------------- #
# Freeze action
# --------------------------------------------------------------------------- #


def freeze_eval_slice_in_session(session: Session, tag: str) -> dict:
    """Freeze *tag*'s current EVAL-side human labels into a new ACTIVE slice.

    Candidates are every picture with a human-labeled (``label_source ==
    'human'``, ``label_state in (POS, NEG)``) ``TagPrediction`` row for *tag*
    whose :class:`~pixlstash.db_models.picture_split.PictureSplit` is
    ``EVAL``. Before freezing, candidates flagged by
    :func:`~pixlstash.services.picture_split_service.has_train_side_conflict`
    (a race between near-dup edge discovery and this freeze) are excluded and
    logged. If the surviving set's POS count is below :data:`MIN_EVAL_N_POS`,
    no slice is created. Otherwise a new ``ACTIVE`` slice is created and the
    tag's prior ``ACTIVE`` slice (if any) is superseded.

    Args:
        session: Active DB session; caller commits are folded into this call
            (this function commits internally, matching the vault-task
            convention used by sibling freeze/assign operations).
        tag: The literal tag to freeze (not ``DEFAULT_TAG_MERGES``-folded).

    Returns:
        ``{"created": bool, "slice_id": Optional[int], "tag": str, "n_pos":
        int, "n_total": int, "excluded_conflict_ids": list[int], "reason":
        Optional[str]}``. ``reason`` is ``None`` when ``created`` is True,
        else one of ``"no_candidates"`` / ``"insufficient_positives"``.
    """
    candidate_rows = session.exec(
        select(TagPrediction.picture_id, TagPrediction.label_state)
        .join(Picture, Picture.id == TagPrediction.picture_id)
        .join(PictureSplit, PictureSplit.picture_id == TagPrediction.picture_id)
        .where(
            TagPrediction.tag == tag,
            TagPrediction.label_source == "human",
            TagPrediction.label_state.in_([POS, NEG]),
            Picture.deleted.is_(False),
            PictureSplit.split == SplitValue.EVAL.value,
        )
    ).all()

    candidates: dict[int, str] = {int(pid): state for pid, state in candidate_rows}
    if not candidates:
        return {
            "created": False,
            "slice_id": None,
            "tag": tag,
            "n_pos": 0,
            "n_total": 0,
            "excluded_conflict_ids": [],
            "reason": "no_candidates",
        }

    excluded = has_train_side_conflict(session, candidates.keys())
    if excluded:
        logger.warning(
            "tag_eval_slice freeze(tag=%r): excluding %d candidate(s) with a "
            "corroborated near-dup on the TRAIN side (race between edge "
            "discovery and freeze): %s",
            tag,
            len(excluded),
            sorted(excluded),
        )
        for pid in excluded:
            candidates.pop(pid, None)

    n_pos = sum(1 for state in candidates.values() if state == POS)
    if n_pos < MIN_EVAL_N_POS:
        logger.info(
            "tag_eval_slice freeze(tag=%r): only %d verified positive(s) on the "
            "EVAL side (need >= %d); not creating an ACTIVE slice",
            tag,
            n_pos,
            MIN_EVAL_N_POS,
        )
        return {
            "created": False,
            "slice_id": None,
            "tag": tag,
            "n_pos": n_pos,
            "n_total": len(candidates),
            "excluded_conflict_ids": sorted(excluded),
            "reason": "insufficient_positives",
        }

    now = datetime.utcnow()
    prior_active = session.exec(
        select(TagEvalSlice).where(
            TagEvalSlice.tag == tag, TagEvalSlice.status == EVAL_SLICE_ACTIVE
        )
    ).all()
    for prior in prior_active:
        prior.status = EVAL_SLICE_SUPERSEDED
        session.add(prior)

    new_slice = TagEvalSlice(tag=tag, status=EVAL_SLICE_ACTIVE, created_at=now)
    session.add(new_slice)
    session.flush()  # populate new_slice.id for the item rows below

    for pid, state in candidates.items():
        session.add(
            TagEvalSliceItem(
                eval_slice_id=new_slice.id,
                picture_id=pid,
                label_state=state,
                frozen_at=now,
            )
        )
    session.commit()
    session.refresh(new_slice)

    logger.info(
        "tag_eval_slice freeze(tag=%r): created slice %d with %d item(s), %d POS",
        tag,
        new_slice.id,
        len(candidates),
        n_pos,
    )
    return {
        "created": True,
        "slice_id": new_slice.id,
        "tag": tag,
        "n_pos": n_pos,
        "n_total": len(candidates),
        "excluded_conflict_ids": sorted(excluded),
        "reason": None,
    }


def freeze_eval_slice(vault: "Vault", tag: str) -> dict:
    """Vault-facing wrapper for :func:`freeze_eval_slice_in_session`."""

    def _freeze(session: Session) -> dict:
        return freeze_eval_slice_in_session(session, tag)

    return vault.db.run_task(_freeze)


# --------------------------------------------------------------------------- #
# Freeze history
# --------------------------------------------------------------------------- #


def list_eval_slices_in_session(session: Session, tag: str) -> list[dict]:
    """Freeze history for *tag*, most recent first, with each slice's size."""
    slices = session.exec(
        select(TagEvalSlice)
        .where(TagEvalSlice.tag == tag)
        .order_by(TagEvalSlice.created_at.desc())
    ).all()
    if not slices:
        return []

    slice_ids = [s.id for s in slices]
    counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # id -> [total, pos]
    for slice_id, label_state, cnt in session.exec(
        select(
            TagEvalSliceItem.eval_slice_id, TagEvalSliceItem.label_state, func.count()
        )
        .where(TagEvalSliceItem.eval_slice_id.in_(slice_ids))
        .group_by(TagEvalSliceItem.eval_slice_id, TagEvalSliceItem.label_state)
    ).all():
        bucket = counts[int(slice_id)]
        bucket[0] += int(cnt)
        if label_state == POS:
            bucket[1] += int(cnt)

    return [
        {
            "id": s.id,
            "tag": s.tag,
            "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "n_total": counts[s.id][0],
            "n_pos": counts[s.id][1],
        }
        for s in slices
    ]


def list_eval_slices(vault: "Vault", tag: str) -> list[dict]:
    """Vault-facing wrapper for :func:`list_eval_slices_in_session`."""
    return vault.db.run_immediate_read_task(list_eval_slices_in_session, tag)


def active_slice_tags_in_session(session: Session) -> dict[str, int]:
    """``{tag: slice_id}`` for every currently ``ACTIVE`` slice.

    Used by :func:`pixlstash.services.tag_health_service.compute_tag_health_rows`
    to know which board rows should get eval columns populated.
    """
    rows = session.exec(
        select(TagEvalSlice.tag, TagEvalSlice.id).where(
            TagEvalSlice.status == EVAL_SLICE_ACTIVE
        )
    ).all()
    return {tag: int(sid) for tag, sid in rows}


# --------------------------------------------------------------------------- #
# Active-slice picture-id discovery (Wave D)
# --------------------------------------------------------------------------- #


def list_active_slice_picture_ids_in_session(
    session: Session, tag: str, *, limit: int = 500, offset: int = 0
) -> Optional[dict]:
    """Paginated picture ids from *tag*'s current ACTIVE eval slice.

    Wave D of the tag-review takeover design (design doc §6): the entire
    "id discovery" surface a downstream consumer (e.g. pixltagger) needs.
    Returns ids only -- no label payload -- by design: a caller feeds the
    returned ids into the existing, unmodified ``bulk_fetch_tags``
    (``POST /pictures/tags/bulk_fetch``) to get current human-corrected
    tags for them, reusing the exact call it already makes for everything
    else rather than learning a new response shape.

    Args:
        session: Open DB session.
        tag: The literal tag to look up the ACTIVE slice for (not
            ``DEFAULT_TAG_MERGES``-folded, matching :func:`freeze_eval_slice_in_session`).
        limit: Max ids to return. The route clamps this before calling in;
            this function trusts the caller's value.
        offset: Pagination offset into the slice's items, ordered by
            ``picture_id`` for a stable, deterministic page sequence.

    Returns:
        ``None`` when no ``ACTIVE`` slice exists for *tag* -- the route maps
        this to 404, the same "unresolvable slice" convention
        ``GET /tag_eval_slices/{id}`` already established, rather than
        inventing a different empty-vs-missing convention for this route.
        Otherwise ``{"tag", "eval_slice_id", "picture_ids", "total",
        "limit", "offset"}``, where ``total`` is the slice's full
        (unpaginated) item count.
    """
    active = session.exec(
        select(TagEvalSlice).where(
            TagEvalSlice.tag == tag, TagEvalSlice.status == EVAL_SLICE_ACTIVE
        )
    ).first()
    if active is None:
        return None

    total_row = session.exec(
        select(func.count())
        .select_from(TagEvalSliceItem)
        .where(TagEvalSliceItem.eval_slice_id == active.id)
    ).one()
    total = total_row[0] if isinstance(total_row, (tuple, list)) else total_row

    picture_ids = session.exec(
        select(TagEvalSliceItem.picture_id)
        .where(TagEvalSliceItem.eval_slice_id == active.id)
        .order_by(TagEvalSliceItem.picture_id)
        .limit(limit)
        .offset(offset)
    ).all()

    return {
        "tag": tag,
        "eval_slice_id": active.id,
        "picture_ids": [int(p) for p in picture_ids],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }


def list_active_slice_picture_ids(
    vault: "Vault", tag: str, *, limit: int = 500, offset: int = 0
) -> Optional[dict]:
    """Vault-facing wrapper for :func:`list_active_slice_picture_ids_in_session`."""
    return vault.db.run_immediate_read_task(
        list_active_slice_picture_ids_in_session, tag, limit=limit, offset=offset
    )


# --------------------------------------------------------------------------- #
# Average Precision (non-interpolated) + picture-level bootstrap CI
# --------------------------------------------------------------------------- #


def average_precision(pairs: list[tuple[float, bool]]) -> Optional[float]:
    """Non-interpolated Average Precision (``sklearn.average_precision_score`` semantics).

    ``AP = sum_n (R_n - R_{n-1}) * P_n`` over unique confidence thresholds
    sorted descending, with tied confidences resolved as a single step (not
    an arbitrary per-item order, which would make the result depend on
    unstable tie-breaking). This is deliberately the step-function estimator,
    NOT trapezoidal PR-AUC (``sklearn.metrics.auc`` over PR points) -- Davis &
    Goadrich (2006) show that estimator is provably over-optimistic for PR
    curves, since linear interpolation between PR points isn't achievable by
    any real classifier.

    Args:
        pairs: ``(confidence, is_positive)`` pairs.

    Returns:
        AP in ``[0, 1]``, or ``None`` when there are no positives (undefined).
    """
    n_pos = sum(1 for _, is_pos in pairs if is_pos)
    if n_pos == 0:
        return None

    groups: dict[float, list[int]] = defaultdict(lambda: [0, 0])  # conf -> [pos, neg]
    for conf, is_pos in pairs:
        bucket = groups[conf]
        if is_pos:
            bucket[0] += 1
        else:
            bucket[1] += 1

    tp = 0
    fp = 0
    ap = 0.0
    prev_recall = 0.0
    for conf in sorted(groups.keys(), reverse=True):
        pos_c, neg_c = groups[conf]
        tp += pos_c
        fp += neg_c
        precision = tp / (tp + fp)
        recall = tp / n_pos
        if pos_c > 0:
            ap += (recall - prev_recall) * precision
        prev_recall = recall
    return ap


def bootstrap_ap_ci(
    pairs: list[tuple[float, bool]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    rng: Optional[np.random.Generator] = None,
) -> tuple[Optional[float], Optional[float]]:
    """Picture-level bootstrap 95% percentile CI for :func:`average_precision`.

    Resamples *pictures* (rows) with replacement -- not confusion-matrix
    counts -- recomputing AP on each resample, mirroring pixltagger's own
    ``paired_bootstrap`` in spirit (picture-level resampling, ~2000
    iterations, percentile CI). This repo has no access to that module's
    source, so this is a from-scratch equivalent, not a port.

    A resample with zero positives has an undefined AP and is dropped from
    the percentile calculation. If more than
    :data:`BOOTSTRAP_DEGENERATE_COLLAPSE_RATIO` of resamples are degenerate,
    the CI itself is untrustworthy -- collapse to ``(None, None)`` regardless
    of how many non-degenerate samples remain (that ratio is itself a signal
    the slice is too thin, even though raw ``n_pos`` cleared the CI floor).

    Args:
        pairs: ``(confidence, is_positive)`` pairs for the full (non-resampled)
            slice.
        iterations: Number of bootstrap resamples.
        rng: Optional seeded ``numpy`` generator for deterministic tests;
            defaults to a fresh unseeded generator.

    Returns:
        ``(ci_low, ci_high)``, or ``(None, None)`` when collapsed.
    """
    n = len(pairs)
    if n == 0 or iterations <= 0:
        return None, None
    if rng is None:
        rng = np.random.default_rng()

    samples: list[float] = []
    degenerate = 0
    for _ in range(iterations):
        idx = rng.integers(0, n, size=n)
        resample = [pairs[i] for i in idx]
        ap = average_precision(resample)
        if ap is None:
            degenerate += 1
            continue
        samples.append(ap)

    if (degenerate / iterations) > BOOTSTRAP_DEGENERATE_COLLAPSE_RATIO:
        logger.warning(
            "bootstrap_ap_ci: %d/%d resamples degenerate (> %.0f%% of %d); "
            "collapsing to no-CI",
            degenerate,
            iterations,
            BOOTSTRAP_DEGENERATE_COLLAPSE_RATIO * 100,
            iterations,
        )
        return None, None
    if not samples:
        return None, None

    low, high = np.percentile(samples, [2.5, 97.5])
    return float(low), float(high)


# --------------------------------------------------------------------------- #
# Threshold sourcing (tiers 3a / 3b) + fixed-threshold P/R/F1
# --------------------------------------------------------------------------- #


def _prf1_at_threshold(
    pairs: list[tuple[float, bool]], threshold: float
) -> tuple[float, float, float]:
    """Standard precision/recall/F1 for ``predicted positive = confidence >= threshold``."""
    tp = sum(1 for c, is_pos in pairs if c >= threshold and is_pos)
    fp = sum(1 for c, is_pos in pairs if c >= threshold and not is_pos)
    fn = sum(1 for c, is_pos in pairs if c < threshold and is_pos)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    )
    return precision, recall, f1


def _threshold_sweep(pairs: list[tuple[float, bool]], min_precision: float) -> float:
    """Lowest threshold clearing ``>= min_precision``; F1-max fallback otherwise.

    Candidate thresholds are the pairs' distinct confidence values -- the
    only points where the predicted-positive set changes. Mirrors the
    tagger's own production-threshold policy in spirit (this repo has no
    access to pixltagger's ``per_class_production_thresholds`` source, so this
    is a from-scratch equivalent applying the same documented policy: lowest
    threshold hitting the target precision, F1-max fallback).
    """
    thresholds = sorted({conf for conf, _ in pairs})
    best_low: Optional[float] = None
    best_f1_score = -1.0
    best_f1_threshold = (
        thresholds[-1] if thresholds else UNCALIBRATED_FALLBACK_THRESHOLD
    )
    for t in thresholds:
        precision, _recall, f1 = _prf1_at_threshold(pairs, t)
        if best_low is None and precision >= min_precision:
            best_low = t
        if f1 > best_f1_score:
            best_f1_score = f1
            best_f1_threshold = t
    return best_low if best_low is not None else best_f1_threshold


def _find_calibrated_threshold(
    session: Session, meta_path: Optional[str], tag: str, model_version: Optional[str]
) -> tuple[Optional[float], str]:
    """Tier 3a: the tagger's own meta-JSON threshold ("calibrated"/"carried_forward").

    **Judgment call** (the design doc's tier 3a wording -- "the tagger's meta
    JSON threshold for this tag from a previous model generation" -- assumed a
    historical archive that does not exist in this codebase): PixlStash keeps
    only the CURRENTLY ACTIVE tagger's ``meta.json`` on disk. Each tagger
    download (``PixlStashTaggerService.download``) overwrites the same
    filename in place, so there is no way to load a specific *older*
    generation's threshold once a newer one has been downloaded -- only
    "whatever the on-disk meta.json currently says" is available.

    This function resolves the ambiguity by distinguishing the two
    ``eval_threshold_source`` states on whether the *scored* ``model_version``
    is the generation that produced the on-disk meta (the vault's current
    active tagger version) or a different one:

    - scored ``model_version`` == the vault's current tagger version -> this
      threshold genuinely IS that generation's own calibration, not tuned on
      this eval slice -> ``"calibrated"``.
    - otherwise (scoring an older generation's live predictions, e.g. against
      a model_version whose own meta.json was long since overwritten) -> the
      only threshold available comes from a DIFFERENT generation than the one
      being scored -> ``"carried_forward"``.

    Flagged explicitly in the Wave C implementation report as a
    disambiguation the design doc left underspecified.
    """
    raw_thresholds = tag_prediction_service.load_raw_label_thresholds(meta_path)
    key = sanitise_tag(tag) or tag
    threshold = raw_thresholds.get(key)
    if threshold is None:
        return None, THRESHOLD_SOURCE_NONE

    current_version = _current_model_version(session)
    source = (
        THRESHOLD_SOURCE_CALIBRATED
        if model_version is not None and model_version == current_version
        else THRESHOLD_SOURCE_CARRIED_FORWARD
    )
    return threshold, source


def _rederive_threshold_from_train_val(
    session: Session, meta_path: Optional[str], tag: str
) -> tuple[Optional[float], str]:
    """Tier 3b: rederive a threshold on a TRAIN-side validation slice.

    Disjoint from EVAL by construction (``PictureSplit.split == TRAIN``,
    human-labeled pictures for *tag*). Gated by the same
    :data:`MIN_EVAL_N_POS` floor applied to this val slice specifically --
    below it, this returns ``(None, "none")`` so the caller falls through to
    tier 4 (AP) rather than trusting a threshold derived from a handful of
    examples. Scores the val slice with the CURRENT model_version's live
    predictions (never a stale generation), then applies the tagger's
    configured policy (``label_thresholds_min_precision``, read from the same
    meta JSON as the calibrated-threshold tier).
    """
    current_version = _current_model_version(session)
    if current_version is None:
        return None, THRESHOLD_SOURCE_NONE

    rows = session.exec(
        select(TagPrediction.picture_id, TagPrediction.label_state)
        .join(Picture, Picture.id == TagPrediction.picture_id)
        .join(PictureSplit, PictureSplit.picture_id == TagPrediction.picture_id)
        .where(
            TagPrediction.tag == tag,
            TagPrediction.label_source == "human",
            TagPrediction.label_state.in_([POS, NEG]),
            Picture.deleted.is_(False),
            PictureSplit.split == SplitValue.TRAIN.value,
        )
    ).all()
    val_state_by_pid = {int(pid): state for pid, state in rows}
    n_pos = sum(1 for state in val_state_by_pid.values() if state == POS)
    if n_pos < MIN_EVAL_N_POS:
        return None, THRESHOLD_SOURCE_NONE

    conf_rows = session.exec(
        select(TagPrediction.picture_id, TagPrediction.confidence).where(
            TagPrediction.tag == tag,
            TagPrediction.model_version == current_version,
            TagPrediction.picture_id.in_(val_state_by_pid.keys()),
        )
    ).all()
    val_pairs = [
        (float(conf), val_state_by_pid[int(pid)] == POS) for pid, conf in conf_rows
    ]
    val_pos = sum(1 for _, is_pos in val_pairs if is_pos)
    if not val_pairs or val_pos < MIN_EVAL_N_POS:
        # The current generation's live confidences don't cover enough of the
        # val slice's positives to trust a derived threshold either.
        return None, THRESHOLD_SOURCE_NONE

    min_precision = tag_prediction_service.load_label_thresholds_min_precision(
        meta_path
    )
    threshold = _threshold_sweep(val_pairs, min_precision)
    return threshold, THRESHOLD_SOURCE_REDERIVED


# --------------------------------------------------------------------------- #
# Top-level tiered computation
# --------------------------------------------------------------------------- #


def compute_eval_metrics_in_session(
    session: Session,
    meta_path: Optional[str],
    eval_slice_id: int,
    model_version: Optional[str] = None,
    *,
    allow_uncalibrated_f1: bool = False,
    bootstrap_rng: Optional[np.random.Generator] = None,
) -> Optional[dict]:
    """Compute the tiered metric/threshold procedure for one frozen slice.

    See the module docstring for the full tier ordering. Takes a plain
    ``meta_path`` string (fetched by the caller via
    ``vault.get_pixlstash_tagger_meta_path()`` *before* dispatching into the
    DB worker) rather than a ``Vault``, so this stays a pure ``*_in_session``
    function with no framework object crossing the session boundary.

    Args:
        session: Active DB session.
        meta_path: Path to the currently active tagger's meta.json, or None.
        eval_slice_id: The :class:`TagEvalSlice` to score.
        model_version: Which generation's live predictions to join against;
            defaults to the vault's current tagger version
            (:func:`pixlstash.services.tag_health_service._current_model_version`).
        allow_uncalibrated_f1: Caller-selectable opt-in for tier 5: when
            True and neither a calibrated nor a rederived threshold is
            available, falls back to a fixed 0.5 threshold and reports an F1
            triple instead of AP. Default False -- AP is the default
            uncalibrated state, not a fallback.
        bootstrap_rng: Optional seeded RNG for deterministic tests.

    Returns:
        ``None`` if the slice doesn't exist; otherwise a dict with the
        slice's identity, its frozen items, and every ``eval_*`` field
        described on :class:`~pixlstash.db_models.tag_health.TagHealth`.
    """
    eval_slice = session.get(TagEvalSlice, eval_slice_id)
    if eval_slice is None:
        return None

    if model_version is None:
        model_version = _current_model_version(session)

    items = session.exec(
        select(TagEvalSliceItem).where(TagEvalSliceItem.eval_slice_id == eval_slice_id)
    ).all()
    item_by_pid = {item.picture_id: item for item in items}

    result: dict = {
        "id": eval_slice.id,
        "tag": eval_slice.tag,
        "status": eval_slice.status,
        "created_at": eval_slice.created_at,
        "model_version": model_version,
        "items": [
            {
                "picture_id": item.picture_id,
                "label_state": item.label_state,
                "frozen_at": item.frozen_at,
            }
            for item in items
        ],
        "eval_precision": None,
        "eval_recall": None,
        "eval_f1": None,
        "eval_ap": None,
        "eval_ap_ci_low": None,
        "eval_ap_ci_high": None,
        "eval_n": 0,
        "eval_n_pos": 0,
        "eval_metric_kind": METRIC_KIND_NONE,
        "eval_threshold_source": THRESHOLD_SOURCE_NONE,
    }

    if not item_by_pid or model_version is None:
        return result

    conf_rows = session.exec(
        select(TagPrediction.picture_id, TagPrediction.confidence).where(
            TagPrediction.tag == eval_slice.tag,
            TagPrediction.model_version == model_version,
            TagPrediction.picture_id.in_(item_by_pid.keys()),
        )
    ).all()
    pairs: list[tuple[float, bool]] = []
    for pid, conf in conf_rows:
        item = item_by_pid.get(int(pid))
        if item is None:
            continue
        pairs.append((float(conf), item.label_state == POS))

    n = len(pairs)
    n_pos = sum(1 for _, is_pos in pairs if is_pos)
    result["eval_n"] = n
    result["eval_n_pos"] = n_pos

    if n == 0:
        return result  # eval_metric_kind stays METRIC_KIND_NONE

    if n_pos < MIN_EVAL_N_POS:
        result["eval_metric_kind"] = METRIC_KIND_INSUFFICIENT
        return result

    threshold, source = _find_calibrated_threshold(
        session, meta_path, eval_slice.tag, model_version
    )
    if threshold is None:
        threshold, source = _rederive_threshold_from_train_val(
            session, meta_path, eval_slice.tag
        )

    if threshold is not None:
        precision, recall, f1 = _prf1_at_threshold(pairs, threshold)
        result.update(
            eval_metric_kind=METRIC_KIND_F1,
            eval_threshold_source=source,
            eval_precision=precision,
            eval_recall=recall,
            eval_f1=f1,
        )
        return result

    if allow_uncalibrated_f1:
        precision, recall, f1 = _prf1_at_threshold(
            pairs, UNCALIBRATED_FALLBACK_THRESHOLD
        )
        result.update(
            eval_metric_kind=METRIC_KIND_F1,
            eval_threshold_source=THRESHOLD_SOURCE_UNCALIBRATED,
            eval_precision=precision,
            eval_recall=recall,
            eval_f1=f1,
        )
        return result

    result["eval_metric_kind"] = METRIC_KIND_AP
    result["eval_ap"] = average_precision(pairs)
    if n_pos >= AP_CI_MIN_N_POS:
        ci_low, ci_high = bootstrap_ap_ci(pairs, rng=bootstrap_rng)
        result["eval_ap_ci_low"] = ci_low
        result["eval_ap_ci_high"] = ci_high
    return result


def get_eval_slice(
    vault: "Vault",
    eval_slice_id: int,
    *,
    model_version: Optional[str] = None,
    allow_uncalibrated_f1: bool = False,
) -> Optional[dict]:
    """Vault-facing wrapper for :func:`compute_eval_metrics_in_session`."""
    meta_path = vault.get_pixlstash_tagger_meta_path()

    def _fetch(session: Session) -> Optional[dict]:
        return compute_eval_metrics_in_session(
            session,
            meta_path,
            eval_slice_id,
            model_version,
            allow_uncalibrated_f1=allow_uncalibrated_f1,
        )

    return vault.db.run_immediate_read_task(_fetch)

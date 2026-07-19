"""Per-label acceptance thresholds for the PixlStash tagger.

This module is the single source of truth for "at what confidence does a prediction
become a tag the user actually sees?". The tagger applies a *per-label* threshold from
its ``meta.json`` (``label_thresholds``, produced by the post-train gate), falling back
to :data:`PIXLSTASH_TAGGER_DEFAULT_THRESHOLD` for labels the gate did not calibrate, and
adds the user's ``threshold_offset`` bias to both — see
``PixlStashTaggerService.tag_items``.

The smart-score anomaly penalty reuses exactly that rule
(:func:`resolve_anomaly_apply_thresholds`) so a picture is only ever penalised for
defects that are visible in its tag list. Before this existed the penalty read every
``TagPrediction`` row regardless of confidence, so predictions that never crossed the
gate — and are invisible in the UI — still pushed the score down.

Kept deliberately dependency-light (file I/O plus ``sanitise_tag``) so both the scoring
path and the tag-prediction service can import it without an import cycle.
"""

import json

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.quality.anomaly_penalty import ANOMALY_PENALTY_TAGS
from pixlstash.utils.service.caption_utils import sanitise_tag

logger = get_logger(__name__)


def load_label_thresholds(meta_path: str | None, bias: float = 0.0) -> dict[str, float]:
    """Load per-label acceptance thresholds from the PixlStash tagger meta JSON.

    Keys are naturalized to match the values stored in TagPrediction.tag.
    The bias is the user-configured offset added to each label's base threshold.
    Returns an empty dict if the file is missing or lacks label_thresholds.

    Args:
        meta_path: Path to the tagger meta JSON file, or None.
        bias: Offset to add to each label's base threshold.

    Returns:
        Dict mapping sanitised tag name → effective threshold.
    """
    if not meta_path:
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        raw = meta.get("label_thresholds", {})
        if not raw:
            return {}
        return {
            sanitise_tag(k) or k: max(0.01, float(v) + bias) for k, v in raw.items()
        }
    except Exception:
        logger.warning(
            "load_label_thresholds: failed to read %r; returning no thresholds",
            meta_path,
            exc_info=True,
        )
        return {}


def load_raw_label_thresholds(meta_path: str | None) -> dict[str, float]:
    """Load per-label thresholds from meta JSON without any offset applied.

    Args:
        meta_path: Path to the tagger meta JSON file, or None.

    Returns:
        Dict mapping sanitised tag name → base threshold.
    """
    if not meta_path:
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        raw = meta.get("label_thresholds", {})
        return {sanitise_tag(k) or k: float(v) for k, v in raw.items()}
    except Exception:
        logger.warning(
            "load_raw_label_thresholds: failed to read %r; returning no thresholds",
            meta_path,
            exc_info=True,
        )
        return {}


def resolve_anomaly_apply_thresholds(vault) -> dict[str, float]:
    """Return the confidence gate the scorer must apply, for every anomaly tag.

    The returned map is **complete** over :data:`ANOMALY_PENALTY_TAGS`: labels the
    post-train gate calibrated get their own threshold, the rest get the tagger's global
    acceptance threshold. Both already include the user's ``threshold_offset`` bias, so
    the map is exactly the rule ``PixlStashTaggerService.tag_items`` uses to decide
    whether a prediction becomes a tag.

    Completeness matters: :func:`pixlstash.picture_scoring.fetch_anomaly_confidences`
    treats a missing key as "do not gate this tag", so a partial map would silently let
    sub-threshold predictions back into the penalty.

    Args:
        vault: The :class:`~pixlstash.vault.Vault`, used to reach the tagger's meta.json
            path and the configured threshold offset.

    Returns:
        ``{anomaly tag: minimum confidence to be penalised}`` for every anomaly tag.
    """
    meta_path = vault.get_pixlstash_tagger_meta_path()
    offset = vault.get_pixlstash_tagger_threshold_offset()
    default_threshold = vault.get_pixlstash_acceptance_threshold()
    per_label = load_label_thresholds(meta_path, offset)
    if not per_label:
        # Not an error: the engine may not be initialised yet (meta_path is None until
        # the tagger service exists), or the model may ship without a calibrated gate.
        # Every anomaly tag then falls back to the tagger's global acceptance threshold,
        # which is the same value the tagger itself would use.
        logger.info(
            "No per-label tagger thresholds available (meta_path=%r); gating every "
            "anomaly tag at the global acceptance threshold %.3f (offset=%.3f).",
            meta_path,
            default_threshold,
            offset,
        )
    return {tag: per_label.get(tag, default_threshold) for tag in ANOMALY_PENALTY_TAGS}

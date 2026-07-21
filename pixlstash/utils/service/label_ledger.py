"""The human-label ledger — one place that records human POS/NEG decisions.

Absence of a ``Tag`` row is overloaded: it means both "a human reviewed this and said
NO" and "nobody has looked." That ambiguity wrecks training for any tag whose negatives
are scarce (every new tag), because absence read as a negative teaches "this tag is
always absent." This module fixes it at the source: every human accept/reject on a
``(picture, tag)`` is recorded on the :class:`TagPrediction` row as an explicit
``label_state`` (POS/NEG) with ``label_source='human'`` — a real supervision signal that
survives tag edits, tagger re-runs, and scans.

Route every human decision through :func:`record_human_label` (or
:func:`record_human_label_if_relevant` for generic tag edits) so accept and reject are
provably symmetric and the hardest data — a human-reviewed negative — is never dropped.
Use :func:`not_human_labeled` as the single guard predicate so model/scan writes never
clobber a human label.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import or_
from sqlmodel import select

from pixlstash.db_models.tag import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_TAG_MERGES,
)
from pixlstash.db_models.tag_prediction import TagPrediction

if TYPE_CHECKING:
    from sqlmodel import Session

# label_state values.
POS = "POS"
NEG = "NEG"
UNKNOWN = "UNKNOWN"

# label_source values.
HUMAN = "human"
PROPAGATED = "propagated"
MODEL = "model"

# The tagger's anomaly vocabulary — the tags a human POS/NEG is worth recording for even
# when no prediction row exists yet. Generic content tags (character names, scenes) are
# outside the tagger's label space, so recording them would only pollute the prediction
# store and the review overlay. Mirrors the penalised-tag set plus the child tags that
# DEFAULT_TAG_MERGES folds into an anomaly parent.
ANOMALY_LABELS = {t.strip().lower() for t in DEFAULT_SMART_SCORE_PENALIZED_TAGS} | {
    c.strip().lower() for c in DEFAULT_TAG_MERGES
}


def is_anomaly_label(tag: str | None) -> bool:
    """Return True if *tag* is part of the tagger's anomaly vocabulary."""
    return bool(tag) and tag.strip().lower() in ANOMALY_LABELS


def not_human_labeled():
    """SQLAlchemy predicate: rows a model/scan write is allowed to delete/overwrite.

    The single source of truth for the human-protection invariant. Use it everywhere the
    tagger or a scan bulk-mutates predictions so a ``label_source='human'`` ledger entry
    is never clobbered — re-running the tagger is then a no-op over human rows by
    construction.
    """
    return or_(
        TagPrediction.label_source != HUMAN,
        TagPrediction.label_source.is_(None),
    )


def record_human_label(
    session: "Session", picture_id: int, tag: str, state: str
) -> TagPrediction:
    """Upsert the human supervision ledger for one ``(picture, tag)``. No commit.

    Snapshots the tagger version/confidence the human was adjudicating (``label_*``)
    from the live prediction row, then marks the row POS/NEG with ``source='human'``.
    Never overwrites the live ``model_version``/``confidence`` (those stay the tagger's).
    Creates a synthetic ``model_version='manual'`` row if none exists, so a removed tag's
    negative still has somewhere to live.

    Args:
        session: Active DB session (caller commits).
        picture_id: Picture the decision is about.
        tag: The label being accepted (POS) or rejected (NEG).
        state: :data:`POS` or :data:`NEG`.

    Returns:
        The (created or updated) :class:`TagPrediction` row.
    """
    if state not in (POS, NEG):
        raise ValueError(f"label state must be POS or NEG, got {state!r}")

    pred = session.exec(
        select(TagPrediction).where(
            TagPrediction.picture_id == picture_id,
            TagPrediction.tag == tag,
        )
    ).first()

    now = datetime.utcnow()
    if pred is None:
        # No prediction on file: a pure-manual decision with nothing to snapshot.
        pred = TagPrediction(
            picture_id=picture_id,
            tag=tag,
            confidence=1.0 if state == POS else 0.0,
            model_version="manual",
            status="CONFIRMED" if state == POS else "REJECTED",
            predicted_at=now,
        )
        session.add(pred)
    else:
        # Snapshot what the human adjudicated, but only when it was a real tagger
        # prediction (not our own synthetic 'manual' row) so re-recording is stable.
        if pred.model_version and pred.model_version != "manual":
            pred.label_model_version = pred.model_version
            pred.label_confidence = pred.confidence
        pred.status = "CONFIRMED" if state == POS else "REJECTED"

    pred.label_state = state
    pred.label_source = HUMAN
    pred.labeled_at = now
    return pred


def record_human_label_if_relevant(
    session: "Session", picture_id: int, tag: str, state: str
) -> Optional[TagPrediction]:
    """Record a human label only when the tag is in the tagger's label space.

    For generic tag edits (the picture tag panel) where the tag may be arbitrary
    content. Records when *tag* is an anomaly label or already has a prediction row;
    otherwise no-ops so the prediction store isn't polluted with content tags.
    """
    if is_anomaly_label(tag):
        return record_human_label(session, picture_id, tag, state)
    existing = session.exec(
        select(TagPrediction.id).where(
            TagPrediction.picture_id == picture_id,
            TagPrediction.tag == tag,
        )
    ).first()
    if existing is not None:
        return record_human_label(session, picture_id, tag, state)
    return None


def clear_human_label(session: "Session", picture_id: int, tag: str) -> None:
    """Undo a human label (reopen/reverse): reset the ledger to UNKNOWN. No commit.

    Leaves the row and its live ``model_version``/``confidence`` intact — only the
    human supervision fields are cleared, so reopening a review is symmetric with
    recording it.
    """
    pred = session.exec(
        select(TagPrediction).where(
            TagPrediction.picture_id == picture_id,
            TagPrediction.tag == tag,
        )
    ).first()
    if pred is None:
        return
    pred.label_state = UNKNOWN
    pred.label_source = None
    pred.labeled_at = None
    pred.label_model_version = None
    pred.label_confidence = None

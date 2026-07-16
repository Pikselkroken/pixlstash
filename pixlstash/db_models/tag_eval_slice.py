from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel

# Freeze-event status values (mirrors Review's OPEN/ARCHIVED/ABORTED idiom).
EVAL_SLICE_ACTIVE = "ACTIVE"
EVAL_SLICE_SUPERSEDED = "SUPERSEDED"


class TagEvalSlice(SQLModel, table=True):
    """One row per freeze event: a tag's frozen, leak-free ground-truth membership.

    Wave C of the tag-review takeover design
    (``docs/reviews/tag-review-tagger-takeover-design.md`` §1). Freezing a tag
    captures, at a point in time, exactly which pictures form that tag's
    verified evaluation set — so precision/recall/F1/AP can be computed
    against a membership that doesn't silently drift as ``TagPrediction``
    rows are later corrected. Mirrors :class:`~pixlstash.db_models.review.Review`'s
    "at most one active X per key" pattern: a partial unique index enforces at
    most one ``ACTIVE`` slice per tag, and re-freezing a tag supersedes its
    prior ``ACTIVE`` slice rather than requiring manual bookkeeping.

    The model's *prediction* is deliberately NOT frozen here — only the
    ground-truth ``label_state`` on each :class:`TagEvalSliceItem` is. Metrics
    are recomputed at read time by joining the frozen membership against
    live ``TagPrediction.confidence`` for a requested ``model_version``, so
    the same ground truth re-scores against every new tagger generation.

    Attributes:
        id: Primary key.
        tag: The literal tag this slice was frozen for (not
            ``DEFAULT_TAG_MERGES``-folded — a freeze targets exactly the tag
            name it was requested for).
        status: ``ACTIVE`` | ``SUPERSEDED``.
        created_at: When this freeze event happened.
    """

    __tablename__ = "tag_eval_slice"

    id: Optional[int] = Field(default=None, primary_key=True)

    tag: str = Field(index=True)
    status: str = Field(default=EVAL_SLICE_ACTIVE, index=True)  # ACTIVE | SUPERSEDED

    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

    __table_args__ = (
        # One ACTIVE slice per tag; superseded history is unlimited.
        sa.Index(
            "uq_tag_eval_slice_active_tag",
            "tag",
            unique=True,
            sqlite_where=sa.text("status = 'ACTIVE'"),
        ),
    )


class TagEvalSliceItem(SQLModel, table=True):
    """One frozen (picture, ground-truth label) pair belonging to a :class:`TagEvalSlice`.

    ``label_state`` is a snapshot copied at freeze time from the live
    ``TagPrediction.label_state`` — never live-joined — so a later correction
    to that picture's ledger entry cannot retroactively change what this
    slice's metrics were computed against. Only ``POS``/``NEG`` candidates are
    ever frozen (see :mod:`pixlstash.services.tag_eval_slice_service`'s freeze
    logic); ``UNKNOWN`` never appears here.

    Attributes:
        id: Primary key.
        eval_slice_id: FK to the owning :class:`TagEvalSlice`.
        picture_id: FK to the picture.
        label_state: ``POS`` | ``NEG``, snapshotted at freeze time.
        frozen_at: When this item was frozen (same instant for every item in
            one freeze event, but stored per-row for auditability).
    """

    __tablename__ = "tag_eval_slice_item"

    id: Optional[int] = Field(default=None, primary_key=True)

    eval_slice_id: int = Field(
        sa_column=Column(
            "eval_slice_id",
            Integer,
            ForeignKey("tag_eval_slice.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    picture_id: int = Field(
        sa_column=Column(
            "picture_id",
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    label_state: str = Field(index=True)  # POS | NEG (snapshotted)
    frozen_at: datetime = Field(default_factory=datetime.utcnow)

    __table_args__ = (
        sa.UniqueConstraint(
            "eval_slice_id", "picture_id", name="uq_tag_eval_slice_item"
        ),
    )

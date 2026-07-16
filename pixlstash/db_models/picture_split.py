from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel


class SplitValue(str, Enum):
    """Train/eval/excluded assignment for a picture's :class:`PictureSplit` row."""

    TRAIN = "TRAIN"
    EVAL = "EVAL"
    NEITHER = "NEITHER"


class PictureSplit(SQLModel, table=True):
    """Component-aware train/eval split assignment — one row per picture.

    Wave B of the tag-review takeover design
    (``docs/reviews/tag-review-tagger-takeover-design.md`` §2): the load-bearing
    leakage guard a frozen eval slice (a later wave) depends on. A picture's
    train/eval identity is vault-wide and stable across every tag it is ever
    used to evaluate — assignment is never per-tag.

    Assignment is **component-aware, not per-picture-hash**: pictures that
    are corroborated near-duplicates of each other (dhash Hamming distance
    <= ``DEFAULT_MAX_TWIN_HAMMING`` AND CLIP cosine >= ``MIN_DISPLAY_TWIN_SIM``,
    or a stored :class:`~pixlstash.db_models.picture_likeness.PictureLikeness`
    row >= ``MISMATCH_LIKENESS_THRESHOLD`` — see
    :mod:`pixlstash.services.picture_split_service`) are unioned into one
    connected *component* and always assigned the same side together, so a
    near-duplicate pair can never straddle train/eval. ``component_key`` is
    the stable identifier for that component (the minimum ``picture_id``
    among its members) — every member of a component shares the same
    ``component_key``, which is how
    :func:`~pixlstash.services.picture_split_service.has_train_side_conflict`
    finds a picture's near-dup siblings without recomputing the graph.

    ``conflict`` / ``conflict_detail`` are this table's own queue: no
    separate conflict table exists — ``SELECT * FROM picture_split WHERE
    conflict = true`` *is* the queue. A conflict is raised, never
    auto-resolved, when a newly-discovered corroborated edge connects two
    pictures whose existing splits disagree (mirrors the ``model_disputes``
    convention elsewhere in this codebase: surfaced, human outranks model).
    Fail-closed: both sides are forced to ``NEITHER`` — a picture is only
    ever pulled *out* of a side automatically, never moved *into* ``EVAL``.
    A human resolves the conflict explicitly via
    ``POST /picture_splits/{picture_id}/resolve``.

    Attributes:
        picture_id: FK to ``picture.id`` (also the primary key — one row per
            picture, uniqueness is structural).
        split: ``TRAIN`` | ``EVAL`` | ``NEITHER`` (see :class:`SplitValue`).
        component_key: The near-dup component's stable identifier (min
            ``picture_id`` among corroborated members).
        assigned_at: When this row's ``split`` was last (re)decided —
            updated on fresh assignment and on conflict resolution, left
            alone when a sibling merely joins an already-decided component.
        conflict: True when this picture's component currently has
            disagreeing pre-existing splits pending human resolution.
        conflict_detail: Human-readable explanation of which edge/signal
            triggered the conflict; null when ``conflict`` is False.
    """

    __tablename__ = "picture_split"

    picture_id: int = Field(
        sa_column=Column(
            "picture_id",
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    split: str = Field(default=SplitValue.NEITHER.value, index=True)
    component_key: int = Field(index=True)
    assigned_at: datetime = Field(default_factory=datetime.utcnow)
    conflict: bool = Field(default=False, index=True)
    conflict_detail: Optional[str] = Field(default=None)

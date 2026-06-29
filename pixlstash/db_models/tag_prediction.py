from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .picture import Picture


class TagPrediction(SQLModel, table=True):
    """Model confidence score for a single tag on a picture.

    Populated by the background TagPredictionTask from the custom (anomaly)
    tagger's raw sigmoid outputs.  Distinct from the Tag table, which holds
    only user-confirmed ground-truth tags.

    Attributes:
        id: Primary key.
        picture_id: Foreign key to the picture this prediction belongs to.
        tag: The label name.
        confidence: Raw sigmoid probability in [0, 1].
        model_version: Epoch string, e.g. "epoch-43".
        status: "PENDING", "CONFIRMED", or "REJECTED" — review-UI state. NOTE this
            is *not* a reliable human signal: the background TagTask auto-flips it
            from the applied tags. Read the label ledger below for supervision.

    Human-label ledger (the per-(picture,tag) supervision record):
        label_state: "UNKNOWN" | "POS" | "NEG". A real label only when
            ``label_source`` is set; UNKNOWN/None means "nobody reviewed this", which
            training must mask out rather than read as a negative.
        label_source: "human" | "propagated" | "model" | None. POS/NEG is supervision
            only when this is non-null; ``human`` outranks everything and is never
            clobbered by the tagger or a scan (see ``not_human_labeled``).
        labeled_at: When the label was last set by a human/propagation/model.
        label_model_version: Snapshot of the tagger version whose output the human
            was adjudicating at decision time (None for a pure-manual decision with
            no prediction on file). Frozen — the tagger never overwrites it, unlike
            the live ``model_version``.
        label_confidence: Snapshot of the raw confidence the human saw at decision
            time (None if there was no prediction to adjudicate).
        predicted_at: UTC timestamp of when this prediction was written.
    """

    __tablename__ = "tag_prediction"

    id: Optional[int] = Field(default=None, primary_key=True)

    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )

    tag: str = Field(index=True)
    confidence: float
    model_version: str = Field(index=True)
    status: str = Field(default="PENDING", index=True)
    predicted_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

    # --- Human-label ledger (supervision record; see class docstring) ---
    label_state: str = Field(default="UNKNOWN", index=True)  # UNKNOWN | POS | NEG
    label_source: Optional[str] = Field(
        default=None, index=True
    )  # human|propagated|model
    labeled_at: Optional[datetime] = Field(default=None)
    # Snapshot of the prediction the human adjudicated, frozen at decision time.
    label_model_version: Optional[str] = Field(default=None)
    label_confidence: Optional[float] = Field(default=None)

    __table_args__ = (
        UniqueConstraint("picture_id", "tag"),
        # Composite index for the missing-count query: WHERE model_version = ? -> count(distinct picture_id)
        sa.Index(
            "ix_tag_prediction_model_version_picture_id", "model_version", "picture_id"
        ),
    )

    picture: Optional["Picture"] = Relationship(
        back_populates="tag_predictions",
        sa_relationship_kwargs={
            "passive_deletes": True,
            "foreign_keys": "[TagPrediction.picture_id]",
        },
    )

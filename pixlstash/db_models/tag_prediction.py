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
        status: "PENDING", "CONFIRMED", or "REJECTED".
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

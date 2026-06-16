from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class TagSuggestion(SQLModel, table=True):
    """A suggested label fix for review — the dataset-refinement queue.

    Distinct from both Tag (user-confirmed ground truth) and TagPrediction (the
    tagger's raw per-tag confidences). A TagSuggestion says "this label is probably
    wrong, here's why" and carries a *direction* so review is fast:

      * direction="add"    – the tag is missing and probably should be present
                             (a likely false negative / rare-class recall miss).
      * direction="remove" – the tag is present and probably should not be
                             (a likely false positive).

    Suggestions come from several signals (``source``), all feeding one queue:
      * "near_neighbor"        – model-independent: visually near-identical images
                                 disagree on the tag (the cold-start signal).
      * "model"                – confident-learning-style mining from the tagger.
      * "propagation"          – kNN label propagation for bootstrapping a new tag.
      * "version_disagreement" – two model versions flip relative to the label.

    A suggestion outlives any single ``model_version``; re-running a scan upserts on
    (picture_id, tag, source) and must not resurrect a row the user already reviewed.
    """

    __tablename__ = "tag_suggestion"

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
    direction: str  # "add" | "remove"
    source: str = Field(index=True)
    score: float  # ranking score in [0, 1]; higher = review sooner.
    reason: Optional[str] = Field(default=None)

    # The neighbour/example that triggered the suggestion, shown to the reviewer.
    # Soft reference (set null if that picture is deleted) — informational only.
    twin_picture_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="SET NULL"),
            index=True,
            nullable=True,
        ),
    )
    twin_sim: Optional[float] = Field(default=None)

    # Producing model for model/version sources; null for embedding-only signals.
    model_version: Optional[str] = Field(default=None)

    status: str = Field(default="PENDING", index=True)  # PENDING | ACCEPTED | DISMISSED
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)

    __table_args__ = (
        UniqueConstraint("picture_id", "tag", "source"),
        # Drives the ranked review queue: WHERE status='PENDING' [AND tag=?] ORDER BY score DESC.
        sa.Index("ix_tag_suggestion_status_score", "status", "score"),
        sa.Index("ix_tag_suggestion_tag_status", "tag", "status"),
    )

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class TagSuggestion(SQLModel, table=True):
    """A suggested label fix for review - the dataset-refinement queue.

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
    # Soft reference (set null if that picture is deleted) - informational only.
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

    # The review session whose scan produced (or re-adopted) this row; NULL for
    # rows from the legacy global queue. ON DELETE SET NULL: deleting a review
    # never deletes the audit history its decisions left behind.
    review_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("review.id", ondelete="SET NULL"),
            index=True,
            nullable=True,
        ),
    )

    # JSON list of the suspect's k nearest neighbours captured at scan time:
    # [{"picture_id": int, "has": bool}, ...] ordered by descending similarity,
    # where "has" is the merged-concept "carries the tag" flag used in the vote.
    # Frozen evidence - never recomputed after the scan that wrote it.
    neighbors: Optional[str] = Field(
        default=None, sa_column=Column(sa.Text(), nullable=True)
    )

    # PENDING | ACCEPTED | DISMISSED | TWIN_FIXED | SWAPPED | SKIPPED.
    # SKIPPED = the reviewer could not decide: the row leaves the queue with no
    # decision made - no Tag write, no ledger write (reopen simply re-pends it).
    status: str = Field(default="PENDING", index=True)
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)

    # Prior-decision snapshot for the include_reviewed re-parent. When a scan
    # re-parents an already-DECIDED row into a new review (reopening it), the
    # decision being overwritten - its (review_id, status, reviewed_at) - is
    # captured here first, so undo can RESTORE that tuple (re-exposing the
    # original decision for a normal reversal) instead of silently erasing it.
    # NULL for rows that were never re-parented over a decision. prior_review_id
    # is a plain id (not an FK): it is a historical pointer used only to restore
    # review_id on undo, and reviews are archived rather than deleted.
    prior_review_id: Optional[int] = Field(default=None)
    prior_status: Optional[str] = Field(default=None)
    prior_reviewed_at: Optional[datetime] = Field(default=None)

    __table_args__ = (
        UniqueConstraint("picture_id", "tag", "source"),
        # Drives the ranked review queue: WHERE status='PENDING' [AND tag=?] ORDER BY score DESC.
        sa.Index("ix_tag_suggestion_status_score", "status", "score"),
        sa.Index("ix_tag_suggestion_tag_status", "tag", "status"),
    )

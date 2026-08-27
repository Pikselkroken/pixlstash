"""Add review.receipt_snapshot and tag_suggestion.prior_* decision snapshot.

Two additive, independent snapshots that make review sessions durable across a
later re-scan re-parenting their rows (see the tag-review-rewrite branch):

* ``review.receipt_snapshot`` (TEXT/JSON) - frozen ``{"receipt", "progress"}``
  written when a review is ARCHIVED/ABORTED. A closed review's receipt/progress
  otherwise aggregate LIVE over its suggestion rows, so a later scan that
  re-parents those rows into a new review would silently shrink the closed
  session's cover sheet. NULL for OPEN reviews (served live) and for reviews
  closed before this column existed (fall back to live aggregation).

* ``tag_suggestion.prior_review_id`` / ``prior_status`` / ``prior_reviewed_at``
  - the decision an ``include_reviewed`` re-parent overwrites is captured here
  first, so undo can restore it (re-exposing the original decision for normal
  reversal) instead of silently erasing it. NULL for rows never re-parented
  over a decision.

Additive only: no existing column or constraint changes. In particular
UNIQUE(picture_id, tag, source) on tag_suggestion is untouched, and no data is
reset (these are new nullable columns, nothing to reprocess).

Revision ID: 0072_add_review_receipt_snapshot_and_suggestion_prior_decision
Revises: 0071_remove_tag_review_scoring_subsystem
Create Date: 2026-07-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0072_add_review_receipt_snapshot_and_suggestion_prior_decision"
down_revision: Union[str, None] = "0071_remove_tag_review_scoring_subsystem"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # A fresh DB gets every current model column from the 0001 baseline
    # create_all(); a blind ALTER TABLE ... ADD COLUMN would then fail. Guard
    # each add on the reflected column set (see CLAUDE.md's add_column policy).
    if "review" in tables:
        review_cols = {c["name"] for c in inspector.get_columns("review")}
        if "receipt_snapshot" not in review_cols:
            op.add_column(
                "review", sa.Column("receipt_snapshot", sa.Text(), nullable=True)
            )

    if "tag_suggestion" in tables:
        sugg_cols = {c["name"] for c in inspector.get_columns("tag_suggestion")}
        if "prior_review_id" not in sugg_cols:
            op.add_column(
                "tag_suggestion",
                sa.Column("prior_review_id", sa.Integer(), nullable=True),
            )
        if "prior_status" not in sugg_cols:
            op.add_column(
                "tag_suggestion", sa.Column("prior_status", sa.String(), nullable=True)
            )
        if "prior_reviewed_at" not in sugg_cols:
            op.add_column(
                "tag_suggestion",
                sa.Column("prior_reviewed_at", sa.DateTime(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "tag_suggestion" in tables:
        sugg_cols = {c["name"] for c in inspector.get_columns("tag_suggestion")}
        if "prior_reviewed_at" in sugg_cols:
            op.drop_column("tag_suggestion", "prior_reviewed_at")
        if "prior_status" in sugg_cols:
            op.drop_column("tag_suggestion", "prior_status")
        if "prior_review_id" in sugg_cols:
            op.drop_column("tag_suggestion", "prior_review_id")

    if "review" in tables:
        review_cols = {c["name"] for c in inspector.get_columns("review")}
        if "receipt_snapshot" in review_cols:
            op.drop_column("review", "receipt_snapshot")

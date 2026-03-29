"""Backfill tag_prediction status: set CONFIRMED where tag already exists

Revision ID: 0016_tag_prediction_confirm_applied
Revises: 0015_tag_prediction_model_version_rename
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_tag_prediction_confirm_applied"
down_revision: Union[str, None] = "0015_tag_prediction_model_version_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    # Step 1: CONFIRMED — prediction tag is a real applied tag for this picture.
    op.execute(
        """
        UPDATE tag_prediction
        SET status = 'CONFIRMED'
        WHERE status = 'PENDING'
          AND EXISTS (
              SELECT 1 FROM tag
              WHERE tag.picture_id = tag_prediction.picture_id
                AND tag.tag = tag_prediction.tag
                AND tag.tag IS NOT NULL
                AND tag.tag != ''
          )
        """
    )
    # Step 2: REJECTED — TagTask has run for this picture (at least one tag row
    # exists, which may be a real tag or the empty sentinel) but this particular
    # prediction tag was not among the applied tags.
    op.execute(
        """
        UPDATE tag_prediction
        SET status = 'REJECTED'
        WHERE status = 'PENDING'
          AND EXISTS (
              SELECT 1 FROM tag
              WHERE tag.picture_id = tag_prediction.picture_id
          )
        """
    )
    # Remaining PENDING rows: picture has no tag rows at all, meaning TagTask
    # has not yet run for it.  These will be resolved when TagTask executes.


def downgrade() -> None:
    op.execute(
        """
        UPDATE tag_prediction
        SET status = 'PENDING'
        WHERE status IN ('CONFIRMED', 'REJECTED')
        """
    )

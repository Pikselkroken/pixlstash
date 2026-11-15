"""Add ``review.source`` and ``review.candidate_limit``.

``source`` records which candidate source filled a review's queue; existing
reviews read ``near_neighbor``, the only source there was. ``candidate_limit``
is the cap a ``model`` review was created with, NULL otherwise.

Schema-only and additive.

Revision ID: 0144_add_review_source
Revises: 0143_add_whats_changed
Create Date: 2026-11-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0144_add_review_source"
down_revision: Union[str, None] = "0143_add_whats_changed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "review" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("review")}
    if "source" not in existing_cols:
        op.add_column(
            "review",
            sa.Column(
                "source", sa.String(), nullable=False, server_default="near_neighbor"
            ),
        )
    if "candidate_limit" not in existing_cols:
        op.add_column(
            "review", sa.Column("candidate_limit", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "review" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("review")}
    for column in ("source", "candidate_limit"):
        if column in existing_cols:
            op.drop_column("review", column)

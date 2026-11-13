"""Add ``review.kind`` and ``tag_suggestion.control_state``.

``review.kind`` says where a review's queue came from: ``SCAN`` (the
near-neighbour scan, every existing review) or ``LIST`` (a supplied picture
list). ``tag_suggestion.control_state`` marks a known-answer control row with
the human verdict the picture already carried; NULL for every ordinary row.

Schema-only and additive.

Revision ID: 0144_add_review_picture_lists_and_controls
Revises: 0143_add_whats_changed
Create Date: 2026-11-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0144_add_review_picture_lists_and_controls"
down_revision: Union[str, None] = "0143_add_whats_changed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = (
    ("review", "kind"),
    ("tag_suggestion", "control_state"),
)


def _column(name: str) -> sa.Column:
    if name == "kind":
        return sa.Column("kind", sa.String(), nullable=False, server_default="SCAN")
    return sa.Column(name, sa.String(), nullable=True)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column in _COLUMNS:
        if table not in tables:
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table)}
        if column not in existing_cols:
            op.add_column(table, _column(column))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column in _COLUMNS:
        if table not in tables:
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table)}
        if column in existing_cols:
            op.drop_column(table, column)

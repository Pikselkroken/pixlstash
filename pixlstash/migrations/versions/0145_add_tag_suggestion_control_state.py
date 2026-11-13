"""Add ``tag_suggestion.control_state``.

Marks a known-answer control row of a supplied-list review with the human
verdict (POS/NEG) the picture already carried; NULL for every ordinary row.

Schema-only and additive.

Revision ID: 0145_add_tag_suggestion_control_state
Revises: 0144_add_review_source
Create Date: 2026-11-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0145_add_tag_suggestion_control_state"
down_revision: Union[str, None] = "0144_add_review_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "tag_suggestion" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("tag_suggestion")}
    if "control_state" not in existing_cols:
        op.add_column(
            "tag_suggestion", sa.Column("control_state", sa.String(), nullable=True)
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "tag_suggestion" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("tag_suggestion")}
    if "control_state" in existing_cols:
        op.drop_column("tag_suggestion", "control_state")

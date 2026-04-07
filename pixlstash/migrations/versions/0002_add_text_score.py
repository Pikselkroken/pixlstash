"""add text_score to quality

Revision ID: 0002_add_text_score
Revises: 0001_baseline
Create Date: 2026-03-14 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_add_text_score"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("quality")}
    if "text_score" not in existing_columns:
        op.add_column(
            "quality",
            sa.Column("text_score", sa.Float(), nullable=True),
        )
    op.create_index(
        "ix_quality_text_score", "quality", ["text_score"], if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index("ix_quality_text_score", table_name="quality")
    op.drop_column("quality", "text_score")

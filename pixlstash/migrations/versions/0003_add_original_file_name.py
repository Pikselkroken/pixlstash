"""add original_file_name to picture

Revision ID: 0003_add_original_file_name
Revises: 0002_add_text_score
Create Date: 2026-03-17 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_original_file_name"
down_revision: Union[str, None] = "0002_add_text_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("picture")}
    if "original_file_name" not in existing_columns:
        op.add_column(
            "picture",
            sa.Column("original_file_name", sa.String(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("picture", "original_file_name")

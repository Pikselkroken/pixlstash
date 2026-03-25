"""add compact_mode to user

Revision ID: 0008_add_compact_mode
Revises: 0007_add_picture_project_id
Create Date: 2026-03-25 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_add_compact_mode"
down_revision: Union[str, None] = "0007_add_picture_project_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = {col["name"] for col in inspector.get_columns("user")}
    if "compact_mode" not in existing_columns:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(
                sa.Column("compact_mode", sa.Boolean(), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("compact_mode")

"""add sidebar_pinned to user

Revision ID: 0064_add_sidebar_pinned
Revises: 0063_rename_pixelated_tag_to_blocky
Create Date: 2026-06-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0064_add_sidebar_pinned"
down_revision: Union[str, None] = "0063_rename_pixelated_tag_to_blocky"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Guard a missing user table (a partial/synthetic DB, e.g. the migration
    # tests that hand-build a minimal schema) before inspecting its columns.
    if "user" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("user")}
    if "sidebar_pinned" not in existing_columns:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(
                sa.Column("sidebar_pinned", sa.Boolean(), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("sidebar_pinned")

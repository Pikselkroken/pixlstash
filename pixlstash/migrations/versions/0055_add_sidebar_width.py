"""add sidebar_width to user

Revision ID: 0055_add_sidebar_width
Revises: 0054_add_sidebar_docked
Create Date: 2026-06-11 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0055_add_sidebar_width"
down_revision: Union[str, None] = "0054_add_sidebar_docked"
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
    if "sidebar_width" not in existing_columns:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(sa.Column("sidebar_width", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("sidebar_width")

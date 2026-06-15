"""add hide_purge_snapshot_warning to user

Revision ID: 0056_add_hide_purge_snapshot_warning
Revises: 0055_add_sidebar_width
Create Date: 2026-06-15 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0056_add_hide_purge_snapshot_warning"
down_revision: Union[str, None] = "0055_add_sidebar_width"
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
    if "hide_purge_snapshot_warning" not in existing_columns:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(
                sa.Column("hide_purge_snapshot_warning", sa.Boolean(), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("hide_purge_snapshot_warning")

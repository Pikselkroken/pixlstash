"""add sidebar_docked to user

Revision ID: 0054_add_sidebar_docked
Revises: 0053_add_face_model_pack
Create Date: 2026-06-11 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0054_add_sidebar_docked"
down_revision: Union[str, None] = "0053_add_face_model_pack"
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
    if "sidebar_docked" not in existing_columns:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(
                sa.Column("sidebar_docked", sa.Boolean(), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("sidebar_docked")

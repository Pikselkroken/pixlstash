"""add ai-toolkit training config columns to user

Revision ID: 0135_add_ai_toolkit_user_config
Revises: 0134_add_trained_lora
Create Date: 2026-06-16 00:00:02.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0135_add_ai_toolkit_user_config"
down_revision: Union[str, None] = "0134_add_trained_lora"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_NEW_COLUMNS = (
    "ai_toolkit_repo_path",
    "ai_toolkit_python",
    "ai_toolkit_commit",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Guard a missing user table (a partial/synthetic DB, e.g. the migration
    # tests that hand-build a minimal schema) before inspecting its columns.
    if "user" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("user")}
    with op.batch_alter_table("user") as batch_op:
        for column_name in _NEW_COLUMNS:
            if column_name not in existing_columns:
                batch_op.add_column(sa.Column(column_name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("user")}
    with op.batch_alter_table("user") as batch_op:
        for column_name in _NEW_COLUMNS:
            if column_name in existing_columns:
                batch_op.drop_column(column_name)

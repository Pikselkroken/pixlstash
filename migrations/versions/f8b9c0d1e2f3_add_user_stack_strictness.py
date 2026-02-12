"""add user stack strictness

Revision ID: f8b9c0d1e2f3
Revises: f4a5b6c7d8e9
Create Date: 2026-02-12 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f8b9c0d1e2f3"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing_cols = {
        row[1]
        for row in conn.execute(sa.text("PRAGMA table_info('user')")).fetchall()
    }
    if "stack_strictness" not in existing_cols:
        op.add_column(
            "user",
            sa.Column("stack_strictness", sa.Float(), nullable=True, server_default="0.92"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    existing_cols = {
        row[1]
        for row in conn.execute(sa.text("PRAGMA table_info('user')")).fetchall()
    }
    if "stack_strictness" in existing_cols:
        op.drop_column("user", "stack_strictness")

"""add user max vram gb

Revision ID: 0123abcd4567
Revises: b17c8d9e0f1a
Create Date: 2026-03-03 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0123abcd4567"  # noqa: F841
down_revision: Union[str, None] = "b17c8d9e0f1a"  # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F841
depends_on: Union[str, Sequence[str], None] = None  # noqa: F841

TABLE_NAME = "user"
COLUMN_NAME = "max_vram_gb"


def _get_columns(conn) -> set[str]:
    return {
        row[1] for row in conn.execute(sa.text("PRAGMA table_info('user')")).fetchall()
    }


def upgrade() -> None:
    conn = op.get_bind()
    existing_cols = _get_columns(conn)
    if COLUMN_NAME in existing_cols:
        return
    op.add_column(
        TABLE_NAME,
        sa.Column(
            COLUMN_NAME, sa.Float(), nullable=True, server_default=sa.text("4.0")
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    existing_cols = _get_columns(conn)
    if COLUMN_NAME not in existing_cols:
        return
    try:
        op.execute(f"ALTER TABLE {TABLE_NAME} DROP COLUMN {COLUMN_NAME}")
    except Exception:
        pass

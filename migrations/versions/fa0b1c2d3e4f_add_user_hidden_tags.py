"""add user hidden tags column

Revision ID: fa0b1c2d3e4f
Revises: f9c0d1e2f3a4
Create Date: 2026-02-12 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fa0b1c2d3e4f"
down_revision: Union[str, None] = "f9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "user"
COLUMN_NAME = "hidden_tags"


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
        sa.Column(COLUMN_NAME, sa.String(), nullable=True),
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

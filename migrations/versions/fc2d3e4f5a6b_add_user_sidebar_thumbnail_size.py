"""add user sidebar thumbnail size

Revision ID: fc2d3e4f5a6b
Revises: fb1c2d3e4f5a
Create Date: 2026-02-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fc2d3e4f5a6b"  # noqa: F841
down_revision: Union[str, None] = "fb1c2d3e4f5a"  # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F841
depends_on: Union[str, Sequence[str], None] = None  # noqa: F841

TABLE_NAME = "user"
COLUMN_NAME = "sidebar_thumbnail_size"
DEFAULT_VALUE = 64


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
            COLUMN_NAME,
            sa.Integer(),
            nullable=True,
            server_default=sa.text(str(DEFAULT_VALUE)),
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

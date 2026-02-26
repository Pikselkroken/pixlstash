"""add user comfyui url

Revision ID: 0a1b2c3d4e5f
Revises: ff5a6b7c8d9e
Create Date: 2026-02-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0a1b2c3d4e5f"  # noqa: F841
down_revision: Union[str, None] = "ff5a6b7c8d9e"  # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F841
depends_on: Union[str, Sequence[str], None] = None  # noqa: F841

TABLE_NAME = "user"
COLUMN_NAME = "comfyui_url"
DEFAULT_URL = "http://127.0.0.1:8188/"


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
            sa.String(),
            nullable=True,
            server_default=sa.text(f"'{DEFAULT_URL}'"),
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

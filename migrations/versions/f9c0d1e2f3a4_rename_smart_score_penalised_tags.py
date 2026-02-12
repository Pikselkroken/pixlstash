"""rename smart score penalised tags column

Revision ID: f9c0d1e2f3a4
Revises: f8b9c0d1e2f3
Create Date: 2026-02-12 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f9c0d1e2f3a4"
down_revision: Union[str, None] = "f8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COLUMN = "smart_score_penalized_tags"
NEW_COLUMN = "smart_score_penalised_tags"
TABLE_NAME = "user"


def _get_columns(conn) -> set[str]:
    return {
        row[1] for row in conn.execute(sa.text("PRAGMA table_info('user')")).fetchall()
    }


def upgrade() -> None:
    conn = op.get_bind()
    existing_cols = _get_columns(conn)
    if NEW_COLUMN in existing_cols:
        return
    if OLD_COLUMN in existing_cols:
        try:
            op.execute(
                f"ALTER TABLE {TABLE_NAME} RENAME COLUMN {OLD_COLUMN} TO {NEW_COLUMN}"
            )
            return
        except Exception:
            pass
    if NEW_COLUMN not in existing_cols:
        op.add_column(
            TABLE_NAME,
            sa.Column(NEW_COLUMN, sa.String(), nullable=True),
        )
    if OLD_COLUMN in existing_cols:
        op.execute(f"UPDATE {TABLE_NAME} SET {NEW_COLUMN} = {OLD_COLUMN}")
        try:
            op.execute(f"ALTER TABLE {TABLE_NAME} DROP COLUMN {OLD_COLUMN}")
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    existing_cols = _get_columns(conn)
    if OLD_COLUMN in existing_cols:
        return
    if NEW_COLUMN in existing_cols:
        try:
            op.execute(
                f"ALTER TABLE {TABLE_NAME} RENAME COLUMN {NEW_COLUMN} TO {OLD_COLUMN}"
            )
            return
        except Exception:
            pass
    if OLD_COLUMN not in existing_cols:
        op.add_column(
            TABLE_NAME,
            sa.Column(OLD_COLUMN, sa.String(), nullable=True),
        )
    if NEW_COLUMN in existing_cols:
        op.execute(f"UPDATE {TABLE_NAME} SET {OLD_COLUMN} = {NEW_COLUMN}")
        try:
            op.execute(f"ALTER TABLE {TABLE_NAME} DROP COLUMN {NEW_COLUMN}")
        except Exception:
            pass

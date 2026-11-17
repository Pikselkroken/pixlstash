"""Add a smart set's two columns (#2148).

``pictureset.smart`` holds the saved filter a smart set's members come from,
and the pictures the owner took out of it. ``picturesetmember.smart_auto``
marks the member rows the filter added, the only ones it may take away again;
every existing row is the owner's own (``0``). Null for every existing set:
they are all ordinary sets.

Schema-only and additive.

Revision ID: 0148_add_picture_set_smart
Revises: 0147_add_picture_set_building
Create Date: 2026-11-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0148_add_picture_set_smart"
down_revision: Union[str, None] = "0147_add_picture_set_building"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# (table, column); `nullable=False` with a server default fills every
# existing row, and keeps a raw link-table INSERT that names no flag valid.
COLUMNS = (
    ("pictureset", sa.Column("smart", sa.JSON(), nullable=True)),
    (
        "picturesetmember",
        sa.Column("smart_auto", sa.Boolean(), nullable=False, server_default="0"),
    ),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column in COLUMNS:
        if table not in tables:
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table)}
        if column.name not in existing_cols:
            op.add_column(table, column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column in COLUMNS:
        if table not in tables:
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table)}
        if column.name in existing_cols:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column.name)

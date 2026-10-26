"""Add ``picture.favourite``, the owner's heart beside the score.

Schema-only and additive: every existing picture starts as not a favourite.

Revision ID: 0133_add_picture_favourite
Revises: 0132_add_picture_pending_person
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0133_add_picture_favourite"
down_revision: Union[str, None] = "0132_add_picture_pending_person"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "favourite" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("favourite", sa.Boolean(), nullable=False, server_default="0"),
        )
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_favourite" not in existing_indexes:
        op.create_index(
            "ix_picture_favourite",
            "picture",
            ["favourite", "id"],
            sqlite_where=sa.text("favourite = 1"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_favourite" in existing_indexes:
        op.drop_index("ix_picture_favourite", table_name="picture")
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "favourite" in existing_cols:
        op.drop_column("picture", "favourite")

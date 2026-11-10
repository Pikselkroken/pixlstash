"""Add ``picture.unseen``, the Inbox of what import folders brought in.

Schema-only and additive: every existing picture starts as seen, so the Inbox
begins empty and fills with what import folders bring in from now on.

Revision ID: 0138_add_picture_unseen
Revises: 0137_add_picture_set_parent
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0138_add_picture_unseen"
down_revision: Union[str, None] = "0137_add_picture_set_parent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "unseen" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("unseen", sa.Boolean(), nullable=False, server_default="0"),
        )
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_unseen" not in existing_indexes:
        op.create_index(
            "ix_picture_unseen",
            "picture",
            ["unseen", "id"],
            sqlite_where=sa.text("unseen IS 1"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_unseen" in existing_indexes:
        op.drop_index("ix_picture_unseen", table_name="picture")
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "unseen" in existing_cols:
        op.drop_column("picture", "unseen")

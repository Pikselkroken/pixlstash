"""Drop ``picture.unseen``: the Inbox is a period of unassigned pictures now.

The Inbox no longer queues what import folders brought in until it is looked
at; it is every picture added within a period that has no person and no set,
read off ``imported_at`` and membership. Nothing reads or writes the flag.

Schema-only. The downgrade puts the column back with every picture seen, as
``0138_add_picture_unseen`` first added it.

Revision ID: 0140_drop_picture_unseen
Revises: 0139_add_keep_workflow_on_export
Create Date: 2026-10-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0140_drop_picture_unseen"
down_revision: Union[str, None] = "0139_add_keep_workflow_on_export"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("picture")}
    if "ix_picture_unseen" in existing_indexes:
        op.drop_index("ix_picture_unseen", table_name="picture")
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "unseen" in existing_cols:
        op.drop_column("picture", "unseen")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
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

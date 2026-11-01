"""Add tag groups: a library-wide map from a tag name to a group.

Two tables, both created only when absent (the baseline builds every model table
with ``create_all``, so a fresh database already has them). ``tag_group`` holds
the named groups and their order; ``tag_group_member`` files a tag name under a
group. Deleting a group cascades to its members. No data is backfilled: groups
start empty and a tag not in the map is Ungrouped.

Revision ID: 0141_add_tag_groups
Revises: 0140_drop_picture_unseen
Create Date: 2026-11-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0141_add_tag_groups"
down_revision: Union[str, None] = "0140_drop_picture_unseen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    if "tag_group" not in existing:
        op.create_table(
            "tag_group",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index("ix_tag_group_position", "tag_group", ["position"])
    if "tag_group_member" not in existing:
        op.create_table(
            "tag_group_member",
            sa.Column("tag", sa.String(), primary_key=True),
            sa.Column(
                "group_id",
                sa.Integer(),
                sa.ForeignKey("tag_group.id", ondelete="CASCADE"),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_tag_group_member_group_id", "tag_group_member", ["group_id"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    if "tag_group_member" in existing:
        op.drop_index("ix_tag_group_member_group_id", table_name="tag_group_member")
        op.drop_table("tag_group_member")
    if "tag_group" in existing:
        op.drop_index("ix_tag_group_position", table_name="tag_group")
        op.drop_table("tag_group")

"""Add tag groups: a library-wide map from a tag name to a group.

Two tables, both created only when absent (the baseline builds every model table
with ``create_all``, so a fresh database already has them). ``tag_group`` holds
the named groups and their order; ``tag_group_member`` files a tag name under a
group and records whether the filing is a seeded default (``is_default``). Deleting
a group cascades to its members. Groups start empty and a tag not in the map is
Ungrouped; the default groups are seeded on open, not here.

Also adds ``library_settings.tag_groups_seeded_version`` (NULL = never seeded),
and ``is_default`` on a ``tag_group_member`` table that an earlier build of this
branch created without it. Every column is added only when absent.

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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
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
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        )
        op.create_index(
            "ix_tag_group_member_group_id", "tag_group_member", ["group_id"]
        )
    else:
        member_cols = {c["name"] for c in inspector.get_columns("tag_group_member")}
        if "is_default" not in member_cols:
            op.add_column(
                "tag_group_member",
                sa.Column(
                    "is_default", sa.Boolean(), nullable=False, server_default="0"
                ),
            )
    if "library_settings" in existing:
        settings_cols = {c["name"] for c in inspector.get_columns("library_settings")}
        if "tag_groups_seeded_version" not in settings_cols:
            op.add_column(
                "library_settings",
                sa.Column("tag_groups_seeded_version", sa.Integer(), nullable=True),
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    if "library_settings" in existing:
        settings_cols = {c["name"] for c in inspector.get_columns("library_settings")}
        if "tag_groups_seeded_version" in settings_cols:
            op.drop_column("library_settings", "tag_groups_seeded_version")
    if "tag_group_member" in existing:
        op.drop_index("ix_tag_group_member_group_id", table_name="tag_group_member")
        op.drop_table("tag_group_member")
    if "tag_group" in existing:
        op.drop_index("ix_tag_group_position", table_name="tag_group")
        op.drop_table("tag_group")

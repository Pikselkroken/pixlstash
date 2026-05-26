"""Add changelog table for tracking all metadata mutations.

Creates the `changelog` table used by the change-log infrastructure to record
every INSERT, UPDATE, and DELETE performed by the writer session.

Revision ID: 0047_add_change_log
Revises: 0046_tag_sentinel_rename
Create Date: 2026-05-26 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0047_add_change_log"
down_revision: Union[str, None] = "0046_tag_sentinel_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "changelog" not in existing_tables:
        op.create_table(
            "changelog",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("txn_id", sa.String, nullable=False, index=True),
            sa.Column("seq_in_txn", sa.Integer, nullable=False),
            sa.Column("table_name", sa.String, nullable=False, index=True),
            sa.Column("row_pk_json", sa.String, nullable=False),
            sa.Column("op", sa.String, nullable=False),
            sa.Column("before_json", sa.Text, nullable=True),
            sa.Column("after_json", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False, index=True),
            sa.Column(
                "actor_user_id",
                sa.Integer,
                sa.ForeignKey("user.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("reason", sa.String, nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "changelog" in inspector.get_table_names():
        op.drop_table("changelog")

"""Add deleted_file_log table for incremental backup tracking.

Records permanently deleted picture files so that incremental backups
can emit a delete manifest alongside newly imported file lists.

Revision ID: 0024_add_deleted_file_log
Revises: 0023_add_show_keyboard_hint
Create Date: 2026-04-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0024_add_deleted_file_log"
down_revision: Union[str, None] = "0023_add_show_keyboard_hint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "deleted_file_log" not in existing_tables:
        op.create_table(
            "deleted_file_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("file_path", sa.String(), nullable=False),
            sa.Column("pixel_sha", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_deleted_file_log_file_path", "deleted_file_log", ["file_path"]
        )
        op.create_index(
            "ix_deleted_file_log_pixel_sha", "deleted_file_log", ["pixel_sha"]
        )
        op.create_index(
            "ix_deleted_file_log_deleted_at", "deleted_file_log", ["deleted_at"]
        )


def downgrade() -> None:
    op.drop_table("deleted_file_log")

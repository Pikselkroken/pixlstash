"""Add the durable folder-mapping commit record.

An import that is interrupted between indexing and assigning leaves a
half-organised library and nothing that remembers what was asked for, because
the read and its accepted assignments only ever lived in server memory. This
table is that memory, so start-up can finish the job instead of the owner
discovering a library that half works. Empty on every existing database.

Revision ID: 0110_add_folder_mapping_commit
Revises: 0109_add_external_move_review
Create Date: 2026-08-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0110_add_folder_mapping_commit"
down_revision: Union[str, None] = "0109_add_external_move_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "folder_mapping_commit" not in inspector.get_table_names():
        op.create_table(
            "folder_mapping_commit",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("root_path", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=True),
            sa.Column("expected_pictures", sa.Integer(), nullable=False),
            sa.Column("assignments", sa.String(), nullable=False),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_folder_mapping_commit_task_id", "folder_mapping_commit", ["task_id"]
        )
        op.create_index(
            "ix_folder_mapping_commit_state", "folder_mapping_commit", ["state"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "folder_mapping_commit" in inspector.get_table_names():
        op.drop_table("folder_mapping_commit")

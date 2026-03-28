"""add pictureprojectmember table for picture-project many-to-many

Revision ID: 0009_add_picture_project_membership
Revises: 0008_add_compact_mode
Create Date: 2026-03-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_add_picture_project_membership"
down_revision: Union[str, None] = "0008_add_compact_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())
    if "pictureprojectmember" not in existing_tables:
        op.create_table(
            "pictureprojectmember",
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("picture_id", "project_id"),
        )
        op.create_index(
            "ix_pictureprojectmember_picture_id",
            "pictureprojectmember",
            ["picture_id"],
            unique=False,
        )
        op.create_index(
            "ix_pictureprojectmember_project_id",
            "pictureprojectmember",
            ["project_id"],
            unique=False,
        )

    # Backfill memberships from legacy picture.project_id values.
    bind.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO pictureprojectmember (picture_id, project_id)
            SELECT p.id, p.project_id
            FROM picture AS p
            WHERE p.project_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "pictureprojectmember" in existing_tables:
        op.drop_index(
            "ix_pictureprojectmember_project_id", table_name="pictureprojectmember"
        )
        op.drop_index(
            "ix_pictureprojectmember_picture_id", table_name="pictureprojectmember"
        )
        op.drop_table("pictureprojectmember")

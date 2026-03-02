"""drop face_tag and hand_tag tables

Revision ID: a06b7c8d9e0f
Revises: ff5a6b7c8d9e
Create Date: 2025-01-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "a06b7c8d9e0f" # noqa: F841
down_revision: Union[str, None] = "ff5a6b7c8d9e" # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None # noqa: F841
depends_on: Union[str, Sequence[str], None] = None # noqa: F841



def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS hand_tag")
    op.execute("DROP TABLE IF EXISTS face_tag")


def downgrade() -> None:
    op.create_table(
        "face_tag",
        sa.Column("face_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["face_id"], ["face.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("face_id", "tag_id"),
    )
    op.create_table(
        "hand_tag",
        sa.Column("hand_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["hand_id"], ["hand.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("hand_id", "tag_id"),
    )

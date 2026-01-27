"""add hand table

Revision ID: b7c2c9f6c0d1
Revises: 9a7f7c1a2b3c
Create Date: 2026-01-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b7c2c9f6c0d1"
down_revision = "9a7f7c1a2b3c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hand",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("picture_id", sa.Integer(), nullable=False),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        sa.Column("hand_index", sa.Integer(), nullable=False),
        sa.Column("bbox", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("picture_id", "frame_index", "hand_index"),
    )


def downgrade() -> None:
    op.drop_table("hand")

"""drop face_character_likeness table

Revision ID: f1a2b3c4d5e6
Revises: c3d4e5f6a7b8
Create Date: 2026-02-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("facecharacterlikeness")


def downgrade() -> None:
    op.create_table(
        "facecharacterlikeness",
        sa.Column(
            "face_id",
            sa.Integer,
            sa.ForeignKey("face.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("character.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("likeness", sa.Float, nullable=True),
        sa.Column("metric", sa.String, nullable=True),
    )
    op.create_index(
        "ix_facecharacterlikeness_likeness",
        "facecharacterlikeness",
        ["likeness"],
    )

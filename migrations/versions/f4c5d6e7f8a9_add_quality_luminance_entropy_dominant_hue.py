"""add quality luminance entropy and dominant hue

Revision ID: f4c5d6e7f8a9
Revises: f3b4c5d6e7f8
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4c5d6e7f8a9"
down_revision: Union[str, None] = "f3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quality",
        sa.Column(
            "luminance_entropy",
            sa.Float(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_quality_luminance_entropy",
        "quality",
        ["luminance_entropy"],
        unique=False,
    )
    op.add_column(
        "quality",
        sa.Column(
            "dominant_hue",
            sa.Float(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_quality_dominant_hue",
        "quality",
        ["dominant_hue"],
        unique=False,
    )
    op.execute("DELETE FROM quality")


def downgrade() -> None:
    op.drop_index("ix_quality_dominant_hue", table_name="quality")
    op.drop_column("quality", "dominant_hue")
    op.drop_index("ix_quality_luminance_entropy", table_name="quality")
    op.drop_column("quality", "luminance_entropy")

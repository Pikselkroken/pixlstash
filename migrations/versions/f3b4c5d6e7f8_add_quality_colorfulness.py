"""add quality colorfulness

Revision ID: f3b4c5d6e7f8
Revises: e1b2c3d4e5f6
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3b4c5d6e7f8"
down_revision: Union[str, None] = "e1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quality",
        sa.Column(
            "colorfulness",
            sa.Float(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_quality_colorfulness",
        "quality",
        ["colorfulness"],
        unique=False,
    )
    op.execute("DELETE FROM quality WHERE face_id IS NULL")


def downgrade() -> None:
    op.drop_index("ix_quality_colorfulness", table_name="quality")
    op.drop_column("quality", "colorfulness")

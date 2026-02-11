"""add picture likeness parameters

Revision ID: e1b2c3d4e5f6
Revises: d6f7a8b9c0d1
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1b2c3d4e5f6"
down_revision: Union[str, None] = "d6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "picture",
        sa.Column(
            "size_bin_index",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "picture",
        sa.Column(
            "likeness_parameters",
            sa.LargeBinary(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_picture_size_bin_index",
        "picture",
        ["size_bin_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_picture_size_bin_index", table_name="picture")
    op.drop_column("picture", "likeness_parameters")
    op.drop_column("picture", "size_bin_index")

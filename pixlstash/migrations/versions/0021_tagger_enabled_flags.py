"""Add tagger enabled flags and threshold settings to user.

Adds wd14_tagger_enabled, custom_tagger_enabled, wd14_threshold and
custom_tagger_threshold_offset columns to the user table.

Revision ID: 0021_tagger_enabled_flags
Revises: 0020_source_picture_id
Create Date: 2026-04-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0021_tagger_enabled_flags"
down_revision: Union[str, None] = "0020_source_picture_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "wd14_tagger_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "custom_tagger_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column("user", sa.Column("wd14_threshold", sa.Float(), nullable=True))
    op.add_column(
        "user", sa.Column("custom_tagger_threshold_offset", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user", "custom_tagger_threshold_offset")
    op.drop_column("user", "wd14_threshold")
    op.drop_column("user", "custom_tagger_enabled")
    op.drop_column("user", "wd14_tagger_enabled")

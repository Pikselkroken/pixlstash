"""Add ``video_copy``, one row per playable copy of a video (#2807).

The copy is an MP4 the browser can play, made by ffmpeg from a video it
refuses and named by the picture's id. The row records the original's size and
modified time when it was made (a changed original makes it stale) and how long
it took (the next estimate divides by that). It goes with its picture.

Schema-only and additive.

Revision ID: 0150_add_video_copy
Revises: 0149_add_picture_colour_shares
Create Date: 2026-11-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0150_add_video_copy"
down_revision: Union[str, None] = "0149_add_picture_colour_shares"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "video_copy" in tables or "picture" not in tables:
        # Partial databases used by migration tests carry no picture table.
        return
    op.create_table(
        "video_copy",
        sa.Column(
            "picture_id",
            sa.Integer(),
            sa.ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("source_bytes", sa.Integer(), nullable=False),
        sa.Column("source_mtime", sa.Float(), nullable=False),
        sa.Column("pixel_frames", sa.Integer(), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("made_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_video_copy_made_at", "video_copy", ["made_at"])


def downgrade() -> None:
    if "video_copy" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("video_copy")

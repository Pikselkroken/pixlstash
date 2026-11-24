"""Add ``videocopy``: one row per playable MP4 copy of a video (#2807).

The copy's file lives under the library's thumbnail folder, named by picture
id. The row holds the original's size and mtime, which decide whether the copy
is still fresh, and the build time, which feeds the estimate for the next one.

Schema-only and additive.

Revision ID: 0149_add_video_copy
Revises: 0148_add_picture_set_smart
Create Date: 2026-11-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0149_add_video_copy"
down_revision: Union[str, None] = "0148_add_picture_set_smart"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

TABLE = "videocopy"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column(
            "picture_id",
            sa.Integer(),
            sa.ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("copy_bytes", sa.Integer(), nullable=False),
        sa.Column("source_bytes", sa.Integer(), nullable=False),
        sa.Column("source_mtime", sa.Float(), nullable=False),
        sa.Column("pixel_frames", sa.Integer(), nullable=False),
        sa.Column("build_seconds", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        op.drop_table(TABLE)

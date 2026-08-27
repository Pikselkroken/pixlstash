"""Add the picture_split table (train/eval leakage guard).

Wave B of the tag-review takeover design
(docs/reviews/tag-review-tagger-takeover-design.md §2): one row per picture
recording its component-aware TRAIN/EVAL/NEITHER split assignment, plus a
fail-closed conflict flag/detail (no separate conflict-queue table -
``SELECT * FROM picture_split WHERE conflict = true`` is the queue).
Additive, no data migration.

Revision ID: 0068_add_picture_split
Revises: 0067_add_tag_health_precision_adj
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0068_add_picture_split"
down_revision: Union[str, None] = "0067_add_tag_health_precision_adj"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "picture_split" in inspector.get_table_names():
        return

    op.create_table(
        "picture_split",
        sa.Column("picture_id", sa.Integer(), nullable=False),
        sa.Column("split", sa.String(), nullable=False, server_default="NEITHER"),
        sa.Column("component_key", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=True),
        sa.Column(
            "conflict", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("conflict_detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("picture_id"),
    )
    op.create_index("ix_picture_split_split", "picture_split", ["split"])
    op.create_index(
        "ix_picture_split_component_key", "picture_split", ["component_key"]
    )
    op.create_index("ix_picture_split_conflict", "picture_split", ["conflict"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "picture_split" in inspector.get_table_names():
        op.drop_table("picture_split")

"""Add tag_suggestion table for the dataset-refinement review queue.

Holds suggested label fixes (add/remove a tag on a picture) with a ranking score,
a source signal, and the example that triggered them, for human review. Distinct
from tag (ground truth) and tag_prediction (raw model confidences).

Revision ID: 0057_add_tag_suggestion
Revises: 0056_add_hide_purge_snapshot_warning
Create Date: 2026-06-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0057_add_tag_suggestion"
down_revision: Union[str, None] = "0056_add_hide_purge_snapshot_warning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tag_suggestion" in inspector.get_table_names():
        return

    op.create_table(
        "tag_suggestion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("picture_id", sa.Integer(), nullable=False),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("twin_picture_id", sa.Integer(), nullable=True),
        sa.Column("twin_sim", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["picture_id"], ["picture.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["twin_picture_id"], ["picture.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("picture_id", "tag", "source"),
    )
    op.create_index("ix_tag_suggestion_picture_id", "tag_suggestion", ["picture_id"])
    op.create_index("ix_tag_suggestion_tag", "tag_suggestion", ["tag"])
    op.create_index("ix_tag_suggestion_source", "tag_suggestion", ["source"])
    op.create_index("ix_tag_suggestion_status", "tag_suggestion", ["status"])
    op.create_index("ix_tag_suggestion_twin_picture_id", "tag_suggestion", ["twin_picture_id"])
    op.create_index("ix_tag_suggestion_status_score", "tag_suggestion", ["status", "score"])
    op.create_index("ix_tag_suggestion_tag_status", "tag_suggestion", ["tag", "status"])


def downgrade() -> None:
    op.drop_table("tag_suggestion")

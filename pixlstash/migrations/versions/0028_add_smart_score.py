"""Add smart_score to picture.

Stores the pre-computed smart score (float, 1–5 range) for each picture so
that the picture stats distribution can be shown without re-running the full
ranking algorithm at query time.

Revision ID: 0028_add_smart_score
Revises: 0027_add_caption_file_mtime
Create Date: 2026-05-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0028_add_smart_score"
down_revision: Union[str, None] = "0027_add_caption_file_mtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "smart_score" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("smart_score", sa.Float, nullable=True),
        )
        op.create_index("ix_picture_smart_score", "picture", ["smart_score"])


def downgrade() -> None:
    op.drop_index("ix_picture_smart_score", table_name="picture")
    op.drop_column("picture", "smart_score")

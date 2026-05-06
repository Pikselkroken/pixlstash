"""Move text_score from quality table to picture table.

Revision ID: 0040_move_text_score_to_picture
Revises: 0039_drop_color_histogram
Create Date: 2026-05-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0040_move_text_score_to_picture"
down_revision: Union[str, None] = "0039_drop_color_histogram"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Add text_score to picture table (conditional).
    picture_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "text_score" not in picture_cols:
        op.add_column(
            "picture",
            sa.Column("text_score", sa.Float(), nullable=True),
        )
        op.create_index(
            "ix_picture_text_score", "picture", ["text_score"], if_not_exists=True
        )

    # Migrate existing values from quality.text_score → picture.text_score.
    quality_cols = {col["name"] for col in inspector.get_columns("quality")}
    if "text_score" in quality_cols:
        op.execute(
            sa.text(
                "UPDATE picture SET text_score = ("
                "  SELECT q.text_score FROM quality q"
                "  WHERE q.picture_id = picture.id AND q.text_score IS NOT NULL"
                ")"
            )
        )
        # Drop the old column from quality.
        op.drop_index("ix_quality_text_score", table_name="quality", if_exists=True)
        op.drop_column("quality", "text_score")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    quality_cols = {col["name"] for col in inspector.get_columns("quality")}
    if "text_score" not in quality_cols:
        op.add_column(
            "quality",
            sa.Column("text_score", sa.Float(), nullable=True),
        )
        op.create_index(
            "ix_quality_text_score", "quality", ["text_score"], if_not_exists=True
        )
        op.execute(
            sa.text(
                "UPDATE quality SET text_score = ("
                "  SELECT p.text_score FROM picture p"
                "  WHERE p.id = quality.picture_id AND p.text_score IS NOT NULL"
                ")"
            )
        )

    picture_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "text_score" in picture_cols:
        op.drop_index("ix_picture_text_score", table_name="picture", if_exists=True)
        op.drop_column("picture", "text_score")

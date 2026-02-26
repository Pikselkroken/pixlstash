"""add quality entropy and hue fields

Revision ID: f4a5b6c7d8e9
Revises: f3b4c5d6e7f8
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"  # noqa: F841
down_revision: Union[str, None] = "0001_baseline"  # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F841
depends_on: Union[str, Sequence[str], None] = None  # noqa: F841


def upgrade() -> None:
    conn = op.get_bind()
    existing_cols = {
        row[1]
        for row in conn.execute(sa.text("PRAGMA table_info('quality')")).fetchall()
    }
    existing_indexes = {
        row[1]
        for row in conn.execute(sa.text("PRAGMA index_list('quality')")).fetchall()
    }
    if "luminance_entropy" not in existing_cols:
        op.add_column(
            "quality",
            sa.Column(
                "luminance_entropy",
                sa.Float(),
                nullable=True,
            ),
        )
    if "dominant_hue" not in existing_cols:
        op.add_column(
            "quality",
            sa.Column(
                "dominant_hue",
                sa.Float(),
                nullable=True,
            ),
        )
    if "color_histogram" not in existing_cols:
        op.add_column(
            "quality",
            sa.Column(
                "color_histogram",
                sa.LargeBinary(),
                nullable=True,
            ),
        )
    if "ix_quality_luminance_entropy" not in existing_indexes:
        op.create_index(
            "ix_quality_luminance_entropy",
            "quality",
            ["luminance_entropy"],
            unique=False,
        )
    if "ix_quality_dominant_hue" not in existing_indexes:
        op.create_index(
            "ix_quality_dominant_hue",
            "quality",
            ["dominant_hue"],
            unique=False,
        )
    op.execute("DELETE FROM quality WHERE face_id IS NULL")


def downgrade() -> None:
    conn = op.get_bind()
    existing_cols = {
        row[1]
        for row in conn.execute(sa.text("PRAGMA table_info('quality')")).fetchall()
    }
    existing_indexes = {
        row[1]
        for row in conn.execute(sa.text("PRAGMA index_list('quality')")).fetchall()
    }
    if "ix_quality_dominant_hue" in existing_indexes:
        op.drop_index("ix_quality_dominant_hue", table_name="quality")
    if "ix_quality_luminance_entropy" in existing_indexes:
        op.drop_index("ix_quality_luminance_entropy", table_name="quality")
    if "color_histogram" in existing_cols:
        op.drop_column("quality", "color_histogram")
    if "dominant_hue" in existing_cols:
        op.drop_column("quality", "dominant_hue")
    if "luminance_entropy" in existing_cols:
        op.drop_column("quality", "luminance_entropy")

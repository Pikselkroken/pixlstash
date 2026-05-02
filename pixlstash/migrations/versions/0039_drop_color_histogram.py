"""Drop color_histogram column from quality table.

Revision ID: 0039_drop_color_histogram
Revises: 0038_add_watermark_fields
Create Date: 2026-05-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0039_drop_color_histogram"
down_revision: Union[str, None] = "0038_add_watermark_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("quality")}
    if "color_histogram" in existing_cols:
        op.drop_column("quality", "color_histogram")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("quality")}
    if "color_histogram" not in existing_cols:
        op.add_column(
            "quality",
            sa.Column("color_histogram", sa.LargeBinary, nullable=True),
        )

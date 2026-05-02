"""Drop face_id column from quality table and remove face quality rows.

Revision ID: 0034_drop_face_quality
Revises: 0033_add_import_folder_host_path
Create Date: 2026-05-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0034_drop_face_quality"
down_revision: Union[str, None] = "0033_add_import_folder_host_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("quality")}
    if "face_id" in existing_cols:
        op.execute("DELETE FROM quality WHERE face_id IS NOT NULL")
        op.drop_column("quality", "face_id")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("quality")}
    if "face_id" not in existing_cols:
        # SQLite does not support ADD COLUMN with a foreign key constraint;
        # restore the column without it. The FK was only used by FaceQualityTask
        # which has been removed.
        op.add_column(
            "quality",
            sa.Column("face_id", sa.Integer, nullable=True),
        )

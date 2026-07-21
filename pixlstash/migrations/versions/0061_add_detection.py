"""Add detection table for object-detection bounding boxes.

Revision ID: 0061_add_detection
Revises: 0060_tag_prediction_label_ledger
Create Date: 2026-06-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0061_add_detection"
down_revision: Union[str, None] = "0060_tag_prediction_label_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # The baseline migration's SQLModel.metadata.create_all() already creates
    # this table with all current model columns on a fresh DB, so this guarded
    # create only runs on pre-existing databases (table analogue of the
    # conditional add_column rule in CLAUDE.md).
    if "detection" not in inspector.get_table_names():
        op.create_table(
            "detection",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "picture_id",
                sa.Integer,
                sa.ForeignKey("picture.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("frame_index", sa.Integer, nullable=False, server_default="0"),
            sa.Column(
                "detection_index", sa.Integer, nullable=False, server_default="0"
            ),
            sa.Column("label", sa.String, nullable=True, index=True),
            sa.Column("bbox", sa.String, nullable=True),
            sa.Column("score", sa.Float, nullable=True),
            sa.Column("source", sa.String, nullable=True),
            sa.Column("attributes", sa.String, nullable=True),
        )
        op.create_index(
            "uq_detection_picture_frame_index",
            "detection",
            ["picture_id", "frame_index", "detection_index"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "detection" in inspector.get_table_names():
        op.drop_table("detection")

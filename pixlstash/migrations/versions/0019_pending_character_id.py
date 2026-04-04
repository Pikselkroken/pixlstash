"""Add pending_character_id to picture for deferred face-to-character assignment

When pictures are imported by dragging them onto a character in the sidebar,
face extraction may not have run yet.  The character-assignment endpoint now
stores the target character id on the picture row so that FaceExtractionTask
can honour it as soon as faces are detected.

Revision ID: 0019_pending_character_id
Revises: 0018_anomaly_tag_uncertainty_disagreement
Create Date: 2026-04-01 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_pending_character_id"
down_revision: Union[str, None] = "0018_anomaly_tag_uncertainty_disagreement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picture" not in existing_tables:
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "pending_character_id" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("pending_character_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_picture_pending_character_id",
            "picture",
            ["pending_character_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picture" not in existing_tables:
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}
    if "ix_picture_pending_character_id" in existing_indexes:
        op.drop_index("ix_picture_pending_character_id", table_name="picture")

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "pending_character_id" in existing_cols:
        op.drop_column("picture", "pending_character_id")

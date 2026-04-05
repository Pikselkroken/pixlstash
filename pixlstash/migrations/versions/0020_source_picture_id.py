"""Add source_picture_id to picture for deferred face-likeness character assignment

When a T2I run has a source picture selected, newly imported pictures get
source_picture_id set so that SourceFaceLikenessTask can compare face
embeddings against the source picture's faces and assign character IDs
once face extraction completes.  The field is cleared after processing.

Revision ID: 0020_source_picture_id
Revises: 0019_pending_character_id
Create Date: 2026-04-05 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_source_picture_id"
down_revision: Union[str, None] = "0019_pending_character_id"
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
    if "source_picture_id" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("source_picture_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_picture_source_picture_id",
            "picture",
            ["source_picture_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picture" not in existing_tables:
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}
    if "ix_picture_source_picture_id" in existing_indexes:
        op.drop_index("ix_picture_source_picture_id", table_name="picture")

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "source_picture_id" in existing_cols:
        op.drop_column("picture", "source_picture_id")

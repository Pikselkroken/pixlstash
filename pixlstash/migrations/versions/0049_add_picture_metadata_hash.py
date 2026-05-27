"""Add metadata_hash column to picture table.

Stores a SHA-256 fingerprint of each picture's user-visible metadata
(column values + sorted tag strings).  Used for fast checkpoint-identity
comparisons in the context-menu submenu without opening every snapshot.

Revision ID: 0049_add_picture_metadata_hash
Revises: 0048_add_checkpoint
Create Date: 2026-05-27 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0049_add_picture_metadata_hash"
down_revision: Union[str, None] = "0048_add_checkpoint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "metadata_hash" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("metadata_hash", sa.String, nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "metadata_hash" in existing_cols:
        op.drop_column("picture", "metadata_hash")

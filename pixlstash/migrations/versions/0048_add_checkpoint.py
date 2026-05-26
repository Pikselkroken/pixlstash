"""Add checkpoint table for full-database snapshot metadata.

Creates the `checkpoint` table used by the checkpoint engine to track
vault snapshots created by VACUUM INTO.

Revision ID: 0048_add_checkpoint
Revises: 0047_add_change_log
Create Date: 2026-05-26 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0048_add_checkpoint"
down_revision: Union[str, None] = "0047_add_change_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "checkpoint" not in existing_tables:
        op.create_table(
            "checkpoint",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("kind", sa.String, nullable=False, index=True),
            sa.Column("created_at", sa.DateTime, nullable=False, index=True),
            sa.Column("relative_path", sa.String, nullable=False),
            sa.Column("manifest_relative_path", sa.String, nullable=False),
            sa.Column("byte_size", sa.Integer, nullable=False),
            sa.Column("picture_count", sa.Integer, nullable=False),
            sa.Column("schema_version", sa.String, nullable=False),
            sa.Column("label", sa.String, nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "checkpoint" in inspector.get_table_names():
        op.drop_table("checkpoint")

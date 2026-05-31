"""Snapshots infrastructure: snapshot table and picture.metadata_hash.

Squashes the former snapshots-branch migrations (add_checkpoint,
add_picture_metadata_hash, reset_metadata_hash) into a single revision that
chains after 0048_normalize_stack_positions:

  - ``snapshot`` table — full-database snapshot metadata created by VACUUM INTO.
  - ``picture.metadata_hash`` column — SHA-256 fingerprint of each picture's
    user-visible metadata, used for fast snapshot-identity comparisons.

Every step is conditional so this is a no-op on a fresh database, where
0001_baseline's ``create_all()`` already produced these objects from the current
models.

Revision ID: 0049_snapshots
Revises: 0048_normalize_stack_positions
Create Date: 2026-05-27 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0049_snapshots"
down_revision: Union[str, None] = "0048_normalize_stack_positions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "snapshot" not in existing_tables:
        op.create_table(
            "snapshot",
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

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "metadata_hash" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("metadata_hash", sa.String, nullable=True),
        )

    # Force metadata_hash to NULL so it is (re)computed with the current
    # algorithm on the next compare. Harmless when the column was just created;
    # clears any value left by an earlier, superseded hash algorithm.
    op.execute("UPDATE picture SET metadata_hash = NULL")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "metadata_hash" in existing_cols:
        op.drop_column("picture", "metadata_hash")

    existing_tables = set(inspector.get_table_names())
    if "snapshot" in existing_tables:
        op.drop_table("snapshot")

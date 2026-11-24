"""Add the picture colour summary and its finder's probe index (#2151).

``picture.colour_shares`` is a JSON array of twelve swatch shares, NULL until
``MissingColourSharesFinder`` computes it. Every existing picture starts NULL,
so the finder backfills the library in the background. ``ix_picture_colour_shares_missing``
serves that finder's idle probe, partial on the NULL column.

Schema-only and additive.

Revision ID: 0149_add_picture_colour_shares
Revises: 0148_add_picture_set_smart
Create Date: 2026-11-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0149_add_picture_colour_shares"
down_revision: Union[str, None] = "0148_add_picture_set_smart"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_INDEX = "ix_picture_colour_shares_missing"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "colour_shares" not in existing_cols:
        op.add_column("picture", sa.Column("colour_shares", sa.Text(), nullable=True))
    if _INDEX not in {ix["name"] for ix in inspector.get_indexes("picture")}:
        op.create_index(
            _INDEX,
            "picture",
            ["colour_shares", "deleted", "id"],
            sqlite_where=sa.text("colour_shares IS NULL"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    if _INDEX in {ix["name"] for ix in inspector.get_indexes("picture")}:
        op.drop_index(_INDEX, table_name="picture")
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "colour_shares" in existing_cols:
        op.drop_column("picture", "colour_shares")

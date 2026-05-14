"""Add composite indexes on picture to speed up grid sort queries.

The default grid sort is ORDER BY score DESC, id DESC with WHERE deleted=0
AND import_excluded=0.  Without a covering index SQLite performs a full table
scan + filesort for every grid load.  This migration adds three composite
indexes that let SQLite satisfy the WHERE clause and the ORDER BY from the
index alone for the three most common sort keys (score, created_at,
imported_at).

Revision ID: 0044_add_grid_sort_indexes
Revises: 0043_pictureset_icon_color
Create Date: 2026-05-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0044_add_grid_sort_indexes"
down_revision: Union[str, None] = "0043_pictureset_icon_color"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install — baseline migration creates the table with all
        # columns via SQLModel.metadata.create_all(); indexes will be
        # created by SQLModel from the field definitions.  Nothing to do.
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}

    # Composite index for score-based grid sort:
    #   WHERE deleted=0 AND import_excluded=0 ORDER BY score DESC, id DESC
    if "ix_picture_grid_score" not in existing_indexes:
        op.create_index(
            "ix_picture_grid_score",
            "picture",
            ["deleted", "import_excluded", "score", "id"],
        )

    # Composite index for creation-date grid sort:
    #   WHERE deleted=0 AND import_excluded=0 ORDER BY created_at DESC, id DESC
    if "ix_picture_grid_created_at" not in existing_indexes:
        op.create_index(
            "ix_picture_grid_created_at",
            "picture",
            ["deleted", "import_excluded", "created_at", "id"],
        )

    # Composite index for import-date grid sort:
    #   WHERE deleted=0 AND import_excluded=0 ORDER BY imported_at DESC, id DESC
    # (imported_at has a single-column index already; the composite replaces it
    # for grid queries and covers the filter columns too)
    if "ix_picture_grid_imported_at" not in existing_indexes:
        op.create_index(
            "ix_picture_grid_imported_at",
            "picture",
            ["deleted", "import_excluded", "imported_at", "id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}

    for index_name in (
        "ix_picture_grid_score",
        "ix_picture_grid_created_at",
        "ix_picture_grid_imported_at",
    ):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="picture")

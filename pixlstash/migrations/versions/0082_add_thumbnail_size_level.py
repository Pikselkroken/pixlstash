"""Add ``user.thumbnail_size_level`` (unified grid thumbnail size).

v1.8.0 unifies the grid thumbnail-size controls behind a single per-user size
index (0..6, larger index = fewer/larger thumbnails, default ``3`` = Medium).
This migration adds:

* ``user.thumbnail_size_level`` - the per-user unified size index.

Existing installs already carry a legacy ``user.columns`` preference (a raw
column count). To preserve each user's chosen density, the new column is
backfilled by mapping every row's ``columns`` value to the NEAREST canonical
size index using the representative square column counts::

    size_index -> columns:  0:12, 1:10, 2:8, 3:6, 4:5, 5:4, 6:3

Ties break toward the LARGER column count (the smaller size index). Rows whose
``columns`` is NULL fall back to the default ``3``.

Why a NEW migration rather than amending an earlier one: prior revisions are
already stamped on installs that ran an earlier v1.8.0 build, so Alembic would
never re-run an amended version. New schema goes in a new file (repo policy).

Revision ID: 0082_add_thumbnail_size_level
Revises: 0080_add_thumbnail_dimensions
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0082_add_thumbnail_size_level"
down_revision: Union[str, None] = "0080_add_thumbnail_dimensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# Canonical size index -> representative square column count.
_SIZE_TO_COLUMNS = {0: 12, 1: 10, 2: 8, 3: 6, 4: 5, 5: 4, 6: 3}
_DEFAULT_SIZE = 3


def _columns_to_size_index(columns: int) -> int:
    """Map a raw ``columns`` count to the nearest canonical size index.

    Ties break toward the LARGER representative column count, i.e. the smaller
    size index (checking indices in ascending order - which is descending column
    count - and only replacing on a strictly smaller distance gives that).
    """
    best_index = _DEFAULT_SIZE
    best_distance = None
    for index in sorted(_SIZE_TO_COLUMNS):  # ascending index = descending columns
        distance = abs(_SIZE_TO_COLUMNS[index] - columns)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user" not in inspector.get_table_names():
        # Fresh install - the baseline migration creates ``user`` with all
        # current model columns (thumbnail_size_level present); nothing to do.
        return

    existing_user_cols = {col["name"] for col in inspector.get_columns("user")}
    if "thumbnail_size_level" not in existing_user_cols:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "thumbnail_size_level",
                    sa.Integer(),
                    nullable=True,
                    server_default="3",
                )
            )

    # One-time data backfill: derive each user's unified size index from their
    # existing ``columns`` preference. NULL columns -> default.
    if "columns" in existing_user_cols:
        rows = bind.execute(sa.text("SELECT id, columns FROM user")).fetchall()
        for row_id, columns in rows:
            if columns is None:
                size_index = _DEFAULT_SIZE
            else:
                size_index = _columns_to_size_index(int(columns))
            bind.execute(
                sa.text("UPDATE user SET thumbnail_size_level = :size WHERE id = :id"),
                {"size": size_index, "id": row_id},
            )
    else:
        # No legacy ``columns`` column to read from - stamp the default so no row
        # is left NULL.
        op.execute("UPDATE user SET thumbnail_size_level = 3")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user" not in inspector.get_table_names():
        return

    existing_user_cols = {col["name"] for col in inspector.get_columns("user")}
    if "thumbnail_size_level" in existing_user_cols:
        with op.batch_alter_table("user") as batch_op:
            batch_op.drop_column("thumbnail_size_level")

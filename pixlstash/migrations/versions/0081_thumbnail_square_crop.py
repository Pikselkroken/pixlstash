"""Single-bitmap thumbnails: add square-crop rectangle, drop original-space crop.

The justified-thumbnail design changed after ``0080`` shipped. Instead of storing
two crops (a square thumbnail and an aspect-ratio thumbnail) and regenerating on
every mode switch, PixlStash now stores ONE aspect-ratio-preserving whole-frame
bitmap plus the face-weighted SQUARE-CROP rectangle within it; the frontend crops
the bitmap for square mode. This migration carries that schema change:

* Adds ``picture.square_crop_x`` / ``square_crop_y`` / ``square_crop_side`` — the
  square crop's top-left and side in the bitmap's own pixel space.
* Drops the superseded original-space ``picture.thumbnail_left`` / ``thumbnail_top``
  / ``thumbnail_side`` (bbox mapping is now done in bitmap space).
* NULLs the thumbnail columns for every row so ``MissingThumbnailFinder`` (keyed on
  ``thumbnail_width IS NULL``) regenerates the whole-frame bitmap exactly ONCE on
  upgrade — existing installs only have square crops, which cannot serve the
  justified layout.

Why a NEW migration rather than amending ``0080``: ``0080`` was already applied on
installs that ran an earlier v1.8.0 build, so Alembic has it stamped and would
never re-run an amended version. New schema goes in a new file (repo policy).

Revision ID: 0081_thumbnail_square_crop
Revises: 0080_add_thumbnail_dimensions
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0081_thumbnail_square_crop"
down_revision: Union[str, None] = "0080_add_thumbnail_dimensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_NEW_COLS = ("square_crop_x", "square_crop_y", "square_crop_side")
_OLD_COLS = ("thumbnail_left", "thumbnail_top", "thumbnail_side")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install — the baseline migration already created ``picture`` with
        # all current model columns (square_crop_* present, thumbnail_left/top/side
        # absent), so there is no schema work and nothing to regenerate.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    to_add = [c for c in _NEW_COLS if c not in existing_cols]
    to_drop = [c for c in _OLD_COLS if c in existing_cols]

    if to_add or to_drop:
        # batch_alter_table: SQLite cannot ALTER ... DROP COLUMN without a table
        # rebuild, which batch mode performs.
        with op.batch_alter_table("picture") as batch_op:
            for name in to_add:
                batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))
            for name in to_drop:
                batch_op.drop_column(name)

    # Force a one-time regeneration of the whole-frame bitmap for existing rows.
    # Only meaningful when thumbnail_width already exists (installs upgrading from
    # 0080); on a fresh install these columns are already NULL with no rows to hit.
    if "thumbnail_width" in existing_cols:
        op.execute(
            "UPDATE picture SET "
            "thumbnail_width = NULL, thumbnail_height = NULL, "
            "square_crop_x = NULL, square_crop_y = NULL, square_crop_side = NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    to_add_back = [c for c in _OLD_COLS if c not in existing_cols]
    to_remove = [c for c in _NEW_COLS if c in existing_cols]

    if to_add_back or to_remove:
        with op.batch_alter_table("picture") as batch_op:
            for name in to_add_back:
                batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))
            for name in to_remove:
                batch_op.drop_column(name)

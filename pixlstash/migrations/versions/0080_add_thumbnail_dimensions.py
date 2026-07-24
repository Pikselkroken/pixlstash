"""Add whole-frame thumbnail bitmap dims + square-crop rect + ``user.thumbnail_mode``.

v1.8.0 stores ONE aspect-ratio-preserving thumbnail bitmap of the whole frame
per picture, plus a face-weighted SQUARE-CROP rectangle within it so the grid can
render a square cell client-side. Mode switching is display-only (frontend); the
backend never regenerates on a switch. This migration:

* Adds ``picture.thumbnail_width`` / ``picture.thumbnail_height`` — the AR bitmap's
  stored pixel dimensions (used to size each cell and to map face/detection
  overlays into bitmap space).
* Adds ``picture.square_crop_x`` / ``square_crop_y`` / ``square_crop_side`` — the
  square-crop rectangle within the bitmap, in bitmap pixels.
* Drops the superseded original-space ``picture.thumbnail_left`` / ``thumbnail_top``
  / ``thumbnail_side`` columns (the bitmap is the whole frame, so overlays map with
  a single scale from the picture dimensions — no stored crop origin is needed).
* Adds ``user.thumbnail_mode`` — the per-user grid shape preference
  (``"square"`` | ``"justified"``, default ``"square"``), now display-only.

Existing installs only have the old square/justified crops, so every thumbnail
column is reset to NULL for ALL rows. ``MissingThumbnailFinder`` (keyed on
``thumbnail_width IS NULL``) then regenerates the whole-frame bitmap exactly ONCE
on the next run. This one-time upgrade regeneration is the only regeneration that
remains — there is no per-mode-switch regeneration.

Revision ID: 0080_add_thumbnail_dimensions
Revises: 0079_add_picture_deleted_at
Create Date: 2026-07-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0080_add_thumbnail_dimensions"
down_revision: Union[str, None] = "0079_add_picture_deleted_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_NEW_PICTURE_COLS = (
    "thumbnail_width",
    "thumbnail_height",
    "square_crop_x",
    "square_crop_y",
    "square_crop_side",
)
_OLD_PICTURE_COLS = ("thumbnail_left", "thumbnail_top", "thumbnail_side")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "picture" in tables:
        existing_cols = {col["name"] for col in inspector.get_columns("picture")}
        add_cols = [c for c in _NEW_PICTURE_COLS if c not in existing_cols]
        drop_cols = [c for c in _OLD_PICTURE_COLS if c in existing_cols]
        if add_cols or drop_cols:
            with op.batch_alter_table("picture") as batch_op:
                for name in add_cols:
                    batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))
                for name in drop_cols:
                    batch_op.drop_column(name)

        # One-time regeneration of the whole-frame AR bitmap for existing rows:
        # NULL every thumbnail column so MissingThumbnailFinder reprocesses each
        # picture exactly once. No-op on a fresh install (no rows).
        op.execute(
            "UPDATE picture SET "
            "thumbnail_width = NULL, thumbnail_height = NULL, "
            "square_crop_x = NULL, square_crop_y = NULL, square_crop_side = NULL"
        )

    if "user" in tables:
        existing_user_cols = {col["name"] for col in inspector.get_columns("user")}
        if "thumbnail_mode" not in existing_user_cols:
            with op.batch_alter_table("user") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "thumbnail_mode",
                        sa.String(),
                        nullable=True,
                        server_default="square",
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "user" in tables:
        existing_user_cols = {col["name"] for col in inspector.get_columns("user")}
        if "thumbnail_mode" in existing_user_cols:
            with op.batch_alter_table("user") as batch_op:
                batch_op.drop_column("thumbnail_mode")

    if "picture" in tables:
        existing_cols = {col["name"] for col in inspector.get_columns("picture")}
        add_back = [c for c in _OLD_PICTURE_COLS if c not in existing_cols]
        drop_new = [c for c in _NEW_PICTURE_COLS if c in existing_cols]
        if add_back or drop_new:
            with op.batch_alter_table("picture") as batch_op:
                for name in add_back:
                    batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))
                for name in drop_new:
                    batch_op.drop_column(name)

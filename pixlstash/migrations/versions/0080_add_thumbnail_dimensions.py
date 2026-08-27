"""Thumbnail bitmap schema: stored dimensions, square-crop rectangle, grid mode.

v1.8.0 stores ONE aspect-ratio-preserving whole-frame thumbnail bitmap plus the
face-weighted square-crop rectangle within it; the frontend crops that bitmap for
square mode and lays the whole frame out for justified mode. This migration adds
the schema for that:

* ``picture.thumbnail_width`` / ``thumbnail_height`` - the stored bitmap's pixel
  dimensions. The frontend needs them to size each cell, and the batch-thumbnail
  endpoint needs them to map face/detection overlays onto non-square thumbnails.
* ``picture.square_crop_x`` / ``square_crop_y`` / ``square_crop_side`` - the square
  crop's top-left and side, in the bitmap's own pixel space.
* ``user.thumbnail_mode`` - the per-user grid shape preference
  (``"square"`` | ``"justified"``, default ``"square"``).

It also drops the superseded original-space ``picture.thumbnail_left`` /
``thumbnail_top`` / ``thumbnail_side`` if a database still carries them (bbox
mapping is now done in bitmap space). No released build ever created those
columns via a migration; the drop only matters for a database whose baseline
``create_all()`` built them from an older model.

Existing thumbnails are not regenerated here as a matter of course: the
dimension columns start NULL and ``MissingThumbnailFinder`` (keyed on
``thumbnail_width IS NULL``) fills them in lazily. The one exception is a
database that already had ``thumbnail_width`` before this migration ran, which
can only hold square crops that cannot serve the justified layout - those rows
are NULLed so the finder regenerates the whole-frame bitmap exactly once.

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

_NEW_COLS = (
    "thumbnail_width",
    "thumbnail_height",
    "square_crop_x",
    "square_crop_y",
    "square_crop_side",
)
_OLD_COLS = ("thumbnail_left", "thumbnail_top", "thumbnail_side")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install - the baseline migration creates the table with all
        # current model columns via SQLModel.metadata.create_all(), so the
        # square_crop_* columns are already present and there is nothing to do.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    # Only a database that already carried thumbnail dimensions can hold stale
    # square-only crops; a fresh upgrade from 0079 starts with these NULL anyway.
    had_thumbnail_dimensions = "thumbnail_width" in existing_cols

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

    if "user" in inspector.get_table_names():
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

    if had_thumbnail_dimensions:
        op.execute(
            "UPDATE picture SET "
            "thumbnail_width = NULL, thumbnail_height = NULL, "
            "square_crop_x = NULL, square_crop_y = NULL, square_crop_side = NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user" in inspector.get_table_names():
        existing_user_cols = {col["name"] for col in inspector.get_columns("user")}
        if "thumbnail_mode" in existing_user_cols:
            with op.batch_alter_table("user") as batch_op:
                batch_op.drop_column("thumbnail_mode")

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

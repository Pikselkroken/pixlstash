"""Add thumbnail dimensions + ``user.thumbnail_mode`` (justified thumbnails).

v1.8.0 adds a "justified" thumbnail mode: thumbnails may be stored
aspect-ratio-preserving instead of force-cropped square, so a justified grid can
lay them out. This migration adds:

* ``picture.thumbnail_width`` / ``picture.thumbnail_height`` — the actual stored
  thumbnail pixel dimensions on disk. The frontend needs them to size each cell,
  and the batch-thumbnail endpoint needs them to map face/detection overlays onto
  non-square thumbnails.
* ``user.thumbnail_mode`` — the per-user grid shape preference
  (``"square"`` | ``"justified"``, default ``"square"``).

The picture dimension columns are **not** backfilled here: existing thumbnails
are perfectly valid square thumbnails and must not be regenerated just because
the schema grew. Dimensions are filled in lazily by ``MissingThumbnailFinder``
(which reads them from the existing thumbnail file — no image reprocessing), and
full regeneration only happens when the user opts in by switching
``thumbnail_mode`` (handled in the config PATCH handler, never in a migration).

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


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install — the baseline migration creates the table with all
        # current columns via SQLModel.metadata.create_all(); nothing to do.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}

    if "thumbnail_width" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("thumbnail_width", sa.Integer(), nullable=True),
        )
    if "thumbnail_height" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("thumbnail_height", sa.Integer(), nullable=True),
        )

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
    if "thumbnail_height" in existing_cols:
        op.drop_column("picture", "thumbnail_height")
    if "thumbnail_width" in existing_cols:
        op.drop_column("picture", "thumbnail_width")

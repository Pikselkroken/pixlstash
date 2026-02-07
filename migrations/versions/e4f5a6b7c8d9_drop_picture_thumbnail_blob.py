"""drop picture thumbnail blob

Revision ID: e4f5a6b7c8d9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-10 00:00:00.000000

"""

from typing import Sequence, Union

import logging
import os

from alembic import op
import sqlalchemy as sa

from pixlvault.picture_utils import PictureUtils


# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    db_path = None
    if bind is not None and getattr(bind, "engine", None) is not None:
        db_path = bind.engine.url.database
    image_root = os.path.dirname(db_path) if db_path else None

    logger = logging.getLogger(__name__)

    picture_table = sa.table(
        "picture",
        sa.column("id", sa.Integer()),
        sa.column("file_path", sa.String()),
        sa.column("thumbnail", sa.LargeBinary()),
    )

    if image_root:
        try:
            rows = bind.execute(
                sa.select(
                    picture_table.c.id,
                    picture_table.c.file_path,
                    picture_table.c.thumbnail,
                )
            ).fetchall()
        except Exception as exc:
            logger.warning("Failed to load thumbnails for migration: %s", exc)
            rows = []

        for row in rows:
            try:
                file_path = row.file_path
                if not file_path:
                    continue
                thumb_bytes = row.thumbnail
                if not thumb_bytes:
                    resolved = PictureUtils.resolve_picture_path(image_root, file_path)
                    if resolved and os.path.exists(resolved):
                        img = PictureUtils.load_image_or_video(resolved)
                        if img is not None:
                            thumb_bytes = PictureUtils.generate_thumbnail_bytes(img)
                if not thumb_bytes:
                    continue
                thumb_path = PictureUtils.get_thumbnail_path(image_root, file_path)
                if thumb_path and os.path.exists(thumb_path):
                    continue
                saved = PictureUtils.write_thumbnail_bytes(
                    image_root, file_path, thumb_bytes
                )
                if not saved:
                    logger.warning(
                        "Failed to write thumbnail during migration for picture %s",
                        row.id,
                    )
            except Exception as exc:
                logger.warning(
                    "Thumbnail migration failed for picture %s: %s", row.id, exc
                )

    with op.batch_alter_table("picture", schema=None) as batch_op:
        batch_op.drop_column("thumbnail")


def downgrade() -> None:
    with op.batch_alter_table("picture", schema=None) as batch_op:
        batch_op.add_column(sa.Column("thumbnail", sa.LargeBinary(), nullable=True))

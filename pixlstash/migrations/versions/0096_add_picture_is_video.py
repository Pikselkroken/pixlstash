"""Persist video/image flag as a column instead of deriving from file extension at query time.

Currently ``GET /characters/{id}/thumbnail`` sorts on a CASE expression that
tests ``file_path`` against five extension patterns, one per frame:

    CASE WHEN file_path ILIKE '%.mp4' THEN 0
         WHEN file_path ILIKE '%.webm' THEN 0
         ...
         ELSE 1
    END

This is non-sargable (defeats LIMIT 1), so every call materialises and sorts
all pictures of the character including on cache revalidation. This migration
adds a persistent ``is_video`` boolean column - False for still images, True
for video - to replace the CASE at query time.

The backfill is extension-based (inlined literal from ``VIDEO_EXTENSIONS``,
``pixlstash/utils/image_processing/video_utils.py``). At runtime the flag
comes from whether the decode path treated the file as a video (``PIL.Image``
fails to open it as an image and the fallback checks ``VideoUtils.is_video_file``).
A video file with an unlisted extension therefore gets ``1`` at import and ``0``
from this backfill. That is acceptable because the consumer it replaces was also
extension-based.

Revision ID: 0096_add_picture_is_video
Revises: 0095_add_finder_partial_indexes
Create Date: 2026-08-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0096_add_picture_is_video"
down_revision: Union[str, None] = "0095_add_finder_partial_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        # Fresh install - the baseline migration creates the table with all
        # current model columns via SQLModel.metadata.create_all(), so the
        # is_video column is already present and there is nothing to do.
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "is_video" not in existing_cols:
        # Only a database that existed before this change needs the ALTER.
        # Fresh installs already have the column from 0001_baseline.
        #
        # NOT NULL with a server default, matching the model: SQLite sorts NULL
        # first, so a NULL would outrank False in the thumbnail ordering this
        # column exists to serve, and the ``= 0`` guard below would never see it
        # to repair it. The server default is what fills the existing rows here.
        with op.batch_alter_table("picture") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_video",
                    sa.Boolean(),
                    nullable=False,
                    server_default="0",
                )
            )

    # Backfill videos by extension (inlined from VIDEO_EXTENSIONS to avoid
    # importing application code in a migration). Restricted to rows still at
    # the default so a replay cannot clobber a runtime-derived True; the column
    # is NOT NULL, so ``= 0`` reaches every not-yet-classified row.
    op.execute(
        sa.text(
            "UPDATE picture SET is_video = 1 "
            "WHERE is_video = 0 "
            "AND (lower(file_path) LIKE '%.mp4' "
            "  OR lower(file_path) LIKE '%.webm' "
            "  OR lower(file_path) LIKE '%.avi' "
            "  OR lower(file_path) LIKE '%.mov' "
            "  OR lower(file_path) LIKE '%.mkv')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "picture" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "is_video" in existing_cols:
        with op.batch_alter_table("picture") as batch_op:
            batch_op.drop_column("is_video")

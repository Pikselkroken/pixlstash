"""Re-embed GIFs now that animated ones are sampled like videos, for #1487.

An animated GIF used to be embedded, hashed and scored from frame 0 alone; it now
averages the same three frames a video does (``VideoUtils.is_animated_gif``).
``ImageEmbeddingTask`` selects on ``image_embedding IS NULL`` or
``aesthetic_score IS NULL``, so those are cleared on every ``.gif``. A static GIF
recomputes to the same values.

Likeness pairs are written with insert-or-ignore, so re-queueing a picture never
replaces a pair built from the old embedding. ``size_bin_index`` is cleared as
well, the marker ``LikenessParametersTask`` selects on, and that task deletes the
picture's pairs before re-queueing it (``reset_likeness_for_pictures``).

Face rows are left alone: deleting them would drop character assignments.

Revision ID: 0123_resample_animated_gifs
Revises: 0122_rescan_pictures_for_controlnet_model_names
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0123_resample_animated_gifs"
down_revision: Union[str, None] = "0122_rescan_pictures_for_controlnet_model_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    if "picture" not in sa.inspect(op.get_bind()).get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    op.execute(
        "UPDATE picture SET image_embedding = NULL, perceptual_hash = NULL, "
        "aesthetic_score = NULL, size_bin_index = NULL "
        "WHERE lower(file_path) LIKE '%.gif'"
    )


def downgrade() -> None:
    # The reset only asks for a recompute; there is nothing to restore.
    pass

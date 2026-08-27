"""Add ``character.thumbnail_picture_id`` - the pinned thumbnail picture.

``GET /characters/{id}/thumbnail`` used to have no input but the data: it took
the character's highest-scoring reference picture and cropped the face out of
it. This column lets the person editor pin one of those reference images
instead. NULL, the default, keeps the automatic choice, so nothing changes for
an existing library until somebody picks.

Plain integer, not a foreign key: pictures are hard-deleted (scrapheap purge,
maintenance) and a real FK would abort those deletes under
``PRAGMA foreign_keys=ON``. The route treats an id that no longer carries a face
of this character as "no pin".

Revision ID: 0105_add_character_thumbnail_picture
Revises: 0104_add_picture_orientation
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0105_add_character_thumbnail_picture"
down_revision: Union[str, None] = "0104_add_picture_orientation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character" not in set(inspector.get_table_names()):
        return

    # Conditional because the baseline runs SQLModel.metadata.create_all(), which
    # builds `character` with every current model column.
    existing_cols = {col["name"] for col in inspector.get_columns("character")}
    if "thumbnail_picture_id" not in existing_cols:
        op.add_column(
            "character", sa.Column("thumbnail_picture_id", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character" not in set(inspector.get_table_names()):
        return
    if "thumbnail_picture_id" in {
        col["name"] for col in inspector.get_columns("character")
    }:
        op.drop_column("character", "thumbnail_picture_id")

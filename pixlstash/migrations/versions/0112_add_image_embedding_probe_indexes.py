"""Index the image-embedding idle probe and retire the empty-blob failure marker.

``ImageEmbeddingTask.fetch_work`` selected ``image_embedding IS NULL OR
length(image_embedding) = 0 OR aesthetic_score IS NULL``. The ``length`` arm
defeated any partial index (``SCAN picture`` on every planner sweep, issue #651
rules), and it only existed because a failed picture was written back as an
empty blob meaning "select me again" -- which NULL already means. The task now
writes NULL on failure, so the one-time ``UPDATE`` here converts the markers
already on disk; nothing else about the column changes.

Two partial indexes, same shape as ``0111``: ``ix_picture_image_embedding_missing``
on ``(image_embedding, id) WHERE image_embedding IS NULL`` and
``ix_picture_aesthetic_score_missing`` on ``(aesthetic_score, id) WHERE
aesthetic_score IS NULL``. SQLite serves the remaining two-arm OR as a
MULTI-INDEX OR over the pair. Both are declared on the model, so each create is
guarded on the index name.

Revision ID: 0112_add_image_embedding_probe_indexes
Revises: 0111_add_text_embedding_missing_index
Create Date: 2026-08-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0112_add_image_embedding_probe_indexes"
down_revision: Union[str, None] = "0111_add_text_embedding_missing_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_INDEXES = {
    "ix_picture_image_embedding_missing": "image_embedding",
    "ix_picture_aesthetic_score_missing": "aesthetic_score",
}


def _picture_indexes(inspector) -> set:
    if "picture" not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes("picture")}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    op.execute(
        "UPDATE picture SET image_embedding = NULL "
        "WHERE image_embedding IS NOT NULL AND length(image_embedding) = 0"
    )
    existing = _picture_indexes(inspector)
    for name, column in _INDEXES.items():
        if name not in existing:
            op.create_index(
                name,
                "picture",
                [column, "id"],
                sqlite_where=sa.text(f"{column} IS NULL"),
            )


def downgrade() -> None:
    existing = _picture_indexes(sa.inspect(op.get_bind()))
    for name in _INDEXES:
        if name in existing:
            op.drop_index(name, table_name="picture")

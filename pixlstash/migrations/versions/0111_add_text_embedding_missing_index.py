"""Add the text-embedding idle-probe partial index.

Schema-only, no data touched. ``ix_picture_text_embedding_missing`` --
``picture (text_embedding, id) WHERE text_embedding IS NULL`` -- serves
``MissingTextEmbeddingFinder``, which now sweeps per picture instead of behind
a stage barrier, so its probe runs on every planner cycle (issue #651 rules).
Same shape and reasoning as ``0095``; no ``deleted`` column because the probe
does not filter on it, and the trailing ``id`` keeps ``ORDER BY picture.id``
free. Declared on the model too, so the create is guarded on the index name.

Revision ID: 0111_add_text_embedding_missing_index
Revises: 0110_add_folder_mapping_commit
Create Date: 2026-08-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0111_add_text_embedding_missing_index"
down_revision: Union[str, None] = "0110_add_folder_mapping_commit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_INDEX = "ix_picture_text_embedding_missing"


def _picture_indexes(inspector) -> set:
    if "picture" not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes("picture")}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" in inspector.get_table_names() and _INDEX not in _picture_indexes(
        inspector
    ):
        op.create_index(
            _INDEX,
            "picture",
            ["text_embedding", "id"],
            sqlite_where=sa.text("text_embedding IS NULL"),
        )


def downgrade() -> None:
    if _INDEX in _picture_indexes(sa.inspect(op.get_bind())):
        op.drop_index(_INDEX, table_name="picture")

"""Add partial indexes for the hot idle work probes and the character listing.

Schema-only (issue #651). Three indexes, no data touched and no reprocessing
reset - nothing about the meaning of any column changes, so no ``Missing*Finder``
needs to re-run.

``ix_picture_thumbnail_missing`` - ``picture (thumbnail_width, deleted, id)
WHERE thumbnail_width IS NULL``. Serves
``MissingThumbnailFinder._fetch_missing``: ``WHERE thumbnail_width IS NULL AND
deleted IS 0 AND file_path IS NOT NULL ORDER BY id``.

``ix_picture_smart_score_missing`` - ``picture (smart_score, deleted, id) WHERE
smart_score IS NULL``. Serves
``SmartScoreTask.find_pictures_missing_smart_score``: ``WHERE image_embedding IS
NOT NULL AND smart_score IS NULL AND deleted IS 0 ORDER BY id``.

Both are *idle probes*: the WorkPlanner sweeps every finder on a short interval,
so they run continuously on a fully-processed library where they match nothing.
Before this migration SQLite served both from ``ix_picture_deleted`` and walked
EVERY non-deleted row to prove there was no work - the cost of "is there
anything to do?" was proportional to library size, paid several times a second.

Column order is load-bearing. The intuitive ``(id) WHERE <col> IS NULL`` form is
NOT selected by the planner: this database never runs ``ANALYZE``, so there is no
``sqlite_stat1``, and without statistics SQLite scores a partial index using the
table's default row estimate rather than the partial row count. Such an index
only wins when it can claim MORE equality terms than the index it competes with.
Leading with the nullable column makes ``<col> IS NULL`` a usable equality term
(SQLite treats ``IS NULL`` as one) and ``deleted`` a second, which beats
single-term ``ix_picture_deleted`` outright instead of tying with it and losing on
an index-creation-order tie-break. Trailing ``id`` keeps ``ORDER BY picture.id``
free (no temp B-tree). Measured on 200k rows with 3 matching, no ``sqlite_stat1``:
thumbnail probe 17.9 ms -> under 0.01 ms, smart-score probe 67.5 ms -> under
0.01 ms.

``ix_face_character_features`` - ``face (character_id) WHERE features IS NOT
NULL``. Answers "which characters have a face carrying an embedding?" behind
``GET /characters``. Plain ``ix_face_character_id`` covers every face, embedded
or not, and needs one table lookup per row to test ``features IS NOT NULL``;
scoped to the embedded faces the answer comes out of the index alone. This one is
only dependable for ONE-PASS shapes (``GROUP BY character_id`` / ``DISTINCT
character_id``); a per-character ``character_id = ?`` probe ties with
``ix_face_character_id`` on cost and the tie is broken by index-creation order,
which varies per process. See the note on ``Face.__table_args__``.

All three are declared on the models as well, so a fresh database already has
them from the baseline's ``SQLModel.metadata.create_all()``. Each create is
therefore guarded on the existing index names, exactly as ``0091`` guards its
own.

Revision ID: 0095_add_finder_partial_indexes
Revises: 0094_add_telemetry_consent
Create Date: 2026-08-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0095_add_finder_partial_indexes"
down_revision: Union[str, None] = "0094_add_telemetry_consent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_THUMBNAIL_INDEX = "ix_picture_thumbnail_missing"
_SMART_SCORE_INDEX = "ix_picture_smart_score_missing"
_FACE_FEATURES_INDEX = "ix_face_character_features"


def _index_names(inspector, existing_tables: set, table: str) -> set:
    """Index names already on *table*, or an empty set when it does not exist.

    A partial/synthetic database (the migration tests hand-build a minimal
    schema) may not carry every table; asking the inspector about a missing one
    would raise.
    """
    if table not in existing_tables:
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    picture_indexes = _index_names(inspector, existing_tables, "picture")
    if "picture" in existing_tables:
        if _THUMBNAIL_INDEX not in picture_indexes:
            op.create_index(
                _THUMBNAIL_INDEX,
                "picture",
                ["thumbnail_width", "deleted", "id"],
                sqlite_where=sa.text("thumbnail_width IS NULL"),
            )
        if _SMART_SCORE_INDEX not in picture_indexes:
            op.create_index(
                _SMART_SCORE_INDEX,
                "picture",
                ["smart_score", "deleted", "id"],
                sqlite_where=sa.text("smart_score IS NULL"),
            )

    if "face" in existing_tables:
        if _FACE_FEATURES_INDEX not in _index_names(inspector, existing_tables, "face"):
            op.create_index(
                _FACE_FEATURES_INDEX,
                "face",
                ["character_id"],
                sqlite_where=sa.text("features IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    picture_indexes = _index_names(inspector, existing_tables, "picture")
    for name in (_SMART_SCORE_INDEX, _THUMBNAIL_INDEX):
        if name in picture_indexes:
            op.drop_index(name, table_name="picture")

    if _FACE_FEATURES_INDEX in _index_names(inspector, existing_tables, "face"):
        op.drop_index(_FACE_FEATURES_INDEX, table_name="face")

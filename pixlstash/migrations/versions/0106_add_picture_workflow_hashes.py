"""Add the workflow-library keys to ``picture``.

Four columns beside the existing ``comfyui_*`` ones, per the workflow
implementation plan §B3. The relationship is 1:1 with no extra attributes and
``picture`` already carries ``pixel_sha``, ``perceptual_hash`` and
``metadata_hash``, so hash columns belong here rather than in a side table.

``workflow_instance_hash`` is the third tier: the recipe with one set of
parameters, the prompt included and the seed excluded. It lives here and only
here -- an instance carries what a person wrote, and the hub-side instance
table is Phase 2 work that moved to v1.12.

``workflow_hash_version`` is the scanned-marker: NULL means never scanned, and
a set value means scanned with all three ``workflow_*_hash`` columns NULL when
the picture carried no executable graph. It is also the re-hash selector when the rule changes, since
``WHERE workflow_hash_version = 'v1'`` names exactly the affected rows -- though
re-queueing them runs them back through the whole extraction task, which also
NULLs ``text_embedding`` on every picture carrying ComfyUI data. The column
selects; it does not make the re-hash cheap.

All three hashes are indexed. The library view's counts are a ``GROUP BY
workflow_topology_hash``, "pictures made with this workflow" is a lookup on the
structural one, and "which pictures share this instance" is a lookup on the
third.

A third, partial index serves the finder's idle probe
(``workflow_hash_version IS NULL AND deleted IS 0 ORDER BY id``), in the column
order ``0095`` measured and explains: leading with the nullable column makes
``IS NULL`` a usable equality term, so it beats ``ix_picture_deleted`` instead
of tying with it, and trailing ``id`` keeps the ORDER BY free.

No NULL-reset here: the columns are new, so every row is already unscanned and
``MissingComfyUIExtractionFinder`` picks them up on the next run.

Revision ID: 0106_add_picture_workflow_hashes
Revises: 0105_add_character_thumbnail_picture
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0106_add_picture_workflow_hashes"
down_revision: Union[str, None] = "0105_add_character_thumbnail_picture"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = (
    "workflow_topology_hash",
    "workflow_structural_hash",
    "workflow_instance_hash",
    "workflow_hash_version",
)

_INDEXES = (
    ("ix_picture_workflow_topology_hash", ["workflow_topology_hash"], None),
    ("ix_picture_workflow_structural_hash", ["workflow_structural_hash"], None),
    ("ix_picture_workflow_instance_hash", ["workflow_instance_hash"], None),
    (
        "ix_picture_workflow_unscanned",
        ["workflow_hash_version", "deleted", "id"],
        "workflow_hash_version IS NULL",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "picture" not in set(inspector.get_table_names()):
        return

    # Conditional because the baseline runs SQLModel.metadata.create_all(), which
    # builds `picture` with every current model column.
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for column in _COLUMNS:
        if column not in existing_cols:
            op.add_column("picture", sa.Column(column, sa.String(), nullable=True))

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}
    for name, columns, where in _INDEXES:
        if name not in existing_indexes:
            kwargs = {"sqlite_where": sa.text(where)} if where else {}
            op.create_index(name, "picture", columns, **kwargs)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "picture" not in set(inspector.get_table_names()):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}
    for name, _columns, _where in _INDEXES:
        if name in existing_indexes:
            op.drop_index(name, table_name="picture")

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    for column in _COLUMNS:
        if column in existing_cols:
            op.drop_column("picture", column)

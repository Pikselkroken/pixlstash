"""Add the quality-crop re-check flag to pictures (#1648).

``quality_crop_pending`` is set by ``POST /taggers/pixlstash_tagger/
quality-crop/recheck`` on already-tagged pictures and cleared by the task that
re-runs the PixlStash tagger's quality crop over them, found through the
partial index ``ix_picture_quality_crop_pending``. Every existing row starts
False: nothing is re-checked until the owner asks.

A flag rather than a NULL-reset, because a NULL-reset would also select every
picture imported or tagged afterwards, and those already ran the crop pass at
the current size.

Conditional, because the baseline's ``create_all()`` already builds the column
and the index from the model on a fresh database.

Revision ID: 0125_add_picture_quality_crop_pending
Revises: 0124_saved_recipe_models_and_workflow_id
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0125_add_picture_quality_crop_pending"
down_revision: Union[str, None] = "0124_saved_recipe_models_and_workflow_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMN = "quality_crop_pending"
_PENDING_INDEX = "ix_picture_quality_crop_pending"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        # Partial databases used by migration tests carry no picture table.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if _COLUMN not in existing_cols:
        op.add_column(
            "picture",
            sa.Column(
                _COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    # Declared on the model too, so a fresh database already has it from the
    # baseline. Guarded by name so both paths converge on the same index.
    if _PENDING_INDEX not in {idx["name"] for idx in inspector.get_indexes("picture")}:
        op.create_index(
            _PENDING_INDEX,
            "picture",
            [_COLUMN, "deleted", "id"],
            sqlite_where=sa.text("quality_crop_pending = 1"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "picture" not in inspector.get_table_names():
        return
    if _PENDING_INDEX in {idx["name"] for idx in inspector.get_indexes("picture")}:
        op.drop_index(_PENDING_INDEX, table_name="picture")
    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    if _COLUMN in existing_cols:
        # A plain DROP COLUMN, as 0120 does: a batch rebuild of ``picture``
        # would recreate the table and lose the triggers declared on it.
        op.drop_column("picture", _COLUMN)

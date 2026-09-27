"""A saved recipe names its workflow and pins its models (#1622).

``workflow_id`` is the owner-facing workflow a recipe runs on, filled in by the
cut-over (#1623) from the hub's ``workflow_key_successor``; ``models`` is the
JSON list of models the recipe pins by loader address. Both NULL on every
existing row, which is the right answer: NULL ``models`` inherits the
workflow's default recipe.

Conditional, because the baseline's ``create_all()`` already builds both
columns from the model on a fresh database.

Revision ID: 0124_saved_recipe_models_and_workflow_id
Revises: 0123_resample_animated_gifs
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0124_saved_recipe_models_and_workflow_id"
down_revision: Union[str, None] = "0123_resample_animated_gifs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = (
    ("workflow_id", sa.String()),
    ("models", sa.Text()),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("saved_recipe"):
        # Partial databases used by migration tests carry no saved_recipe.
        return
    existing_cols = {col["name"] for col in inspector.get_columns("saved_recipe")}
    for name, column_type in _COLUMNS:
        if name not in existing_cols:
            op.add_column("saved_recipe", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("saved_recipe"):
        return
    existing_cols = {col["name"] for col in inspector.get_columns("saved_recipe")}
    with op.batch_alter_table("saved_recipe") as batch:
        for name, _ in _COLUMNS:
            if name in existing_cols:
                batch.drop_column(name)

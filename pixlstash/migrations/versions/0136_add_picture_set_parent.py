"""Add ``parent_set_id`` and ``parent_character_id`` to ``pictureset``.

A child set sits under another set or under a person (#2124). Both nullable
INTEGER, indexed; NULL is a top-level set, so existing sets need no data step.

Schema-only and additive.

Revision ID: 0136_add_picture_set_parent
Revises: 0135_add_training_presets_auto_check
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0136_add_picture_set_parent"
down_revision: Union[str, None] = "0135_add_training_presets_auto_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_TABLE = "pictureset"
_COLUMNS = ("parent_set_id", "parent_character_id")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns(_TABLE)}
    for column in _COLUMNS:
        if column not in existing_cols:
            op.add_column(_TABLE, sa.Column(column, sa.Integer(), nullable=True))
        # `if_not_exists` rather than reflecting the indexes: the table carries
        # an expression index SQLAlchemy cannot reflect, and warns on every run.
        op.create_index(f"ix_{_TABLE}_{column}", _TABLE, [column], if_not_exists=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns(_TABLE)}
    for column in _COLUMNS:
        op.drop_index(f"ix_{_TABLE}_{column}", table_name=_TABLE, if_exists=True)
        if column in existing_cols:
            op.drop_column(_TABLE, column)

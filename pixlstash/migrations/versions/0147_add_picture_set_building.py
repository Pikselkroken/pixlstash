"""Add ``pictureset.building``: a training set's build, stored on the set.

"Create a training set…" makes the set at once, and the screen that builds it
keeps what it holds (its step, the sheet draws, the picker's choices) here, so
leaving the screen loses nothing. Null for every existing set: they are all
finished.

Schema-only and additive.

Revision ID: 0147_add_picture_set_building
Revises: 0146_add_character_reference_cluster
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0147_add_picture_set_building"
down_revision: Union[str, None] = "0146_add_character_reference_cluster"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

TABLE = "pictureset"
COLUMN = "building"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns(TABLE)}
    if COLUMN not in existing_cols:
        op.add_column(TABLE, sa.Column(COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns(TABLE)}
    if COLUMN in existing_cols:
        op.drop_column(TABLE, COLUMN)

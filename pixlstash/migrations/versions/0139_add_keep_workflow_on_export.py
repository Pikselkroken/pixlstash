"""Add ``user.keep_workflow_on_export``: whether an export keeps generation data.

A nullable boolean; NULL reads as the model default, strip, so an existing user
needs no data step.

Schema-only and additive.

Revision ID: 0139_add_keep_workflow_on_export
Revises: 0138_add_picture_unseen
Create Date: 2026-10-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0139_add_keep_workflow_on_export"
down_revision: Union[str, None] = "0138_add_picture_unseen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMN = "keep_workflow_on_export"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if _COLUMN not in existing_cols:
        op.add_column("user", sa.Column(_COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if _COLUMN in existing_cols:
        op.drop_column("user", _COLUMN)

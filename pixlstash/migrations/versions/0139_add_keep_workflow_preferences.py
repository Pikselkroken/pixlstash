"""Add the shared-copies preferences ``keep_workflow_copy``/``_drag``/``_share``.

Whether each way a file leaves PixlStash keeps its generation data. Nullable
booleans; NULL reads as the model default, strip, so an existing user needs no
data step.

Schema-only and additive.

Revision ID: 0139_add_keep_workflow_preferences
Revises: 0138_add_picture_unseen
Create Date: 2026-10-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0139_add_keep_workflow_preferences"
down_revision: Union[str, None] = "0138_add_picture_unseen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = ("keep_workflow_copy", "keep_workflow_drag", "keep_workflow_share")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    for column in _COLUMNS:
        if column not in existing_cols:
            op.add_column("user", sa.Column(column, sa.Boolean(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    for column in _COLUMNS:
        if column in existing_cols:
            op.drop_column("user", column)

"""Add the mouse preferences ``grid_open_gesture`` and ``viewer_wheel_action``.

Both nullable TEXT. NULL reads as the model default (``"double"`` and
``"zoom"``), so an existing user gets double-click-to-open without a data step.

Schema-only and additive.

Revision ID: 0133_add_mouse_preferences
Revises: 0132_add_picture_pending_person
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0133_add_mouse_preferences"
down_revision: Union[str, None] = "0132_add_picture_pending_person"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = ("grid_open_gesture", "viewer_wheel_action")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    for column in _COLUMNS:
        if column not in existing_cols:
            op.add_column("user", sa.Column(column, sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    for column in _COLUMNS:
        if column in existing_cols:
            op.drop_column("user", column)

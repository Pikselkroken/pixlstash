"""Add the What's changed columns ``last_seen_version`` and ``whats_changed``.

Both nullable TEXT. NULL reads as never seen (``last_seen_version``) and as
nothing to show (``whats_changed``); the frontend fills both the first time
it runs 2.0, so an existing user needs no data step.

Schema-only and additive.

Revision ID: 0143_add_whats_changed
Revises: 0142_recompute_smart_score_for_face_resolution
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0143_add_whats_changed"
down_revision: Union[str, None] = "0142_recompute_smart_score_for_face_resolution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMNS = ("last_seen_version", "whats_changed")


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

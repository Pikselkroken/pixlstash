"""Add the keyed owner-settings fingerprint to each library.

This is separate from guarded migration 0092 so a developer vault that already
ran the earlier feature-lane revision is upgraded instead of silently missing
the column.

Revision ID: 0095_add_settings_fingerprint
Revises: 0094_add_pending_score_invalidation
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0095_add_settings_fingerprint"
down_revision: Union[str, None] = "0094_add_pending_score_invalidation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "library_settings" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("library_settings")}
    if "settings_fingerprint" not in columns:
        op.add_column(
            "library_settings",
            sa.Column("settings_fingerprint", sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "library_settings" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("library_settings")}
    if "settings_fingerprint" in columns:
        op.drop_column("library_settings", "settings_fingerprint")

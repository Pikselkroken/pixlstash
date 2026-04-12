"""Add show_keyboard_hint preference to user.

Controls whether the F1 keyboard shortcut indicator FAB is shown
in the bottom-right corner of the UI. Defaults to True (visible).

Revision ID: 0023_add_show_keyboard_hint
Revises: 0022_add_check_for_updates
Create Date: 2026-04-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0023_add_show_keyboard_hint"
down_revision: Union[str, None] = "0022_add_check_for_updates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("user")}

    if "show_keyboard_hint" not in existing_cols:
        op.add_column(
            "user",
            sa.Column("show_keyboard_hint", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("user", "show_keyboard_hint")

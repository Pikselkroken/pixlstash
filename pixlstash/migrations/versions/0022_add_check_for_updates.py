"""Add check_for_updates preference to user.

Null means the user has not yet been asked (undecided).
True / False reflects their explicit choice.

Revision ID: 0022_add_check_for_updates
Revises: 0021_tagger_enabled_flags
Create Date: 2026-04-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0022_add_check_for_updates"
down_revision: Union[str, None] = "0021_tagger_enabled_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("user")}

    if "check_for_updates" not in existing_cols:
        op.add_column(
            "user",
            sa.Column("check_for_updates", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("user", "check_for_updates")

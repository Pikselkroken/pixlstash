"""Add ``user.guide_welcome_seen`` for the Guide's welcome (#2825).

Nullable TEXT: the newest New in 2.x headline id the welcome was shown for.
NULL reads as never shown, which is right for every existing user.

Schema-only and additive.

Revision ID: 0152_add_guide_welcome_seen
Revises: 0151_add_studio
Create Date: 2026-11-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0152_add_guide_welcome_seen"
down_revision: Union[str, None] = "0151_add_studio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if "guide_welcome_seen" not in existing_cols:
        op.add_column(
            "user", sa.Column("guide_welcome_seen", sa.String(), nullable=True)
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if "guide_welcome_seen" in existing_cols:
        op.drop_column("user", "guide_welcome_seen")

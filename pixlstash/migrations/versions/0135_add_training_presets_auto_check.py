"""Add ``training_presets_auto_check``: check pixlstash.dev for newer recipes.

Nullable BOOLEAN. NULL means the owner has not been asked, which reads as off,
so an existing user is never checked for without saying so.

Schema-only and additive.

Revision ID: 0135_add_training_presets_auto_check
Revises: 0134_add_picture_favourite
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0135_add_training_presets_auto_check"
down_revision: Union[str, None] = "0134_add_picture_favourite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMN = "training_presets_auto_check"


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

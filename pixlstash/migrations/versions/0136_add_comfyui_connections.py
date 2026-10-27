"""Add ``comfyui_connections``: every ComfyUI the owner can run on (#2167).

Nullable TEXT (a JSON list). NULL reads as one connection for the saved
``comfyui_url`` (``services/comfyui_connections.read``), so an existing setup
upgrades with no data step and runs go where they went.

Schema-only and additive.

Revision ID: 0136_add_comfyui_connections
Revises: 0135_add_training_presets_auto_check
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0136_add_comfyui_connections"
down_revision: Union[str, None] = "0135_add_training_presets_auto_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_COLUMN = "comfyui_connections"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if _COLUMN not in existing_cols:
        op.add_column("user", sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if _COLUMN in existing_cols:
        op.drop_column("user", _COLUMN)

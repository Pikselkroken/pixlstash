"""add comfyui_positive_prompt, comfyui_models, comfyui_loras to picture

Revision ID: 0004_add_comfyui_fields
Revises: 0003_add_original_file_name
Create Date: 2026-03-21 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_comfyui_fields"
down_revision: Union[str, None] = "0003_add_original_file_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("picture")}
    if "comfyui_positive_prompt" not in existing_columns:
        op.add_column(
            "picture",
            sa.Column("comfyui_positive_prompt", sa.String(), nullable=True),
        )
    if "comfyui_models" not in existing_columns:
        op.add_column(
            "picture",
            sa.Column("comfyui_models", sa.String(), nullable=True),
        )
    if "comfyui_loras" not in existing_columns:
        op.add_column(
            "picture",
            sa.Column("comfyui_loras", sa.String(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("picture", "comfyui_positive_prompt")
    op.drop_column("picture", "comfyui_models")
    op.drop_column("picture", "comfyui_loras")

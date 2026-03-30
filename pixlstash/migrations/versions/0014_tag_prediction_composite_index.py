"""Add composite index on tag_prediction(model_version, picture_id)

Revision ID: 0014_tag_prediction_composite_index
Revises: 0013_tag_prediction
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_tag_prediction_composite_index"
down_revision: Union[str, None] = "0013_tag_prediction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("tag_prediction")}
    if "ix_tag_prediction_model_version_picture_id" not in existing_indexes:
        op.create_index(
            "ix_tag_prediction_model_version_picture_id",
            "tag_prediction",
            ["model_version", "picture_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_tag_prediction_model_version_picture_id",
        table_name="tag_prediction",
    )

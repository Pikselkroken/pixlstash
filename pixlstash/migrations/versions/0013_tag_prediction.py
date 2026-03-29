"""Add tag_prediction table and tag_uncertainty column

Revision ID: 0013_tag_prediction
Revises: 0012_character_name_uniqueness
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_tag_prediction"
down_revision: Union[str, None] = "0012_character_name_uniqueness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Add tag_uncertainty column to picture table
    if "picture" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("picture")}
        if "tag_uncertainty" not in existing_cols:
            op.add_column(
                "picture",
                sa.Column("tag_uncertainty", sa.Float(), nullable=True),
            )
            op.create_index(
                "ix_picture_tag_uncertainty",
                "picture",
                ["tag_uncertainty"],
            )

    # Create tag_prediction table
    if "tag_prediction" not in existing_tables:
        op.create_table(
            "tag_prediction",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("model_version", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
            sa.Column("predicted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["picture_id"],
                ["picture.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "picture_id", "tag", name="uq_tag_prediction_picture_tag"
            ),
        )
        op.create_index(
            "ix_tag_prediction_picture_id", "tag_prediction", ["picture_id"]
        )
        op.create_index("ix_tag_prediction_tag", "tag_prediction", ["tag"])
        op.create_index(
            "ix_tag_prediction_model_version", "tag_prediction", ["model_version"]
        )
        op.create_index("ix_tag_prediction_status", "tag_prediction", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "tag_prediction" in existing_tables:
        op.drop_index("ix_tag_prediction_status", table_name="tag_prediction")
        op.drop_index("ix_tag_prediction_model_version", table_name="tag_prediction")
        op.drop_index("ix_tag_prediction_tag", table_name="tag_prediction")
        op.drop_index("ix_tag_prediction_picture_id", table_name="tag_prediction")
        op.drop_table("tag_prediction")

    if "picture" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("picture")}
        if "tag_uncertainty" in existing_cols:
            op.drop_index("ix_picture_tag_uncertainty", table_name="picture")
            op.drop_column("picture", "tag_uncertainty")

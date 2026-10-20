"""add trained_lora table

Revision ID: 0134_add_trained_lora
Revises: 0133_add_training_job
Create Date: 2026-06-16 00:00:01.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0134_add_trained_lora"
down_revision: Union[str, None] = "0133_add_training_job"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "trained_lora" not in existing_tables:
        op.create_table(
            "trained_lora",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("uuid", sa.String(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=True),
            sa.Column("set_id", sa.Integer(), nullable=True),
            sa.Column("character_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("file_path", sa.String(), nullable=True),
            sa.Column("file_size_bytes", sa.Integer(), nullable=True),
            sa.Column("file_sha", sa.String(), nullable=True),
            sa.Column("step", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["job_id"], ["training_job.id"]),
            sa.ForeignKeyConstraint(["set_id"], ["pictureset.id"]),
            sa.ForeignKeyConstraint(["character_id"], ["character.id"]),
            sa.UniqueConstraint("uuid", name="uq_trained_lora_uuid"),
        )
        op.create_index("ix_trained_lora_uuid", "trained_lora", ["uuid"], unique=True)
        op.create_index("ix_trained_lora_job_id", "trained_lora", ["job_id"])
        op.create_index("ix_trained_lora_set_id", "trained_lora", ["set_id"])
        op.create_index(
            "ix_trained_lora_character_id", "trained_lora", ["character_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "trained_lora" in set(inspector.get_table_names()):
        op.drop_index("ix_trained_lora_character_id", table_name="trained_lora")
        op.drop_index("ix_trained_lora_set_id", table_name="trained_lora")
        op.drop_index("ix_trained_lora_job_id", table_name="trained_lora")
        op.drop_index("ix_trained_lora_uuid", table_name="trained_lora")
        op.drop_table("trained_lora")

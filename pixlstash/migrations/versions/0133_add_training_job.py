"""add training_job table

Revision ID: 0133_add_training_job
Revises: 0132_add_picture_pending_person
Create Date: 2026-06-16 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0133_add_training_job"
down_revision: Union[str, None] = "0132_add_picture_pending_person"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "training_job" not in existing_tables:
        op.create_table(
            "training_job",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("uuid", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="queued"),
            sa.Column("set_id", sa.Integer(), nullable=True),
            sa.Column("character_id", sa.Integer(), nullable=True),
            sa.Column("base_model", sa.String(), nullable=True),
            sa.Column("trigger_word", sa.String(), nullable=True),
            sa.Column("rendered_config", sa.String(), nullable=True),
            sa.Column("dataset_snapshot_path", sa.String(), nullable=True),
            sa.Column("dataset_picture_ids", sa.String(), nullable=True),
            sa.Column("ai_toolkit_commit", sa.String(), nullable=True),
            sa.Column("training_folder", sa.String(), nullable=True),
            sa.Column("pid", sa.Integer(), nullable=True),
            sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_steps", sa.Integer(), nullable=True),
            sa.Column("loss_curve", sa.String(), nullable=True),
            sa.Column("sample_image_paths", sa.String(), nullable=True),
            sa.Column("log_tail", sa.String(), nullable=True),
            sa.Column("report", sa.String(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["set_id"], ["pictureset.id"]),
            sa.ForeignKeyConstraint(["character_id"], ["character.id"]),
            sa.UniqueConstraint("uuid", name="uq_training_job_uuid"),
        )
        op.create_index("ix_training_job_uuid", "training_job", ["uuid"], unique=True)
        op.create_index("ix_training_job_status", "training_job", ["status"])
        op.create_index("ix_training_job_set_id", "training_job", ["set_id"])
        op.create_index(
            "ix_training_job_character_id", "training_job", ["character_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "training_job" in set(inspector.get_table_names()):
        op.drop_index("ix_training_job_character_id", table_name="training_job")
        op.drop_index("ix_training_job_set_id", table_name="training_job")
        op.drop_index("ix_training_job_status", table_name="training_job")
        op.drop_index("ix_training_job_uuid", table_name="training_job")
        op.drop_table("training_job")

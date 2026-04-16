"""Add reference_folder table and reference_folder_id to picture.

Revision ID: 0025_add_reference_folders
Revises: 0024_add_deleted_file_log
Create Date: 2026-04-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0025_add_reference_folders"
down_revision: Union[str, None] = "0024_add_deleted_file_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "reference_folder" not in existing_tables:
        op.create_table(
            "reference_folder",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("folder", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=False, server_default=""),
            sa.Column(
                "allow_delete_file", sa.Boolean(), nullable=False, server_default="0"
            ),
            sa.Column(
                "status", sa.String(), nullable=False, server_default="pending_mount"
            ),
            sa.Column("last_scanned", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_reference_folder_folder", "reference_folder", ["folder"])
        op.create_index("ix_reference_folder_status", "reference_folder", ["status"])

    existing_picture_cols = {col["name"] for col in inspector.get_columns("picture")}
    if "reference_folder_id" not in existing_picture_cols:
        op.add_column(
            "picture",
            sa.Column("reference_folder_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_picture_reference_folder_id", "picture", ["reference_folder_id"]
        )


def downgrade() -> None:
    op.drop_index("ix_picture_reference_folder_id", table_name="picture")
    op.drop_column("picture", "reference_folder_id")
    op.drop_index("ix_reference_folder_status", table_name="reference_folder")
    op.drop_index("ix_reference_folder_folder", table_name="reference_folder")
    op.drop_table("reference_folder")

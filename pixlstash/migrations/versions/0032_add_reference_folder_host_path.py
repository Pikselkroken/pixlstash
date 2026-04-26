"""Add host_path to reference_folder.

Revision ID: 0032_add_reference_folder_host_path
Revises: 0031_migrate_watch_folders_to_import_folder
Create Date: 2026-04-26 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0032_add_reference_folder_host_path"
down_revision: Union[str, None] = "0031_migrate_watch_folders_to_import_folder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "reference_folder" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("reference_folder")}
    if "host_path" not in existing_cols:
        op.add_column(
            "reference_folder", sa.Column("host_path", sa.String(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "reference_folder" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("reference_folder")}
    if "host_path" in existing_cols:
        op.drop_column("reference_folder", "host_path")

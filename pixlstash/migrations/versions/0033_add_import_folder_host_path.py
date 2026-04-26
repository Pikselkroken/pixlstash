"""Add host_path to import_folder.

Revision ID: 0033_add_import_folder_host_path
Revises: 0032_add_reference_folder_host_path
Create Date: 2026-04-26 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0033_add_import_folder_host_path"
down_revision: Union[str, None] = "0032_add_reference_folder_host_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "import_folder" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("import_folder")}
    if "host_path" not in existing_cols:
        op.add_column(
            "import_folder", sa.Column("host_path", sa.String(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "import_folder" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("import_folder")}
    if "host_path" in existing_cols:
        op.drop_column("import_folder", "host_path")

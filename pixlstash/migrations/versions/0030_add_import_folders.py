"""Add import folders and picture import provenance.

Stores watch/import folders in the database so they can be managed through the
same API pattern as reference folders, and stores the import folder path used
for each imported picture.

Revision ID: 0030_add_import_folders
Revises: 0029_add_import_excluded_to_picture
Create Date: 2026-04-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0030_add_import_folders"
down_revision: Union[str, None] = "0029_add_import_excluded_to_picture"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "import_folder" not in existing_tables:
        op.create_table(
            "import_folder",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("folder", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=False, server_default=""),
            sa.Column(
                "delete_after_import", sa.Boolean(), nullable=False, server_default="0"
            ),
            sa.Column("last_checked", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    import_folder_indexes = (
        {idx["name"] for idx in inspector.get_indexes("import_folder")}
        if "import_folder" in existing_tables
        else set()
    )
    if "ix_import_folder_folder" not in import_folder_indexes:
        op.create_index("ix_import_folder_folder", "import_folder", ["folder"])

    existing_cols = {col["name"] for col in inspector.get_columns("picture")}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}

    if "import_source_folder" not in existing_cols:
        op.add_column(
            "picture",
            sa.Column("import_source_folder", sa.String(), nullable=True),
        )

    if "ix_picture_import_source_folder" not in existing_indexes:
        op.create_index(
            "ix_picture_import_source_folder",
            "picture",
            ["import_source_folder"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "picture" in existing_tables:
        existing_cols = {col["name"] for col in inspector.get_columns("picture")}
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("picture")}

        if "ix_picture_import_source_folder" in existing_indexes:
            op.drop_index("ix_picture_import_source_folder", table_name="picture")

        if "import_source_folder" in existing_cols:
            op.drop_column("picture", "import_source_folder")

    if "import_folder" in existing_tables:
        import_folder_indexes = {
            idx["name"] for idx in inspector.get_indexes("import_folder")
        }
        if "ix_import_folder_folder" in import_folder_indexes:
            op.drop_index("ix_import_folder_folder", table_name="import_folder")
        op.drop_table("import_folder")

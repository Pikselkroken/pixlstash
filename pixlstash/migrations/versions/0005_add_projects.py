"""add projects and project_attachments tables; add project_id to character and pictureset

Revision ID: 0005_add_projects
Revises: 0004_add_comfyui_fields
Create Date: 2026-03-21 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_add_projects"  # noqa: F841
down_revision: Union[str, None] = "0004_add_comfyui_fields"  # noqa: F841
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F841
depends_on: Union[str, Sequence[str], None] = None  # noqa: F841


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # Create project table
    if "project" not in existing_tables:
        op.create_table(
            "project",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("cover_image_path", sa.String(), nullable=True),
            sa.Column("extra_metadata", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_project_name", "project", ["name"])

    # Create projectattachment table
    if "projectattachment" not in existing_tables:
        op.create_table(
            "projectattachment",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("original_filename", sa.String(), nullable=False),
            sa.Column("stored_path", sa.String(), nullable=False),
            sa.Column("mime_type", sa.String(), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_projectattachment_project_id",
            "projectattachment",
            ["project_id"],
        )

    # Add project_id to character (batch mode required for FK in SQLite)
    character_columns = {col["name"] for col in inspector.get_columns("character")}
    if "project_id" not in character_columns:
        with op.batch_alter_table("character") as batch_op:
            batch_op.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_character_project_id",
                "project",
                ["project_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index("ix_character_project_id", ["project_id"])

    # Add project_id to pictureset (batch mode required for FK in SQLite)
    pictureset_columns = {col["name"] for col in inspector.get_columns("pictureset")}
    if "project_id" not in pictureset_columns:
        with op.batch_alter_table("pictureset") as batch_op:
            batch_op.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_pictureset_project_id",
                "project",
                ["project_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index("ix_pictureset_project_id", ["project_id"])


def downgrade() -> None:
    with op.batch_alter_table("pictureset") as batch_op:
        batch_op.drop_index("ix_pictureset_project_id")
        batch_op.drop_constraint("fk_pictureset_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")
    with op.batch_alter_table("character") as batch_op:
        batch_op.drop_index("ix_character_project_id")
        batch_op.drop_constraint("fk_character_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")
    op.drop_index("ix_projectattachment_project_id", table_name="projectattachment")
    op.drop_table("projectattachment")
    op.drop_index("ix_project_name", table_name="project")
    op.drop_table("project")

"""add picture stacks

Revision ID: 0b1c2d3e4f5a
Revises: ff5a6b7c8d9e
Create Date: 2026-02-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


# revision identifiers, used by Alembic.
revision = "0b1c2d3e4f5a"  # noqa: F841
down_revision = "ff5a6b7c8d9e"  # noqa: F841
branch_labels = None  # noqa: F841
depends_on = None  # noqa: F841


def upgrade() -> None:
    if not _table_exists("picturestack"):
        op.create_table(
            "picturestack",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_picturestack_name",
            "picturestack",
            ["name"],
            unique=False,
        )

    if not _table_exists("picture"):
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("picture")}
    if "stack_id" not in columns:
        op.add_column(
            "picture",
            sa.Column("stack_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_picture_stack_id",
            "picture",
            ["stack_id"],
            unique=False,
        )
        op.create_foreign_key(
            "fk_picture_stack_id_picturestack",
            "picture",
            "picturestack",
            ["stack_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_picture_stack_id_picturestack", "picture", type_="foreignkey"
    )
    op.drop_index("ix_picture_stack_id", table_name="picture")
    op.drop_index("ix_picturestack_name", table_name="picturestack")
    op.drop_column("picture", "stack_id")
    op.drop_table("picturestack")

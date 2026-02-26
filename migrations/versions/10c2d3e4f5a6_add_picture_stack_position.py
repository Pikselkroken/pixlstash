"""add picture stack position

Revision ID: 10c2d3e4f5a6
Revises: 0b1c2d3e4f5a
Create Date: 2026-02-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


# revision identifiers, used by Alembic.
revision = "10c2d3e4f5a6"  # noqa: F841
down_revision = "0b1c2d3e4f5a"  # noqa: F841
branch_labels = None  # noqa: F841
depends_on = None  # noqa: F841


def upgrade() -> None:
    if not _table_exists("picture"):
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("picture")}
    if "stack_position" not in columns:
        op.add_column(
            "picture",
            sa.Column("stack_position", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_picture_stack_position",
            "picture",
            ["stack_position"],
            unique=False,
        )
        op.create_index(
            "ix_picture_stack_id_position",
            "picture",
            ["stack_id", "stack_position"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_picture_stack_id_position", table_name="picture")
    op.drop_index("ix_picture_stack_position", table_name="picture")
    op.drop_column("picture", "stack_position")

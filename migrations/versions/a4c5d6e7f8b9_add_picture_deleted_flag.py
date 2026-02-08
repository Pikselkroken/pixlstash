"""add picture deleted flag

Revision ID: a4c5d6e7f8b9
Revises: f2b3c4d5e6f7
Create Date: 2026-02-10 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a4c5d6e7f8b9"
down_revision: Union[str, None] = "f2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("picture", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index("ix_picture_deleted", ["deleted"], unique=False)

    op.execute("UPDATE picture SET deleted = 0 WHERE deleted IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("picture", schema=None) as batch_op:
        batch_op.drop_index("ix_picture_deleted")
        batch_op.drop_column("deleted")

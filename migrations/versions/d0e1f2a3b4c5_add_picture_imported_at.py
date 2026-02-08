"""add picture imported_at

Revision ID: d0e1f2a3b4c5
Revises: c7d8e9f0a1b2
Create Date: 2026-02-08 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("picture", schema=None) as batch_op:
        batch_op.add_column(sa.Column("imported_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_picture_imported_at", ["imported_at"], unique=False)

    op.execute("UPDATE picture SET imported_at = created_at WHERE imported_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("picture", schema=None) as batch_op:
        batch_op.drop_index("ix_picture_imported_at")
        batch_op.drop_column("imported_at")

"""add user overlay preferences

Revision ID: c2f3d4e5a6b7
Revises: b7c2c9f6c0d1
Create Date: 2026-01-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2f3d4e5a6b7"
down_revision: Union[str, None] = "e3f2a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user", sa.Column("show_hand_bboxes", sa.Boolean(), nullable=True))
    op.add_column("user", sa.Column("show_format", sa.Boolean(), nullable=True))
    op.add_column("user", sa.Column("show_resolution", sa.Boolean(), nullable=True))
    op.add_column("user", sa.Column("show_problem_icon", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("show_problem_icon")
        batch_op.drop_column("show_resolution")
        batch_op.drop_column("show_format")
        batch_op.drop_column("show_hand_bboxes")

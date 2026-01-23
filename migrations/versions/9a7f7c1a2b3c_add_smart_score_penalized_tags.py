"""add smart score penalized tags

Revision ID: 9a7f7c1a2b3c
Revises: 3c8a6f2e5d21
Create Date: 2026-01-26 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9a7f7c1a2b3c"
down_revision: Union[str, None] = "3c8a6f2e5d21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("smart_score_penalized_tags", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("smart_score_penalized_tags")

"""drop face likeness column

Revision ID: b1c2d3e4f5a6
Revises: 9a7f7c1a2b3c
Create Date: 2026-02-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "9a7f7c1a2b3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("face", schema=None) as batch_op:
        batch_op.drop_column("likeness")


def downgrade() -> None:
    with op.batch_alter_table("face", schema=None) as batch_op:
        batch_op.add_column(sa.Column("likeness", sa.Float(), nullable=True))

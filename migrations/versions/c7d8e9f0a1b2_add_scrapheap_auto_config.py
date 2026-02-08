"""add smart score scrapheap settings

Revision ID: c7d8e9f0a1b2
Revises: b2c4d6e8f0a1
Create Date: 2026-02-08 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b2c4d6e8f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "auto_scrapheap_smart_score_threshold",
            sa.Float(),
            nullable=True,
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "auto_scrapheap_lookback_minutes",
            sa.Integer(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("auto_scrapheap_lookback_minutes")
        batch_op.drop_column("auto_scrapheap_smart_score_threshold")

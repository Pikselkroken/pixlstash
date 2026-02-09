"""merge heads

Revision ID: d6f7a8b9c0d1
Revises: d0e1f2a3b4c5, d5e6f7a8b9c0
Create Date: 2026-02-09 00:00:00.000000
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "d6f7a8b9c0d1"
down_revision: Union[str, None] = ("d0e1f2a3b4c5", "d5e6f7a8b9c0")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

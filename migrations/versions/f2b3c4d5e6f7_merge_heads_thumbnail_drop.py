"""merge heads (thumbnail drop)

Revision ID: f2b3c4d5e6f7
Revises: e4f5a6b7c8d9, f1a2b3c4d5e6
Create Date: 2026-02-06 00:00:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "f2b3c4d5e6f7"
down_revision: Union[str, None] = ("e4f5a6b7c8d9", "f1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

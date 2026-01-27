"""merge heads

Revision ID: c3d4e5f6a7b8
Revises: a8b9c0d1e2f3, b1c2d3e4f5a6
Create Date: 2026-02-02 00:00:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = ("a8b9c0d1e2f3", "b1c2d3e4f5a6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

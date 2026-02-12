"""merge heads quality and likeness

Revision ID: f7a8b9c0d1e2
Revises: f4c5d6e7f8a9, f6a7b8c9d0e1
Create Date: 2026-02-12 00:00:00.000000
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = (
    "f4c5d6e7f8a9",
    "f6a7b8c9d0e1",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

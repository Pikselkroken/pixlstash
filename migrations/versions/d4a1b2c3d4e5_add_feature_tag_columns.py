"""add feature tag columns

Revision ID: d4a1b2c3d4e5
Revises: b7c2c9f6c0d1
Create Date: 2026-01-29 00:00:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "d4a1b2c3d4e5"
down_revision: Union[str, None] = "b7c2c9f6c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Legacy placeholder migration kept for revision continuity.
    pass


def downgrade() -> None:
    pass

"""merge heads

Revision ID: b17c8d9e0f1a
Revises: 11a2b3c4d5e6, a06b7c8d9e0f
Create Date: 2026-03-01 00:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b17c8d9e0f1a"  # noqa: F841
down_revision = ("11a2b3c4d5e6", "a06b7c8d9e0f")  # noqa: F841
branch_labels = None  # noqa: F841
depends_on = None  # noqa: F841


def upgrade() -> None:
    op.execute("SELECT 1")


def downgrade() -> None:
    pass

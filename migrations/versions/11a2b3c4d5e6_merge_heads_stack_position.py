"""merge heads stack position

Revision ID: 11a2b3c4d5e6
Revises: 0d1e2f3a4b5c, 10c2d3e4f5a6
Create Date: 2026-02-22 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "11a2b3c4d5e6"  # noqa: F841
down_revision = ("0d1e2f3a4b5c", "10c2d3e4f5a6")  # noqa: F841
branch_labels = None  # noqa: F841
depends_on = None  # noqa: F841


def upgrade() -> None:
    op.execute("SELECT 1")


def downgrade() -> None:
    op.execute("SELECT 1")

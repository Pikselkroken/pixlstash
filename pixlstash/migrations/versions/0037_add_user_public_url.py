"""Add public_url column to user table.

Revision ID: 0037_add_user_public_url
Revises: 0036_add_token_include_attachments
Create Date: 2026-05-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0037_add_user_public_url"
down_revision: Union[str, None] = "0036_add_token_include_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("user")}
    if "public_url" not in existing_cols:
        op.add_column("user", sa.Column("public_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "public_url")

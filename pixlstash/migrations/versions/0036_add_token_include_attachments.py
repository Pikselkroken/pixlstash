"""Add include_attachments column to usertoken table.

Revision ID: 0036_add_token_include_attachments
Revises: 0035_drop_face_quality
Create Date: 2026-05-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0036_add_token_include_attachments"
down_revision: Union[str, None] = "0035_drop_face_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("usertoken")}
    if "include_attachments" not in existing_cols:
        op.add_column(
            "usertoken",
            sa.Column(
                "include_attachments",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    op.drop_column("usertoken", "include_attachments")

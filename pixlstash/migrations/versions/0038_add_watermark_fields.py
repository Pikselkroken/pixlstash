"""Add watermark fields to user and usertoken tables.

Revision ID: 0038_add_watermark_fields
Revises: 0037_add_user_public_url
Create Date: 2026-05-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0038_add_watermark_fields"
down_revision: Union[str, None] = "0037_add_user_public_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # usertoken table
    token_cols = {col["name"] for col in inspector.get_columns("usertoken")}
    if "watermark" not in token_cols:
        op.add_column(
            "usertoken",
            sa.Column(
                "watermark",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    # user table
    user_cols = {col["name"] for col in inspector.get_columns("user")}
    if "embed_watermark" not in user_cols:
        op.add_column(
            "user",
            sa.Column(
                "embed_watermark",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )
    if "watermark_image" not in user_cols:
        op.add_column(
            "user",
            sa.Column("watermark_image", sa.LargeBinary(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("usertoken", "watermark")
    op.drop_column("user", "embed_watermark")
    op.drop_column("user", "watermark_image")

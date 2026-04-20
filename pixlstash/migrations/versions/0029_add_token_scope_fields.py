"""Add scope, resource, and expiry fields to usertoken.

Extends UserToken with:
- token_prefix: first 8 chars of raw token for fast indexed lookup
- scope: "ALL" (default, existing behaviour) or "READ"
- resource_type: optional target resource kind ("picture_set", "character", "project")
- resource_id: optional target resource PK
- expires_at: optional token expiry timestamp

Revision ID: 0029_add_token_scope_fields
Revises: 0028_add_smart_score
Create Date: 2026-04-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0029_add_token_scope_fields"
down_revision: Union[str, None] = "0028_add_smart_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("usertoken")}

    if "token_prefix" not in existing_cols:
        op.add_column("usertoken", sa.Column("token_prefix", sa.String, nullable=True))
        op.create_index("ix_usertoken_token_prefix", "usertoken", ["token_prefix"])

    if "scope" not in existing_cols:
        # server_default ensures existing rows get "ALL" without requiring a NULL column
        op.add_column(
            "usertoken",
            sa.Column("scope", sa.String, nullable=False, server_default="ALL"),
        )

    if "resource_type" not in existing_cols:
        op.add_column("usertoken", sa.Column("resource_type", sa.String, nullable=True))

    if "resource_id" not in existing_cols:
        op.add_column("usertoken", sa.Column("resource_id", sa.Integer, nullable=True))

    if "expires_at" not in existing_cols:
        op.add_column("usertoken", sa.Column("expires_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("usertoken", "expires_at")
    op.drop_column("usertoken", "resource_id")
    op.drop_column("usertoken", "resource_type")
    op.drop_column("usertoken", "scope")
    op.drop_index("ix_usertoken_token_prefix", table_name="usertoken")
    op.drop_column("usertoken", "token_prefix")

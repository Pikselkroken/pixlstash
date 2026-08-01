"""Add ``usertoken.library_uuid`` so the shared model maps in both databases.

The live binding lives in the hub, where the column is NOT NULL and names the
one library a token grants access to (multi-library plan §4). The same
``UserToken`` model also maps to this vault table, so the column has to exist
here too or every query against it fails. It is nullable and unused in the
vault: identity moves to the hub at first run, after which this table is dead
weight kept only until a post-1.12 cleanup drops it.

Revision ID: 0091_add_usertoken_library_uuid
Revises: 0090_add_usertoken_public_id
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0091_add_usertoken_library_uuid"
down_revision: Union[str, None] = "0090_add_usertoken_public_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("usertoken")}
    if "library_uuid" not in existing_cols:
        op.add_column(
            "usertoken", sa.Column("library_uuid", sa.String(), nullable=True)
        )
        op.create_index(
            "ix_usertoken_library_uuid", "usertoken", ["library_uuid"], unique=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("usertoken")}
    if "library_uuid" in existing_cols:
        op.drop_index("ix_usertoken_library_uuid", table_name="usertoken")
        op.drop_column("usertoken", "library_uuid")

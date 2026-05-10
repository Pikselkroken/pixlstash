"""Add cookie_token column to guest_session for server-generated session tokens.

The cookie_token is a server-generated URL-safe base64 string stored in the
HttpOnly guest_session cookie.  It is separate from the client-supplied
session_id (the DB primary key) so that no user-controlled value ever flows
into set_cookie().

Revision ID: 0042_guest_session_cookie_token
Revises: 0041_guest_scores
Create Date: 2026-05-10 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0042_guest_session_cookie_token"
down_revision: Union[str, None] = "0041_guest_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "guest_session" not in existing_tables:
        # Table does not exist yet; the baseline or 0041 migration will create
        # it with all current columns via SQLModel.metadata.create_all().
        return

    existing_cols = {col["name"] for col in inspector.get_columns("guest_session")}
    if "cookie_token" not in existing_cols:
        op.add_column(
            "guest_session",
            sa.Column("cookie_token", sa.String(64), nullable=True),
        )
        op.create_index(
            "ix_guest_session_cookie_token",
            "guest_session",
            ["cookie_token"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "guest_session" not in existing_tables:
        return
    existing_cols = {col["name"] for col in inspector.get_columns("guest_session")}
    if "cookie_token" in existing_cols:
        op.drop_index("ix_guest_session_cookie_token", table_name="guest_session")
        op.drop_column("guest_session", "cookie_token")

"""Point the guest tables at ``token_public_id`` and clear their rows.

Tokens moved to the hub with the hub/vault split, while guest sessions and
guest scores stay per-vault: a guest session is scoped to a share link into one
library, so it belongs with that library. SQLite has no cross-database foreign
keys, which leaves ``guest_session.token_id`` and ``guest_score.token_id``
pointing at a ``usertoken`` table that is now empty in every vault, and an
insert against it fails outright.

The replacement is the token's ``public_id``, which is precisely the identifier
``UserToken.public_id`` was introduced for: the one safe to hold when the thing
it names lives elsewhere or may outlive the reference. An integer id would be
worse than merely broken here, because SQLite reuses freed integer ids, so a
stale reference could come to name a different token.

**Existing rows are cleared** (user decision 2026-08-01). They reference tokens
by an id that no longer resolves, and a best-effort remap is not worth it: guest
sessions are ephemeral share-link sessions, so clearing them logs any active
guest out once, and guest scores have never had a UI to read them.

The tables are rebuilt rather than altered because the foreign key lives in the
table definition, and SQLite cannot drop a constraint in place.

Revision ID: 0099_guest_tables_reference_token_public_id
Revises: 0098_add_library_settings
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0099_guest_tables_reference_token_public_id"
down_revision: Union[str, None] = "0098_add_library_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "guest_session" in tables:
        columns = {col["name"] for col in inspector.get_columns("guest_session")}
        if "token_public_id" not in columns:
            op.drop_table("guest_session")
            tables.discard("guest_session")

    if "guest_session" not in tables:
        op.create_table(
            "guest_session",
            sa.Column("session_id", sa.String(64), primary_key=True, nullable=False),
            sa.Column("token_public_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_active_at", sa.DateTime(), nullable=False),
            sa.Column("cookie_token", sa.String(64), nullable=True),
            sa.UniqueConstraint("cookie_token", name="uq_guest_session_cookie_token"),
        )
        op.create_index(
            "ix_guest_session_token_public_id",
            "guest_session",
            ["token_public_id"],
        )

    if "guest_score" in tables:
        columns = {col["name"] for col in inspector.get_columns("guest_score")}
        if "token_public_id" not in columns:
            op.drop_table("guest_score")
            tables.discard("guest_score")

    if "guest_score" not in tables:
        op.create_table(
            "guest_score",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.String(64), nullable=False),
            sa.Column("token_public_id", sa.String(64), nullable=False),
            sa.Column("picture_id", sa.Integer(), nullable=False),
            sa.Column("score", sa.Integer(), nullable=False),
            sa.Column("scored_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "session_id", "picture_id", name="uq_guest_score_session_picture"
            ),
        )
        op.create_index("ix_guest_score_session_id", "guest_score", ["session_id"])
        op.create_index(
            "ix_guest_score_token_public_id", "guest_score", ["token_public_id"]
        )
        op.create_index("ix_guest_score_picture_id", "guest_score", ["picture_id"])


def downgrade() -> None:
    # The old shape carried a foreign key into ``usertoken``, which no longer
    # holds tokens, so recreating it would restore a constraint that cannot be
    # satisfied. Dropping the tables is the honest inverse: they are rebuilt
    # empty on the next upgrade either way.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table in ("guest_score", "guest_session"):
        if table in tables:
            op.drop_table(table)

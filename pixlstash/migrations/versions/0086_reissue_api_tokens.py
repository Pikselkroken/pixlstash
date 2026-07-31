"""Clear API tokens so they are issued again.

Sign-in now requires a token that carries full, unrestricted owner authority, and a
token's sessions end when the token does. Neither change can reach a token row that
already exists: a stored token stays valid on its own terms, so any present when this
migration runs would carry forward unchanged.

Every token is therefore cleared and must be created again from Settings, including
share links, which have to be re-shared with their new values.

The reset cannot be narrowed. A row records only its scope and resource restriction,
not when or by what route it was issued, so a per-token decision is not derivable.
Clearing every row is the only reliable outcome, and a partial sweep would leave
exactly the rows a decision procedure cannot vouch for.

Two stored addresses are cleared with the tokens, because reissuing is only worth
doing if the new values go where the owner expects. ``user.public_url`` is the base
the share dialog puts in front of every share link, and ``user.comfyui_url`` is where
pictures are sent for generation and fetched back from. Both are free-form settings
saved as typed, both live in the ``user`` table rather than in ``usertoken``, and a
value saved earlier would otherwise decide the destination of every replacement token
and generated picture. Clearing them means the owner enters each address again and the
new tokens start against an address they chose now. Nothing else in Settings changes.

The guest session and guest score rows are cleared too, child first. Both reference a
token by id, and while those foreign keys declare a cascade, this migration's
connection does not have SQLite foreign key enforcement switched on, so the cascade
does not run here. A SQLite integer primary key also reuses the lowest free id, so
rows kept past the token they name would come to describe whichever token is created
first afterwards. Guest sessions and their scores belong to the share links being
replaced, so they go with them.

The owner's password login is unaffected, and the desktop shell seeds its own
per-launch session rather than using a stored token, so neither needs re-doing.

Data-only: no columns or tables are added or removed.

Revision ID: 0086_reissue_api_tokens
Revises: 0085_recompute_smart_score_restored_builtin_anchors
Create Date: 2026-07-31 07:50:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0086_reissue_api_tokens"
down_revision: Union[str, None] = "0085_recompute_smart_score_restored_builtin_anchors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "usertoken" in table_names:
        op.execute(sa.text("DELETE FROM usertoken"))

    # Child first: guest_score references guest_session, and both reference a
    # token. The declared cascades do not run on this connection, and a reused
    # integer primary key would re-attach anything left behind to the next
    # token created.
    for table in ("guest_score", "guest_session"):
        if table in table_names:
            op.execute(sa.text(f"DELETE FROM {table}"))

    if "user" in table_names:
        user_columns = {col["name"] for col in inspector.get_columns("user")}
        for column in ("public_url", "comfyui_url"):
            if column in user_columns:
                op.execute(sa.text(f"UPDATE user SET {column} = NULL"))


def downgrade() -> None:
    # No-op: the cleared token rows hold bcrypt hashes of values that were never
    # stored in plaintext, and the cleared addresses were free-form settings, so
    # there is nothing to restore. Both are entered again from Settings.
    pass

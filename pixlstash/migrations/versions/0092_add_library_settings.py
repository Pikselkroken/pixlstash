"""Add the vault's ``library_settings`` row and move library-scoped settings into it.

The hub/vault split sorts the old ``user`` row by destination (multi-library
plan §5). Identity, preferences and machine settings go to the hub. What stays
here is what would be *wrong* in another library rather than merely unhelpful.

Enumerated and decided 2026-08-02: exactly one setting qualifies.
``similarity_character`` is a row id in this vault's character table, so a
per-user copy silently names a different person after a library switch. Hidden
tags, the tag filter and the penalised-tag weights name library vocabulary but
are the user's own working preferences, and the owner wants the same defects
penalised everywhere, so they stay in the hub. ``stack_strictness`` is consumed
as the owner's similarity threshold for stack ordering; it identifies no vault
row, so it also remains in the hub.

``library_uuid`` is the library's fingerprint, written by PixlStash for a
library it owns. It answers "is the folder at this path the same library I
registered before?" when a detached library is re-attached. It is deliberately
left NULL here and stamped by :mod:`pixlstash.hub.bootstrap` once the hub has
minted the registry identity, because the vault has no way to know it.

**Credential blanking is not done here, on purpose.** The vault's migrations run
when the file is opened, which is *before* the hub has had a chance to copy the
user and tokens across. Blanking at this point would destroy the credentials
this release exists to preserve. It happens in the bootstrap, after a verified
copy. See ``pixlstash/hub/bootstrap.py``.

Revision ID: 0092_add_library_settings
Revises: 0091_add_usertoken_library_uuid
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0092_add_library_settings"
down_revision: Union[str, None] = "0091_add_usertoken_library_uuid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "library_settings" not in inspector.get_table_names():
        op.create_table(
            "library_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("library_uuid", sa.String(), nullable=True),
            sa.Column("similarity_character", sa.Integer(), nullable=True),
        )

    # One row, ever. Seeded from the existing user row where there is one, so an
    # upgrading install keeps the stack strictness and tag filters it had.
    existing = bind.execute(sa.text("SELECT COUNT(*) FROM library_settings")).scalar()
    if existing:
        return

    tables = set(inspector.get_table_names())
    user_cols = (
        {col["name"] for col in inspector.get_columns("user")}
        if "user" in tables
        else set()
    )
    wanted = ["similarity_character"]
    available = [name for name in wanted if name in user_cols]

    row = None
    if available:
        row = bind.execute(
            sa.text(f"SELECT {', '.join(available)} FROM user LIMIT 1")
        ).fetchone()

    values = dict(zip(available, row)) if row else {}
    columns = ", ".join(values) or None
    if columns:
        placeholders = ", ".join(f":{name}" for name in values)
        bind.execute(
            sa.text(
                f"INSERT INTO library_settings ({columns}) VALUES ({placeholders})"
            ),
            values,
        )
    else:
        bind.execute(sa.text("INSERT INTO library_settings (id) VALUES (1)"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "library_settings" in inspector.get_table_names():
        op.drop_table("library_settings")

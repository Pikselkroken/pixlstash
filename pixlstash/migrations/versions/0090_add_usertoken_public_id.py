"""Add ``usertoken.public_id`` - a stable, never-reused identity for a token.

``usertoken.id`` is a plain SQLite ``INTEGER PRIMARY KEY``: a rowid alias with
no ``AUTOINCREMENT``, so SQLite hands out the lowest free value and an id is
recycled once its row is deleted. With tokens 1..5 present, deleting all five
and creating one more yields id 1 again. Anything that stored that id - the
in-memory session-to-token maps in ``AuthService``, ``guest_session.token_id``,
``guest_score.token_id`` - then names a *different* token than the one it was
given, silently. Revoking the right token does not end the session it created,
and revoking an unrelated one ends the wrong session. That is fail-open, and it
is what this column closes.

``public_id`` is 128 bits of randomness as lowercase hex, generated per row and
never reissued, in this database or any other. A stale reference to it either
resolves to the same token or resolves to nothing; it can never resolve to a
different token. That is the fail-safe direction, and the same reason session
identifiers are not recycled.

**Why a new column rather than ``AUTOINCREMENT`` on the existing id.**
``AUTOINCREMENT`` only makes ids monotonic *within one database file*: its
high-water mark lives in ``sqlite_sequence``, which is inside the database and
is therefore replaced wholesale by a full restore, so a restored older snapshot
would go on to reissue ids that in-memory state still remembers. It does not
fix the case this is actually about. It would also require rebuilding the table
(SQLite cannot add ``AUTOINCREMENT`` in place), re-declaring the ``user_id``
foreign key and re-creating all three of ``ix_usertoken_user_id`` /
``ix_usertoken_token_hash`` / ``ix_usertoken_token_prefix`` - a dropped table
takes its indexes with it silently, and losing the prefix index would
deoptimise the token lookup path rather than fail visibly. This change is
purely additive, so none of that applies.

**Why the three statements.** SQLite refuses ``ALTER TABLE ... ADD COLUMN ...
UNIQUE`` outright ("Cannot add a UNIQUE column"), and it refuses a NOT NULL
column whose default is a non-constant expression, so a single ``ADD COLUMN``
cannot produce the finished shape. The column is therefore added plain,
backfilled in one set-based ``UPDATE`` (``lower(hex(randomblob(16)))``, per-row
random and matching what the application's ``new_token_public_id`` produces),
and the unique index added last, once every row holds a distinct value. No
Python loop and no application logic: the migration stays declarative.

The column stays nullable, because making it NOT NULL afterwards would again
require rebuilding the table. Uniqueness is what carries the guarantee, and
every row the application writes gets a value from the model's default factory.

Existing tokens are untouched: same integer ids, same ``token_hash``, same
indexes, same foreign key. Nothing is cleared and no reprocessing is triggered.

Revision ID: 0090_add_usertoken_public_id
Revises: 0089_add_dedupverdict_reopen_batch_id
Create Date: 2026-07-31 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0090_add_usertoken_public_id"
down_revision: Union[str, None] = "0089_add_dedupverdict_reopen_batch_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_INDEX_NAME = "ix_usertoken_public_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "usertoken" not in set(inspector.get_table_names()):
        return

    existing_cols = {col["name"] for col in inspector.get_columns("usertoken")}
    if "public_id" not in existing_cols:
        # A fresh database already has the column (and its unique index) from
        # the baseline's ``SQLModel.metadata.create_all()``; only a table
        # created before this change needs the ALTER.
        op.add_column("usertoken", sa.Column("public_id", sa.String(), nullable=True))

    # Backfill every row that has no value yet, one random id each. Guarded by
    # ``IS NULL`` so a replay over an already-migrated database cannot reissue
    # identities to tokens that already have one - that would be exactly the
    # bug this migration exists to remove.
    op.execute(
        sa.text(
            "UPDATE usertoken SET public_id = lower(hex(randomblob(16))) "
            "WHERE public_id IS NULL"
        )
    )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("usertoken")}
    if _INDEX_NAME not in existing_indexes:
        op.create_index(_INDEX_NAME, "usertoken", ["public_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "usertoken" not in set(inspector.get_table_names()):
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("usertoken")}
    if _INDEX_NAME in existing_indexes:
        op.drop_index(_INDEX_NAME, table_name="usertoken")
    existing_cols = {col["name"] for col in inspector.get_columns("usertoken")}
    if "public_id" in existing_cols:
        op.drop_column("usertoken", "public_id")

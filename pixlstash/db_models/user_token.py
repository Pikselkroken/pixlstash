import secrets
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import Column, ForeignKey
from sqlmodel import SQLModel, Field, Integer, DateTime, Relationship

if TYPE_CHECKING:
    from .user import User

# Bytes of randomness behind a token's ``public_id``. 16 bytes rendered as 32
# lowercase hex characters, which is exactly what the backfill in migration
# 0090 produces with ``lower(hex(randomblob(16)))`` - the two must agree, so a
# row minted by the application and a row minted by the migration are
# indistinguishable in shape.
PUBLIC_ID_BYTES = 16


def new_token_public_id() -> str:
    """Return a fresh, opaque public identifier for a token.

    128 bits of randomness as lowercase hex. Unlike the integer primary key
    this value is **never reused**, in this database or any other: SQLite hands
    out the lowest free ``INTEGER PRIMARY KEY``, so a deleted token's id is
    given to the next token created, and anything still holding that id then
    names a *different* token. A random public id has no such successor, so a
    stale reference either resolves to the same token or resolves to nothing.

    Not a secret and not a credential: it identifies a token row, it does not
    authenticate one. The secret is the token value itself, of which only a
    bcrypt hash is stored (``token_hash``).
    """
    return secrets.token_hex(PUBLIC_ID_BYTES)


class UserToken(SQLModel, table=True):
    """
    SQLModel for API tokens associated with a user.
    """

    id: int = Field(default=None, primary_key=True)
    # Stable, opaque, never-reused identity for this token; see
    # ``new_token_public_id``. Used by anything that can outlive the row - in
    # particular the in-memory session-to-token maps in ``AuthService``, where
    # a recycled integer id would let a surviving session come to name a token
    # it was never issued for. Nullable in the schema because SQLite cannot add
    # a NOT NULL column to a populated table without rebuilding it (0090 adds
    # the column, backfills every row, then adds the unique index); every row
    # written by the application gets a value from the default factory.
    public_id: Optional[str] = Field(
        default_factory=new_token_public_id, index=True, unique=True
    )
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    )
    # The library this token grants access to. Every token belongs to exactly
    # one: an unpinned token would change what it grants the moment the owner
    # switched library, so a share link would start serving different pictures
    # and an automation would silently write into the wrong place.
    #
    # Declared Optional because this model maps to two tables. In the hub
    # (``pixlstash/hub/schema.py``) the column is NOT NULL and is the live
    # binding; in the vault's legacy ``usertoken`` table it is nullable and
    # unused, that table being abandoned once identity moves to the hub. A hub
    # write that leaves it None is rejected by the database rather than silently
    # producing an unpinned token.
    library_uuid: Optional[str] = Field(default=None, index=True)
    token_hash: str = Field(index=True)
    token_prefix: Optional[str] = Field(default=None, index=True)
    description: Optional[str] = Field(default=None)
    scope: str = Field(default="ALL")
    resource_type: Optional[str] = Field(default=None)
    resource_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    include_attachments: bool = Field(default=False)
    watermark: bool = Field(default=True)

    user: Optional["User"] = Relationship(
        back_populates="tokens",
        sa_relationship_kwargs={"passive_deletes": True},
    )

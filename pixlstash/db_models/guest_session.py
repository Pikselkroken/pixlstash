from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlmodel import SQLModel, Field

if TYPE_CHECKING:
    pass


class GuestSession(SQLModel, table=True):
    """A guest rating session created by a READ-token user.

    One row is created the first time a guest submits scores.  The session_id
    is a client-generated UUID that may be stored in a browser cookie (when the
    user accepts) or in sessionStorage only (when the user declines).

    Attributes:
        session_id: Client-generated UUID stored as the primary key (max 64 chars).
        token_id: The share token that was used to create the session.  Cascades
            on token deletion so all sessions for a revoked link are removed.
        created_at: Wall-clock UTC timestamp used for FIFO eviction ordering.
        last_active_at: Updated on each score POST; used to determine whether
            the session is currently active (within the last hour).
    """

    __tablename__ = "guest_session"

    session_id: str = Field(
        sa_column=Column(String(64), primary_key=True, nullable=False)
    )
    token_id: int = Field(
        sa_column=Column(
            ForeignKey("usertoken.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        default_factory=datetime.utcnow,
    )
    last_active_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        default_factory=datetime.utcnow,
    )

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import Column, ForeignKey
from sqlmodel import SQLModel, Field, Integer, DateTime, Relationship

if TYPE_CHECKING:
    from .user import User


class UserToken(SQLModel, table=True):
    """
    SQLModel for API tokens associated with a user.
    """

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True
        )
    )
    token_hash: str = Field(index=True)
    description: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )

    user: Optional["User"] = Relationship(
        back_populates="tokens",
        sa_relationship_kwargs={"passive_deletes": True},
    )

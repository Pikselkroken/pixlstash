from datetime import datetime, timezone

from sqlmodel import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SQLModel,
    Field,
    Relationship,
)
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .character import Character
    from .picture import Picture


class Conversation(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    character_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("character.id", ondelete="CASCADE"), index=True
        )
    )

    description: Optional[str] = Field(default=None)
    created_at: Optional[datetime] = Field(
        sa_column=Column(
            "created_at",
            DateTime,
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )
    )

    # Relationships
    messages: list["Message"] = Relationship(back_populates="conversation")
    character: Optional["Character"] = Relationship(back_populates="conversations")


class Message(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    conversation_id: int = Field(
        sa_column=Column(Integer, ForeignKey("conversation.id", ondelete="CASCADE"))
    )

    role: str = Field(index=True)  # e.g., 'user' or 'bot'
    content: str
    timestamp: Optional[datetime] = Field(
        sa_column=Column(
            "timestamp",
            DateTime,
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )
    )
    picture_id: Optional[str] = Field(default=None, foreign_key="picture.id")

    # Relationships
    conversation: Optional["Conversation"] = Relationship(back_populates="messages")
    picture: Optional["Picture"] = Relationship()

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlmodel import SQLModel, Field


class GuestScore(SQLModel, table=True):
    """A single star-score submitted by a guest session for one picture.

    Attributes:
        id: Auto-incrementing primary key.
        session_id: FK to the GuestSession that submitted this score.  Cascades
            on session deletion.
        token_id: Denormalized FK to the share token; allows the owner to query
            all scores across all sessions for a given token without a join
            through guest_session.  Cascades on token deletion.
        picture_id: The rated picture.  Cascades on picture deletion.
        score: Integer 0–5 matching the existing star-score convention.
        scored_at: Wall-clock UTC timestamp of the most recent upsert.
    """

    __tablename__ = "guest_score"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "picture_id", name="uq_guest_score_session_picture"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("guest_session.session_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    token_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("usertoken.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    score: int = Field(nullable=False)
    scored_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        default_factory=datetime.utcnow,
    )

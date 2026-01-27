from sqlmodel import SQLModel, Field, Integer
from sqlalchemy import Column, ForeignKey


class HandTag(SQLModel, table=True):
    __tablename__ = "hand_tag"

    hand_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("hand.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tag.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )

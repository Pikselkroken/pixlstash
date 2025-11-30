from sqlalchemy import String, Column, ForeignKey
from sqlmodel import SQLModel, Field, Relationship

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .picture import Picture


class Tag(SQLModel, table=True):
    """
    SQLModel for the picture_tags table.
    """

    picture_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    tag: str = Field(primary_key=True, index=True)

    picture: Optional["Picture"] = Relationship(back_populates="tags")

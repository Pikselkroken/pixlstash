from sqlalchemy import Column, ForeignKey
from sqlmodel import SQLModel, Field, Integer, Relationship

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .picture import Picture


class Tag(SQLModel, table=True):
    """
    SQLModel for the picture_tags table.
    """

    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    tag: str = Field(primary_key=True, index=True)

    picture: Optional["Picture"] = Relationship(
        back_populates="tags", sa_relationship_kwargs={"passive_deletes": True}
    )

from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlmodel import SQLModel, Field, Integer, Relationship

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .picture import Picture


DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT = 3
DEFAULT_SMART_SCORE_PENALIZED_TAGS = {
    "incorrect reflection": DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    "bad anatomy": 5,
    "malformed hand": 5,
    "malformed teeth": 4,
    "missing nipples": 5,
    "malformed nipples": 4,
    "waxy skin": 2,
    "flux chin": 1,
    "silicone breasts": 0,
    "malformed foot": 4,
    "pixelated": 2,
}
TAG_EMPTY_SENTINEL = ""


class Tag(SQLModel, table=True):
    """
    SQLModel for the picture_tags table.
    """

    id: int = Field(default=None, primary_key=True)

    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    tag: str = Field(index=True)

    __table_args__ = (UniqueConstraint("picture_id", "tag"),)

    picture: Optional["Picture"] = Relationship(
        back_populates="tags",
        sa_relationship_kwargs={
            "passive_deletes": True,
            "foreign_keys": "[Tag.picture_id]",
        },
    )

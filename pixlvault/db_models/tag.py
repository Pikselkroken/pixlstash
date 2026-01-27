from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlmodel import SQLModel, Field, Integer, Relationship

from typing import TYPE_CHECKING, Optional

from .face_tag import FaceTag
from .hand_tag import HandTag

if TYPE_CHECKING:
    from .picture import Picture
    from .face import Face
    from .hand import Hand


DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT = 3
DEFAULT_SMART_SCORE_PENALIZED_TAGS = {
    "incorrect reflection": DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    "fused fingers": 5,
    "malformed eye": DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    "bad anatomy": 5,
    "extra digit": 5,
    "missing digit": 4,
    "extra limb": 5,
    "missing limb": 5,
    "malformed hand": 5,
    "malformed teeth": 4,
    "missing nipples": 5,
    "malformed nipples": 4,
    "waxy skin": 2,
    "flux chin": 1,
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
    faces: list["Face"] = Relationship(
        back_populates="tags",
        link_model=FaceTag,
        sa_relationship_kwargs={"passive_deletes": True},
    )
    hands: list["Hand"] = Relationship(
        back_populates="tags",
        link_model=HandTag,
        sa_relationship_kwargs={"passive_deletes": True},
    )

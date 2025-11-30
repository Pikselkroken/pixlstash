from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .face import Face


class FaceLikeness(SQLModel, table=True):
    """
    Database model for the Face_likeness table.
    Stores likeness scores for each (Face, Face) combination.
    Note, this is NOT picture likeness, but individual face likeness.
    """

    face_id_a: str = Field(
        foreign_key="face.id",
        primary_key=True,
    )
    face_id_b: str = Field(
        foreign_key="face.id",
        primary_key=True,
    )
    likeness: float = Field(default=None)
    metric: str = Field(default=None)

    face_a: Optional["Face"] = Relationship(
        back_populates="likeness_a",
        sa_relationship_kwargs={"foreign_keys": "[FaceLikeness.face_id_a]"},
    )
    face_b: Optional["Face"] = Relationship(
        back_populates="likeness_b",
        sa_relationship_kwargs={"foreign_keys": "[FaceLikeness.face_id_b]"},
    )

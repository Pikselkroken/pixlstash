from sqlmodel import SQLModel, Field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class PictureLikeness(SQLModel, table=True):
    """
    Database model for the picture_likeness table.
    Stores likeness scores for each (picture, picture) combination.
    Note, this is NOT face likeness, but overall picture likeness.
    """

    picture_id_a: str = Field(
        foreign_key="picture.id",
        primary_key=True,
    )
    picture_id_b: str = Field(
        foreign_key="picture.id",
        primary_key=True,
    )
    likeness: float = Field(default=None)
    metric: str = Field(default=None)

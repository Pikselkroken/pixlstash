from sqlmodel import Column, ForeignKey, Integer, Relationship, SQLModel, Field

from typing import List, TYPE_CHECKING


if TYPE_CHECKING:
    from pixlvault.db_models.picture import Picture


class PictureSetMember(SQLModel, table=True):
    """
    Database model for the picture_set_members table.
    Many-to-many junction table between picture_sets and pictures.
    """

    set_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("pictureset.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )
    picture_id: str = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )


class PictureSet(SQLModel, table=True):
    """
    Database model for the picture_sets table.
    A picture set is a named collection of pictures.
    """

    id: int = Field(default=None, primary_key=True)
    name: str = Field(default=None, index=True)
    description: str = Field(default=None)
    reference_pictures: List["Picture"] = Relationship(
        back_populates="reference_picture_set",
        link_model=PictureSetMember,
    )

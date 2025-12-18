from sqlmodel import Column, ForeignKey, Integer, Relationship, SQLModel, Field

from typing import List, Optional, TYPE_CHECKING


if TYPE_CHECKING:
    from pixlvault.db_models.character import Character
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
    picture_id: int = Field(
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

    # Relationships
    members: List["Picture"] = Relationship(
        back_populates="picture_sets", link_model=PictureSetMember
    )
    reference_character: Optional["Character"] = Relationship(
        back_populates="reference_picture_set"
    )

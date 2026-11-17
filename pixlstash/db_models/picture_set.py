from sqlalchemy import Boolean
from sqlmodel import JSON, Column, ForeignKey, Integer, Relationship, SQLModel, Field

from typing import List, Optional, TYPE_CHECKING


if TYPE_CHECKING:
    from pixlstash.db_models.character import Character
    from pixlstash.db_models.picture import Picture
    from pixlstash.db_models.project import Project


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
    # The row is a smart set's filter's own (#2148): the refresh added it and
    # may take it away. Every other path that adds a picture to a set leaves it
    # False, so the owner's additions are never undone by the filter.
    smart_auto: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )


class PictureSet(SQLModel, table=True):
    """
    Database model for the picture_sets table.
    A picture set is a named collection of pictures.
    """

    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True, nullable=False)
    description: Optional[str] = Field(default=None)
    project_id: Optional[int] = Field(
        default=None, foreign_key="project.id", index=True
    )
    set_icon: Optional[str] = Field(default=None)
    set_color: Optional[str] = Field(default=None)
    # When True the set is a hard, whole-set freeze: neither the set's own fields
    # nor the label data of any member picture may be mutated until it is unlocked
    # (mirrors the boolean `deleted` flag pattern on Picture).
    locked: bool = Field(default=False)
    # A child set sits under one parent: another set or a person, never both
    # (a LoRA dataset under the person or set it is of). Plain ids rather than
    # foreign keys, like ``Picture.pending_character_id``: the set and character
    # delete handlers lift a deleted parent's children themselves.
    parent_set_id: Optional[int] = Field(default=None, index=True)
    parent_character_id: Optional[int] = Field(default=None, index=True)
    # A training set still being built under its person: what the build screen
    # holds (its step, the sheet draws and their runs, the picker's choices),
    # written by that screen and read back when it opens again. Null for every
    # finished set; "Create set" clears it. Opaque to the backend except for
    # ``run_keys``, the sheet runs a delete of the set stops.
    building: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # A smart set (#2148): its members come from a saved picture filter.
    # ``{"filter": <GET /pictures query string>, "removed": [ids]}``, the ids
    # being pictures the owner took out, which the filter does not put back.
    # Null for every ordinary set. The members are still ``PictureSetMember``
    # rows (services/smart_sets.py), so everything that reads a set reads a
    # smart one unchanged. Replace the dict, never mutate it: a JSON column
    # does not see an in-place change.
    smart: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Relationships
    members: List["Picture"] = Relationship(
        back_populates="picture_sets", link_model=PictureSetMember
    )
    reference_character: Optional["Character"] = Relationship(
        back_populates="reference_picture_set"
    )
    project: Optional["Project"] = Relationship(back_populates="picture_sets")

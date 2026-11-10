"""Tag groups: a library-wide map from a tag NAME to a group.

A group (Character, Copyright, Meta, General...) belongs to the tag, not to a
picture: filing ``film grain`` under Meta files it on every picture that carries
it. A tag with no row in :class:`TagGroupMember` is Ungrouped. Groups start
empty, so the map is pure user vocabulary and nothing is derived from it.

Tags are matched by exact name, the same way the ``tag`` filter matches them, so
a tag need not exist on any picture yet to be filed.
"""

from typing import Optional

from sqlalchemy import Boolean, Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel


class TagGroup(SQLModel, table=True):
    """One named group. ``name`` is unique case-insensitively, enforced in the service."""

    __tablename__ = "tag_group"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=64)
    position: int = Field(default=0, index=True)


class TagGroupMember(SQLModel, table=True):
    """The group a tag name is filed under. Deleting a group cascades to its members.

    ``is_default`` is provenance: True for a filing the seeding of the default
    groups made, False for one the user made (a move, or an import). Seeding
    never rewrites an existing row, and a user move or an import clears it.
    """

    __tablename__ = "tag_group_member"

    tag: str = Field(primary_key=True)
    group_id: int = Field(
        sa_column=Column(
            "group_id",
            Integer,
            ForeignKey("tag_group.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    is_default: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )

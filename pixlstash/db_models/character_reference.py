"""A person's reference cluster: the pictures the owner chose as who they are."""

from sqlmodel import Column, Field, ForeignKey, Integer, SQLModel


class CharacterReferenceMember(SQLModel, table=True):
    """One picture of a person's reference cluster.

    The cluster is the face cluster the owner picked in *Faces and reference
    cluster…*, stored as its member pictures at the moment of the pick and
    never grown afterwards (``services/face_clusters.py``). Its own table
    rather than a ``Character`` column so no character payload, and not the
    by-name field reader, serves the ids.

    Attributes:
        character_id: FK to ``character.id`` (cascade-deletes with the person).
        picture_id: The picture. A plain id, not a foreign key, for the reason
            ``Character.thumbnail_picture_id`` is one: pictures are hard-deleted
            on Scrapheap purge. Readers drop an id whose picture is gone or no
            longer carries the person's face.
    """

    __tablename__ = "character_reference_member"

    character_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("character.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )
    picture_id: int = Field(
        default=None, sa_column=Column(Integer, primary_key=True, nullable=False)
    )

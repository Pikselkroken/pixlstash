from sqlmodel import SQLModel, Field, Relationship, select
from typing import Optional, List, TYPE_CHECKING

from .face import Face

if TYPE_CHECKING:
    from .picture import Picture
    from .picture_set import PictureSet
    from .project import Project


class Character(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True, nullable=False)
    description: Optional[str] = Field(default=None)
    extra_metadata: Optional[str] = Field(default=None)
    # Mirrors PictureSet.set_color: a hex string seeded by POSITION in the
    # shared 48-colour list, never by value. The list is therefore never
    # reordered or trimmed. See #761.
    character_color: Optional[str] = Field(default=None)

    reference_picture_set_id: Optional[int] = Field(
        default=None, foreign_key="pictureset.id"
    )
    # The reference picture the user pinned as this person's thumbnail, or NULL
    # for "pick the best one automatically" (the default). Deliberately a plain
    # column and NOT a foreign key: pictures are hard-deleted on scrapheap purge
    # and by maintenance, and with PRAGMA foreign_keys=ON a real FK would abort
    # those deletes for every character that happened to pin the picture. A
    # dangling id is harmless instead - GET /characters/{id}/thumbnail only
    # honours the pin when that picture still carries a face of this character,
    # and otherwise falls back to the automatic choice.
    thumbnail_picture_id: Optional[int] = Field(default=None)
    project_id: Optional[int] = Field(
        default=None, foreign_key="project.id", index=True
    )

    # Relationships
    faces: List["Face"] = Relationship(
        back_populates="character", sa_relationship_kwargs={"overlaps": "pictures"}
    )
    pictures: List["Picture"] = Relationship(  # Many-to-many via Face
        back_populates="characters",
        link_model=Face,
        sa_relationship_kwargs={"overlaps": "faces,character,picture"},
    )

    reference_picture_set: Optional["PictureSet"] = Relationship(
        back_populates="reference_character"
    )
    project: Optional["Project"] = Relationship(back_populates="characters")

    @classmethod
    def find(
        cls, session, select_fields: Optional[List[str]] = None, **filters
    ) -> List["Character"]:
        """
        Find characters matching the given filters.
        """
        query = select(cls)

        # Apply select_fields logic
        if select_fields:
            select_fields = list(set(select_fields) | {"id"})
            from sqlalchemy.orm import load_only, selectinload

            # Use load_only for scalar fields
            scalar_attrs = [
                getattr(cls, field)
                for field in cls.scalar_fields().intersection(select_fields)
            ]
            if scalar_attrs:
                query = query.options(load_only(*scalar_attrs))
            # Use selectinload for relationships present in select_fields
            rel_attrs = [
                getattr(cls, field)
                for field in cls.relationship_fields().intersection(select_fields)
            ]
            for rel_attr in rel_attrs:
                query = query.options(selectinload(rel_attr))

        for attr, value in filters.items():
            if hasattr(cls, attr) and value is not None:
                query = query.where(getattr(cls, attr) == value)

        return session.exec(query).all()

    @classmethod
    def scalar_fields(cls):
        """
        Return a list of simple scalar fields
        """
        return set(cls.__table__.columns.keys())

    @classmethod
    def relationship_fields(cls):
        """
        Return a list of relationship fields
        """
        return set(cls.__mapper__.relationships.keys())

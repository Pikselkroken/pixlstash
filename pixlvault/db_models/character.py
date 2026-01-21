from sqlmodel import SQLModel, Field, Relationship, select
from typing import Optional, List, TYPE_CHECKING

from .face import Face

if TYPE_CHECKING:
    from .picture import Picture
    from .picture_set import PictureSet


class Character(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(default=None, index=True)
    description: Optional[str] = Field(default=None)
    extra_metadata: Optional[str] = Field(default=None)

    reference_picture_set_id: Optional[int] = Field(
        default=None, foreign_key="pictureset.id"
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

    @classmethod
    def find(
        cls, session, select_fields: Optional[List[str]] = None, **filters
    ) -> Optional["Character"]:
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

        characters = session.exec(query).all()
        return characters

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

    @classmethod
    def all_fields(cls):
        """
        Return a list of all field names
        """
        return cls.scalar_fields().union(cls.relationship_fields())

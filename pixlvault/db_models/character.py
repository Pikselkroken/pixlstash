import json

from sqlmodel import Column, SQLModel, Field, Relationship, select, String
from typing import Optional, List, TYPE_CHECKING

from .chat import Conversation
from .face import Face

if TYPE_CHECKING:
    from .picture import Picture


class Character(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(default=None, index=True)
    original_seed: Optional[int] = Field(default=None)
    original_prompt: Optional[str] = Field(default=None)
    loras_: Optional[str] = Field(sa_column=Column("loras", String, default=None))
    description: Optional[str] = Field(default=None)

    # Relationships
    faces: List["Face"] = Relationship(
        back_populates="character", sa_relationship_kwargs={"overlaps": "pictures"}
    )
    pictures: List["Picture"] = Relationship(  # Many-to-many via Face
        back_populates="characters",
        link_model=Face,
        sa_relationship_kwargs={"overlaps": "faces,character,picture"},
    )
    conversations: List["Conversation"] = Relationship(back_populates="character")

    @property
    def loras(self) -> Optional[List[str]]:
        """
        Return the list of Loras associated with this character.
        """
        if self.loras_:
            return json.loads(self.loras_)
        return None

    @loras.setter
    def loras(self, loras: List[str]):
        """
        Set the list of Loras associated with this character.
        """
        self.loras_ = json.dumps(loras)

    @classmethod
    def find(cls, session, **filters) -> Optional["Character"]:
        """
        Find characters matching the given filters.
        """
        query = select(cls)
        for attr, value in filters.items():
            if hasattr(cls, attr) and value is not None:
                query = query.where(getattr(cls, attr) == value)

        characters = session.exec(query).all()
        return characters

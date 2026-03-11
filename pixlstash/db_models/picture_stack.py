from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel


if TYPE_CHECKING:
    from pixlstash.db_models.picture import Picture


class PictureStack(SQLModel, table=True):
    """
    Database model for the picturestack table.
    A stack is a collection of related pictures.
    """

    id: int = Field(default=None, primary_key=True)
    name: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    pictures: List["Picture"] = Relationship(back_populates="stack")

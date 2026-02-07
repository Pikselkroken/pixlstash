import json

from sqlmodel import (
    Column,
    ForeignKey,
    Integer,
    select,
    String,
    SQLModel,
    Field,
    Relationship,
    UniqueConstraint,
)
from typing import List, Optional, TYPE_CHECKING

from pixlvault.db_models.hand_tag import HandTag

if TYPE_CHECKING:
    from .picture import Picture
    from .tag import Tag


class Hand(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)

    picture_id: int = Field(
        sa_column=Column(Integer, ForeignKey("picture.id", ondelete="CASCADE")),
        default=None,
    )
    frame_index: int = Field(default=0)
    hand_index: int = Field(default=0)

    bbox_: Optional[str] = Field(sa_column=Column("bbox", String, default=None))

    # Relationships
    picture: Optional["Picture"] = Relationship(back_populates="hands")
    tags: List["Tag"] = Relationship(
        back_populates="hands",
        link_model=HandTag,
        sa_relationship_kwargs={"passive_deletes": True},
    )

    __table_args__ = (UniqueConstraint("picture_id", "frame_index", "hand_index"),)

    def __init__(self, *args, bbox=None, **kwargs):
        super().__init__(*args, **kwargs)
        if bbox is not None:
            self.bbox = bbox

    @property
    def bbox(self) -> Optional[List[int]]:
        """
        Return the bounding box as a list of integers, or None if not set.
        """
        if self.bbox_:
            return json.loads(self.bbox_)
        return None

    @bbox.setter
    def bbox(self, bbox: List[int]):
        """
        Set the bounding box from a list of integers.
        """
        self.bbox_ = json.dumps(bbox)

    @property
    def width(self) -> Optional[float]:
        """
        Return the width of the hand bounding box, or 0.0 if bbox is not set.
        """
        if self.bbox and len(self.bbox) == 4:
            return self.bbox[2] - self.bbox[0]
        return 0.0

    @property
    def height(self) -> Optional[float]:
        """
        Return the height of the hand bounding box, or 0.0 if bbox is not set.
        """
        if self.bbox and len(self.bbox) == 4:
            return self.bbox[3] - self.bbox[1]
        return 0.0

    @classmethod
    def find(cls, session, **filters) -> Optional["Hand"]:
        """
        Find hands by picture_id, frame_index, and/or hand_index.
        Supports passing a list for picture_id (uses IN_ if so).
        """
        query = select(cls).where(cls.hand_index != -1)
        for attr, value in filters.items():
            if hasattr(cls, attr):
                col = getattr(cls, attr)
                if attr == "picture_id" and isinstance(value, list):
                    query = query.where(col.in_(value))
                else:
                    query = query.where(col == value)

        hands = session.exec(query).all()
        return hands

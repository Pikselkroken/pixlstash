import json
import math

from sqlalchemy.orm import joinedload
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

from .quality import Quality

if TYPE_CHECKING:
    from .picture import Picture
    from .character import Character
    from .face_likeness import FaceLikeness


class Face(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)

    picture_id: str = Field(
        sa_column=Column(String, ForeignKey("picture.id", ondelete="CASCADE")),
        default=None,
    )
    frame_index: int = Field(default=0)
    face_index: int = Field(default=0)

    character_id: Optional[int] = Field(
        sa_column=Column(Integer, ForeignKey("character.id"), default=None, index=True)
    )
    bbox_: Optional[str] = Field(sa_column=Column("bbox", String, default=None))
    features: Optional[bytes] = None
    likeness: Optional[float] = None

    # Relationships
    quality: Optional[Quality] = Relationship(back_populates="face")
    picture: Optional["Picture"] = Relationship(
        back_populates="faces", sa_relationship_kwargs={"overlaps": "character"}
    )
    character: Optional["Character"] = Relationship(
        back_populates="faces", sa_relationship_kwargs={"overlaps": "picture"}
    )

    likeness_a: List["FaceLikeness"] = Relationship(
        back_populates="face_a",
        sa_relationship_kwargs={"primaryjoin": "Face.id==FaceLikeness.face_id_a"},
    )
    likeness_b: List["FaceLikeness"] = Relationship(
        back_populates="face_b",
        sa_relationship_kwargs={"primaryjoin": "Face.id==FaceLikeness.face_id_b"},
    )

    __table_args__ = (UniqueConstraint("picture_id", "frame_index", "face_index"),)

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
        Return the width of the face bounding box, or 0.0 if bbox is not set.
        """
        if self.bbox and len(self.bbox) == 4:
            return self.bbox[2] - self.bbox[0]
        return 0.0

    @property
    def height(self) -> Optional[float]:
        """
        Return the height of the face bounding box, or 0.0 if bbox is not set.
        """
        if self.bbox and len(self.bbox) == 4:
            return self.bbox[3] - self.bbox[1]
        return 0.0

    @classmethod
    def find(cls, session, **filters) -> Optional["Face"]:
        """
        Find faces by picture_id, frame_index, and/or face_index.
        """
        query = select(cls).options(joinedload(cls.quality))
        for attr, value in filters.items():
            if hasattr(cls, attr):
                query = query.where(getattr(cls, attr) == value)

        faces = session.exec(query).all()
        return faces

    @staticmethod
    def expand_face_bbox(
        bbox: List[int],
        picture_width: int,
        picture_height: int,
        expansion_fraction: float,
    ) -> List[int]:
        """
        Expand the bounding box by a given expansion fraction and align to 64-pixel boundaries.
        Args:
            bbox: List or tuple of [x_min, y_min, x_max, y_max]
            expansion_fraction: Fraction to expand the bbox on each side
        Returns:
            Expanded bbox as [x_min, y_min, x_max, y_max]
        """
        if bbox is None or len(bbox) != 4:
            return bbox
        x_min, y_min, x_max, y_max = bbox

        width = x_max - x_min
        height = y_max - y_min

        def round64(val):
            return int(math.ceil(val / 64.0) * 64)

        new_width = round64(width + width * expansion_fraction)
        new_height = round64(height + height * expansion_fraction)

        width_expansion = new_width - width
        height_expansion = new_height - height

        x_min = max(0, int(round(x_min - width_expansion / 2)))
        x_max = min(picture_width, int(round(x_min + new_width)))
        y_min = max(0, int(round(y_min - height_expansion / 2)))
        y_max = min(picture_height, int(round(y_min + new_height)))

        return [
            x_min,
            y_min,
            x_max,
            y_max,
        ]

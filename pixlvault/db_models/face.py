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

from pixlvault.db_models.face_character_likeness import FaceCharacterLikeness

from .quality import Quality

if TYPE_CHECKING:
    from .picture import Picture
    from .character import Character
    from .face_likeness import FaceLikeness


class Face(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)

    picture_id: int = Field(
        sa_column=Column(Integer, ForeignKey("picture.id", ondelete="CASCADE")),
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
    quality: Optional[Quality] = Relationship(
        back_populates="face",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
    picture: Optional["Picture"] = Relationship(
        back_populates="faces", sa_relationship_kwargs={"overlaps": "character"}
    )
    character: Optional["Character"] = Relationship(
        back_populates="faces", sa_relationship_kwargs={"overlaps": "picture"}
    )

    likeness_a: List["FaceLikeness"] = Relationship(
        back_populates="face_a",
        sa_relationship_kwargs={
            "primaryjoin": "Face.id==FaceLikeness.face_id_a",
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
    likeness_b: List["FaceLikeness"] = Relationship(
        back_populates="face_b",
        sa_relationship_kwargs={
            "primaryjoin": "Face.id==FaceLikeness.face_id_b",
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
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
        Supports passing a list for picture_id (uses IN_ if so).
        """
        query = select(cls).options(joinedload(cls.quality)).where(cls.face_index != -1)
        for attr, value in filters.items():
            if hasattr(cls, attr):
                col = getattr(cls, attr)
                if attr == "picture_id" and isinstance(value, list):
                    query = query.where(col.in_(value))
                else:
                    query = query.where(col == value)

        faces = session.exec(query).all()
        return faces

    @classmethod
    def find_faces_without_character_likeness(
        cls, session, character_id: int
    ) -> List["Face"]:
        """
        Find all faces that do not have a FaceCharacterLikeness entry for the given character_id.
        Args:
            session: The database session to use for the query.
            character_id: The ID of the character to check likeness entries for.
        Returns:
            A list of Face objects without a FaceCharacterLikeness entry for the given character_id.
        """
        subquery = (
            select(FaceCharacterLikeness.face_id)
            .where((FaceCharacterLikeness.character_id == character_id))
            .union(
                select(FaceCharacterLikeness.face_id).where(
                    (FaceCharacterLikeness.character_id == character_id)
                )
            )
            .subquery()
        )

        query = select(cls).where(~cls.id.in_(select(subquery)))
        return session.exec(query).all()

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

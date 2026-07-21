import json

from sqlalchemy import Float
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

if TYPE_CHECKING:
    from .picture import Picture


class Detection(SQLModel, table=True):
    """An object-detection bounding box for a picture.

    Mirrors :class:`~pixlstash.db_models.face.Face`'s bbox-as-JSON convention so
    the frontend overlay, scope enforcement, and copy-on-output logic can be
    reused — but stores an open-vocabulary ``label`` (e.g. ``"dog"``) instead of
    a face/character. Produced by a user-triggered detection pass (see
    :class:`~pixlstash.tasks.detection_task.DetectionTask`), never by a
    background WorkFinder.
    """

    id: int = Field(default=None, primary_key=True)

    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            index=True,
        ),
        default=None,
    )
    frame_index: int = Field(default=0)
    detection_index: int = Field(default=0)

    label: Optional[str] = Field(
        sa_column=Column("label", String, default=None, index=True)
    )
    bbox_: Optional[str] = Field(sa_column=Column("bbox", String, default=None))
    # Florence grounding/OD returns no per-box confidence, so score is nullable;
    # other detectors may populate it.
    score: Optional[float] = Field(sa_column=Column("score", Float, default=None))
    # Detector provenance, e.g. "florence2:od" or "florence2:grounding".
    source: Optional[str] = Field(sa_column=Column("source", String, default=None))
    # JSON escape-hatch for future per-detection data (masks, generation params).
    attributes_: Optional[str] = Field(
        sa_column=Column("attributes", String, default=None)
    )

    # Relationships
    picture: Optional["Picture"] = Relationship(back_populates="detections")

    __table_args__ = (UniqueConstraint("picture_id", "frame_index", "detection_index"),)

    def __init__(self, *args, bbox=None, **kwargs):
        super().__init__(*args, **kwargs)
        if bbox is not None:
            self.bbox = bbox

    @property
    def bbox(self) -> Optional[List[int]]:
        """Return the bounding box as ``[x1, y1, x2, y2]`` pixels, or None."""
        if self.bbox_:
            return json.loads(self.bbox_)
        return None

    @bbox.setter
    def bbox(self, bbox: List[int]):
        """Set the bounding box from a list of integers."""
        self.bbox_ = json.dumps(bbox)

    @classmethod
    def find(cls, session, **filters) -> List["Detection"]:
        """Find detections by picture_id, frame_index, and/or detection_index.

        Supports passing a list for picture_id (uses ``IN`` if so).
        """
        query = select(cls)
        for attr, value in filters.items():
            if hasattr(cls, attr):
                col = getattr(cls, attr)
                if attr == "picture_id" and isinstance(value, list):
                    query = query.where(col.in_(value))
                else:
                    query = query.where(col == value)
        return session.exec(query).all()

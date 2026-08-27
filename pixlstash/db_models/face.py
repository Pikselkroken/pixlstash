import json
import math

from sqlmodel import (
    Column,
    ForeignKey,
    Index,
    Integer,
    select,
    String,
    SQLModel,
    Field,
    Relationship,
    text,
    UniqueConstraint,
)
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .picture import Picture
    from .character import Character


class Face(SQLModel, table=True):
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
    face_index: int = Field(default=0)

    character_id: Optional[int] = Field(
        sa_column=Column(Integer, ForeignKey("character.id"), default=None, index=True)
    )
    bbox_: Optional[str] = Field(sa_column=Column("bbox", String, default=None))
    features: Optional[bytes] = None
    # Name of the InsightFace model pack that produced this face's embedding
    # (e.g. "buffalo_l" or "auraface"). Nullable for rows created before the
    # column existed; backfilled to "buffalo_l" by the Alembic migration.
    model_pack: Optional[str] = Field(
        sa_column=Column("model_pack", String, default=None)
    )

    # Relationships
    picture: Optional["Picture"] = Relationship(
        back_populates="faces", sa_relationship_kwargs={"overlaps": "character"}
    )
    character: Optional["Character"] = Relationship(
        back_populates="faces", sa_relationship_kwargs={"overlaps": "picture"}
    )

    __table_args__ = (
        UniqueConstraint("picture_id", "frame_index", "face_index"),
        # "Which characters have a face that carries an embedding?", behind
        # ``GET /characters``. Plain ``ix_face_character_id`` covers every face,
        # embedded or not, and needs a table lookup per row to test ``features``;
        # scoped to the embedded faces the answer comes out of the index alone.
        # Issue #651.
        #
        # ONLY RELY ON THIS FOR ONE-PASS SHAPES: ``GROUP BY character_id`` or
        # ``DISTINCT character_id`` over ``features IS NOT NULL``. For those the
        # planner picks it unconditionally. A PER-CHARACTER probe
        # (``character_id = ?`` / ``IN (...)``) is a coin flip against
        # ``ix_face_character_id``: this database never runs ``ANALYZE``, so with
        # no ``sqlite_stat1`` the two indexes tie on cost, and the tie is
        # broken by index-creation order - which ``metadata.create_all()``
        # iterates from a set, so it varies per process. Making the per-character
        # probe deterministic would mean adding ``features`` itself as a second
        # index column, i.e. duplicating every face embedding into the index.
        # Answer the question in one grouped pass instead; that is also the fix
        # for the N+1 the per-character form implies.
        Index(
            "ix_face_character_features",
            "character_id",
            sqlite_where=text("features IS NOT NULL"),
        ),
    )

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

    def to_public_dict(self) -> dict:
        """Return the face fields that may be served over the API.

        This is an allowlist, not a dump: it is the projection behind
        ``GET /pictures/{id}/faces`` and ``GET /characters/{id}/faces``, which
        replaced serving the ``faces`` relationship through the generic by-name
        readers (issue #721). Two columns are deliberately **omitted**:

        * ``features`` - the ArcFace embedding. It is biometric data, and the
          generic reader used to hand it out base64-encoded to any token that
          could reach the picture. Nothing on the wire has ever read it: the
          SPA's face-box overlay uses ``frame_index``, ``bbox`` and
          ``character_id`` only.
        * ``model_pack`` - names the InsightFace pack that produced the
          embedding (``buffalo_l`` / ``auraface``). Not biometric in itself, but
          it tells a caller how embeddings obtained elsewhere could be compared
          against these, and no caller reads it (verified across
          ``frontend/src``, ``tests/``, ``pixlstash/`` and ``scripts/``: every
          occurrence is server-side model loading). Omitted by the same
          least-disclosure rule; add it back only with a named consumer.

        ``bbox`` is taken from the property, which parses the ``bbox_`` text
        column. That reproduces exactly what ``safe_model_dict`` did for the
        trailing-underscore field on the old path, so the wire value is
        unchanged.

        Returns:
            A JSON-safe dict with ``id``, ``picture_id``, ``character_id``,
            ``frame_index``, ``face_index`` and ``bbox``.
        """
        return {
            "id": self.id,
            "picture_id": self.picture_id,
            "character_id": self.character_id,
            "frame_index": self.frame_index,
            "face_index": self.face_index,
            "bbox": self.bbox,
        }

    @classmethod
    def find(cls, session, **filters) -> Optional["Face"]:
        """
        Find faces by picture_id, frame_index, and/or face_index.
        Supports passing a list for picture_id (uses IN_ if so).
        """
        query = select(cls).where(cls.face_index != -1)
        for attr, value in filters.items():
            if hasattr(cls, attr):
                col = getattr(cls, attr)
                if attr == "picture_id" and isinstance(value, list):
                    query = query.where(col.in_(value))
                else:
                    query = query.where(col == value)

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

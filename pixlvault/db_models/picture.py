import base64
import sys
import numpy as np

from datetime import datetime

from enum import Enum, auto, IntEnum
from sqlalchemy import String, desc, func
from sqlalchemy.orm import load_only, selectinload
from sqlalchemy.types import LargeBinary
from sqlmodel import (
    Column,
    DateTime,
    SQLModel,
    Field,
    Relationship,
    exists,
    select,
    Session,
)
from typing import ClassVar, Optional, List, TYPE_CHECKING, Type

from pixlvault.db_models.face_tag import FaceTag
from pixlvault.db_models.hand_tag import HandTag


from .face import Face
from .hand import Hand
from .picture_set import PictureSet, PictureSetMember
from .quality import Quality
from .tag import Tag

from pixlvault.pixl_logging import get_logger

if TYPE_CHECKING:
    from .character import Character
    from .picture_likeness import PictureLikeness


# Configure logging for the module
logger = get_logger(__name__)


# Class for sorting mechanisms (replaces Enum)
class SortMechanism:
    class Keys(Enum):
        DATE = auto()
        SCORE = auto()
        CHARACTER_LIKENESS = auto()
        PICTURE_STACKS = auto()
        IMAGE_SIZE = auto()
        SMART_SCORE = auto()

    MECHANISMS = {
        Keys.DATE: {
            "field": "created_at",
            "description": "Date Created",
        },
        Keys.SCORE: {
            "field": "score",
            "description": "Score",
        },
        Keys.SMART_SCORE: {
            "field": None,
            "description": "Smart Score",
        },
        Keys.CHARACTER_LIKENESS: {
            "field": "character_likeness",
            "description": "Similarity to",
        },
        Keys.PICTURE_STACKS: {
            "field": "id",
            "description": "Stacks",
        },
        Keys.IMAGE_SIZE: {
            "field": None,  # Special case, not a direct field
            "description": "Image Size",
        },
    }

    def __init__(self, key, descending: bool = True):
        self.key = key
        self.descending = descending

    @property
    def field(self):
        return self.MECHANISMS[self.key]["field"]

    @classmethod
    def all(cls):
        mechanisms = []
        for key, data in cls.MECHANISMS.items():
            data = {"key": str(key.name), **data}
            mechanisms.append(data)
        return mechanisms

    @classmethod
    def from_string(cls, key_string: str, descending: bool = True) -> "SortMechanism":
        # Try by name
        if key_string in cls.Keys.__members__:
            return SortMechanism(cls.Keys[key_string], descending=descending)

        raise ValueError(f"{key_string!r} is not a valid SortMechanism")


class LikenessParameter(IntEnum):
    SIZE_BIN = 0
    BRIGHTNESS = 1
    CONTRAST = 2
    EDGE_DENSITY = 3
    NOISE_LEVEL = 4
    ASPECT_RATIO = 5
    PHASH_PREFIX = 6
    DATE = 7
    COLORFULNESS = 8
    LUMINANCE_ENTROPY = 9
    DOMINANT_HUE = 10


LIKENESS_PARAMETER_SENTINEL = -1.0


def _default_likeness_parameters() -> np.ndarray:
    return np.full(
        len(LikenessParameter), LIKENESS_PARAMETER_SENTINEL, dtype=np.float32
    )


class ExportType(Enum):
    FULL = "full"
    FACE = "face"
    HAND = "hand"
    FACE_HAND = "face_hand"

    @classmethod
    def from_string(cls, value: str) -> "ExportType":
        d = (value or "").lower()
        for member in cls:
            if member.value == d:
                return member
        return cls.FULL


class Picture(SQLModel, table=True):
    ExportType: ClassVar[Type[ExportType]] = ExportType
    id: int = Field(default=None, primary_key=True)
    file_path: Optional[str] = None
    description: Optional[str] = None
    format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    size_bin_index: Optional[int] = Field(default=None, index=True)
    created_at: Optional[datetime] = Field(
        default=None, sa_column=Column("created_at", type_=DateTime, nullable=True)
    )
    imported_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column("imported_at", type_=DateTime, nullable=True, index=True),
    )
    text_embedding: Optional[np.ndarray] = Field(
        sa_column=Column("text_embedding", LargeBinary, default=None, nullable=True)
    )
    image_embedding: Optional[np.ndarray] = Field(
        sa_column=Column("image_embedding", LargeBinary, default=None, nullable=True)
    )
    likeness_parameters: Optional[np.ndarray] = Field(
        default_factory=_default_likeness_parameters,
        sa_column=Column(
            "likeness_parameters", LargeBinary, default=None, nullable=True
        ),
    )
    perceptual_hash: Optional[str] = Field(
        default=None,
        sa_column=Column("perceptual_hash", String, default=None, nullable=True),
    )
    thumbnail_left: Optional[int] = Field(default=None)
    thumbnail_top: Optional[int] = Field(default=None)
    thumbnail_side: Optional[int] = Field(default=None)
    score: Optional[int] = None
    aesthetic_score: Optional[float] = None
    pixel_sha: Optional[str] = Field(default=None, index=True)
    deleted: bool = Field(default=False, index=True)

    # Relationships
    quality: Optional["Quality"] = Relationship(back_populates="picture")
    faces: List["Face"] = Relationship(
        back_populates="picture",
        sa_relationship_kwargs={
            "overlaps": "characters",
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
    hands: List["Hand"] = Relationship(
        back_populates="picture",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
    tags: List["Tag"] = Relationship(
        back_populates="picture",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
            "foreign_keys": "[Tag.picture_id]",
        },
    )
    characters: List["Character"] = Relationship(  # Many-to-many via Face
        back_populates="pictures",
        link_model=Face,
        sa_relationship_kwargs={"overlaps": "faces,picture,character"},
    )
    picture_sets: List["PictureSet"] = Relationship(
        back_populates="members", link_model=PictureSetMember
    )

    likeness_a: List["PictureLikeness"] = Relationship(
        back_populates="picture_a",
        sa_relationship_kwargs={
            "primaryjoin": "Picture.id==PictureLikeness.picture_id_a",
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
    likeness_b: List["PictureLikeness"] = Relationship(
        back_populates="picture_b",
        sa_relationship_kwargs={
            "primaryjoin": "Picture.id==PictureLikeness.picture_id_b",
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )

    class Config:
        arbitrary_types_allowed = True

    def __hash__(self):
        # Use the unique id for hashing
        return hash(self.id)

    def __eq__(self, other):
        # Compare by id for equality
        if isinstance(other, Picture):
            return self.id == other.id
        return False

    def text_embedding_data(self):
        """
        Returns a structured dict for embedding: description, tags, and character info.
        """
        data = {
            "description": self.description or None,
            "tags": [
                tag.tag
                for tag in getattr(self, "tags", [])
                if getattr(tag, "tag", None)
            ],
            "characters": [],
        }
        for character in getattr(self, "characters", []):
            char_info = {
                "name": getattr(character, "name", None),
                "description": getattr(character, "description", None),
            }
            data["characters"].append(char_info)
        return data

    @classmethod
    def semantic_search(
        cls: "Picture",
        session: Session,
        query: str,
        query_words: List[str],
        text_to_embedding: callable,
        clip_text_to_embedding: callable = None,
        fuzzy_weight: float = 0.5,
        embedding_weight: float = 0.5,
        threshold: float = 0.0,
        offset: int = 0,
        limit: int = sys.maxsize,
        format: Optional[List[str]] = None,
        select_fields: Optional[List[str]] = None,
        include_deleted: bool = False,
        only_deleted: bool = False,
        include_unimported: bool = False,
        candidate_ids: Optional[List[int]] = None,
    ) -> List["Picture"]:
        """
        Hybrid semantic search: combines fuzzy tag search (levenshtein SQL function) and embedding similarity (cosine_similarity SQL function).
        Orders by combined score in SQL.
        """
        if candidate_ids is not None and not candidate_ids:
            return []
        # 1. Generate SBERT embedding for tag search (Text-to-Text)
        query_embedding = text_to_embedding(query)
        if query_embedding is None:
            logger.warning("Semantic search: Failed to generate SBERT embedding.")
            query_embedding_bytes = None
        else:
            query_embedding_bytes = query_embedding.tobytes()

        # 2. Generate CLIP embedding for visual search (Text-to-Image)
        if clip_text_to_embedding:
            clip_query_embedding = clip_text_to_embedding(query)
            clip_query_embedding_bytes = (
                clip_query_embedding.tobytes()
                if clip_query_embedding is not None
                else None
            )
        else:
            clip_query_embedding_bytes = None

        logger.debug(
            f"Performing semantic search for query='{query}' and query_words={query_words} with fuzzy_weight={fuzzy_weight}, embedding_weight={embedding_weight}"
        )

        query_str = " ".join(query_words)
        # Subquery: calculate levenshtein distance for all tags of each picture
        tag_query = select(
            Tag.picture_id,
            func.levenshtein_with_id(
                func.group_concat(Tag.tag, " "), query_str, Tag.picture_id
            ).label("min_tag_dist"),
        ).group_by(Tag.picture_id)

        if candidate_ids:
            tag_query = tag_query.where(Tag.picture_id.in_(candidate_ids))

        tag_subq = tag_query.subquery()

        # Calculate cosine similarity for both text (tags) and image (visuals) embeddings
        if query_embedding_bytes:
            text_sim = (
                func.coalesce(
                    func.cosine_similarity(cls.text_embedding, query_embedding_bytes),
                    0.0,
                )
                * 2.0
            )
        else:
            text_sim = 0.0

        if clip_query_embedding_bytes:
            # Boost logic: CLIP similarity for unrelated text-image pairs is low (0.1-0.2).
            # A good match is often 0.25-0.35.
            # Fuzzy match is 0.0 to 1.0 (usually 1.0 for matches).
            # To make CLIP comparable and impactful, we multiply by a factor (e.g., 2.5).
            # This brings 0.3 -> 0.75, which can rival a messy tag match.
            image_sim = (
                func.coalesce(
                    func.cosine_similarity(
                        cls.image_embedding, clip_query_embedding_bytes
                    ),
                    0.0,
                )
                * 2.5
            )
        else:
            image_sim = 0.0

        # Combined embedding score: average of text and image similarity to capture both explicit tags and visual concepts
        embedding_score_raw = (text_sim + image_sim) / 2.0
        embedding_score = func.sqrt(func.max(0.0, embedding_score_raw))

        raw_fuzzy_score = func.max(
            0.0, 1.0 - func.coalesce(tag_subq.c.min_tag_dist, 1.0)
        )
        fuzzy_score = func.pow(raw_fuzzy_score, 1.5)

        # Main query: join pictures with tag_subq, compute combined score
        stmt = (
            select(
                cls,
                (fuzzy_weight * fuzzy_score + embedding_weight * embedding_score).label(
                    "combined_score"
                ),
                fuzzy_score.label("fuzzy_score"),
                embedding_score.label("embedding_score"),
                tag_subq.c.min_tag_dist.label(
                    "min_tag_dist"
                ),  # Explicitly include min_tag_dist
            )
            .outerjoin(tag_subq, cls.id == tag_subq.c.picture_id)
            .order_by(desc("combined_score"))
            .offset(offset)
            .limit(limit)
        )

        if only_deleted:
            stmt = stmt.where(cls.deleted.is_(True))
        elif not include_deleted:
            stmt = stmt.where(cls.deleted.is_(False))

        if not include_unimported:
            stmt = stmt.where(cls.imported_at.is_not(None))

        # Apply select_fields logic (like in find)
        if select_fields:
            select_fields = list(set(select_fields) | {"id"})

            # Use load_only for scalar fields
            scalar_attrs = [
                getattr(cls, field)
                for field in cls.scalar_fields().intersection(select_fields)
            ]
            if scalar_attrs:
                stmt = stmt.options(load_only(*scalar_attrs))
            # Use selectinload for relationships present in select_fields
            rel_attrs = [
                getattr(cls, field)
                for field in cls.relationship_fields().intersection(select_fields)
            ]
            for rel_attr in rel_attrs:
                stmt = stmt.options(selectinload(rel_attr))

        if format:
            stmt = stmt.where(cls.format.in_(format))

        if candidate_ids:
            stmt = stmt.where(cls.id.in_(candidate_ids))

        results = session.exec(stmt).all()

        if results:
            top_rows = results[:20]
            header = f"{'rank':>4} {'id':>6} {'combined':>9} {'fuzzy':>7} {'embed':>7} {'min_tag':>8}"
            lines = [header]
            for idx, row in enumerate(top_rows, start=1):
                pic = row[0]
                combined_score = row[1] if row[1] is not None else 0.0
                fuzzy_val = row[2] if row[2] is not None else 0.0
                embed_val = row[3] if row[3] is not None else 0.0
                min_tag_val = row[4] if row[4] is not None else 0.0
                pic_id = getattr(pic, "id", None)
                lines.append(
                    f"{idx:>4} {pic_id:>6} {float(combined_score):>9.4f} {float(fuzzy_val):>7.4f} {float(embed_val):>7.4f} {float(min_tag_val):>8.4f}"
                )
            logger.info(
                "Semantic search score breakdown (top %d) query=%r:\n%s",
                len(top_rows),
                query,
                "\n".join(lines),
            )

        # Log tag contribution for each result
        for result in results:
            logger.debug(
                f"Picture ID: {result.Picture.id}, "
                f"Tag contribution: min_tag_dist={getattr(result, 'min_tag_dist', 'N/A')}, "
                f"fuzzy_score={getattr(result, 'fuzzy_score', 'N/A')}, "
                f"embedding_score={getattr(result, 'embedding_score', 'N/A')}, "
                f"combined_score={getattr(result, 'combined_score', 'N/A')}"
            )
        output = []
        for row in results:
            pic, combined_score, _, _ = (
                row[0],
                row[1],
                row[2],
                row[3],
            )
            if combined_score and combined_score >= threshold:
                output.append((pic, combined_score))
        return output

    @staticmethod
    def serialize_with_likeness(picture_and_score):
        pic, score = picture_and_score
        d = pic.to_serializable_dict()
        d["likeness_score"] = score
        return d

    @classmethod
    def find(
        cls,
        session,
        *,
        sort_mech: Optional[SortMechanism] = None,
        offset: int = 0,
        limit: int = sys.maxsize,
        select_fields: Optional[List[str]] = None,
        format: Optional[List[str]] = None,
        include_deleted: bool = False,
        only_deleted: bool = False,
        include_unimported: bool = False,
        **search,
    ) -> List["Picture"]:
        """
        Find pictures based on provided filters.
        """
        query = select(cls)

        logger.debug("Got search parameters: %s", search)
        if select_fields:
            # Always include 'id' in select_fields
            select_fields = list(set(select_fields) | {"id"})
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

        if only_deleted:
            query = query.where(cls.deleted.is_(True))
        elif not include_deleted:
            query = query.where(cls.deleted.is_(False))

        if not include_unimported:
            query = query.where(cls.imported_at.is_not(None))

        for attr, value in search.items():
            if hasattr(cls, attr):
                if isinstance(value, list):
                    query = query.where(getattr(cls, attr).in_(value))
                else:
                    query = query.where(getattr(cls, attr) == value)

        if format:
            query = query.where(cls.format.in_(format))

        if sort_mech:
            if sort_mech.key == SortMechanism.Keys.IMAGE_SIZE:
                # Sort by width * height
                if sort_mech.descending:
                    query = query.order_by(
                        (cls.width * cls.height).desc(), cls.id.desc()
                    )
                else:
                    query = query.order_by((cls.width * cls.height).asc(), cls.id.asc())
            else:
                field_name = sort_mech.field
                field = getattr(cls, field_name, None)
                if field is not None:
                    if sort_mech.descending:
                        query = query.order_by(field.desc(), cls.id.desc())
                    else:
                        query = query.order_by(field.asc(), cls.id.asc())
        if offset > 0 or limit != sys.maxsize:
            query = query.offset(offset).limit(limit)

        results = session.exec(query).all()
        return results

    @classmethod
    def metadata_fields(cls):
        """
        Return a list of simple scalar fields
        """
        return cls.scalar_fields() - cls.large_binary_fields()

    @classmethod
    def grid_fields(cls):
        """
        Return a minimal set of fields for grid listing.
        """
        return {
            "id",
            "width",
            "height",
            "format",
            "score",
            "created_at",
        }

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
        return set(Picture.__mapper__.relationships.keys())

    @classmethod
    def large_binary_fields(cls):
        """
        Return a list of LargeBinary fields
        """
        return {
            field.name
            for field in cls.__table__.columns
            if isinstance(field.type, LargeBinary)
        }

    def to_serializable_dict(self):
        """
        Returns a dict suitable for JSON serialization, encoding all large binary fields as base64 if present.
        """
        d = self.model_dump()
        for field in self.large_binary_fields():
            val = d.get(field, None)
            if val is not None:
                try:
                    if isinstance(val, np.ndarray):
                        val_bytes = val.tobytes()
                    elif isinstance(val, (bytes, bytearray)):
                        val_bytes = val
                    else:
                        val_bytes = bytes(val)
                    d[field] = base64.b64encode(val_bytes).decode("utf-8")
                except Exception:
                    d[field] = None
        return d

    @classmethod
    def clear_field(cls, session, picture_ids, field_name: str):
        pictures = cls.find(session=session, id=picture_ids, select_fields=[field_name])
        for pic in pictures:
            if hasattr(pic, field_name):
                setattr(pic, field_name, None)
        session.add_all(pictures)
        session.commit()

    @classmethod
    def find_unassigned(
        cls,
        session,
        sort_mech: Optional[SortMechanism] = None,
        offset: int = 0,
        limit: int = sys.maxsize,
        format: list[str] | None = None,
        metadata_fields: list[str] | None = None,
    ):
        query = select(Picture)
        unassigned_condition = ~exists(
            select(Face.id).where(
                Face.picture_id == Picture.id,
                Face.character_id.is_not(None),
            )
        )
        not_in_set_condition = ~exists(
            select(PictureSetMember.picture_id).where(
                PictureSetMember.picture_id == Picture.id
            )
        )
        query = query.where(
            unassigned_condition,
            not_in_set_condition,
            Picture.deleted.is_(False),
            Picture.imported_at.is_not(None),
        )

        if format:
            query = query.where(Picture.format.in_(format))

        select_fields = metadata_fields or cls.metadata_fields()
        if select_fields:
            select_fields = list(set(select_fields) | {"id"})
            scalar_attrs = [
                getattr(Picture, field)
                for field in Picture.scalar_fields().intersection(select_fields)
            ]
            if scalar_attrs:
                query = query.options(load_only(*scalar_attrs))
            rel_attrs = [
                getattr(Picture, field)
                for field in Picture.relationship_fields().intersection(select_fields)
            ]
            for rel_attr in rel_attrs:
                query = query.options(selectinload(rel_attr))

        if sort_mech:
            if sort_mech.key == SortMechanism.Keys.IMAGE_SIZE:
                order_expr = Picture.width * Picture.height
                query = query.order_by(
                    order_expr.desc() if sort_mech.descending else order_expr.asc(),
                    Picture.id.desc() if sort_mech.descending else Picture.id.asc(),
                )
            else:
                field = getattr(Picture, sort_mech.field, None)
                if field is not None:
                    query = query.order_by(
                        field.desc() if sort_mech.descending else field.asc(),
                        Picture.id.desc() if sort_mech.descending else Picture.id.asc(),
                    )

        if offset > 0 or limit != sys.maxsize:
            query = query.offset(offset).limit(limit)

        return session.exec(query).all()

    @staticmethod
    def fetch_features(session, picture_ids):
        """
        Fetch faces, hands, and their tags for a list of picture IDs.
        Returns:
            faces_by_pic: dict[picture_id, list[Face]]
            hands_by_pic: dict[picture_id, list[Hand]]
            face_tags_by_face: dict[face_id, list[tag]]
            hand_tags_by_hand: dict[hand_id, list[tag]]
        """
        faces = session.exec(select(Face).where(Face.picture_id.in_(picture_ids))).all()
        hands = session.exec(select(Hand).where(Hand.picture_id.in_(picture_ids))).all()
        face_ids = [face.id for face in faces]
        hand_ids = [hand.id for hand in hands]

        face_tags = []
        hand_tags = []
        if face_ids:
            face_tags = session.exec(
                select(FaceTag.face_id, Tag.tag)
                .join(Tag, Tag.id == FaceTag.tag_id)
                .where(FaceTag.face_id.in_(face_ids))
            ).all()
        if hand_ids:
            hand_tags = session.exec(
                select(HandTag.hand_id, Tag.tag)
                .join(Tag, Tag.id == HandTag.tag_id)
                .where(HandTag.hand_id.in_(hand_ids))
            ).all()

        faces_by_pic = {}
        for face in faces:
            faces_by_pic.setdefault(face.picture_id, []).append(face)

        hands_by_pic = {}
        for hand in hands:
            hands_by_pic.setdefault(hand.picture_id, []).append(hand)

        face_tags_by_face = {}
        for face_id, tag in face_tags:
            face_tags_by_face.setdefault(face_id, []).append(tag)

        hand_tags_by_hand = {}
        for hand_id, tag in hand_tags:
            hand_tags_by_hand.setdefault(hand_id, []).append(tag)

        return (
            faces_by_pic,
            hands_by_pic,
            face_tags_by_face,
            hand_tags_by_hand,
        )

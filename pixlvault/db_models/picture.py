import base64
import sys
import numpy as np

from datetime import datetime

from enum import Enum, auto
from PIL import Image
from sqlalchemy import desc, func
from sqlalchemy.orm import load_only, selectinload
from sqlalchemy.types import LargeBinary
from sqlmodel import Column, DateTime, SQLModel, Field, Relationship, select, Session
from typing import Optional, List, TYPE_CHECKING


from .face import Face
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
        SHARPNESS = auto()
        EDGE_DENSITY = auto()
        NOISE_LEVEL = auto()
        DESCRIPTION = auto()
        FORMAT = auto()

    MECHANISMS = {
        Keys.DATE: {
            "field": "created_at",
            "description": "Date Created",
        },
        Keys.SCORE: {
            "field": "score",
            "description": "Score",
        },
        Keys.CHARACTER_LIKENESS: {
            "field": "character_likeness",
            "description": "Similarity to",
        },
        Keys.SHARPNESS: {
            "field": "sharpness",
            "description": "Sharpness",
        },
        Keys.EDGE_DENSITY: {
            "field": "edge_density",
            "description": "Edge Density",
        },
        Keys.NOISE_LEVEL: {
            "field": "noise_level",
            "description": "Noise Level",
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


class Picture(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    file_path: Optional[str] = None
    description: Optional[str] = None
    format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    created_at: Optional[datetime] = Field(
        default=None, sa_column=Column("created_at", type_=DateTime, nullable=True)
    )
    text_embedding: Optional[np.ndarray] = Field(
        sa_column=Column("text_embedding", LargeBinary, default=None, nullable=True)
    )
    thumbnail: Optional[Image.Image] = Field(
        sa_column=Column("thumbnail", LargeBinary, default=None, nullable=True)
    )
    score: Optional[int] = None
    pixel_sha: Optional[str] = Field(default=None, index=True)

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
    tags: List["Tag"] = Relationship(
        back_populates="picture",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
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
                "original_prompt": getattr(character, "original_prompt", None),
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
        fuzzy_weight: float = 0.5,
        embedding_weight: float = 0.5,
        threshold: float = 0.0,
        offset: int = 0,
        limit: int = sys.maxsize,
        select_fields: Optional[List[str]] = None,
    ) -> List["Picture"]:
        """
        Hybrid semantic search: combines fuzzy tag search (levenshtein SQL function) and embedding similarity (cosine_similarity SQL function).
        Orders by combined score in SQL.
        """
        query_embedding = text_to_embedding(query)
        query_embedding_bytes = query_embedding.tobytes()

        logger.debug(
            f"Performing semantic search for query='{query}' and query_words={query_words} with fuzzy_weight={fuzzy_weight}, embedding_weight={embedding_weight}"
        )

        query_str = " ".join(query_words)
        # Subquery: get avg levenshtein distance for each picture's tags
        tag_subq = (
            select(
                Tag.picture_id,
                func.avg(func.levenshtein(Tag.tag, query_str)).label("avg_tag_dist"),
            )
            .group_by(Tag.picture_id)
            .subquery()
        )

        # Main query: join pictures with tag_subq, compute combined score
        stmt = (
            select(
                cls,
                (
                    fuzzy_weight
                    * (
                        1.0
                        - func.pow(
                            func.coalesce(tag_subq.c.avg_tag_dist, func.length(query))
                            / func.length(query),
                            1.0 / 3.0,
                        )
                    )
                    + embedding_weight
                    * func.cosine_similarity(cls.text_embedding, query_embedding_bytes)
                ).label("combined_score"),
                (
                    1.0
                    - func.pow(
                        func.coalesce(tag_subq.c.avg_tag_dist, func.length(query))
                        / func.length(query),
                        1.0 / 3.0,
                    )
                ).label("fuzzy_score"),
                func.cosine_similarity(cls.text_embedding, query_embedding_bytes).label(
                    "embedding_score"
                ),
            )
            .outerjoin(tag_subq, cls.id == tag_subq.c.picture_id)
            .order_by(desc("combined_score"))
            .offset(offset)
            .limit(limit)
        )

        # Apply select_fields logic (like in find)
        if select_fields:
            select_fields = list(set(select_fields) | {"id"})
            from sqlalchemy.orm import load_only, selectinload

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

        results = session.exec(stmt).all()
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
        sort: Optional[SortMechanism] = None,
        offset: int = 0,
        limit: int = sys.maxsize,
        select_fields: Optional[List[str]] = None,
        format: Optional[List[str]] = None,
        **search,
    ) -> List["Picture"]:
        """
        Find pictures based on provided filters.
        """
        query = select(cls)

        logger.info("Got search parameters: %s", search)
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

        for attr, value in search.items():
            if hasattr(cls, attr):
                if isinstance(value, list):
                    query = query.where(getattr(cls, attr).in_(value))
                else:
                    query = query.where(getattr(cls, attr) == value)

        if format:
            query = query.where(cls.format.in_(format))

        if sort:
            field_name = sort.field
            field = getattr(cls, field_name, None)
            if field is not None:
                if sort.descending:
                    query = query.order_by(field.desc())
                else:
                    query = query.order_by(field.asc())
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

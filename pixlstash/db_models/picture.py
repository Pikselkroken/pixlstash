import base64
import json
import sys
import numpy as np

from datetime import datetime

from enum import Enum, auto, IntEnum
from sqlalchemy import Float, String, desc, func, or_, text
from sqlalchemy.orm import aliased, load_only, selectinload
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
from typing import ClassVar, Optional, List, Union, TYPE_CHECKING

from .character import Character
from .face import Face
from .picture_project import PictureProjectMember
from .picture_set import PictureSet, PictureSetMember
from .picture_stack import PictureStack
from .quality import Quality
from .guest_score import GuestScore
from .tag import Tag
from .tag_prediction import TagPrediction

from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from .character import Character
    from .picture_likeness import PictureLikeness
    from .project import Project
    from .reference_folder import ReferenceFolder


# Configure logging for the module
logger = get_logger(__name__)


# Class for sorting mechanisms (replaces Enum)
class SortMechanism:
    class Keys(Enum):
        DATE = auto()
        IMPORTED_AT = auto()
        SCORE = auto()
        CHARACTER_LIKENESS = auto()
        LIKENESS_GROUPS = auto()
        IMAGE_SIZE = auto()
        SMART_SCORE = auto()
        TEXT_CONTENT = auto()

    MECHANISMS = {
        Keys.DATE: {
            "field": "created_at",
            "description": "Date Created",
        },
        Keys.IMPORTED_AT: {
            "field": "imported_at",
            "description": "Date Imported",
        },
        Keys.SCORE: {
            "field": "score",
            "description": "Score",
        },
        Keys.SMART_SCORE: {
            "field": "smart_score",
            "description": "Smart Score",
        },
        Keys.IMAGE_SIZE: {
            "field": None,  # Special case, not a direct field
            "description": "Image Size",
        },
        Keys.TEXT_CONTENT: {
            "field": None,  # Special case, requires Quality join
            "description": "Text Content",
        },
        Keys.CHARACTER_LIKENESS: {
            "field": "character_likeness",
            "description": "Similarity to ...",
        },
        Keys.LIKENESS_GROUPS: {
            "field": "id",
            "description": "Likeness Groups ...",
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

# Sentinel marking "no project_id filter supplied" in Picture.find, so a real
# project_id of None (the unassigned scope) can be told apart from absence.
_NO_PROJECT_SCOPE = object()


def _default_likeness_parameters() -> np.ndarray:
    return np.full(
        len(LikenessParameter), LIKENESS_PARAMETER_SENTINEL, dtype=np.float32
    )


class ExportType(Enum):
    FULL = "full"
    FACE = "face"

    @classmethod
    def from_string(cls, value: str) -> "ExportType":
        d = (value or "").lower()
        for member in cls:
            if member.value == d:
                return member
        return cls.FULL


class Picture(SQLModel, table=True):
    ExportType: ClassVar[type["ExportType"]] = ExportType
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
    smart_score: Optional[float] = Field(default=None, index=True)
    text_score: Optional[float] = Field(default=None, index=True)
    pixel_sha: Optional[str] = Field(default=None, index=True)
    deleted: bool = Field(default=False, index=True)
    stack_id: Optional[int] = Field(
        default=None, foreign_key="picturestack.id", index=True
    )
    stack_position: Optional[int] = Field(default=None, index=True)
    original_file_name: Optional[str] = Field(
        default=None,
        sa_column=Column("original_file_name", String, default=None, nullable=True),
    )
    comfyui_positive_prompt: Optional[str] = Field(
        default=None,
        sa_column=Column(
            "comfyui_positive_prompt", String, default=None, nullable=True
        ),
    )
    # JSON-serialised list[str] of checkpoint / UNET names used at generation time.
    # NULL means this picture has never been checked for ComfyUI metadata;
    # "[]" is the sentinel meaning checked but no models found.
    comfyui_models: Optional[str] = Field(
        default=None,
        sa_column=Column("comfyui_models", String, default=None, nullable=True),
    )
    # JSON-serialised list[str] of LoRA names used at generation time.
    # NULL means this picture has never been checked for ComfyUI metadata;
    # "[]" is the sentinel meaning checked but no LoRAs found.
    comfyui_loras: Optional[str] = Field(
        default=None,
        sa_column=Column("comfyui_loras", String, default=None, nullable=True),
    )
    project_id: Optional[int] = Field(
        default=None, foreign_key="project.id", index=True
    )
    tag_uncertainty: Optional[float] = Field(default=None, index=True)
    anomaly_tag_uncertainty: Optional[float] = Field(default=None, index=True)
    # Set by the character-assignment endpoint when picture_ids are provided but
    # face extraction has not yet run.  Cleared (and the best face assigned) when
    # FaceExtractionTask completes for the picture.
    pending_character_id: Optional[int] = Field(default=None, index=True)
    # Set on T2I-generated pictures when the prompt was associated with a source
    # picture.  SourceFaceLikenessTask compares face embeddings against the source
    # faces and assigns character IDs where cosine similarity >= 0.7, then clears
    # this field to mark the picture as processed.
    source_picture_id: Optional[int] = Field(
        default=None, foreign_key="picture.id", index=True
    )
    reference_folder_id: Optional[int] = Field(
        default=None, foreign_key="reference_folder.id", index=True
    )
    # Absolute path to the import-folder root that produced this picture.
    # NULL for pictures imported through other workflows.
    import_source_folder: Optional[str] = Field(
        default=None,
        sa_column=Column(
            "import_source_folder",
            String,
            default=None,
            nullable=True,
            index=True,
        ),
    )
    # Absolute path to the tags sidecar file (comma-separated tags) tracked for
    # this reference-folder picture.  NULL when no tags sidecar exists / applies.
    tags_file: Optional[str] = Field(
        default=None,
        sa_column=Column("tags_file", String, default=None, nullable=True),
    )
    # Unix timestamp (float) of the tags sidecar's mtime when it was last read or
    # written.  Used to detect external changes on subsequent scans without
    # reading file content, and to avoid re-importing our own write-back.
    tags_file_mtime: Optional[float] = Field(
        default=None,
        sa_column=Column("tags_file_mtime", Float, default=None, nullable=True),
    )
    # Absolute path to the description sidecar file (free-form text) tracked for
    # this reference-folder picture.  NULL when none exists / applies.
    description_file: Optional[str] = Field(
        default=None,
        sa_column=Column("description_file", String, default=None, nullable=True),
    )
    # Unix timestamp (float) of the description sidecar's mtime when last read or
    # written.
    description_file_mtime: Optional[float] = Field(
        default=None,
        sa_column=Column("description_file_mtime", Float, default=None, nullable=True),
    )
    # SHA-256 hex digest of the picture's user-visible metadata (column values
    # + sorted tag strings).  Recomputed automatically via an after_flush hook
    # in database.py whenever Picture/Tag rows change.  NULL means the hash
    # has not been computed yet.  Used for fast checkpoint-identity comparisons
    # in the context-menu submenu.
    metadata_hash: Optional[str] = Field(
        default=None,
        sa_column=Column("metadata_hash", String, default=None, nullable=True),
    )

    # Relationships
    quality: Optional["Quality"] = Relationship(
        back_populates="picture",
        sa_relationship_kwargs={
            "foreign_keys": "[Quality.picture_id]",
        },
    )
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
            "foreign_keys": "[Tag.picture_id]",
        },
    )
    tag_predictions: List["TagPrediction"] = Relationship(
        back_populates="picture",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
            "foreign_keys": "[TagPrediction.picture_id]",
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
    stack: Optional["PictureStack"] = Relationship(back_populates="pictures")
    projects: List["Project"] = Relationship(
        back_populates="pictures", link_model=PictureProjectMember
    )
    reference_folder: Optional["ReferenceFolder"] = Relationship(
        back_populates="pictures"
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
        Returns a structured dict for embedding: description, tags, character info,
        and ComfyUI generation metadata (positive prompt, models, LoRAs) when stored.
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
        # comfyui_models is NULL when not yet checked; "[]" is the sentinel for
        # "checked, no models".  Only add the comfyui key when there is actually
        # something useful to embed.
        comfyui_models_list = (
            json.loads(self.comfyui_models) if self.comfyui_models else []
        )
        comfyui_loras_list = (
            json.loads(self.comfyui_loras) if self.comfyui_loras else []
        )
        if self.comfyui_positive_prompt or comfyui_models_list or comfyui_loras_list:
            data["comfyui"] = {
                "positive_prompt": self.comfyui_positive_prompt or None,
                "models": comfyui_models_list,
                "loras": comfyui_loras_list,
            }
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
        include_unimported: bool = True,
        candidate_ids: Optional[List[int]] = None,
        min_score: Optional[int] = None,
        max_score: Optional[int] = None,
        smart_score_bucket: Optional[str] = None,
        resolution_bucket: Optional[str] = None,
        comfyui_models_filter: Optional[List[str]] = None,
        comfyui_loras_filter: Optional[List[str]] = None,
        tags_filter: Optional[List[str]] = None,
        tags_rejected_filter: Optional[List[str]] = None,
    ) -> List["Picture"]:
        """
        Hybrid semantic search: combines fuzzy tag search (levenshtein SQL function) and embedding similarity (cosine_similarity SQL function).
        Orders by combined score in SQL.
        """
        if candidate_ids is not None and not candidate_ids:
            return []
        # Imported lazily to avoid a circular import (predicate_filter imports Picture).
        from pixlstash.utils.query.predicate_filter import PredicateFilter

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

        # The deleted / unimported lifecycle predicates are applied exactly once,
        # by the PredicateFilter compiler below (it receives only_deleted /
        # include_deleted / include_unimported). The previously-inline copy here
        # was redundant (idempotent AND, no row drift) and is removed so the
        # filter has a single owner.

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

        if candidate_ids:
            stmt = stmt.where(cls.id.in_(candidate_ids))

        # Lifecycle + intrinsic predicates via the shared compiler.  semantic_search
        # carries a subset of the vocabulary (no hidden-tags / confidence / face /
        # file-path); unset fields are no-ops.  ComfyUI here is AND-of-EXISTS (no
        # stack expansion), which PredicateFilter emits directly.
        stmt = PredicateFilter(
            format=format,
            min_score=min_score,
            max_score=max_score,
            smart_score_bucket=smart_score_bucket,
            resolution_bucket=resolution_bucket,
            comfyui_models_filter=comfyui_models_filter,
            comfyui_loras_filter=comfyui_loras_filter,
            tags_filter=tags_filter,
            tags_rejected_filter=tags_rejected_filter,
            only_deleted=only_deleted,
            include_deleted=include_deleted,
            include_unimported=include_unimported,
        ).apply(stmt)

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
        d["likeness_score"] = max(0.0, score)
        return d

    @classmethod
    def _get_stack_leader_ids(cls, session, only_deleted: bool = False) -> set[int]:
        """Return the set of picture IDs that are the leader of their stack.

        Uses a single window-function query (ROW_NUMBER OVER PARTITION BY stack_id)
        so cost is O(N) over stacked pictures rather than a correlated subquery
        that runs once per row. Matches the JS compareStackOrder ranking:
        lowest stack_position (NULLs last) → highest score → newest created_at → lowest id.
        """
        deleted_val = 1 if only_deleted else 0
        sql = text("""
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY stack_id
                           ORDER BY COALESCE(stack_position, 999999) ASC,
                                    COALESCE(score, 0) DESC,
                                    created_at DESC,
                                    id ASC
                       ) AS rn
                FROM picture
                WHERE stack_id IS NOT NULL
                  AND deleted = :deleted
            ) WHERE rn = 1
        """)
        rows = session.execute(sql.bindparams(deleted=deleted_val)).all()
        return {row[0] for row in rows}

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
        include_unimported: bool = True,
        stack_leaders_only: bool = False,
        comfyui_models_filter: Optional[List[str]] = None,
        comfyui_loras_filter: Optional[List[str]] = None,
        tags_filter: Optional[List[str]] = None,
        tags_rejected_filter: Optional[List[str]] = None,
        tags_confidence_above_filter: Optional[List[str]] = None,
        tags_confidence_below_filter: Optional[List[str]] = None,
        hidden_tags_filter: Optional[List[str]] = None,
        face_filter: Optional[str] = None,
        impossible_sources: Optional[List[str]] = None,
        min_score: Optional[int] = None,
        max_score: Optional[int] = None,
        smart_score_bucket: Optional[str] = None,
        resolution_bucket: Optional[str] = None,
        file_path_prefix: Optional[str] = None,
        guest_session_id: Optional[str] = None,
        guest_token_id: Optional[int] = None,
        count_only: bool = False,
        **search,
    ) -> Union[List["Picture"], int]:
        """
        Find pictures based on provided filters.

        When count_only=True, returns the integer count of matching pictures
        instead of the picture objects.  Sort, offset, and limit are ignored.
        """
        # Imported lazily: predicate_filter imports Picture, so a module-level
        # import here would be circular.
        from pixlstash.utils.query.predicate_filter import (
            PredicateFilter,
            comfyui_leaf_parts,
        )

        query = select(func.count(cls.id)) if count_only else select(cls)

        logger.debug("Got search parameters: %s", search)
        if select_fields and not count_only:
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

        # The deleted / unimported lifecycle predicates are applied exactly once,
        # by the PredicateFilter compiler below (it receives only_deleted /
        # include_deleted / include_unimported). The previously-inline copy here
        # was redundant (idempotent AND, no row drift) and is removed so the
        # filter has a single owner.

        for attr, value in search.items():
            if attr == "project_id":
                membership_query = select(PictureProjectMember.picture_id).where(
                    PictureProjectMember.picture_id == cls.id
                )
                if value is None:
                    query = query.where(~exists(membership_query))
                elif isinstance(value, list):
                    query = query.where(
                        exists(
                            membership_query.where(
                                PictureProjectMember.project_id.in_(value)
                            )
                        )
                    )
                else:
                    query = query.where(
                        exists(
                            membership_query.where(
                                PictureProjectMember.project_id == value
                            )
                        )
                    )
                continue
            if hasattr(cls, attr):
                if isinstance(value, list):
                    query = query.where(getattr(cls, attr).in_(value))
                else:
                    query = query.where(getattr(cls, attr) == value)

        # All lifecycle + intrinsic-attribute predicates are compiled by the shared
        # PredicateFilter (the single source of truth for the WHERE logic that used
        # to be duplicated across the builder sites).  ComfyUI membership is the one
        # leaf find() does NOT delegate: it expands the match across stack members
        # (an OR over the whole stack), so it is applied separately below.
        predicate_filter = PredicateFilter(
            format=format,
            min_score=min_score,
            max_score=max_score,
            smart_score_bucket=smart_score_bucket,
            resolution_bucket=resolution_bucket,
            tags_filter=tags_filter,
            tags_rejected_filter=tags_rejected_filter,
            hidden_tags_filter=hidden_tags_filter,
            tags_confidence_above_filter=tags_confidence_above_filter,
            tags_confidence_below_filter=tags_confidence_below_filter,
            face_filter=face_filter,
            impossible_sources=impossible_sources,
            file_path_prefix=file_path_prefix,
            only_deleted=only_deleted,
            include_deleted=include_deleted,
            include_unimported=include_unimported,
        )
        query = predicate_filter.apply(query)

        # Build comfyui filter conditions via the shared leaf-snippet helper.  Two
        # parallel fragment lists are returned:
        # comfyui_self_parts   – fragments that test the picture row itself.
        # comfyui_member_parts – equivalent fragments that test an aliased member
        #                        row (_m) used in a stack-member EXISTS subquery.
        # comfyui_bind_params  – shared bind-parameter dict (same names in both).
        comfyui_self_parts, comfyui_member_parts, comfyui_bind_params = (
            comfyui_leaf_parts(comfyui_models_filter, comfyui_loras_filter)
        )

        # A stack leader is either an unstacked picture or the picture with
        # stack_position == 0 in its stack.  This inline condition replaces the
        # former _get_stack_leader_ids() round-trip + IN(ids) approach, which
        # was unacceptably slow on large libraries.
        if stack_leaders_only:
            project_scope = search.get("project_id", _NO_PROJECT_SCOPE)
            if project_scope is _NO_PROJECT_SCOPE or isinstance(
                project_scope, (list, tuple)
            ):
                # Unscoped (or multi-project) grid: fast path — leader is the
                # global stack_position == 0, backed by the partial leader index.
                query = query.where(
                    or_(cls.stack_id.is_(None), cls.stack_position == 0)
                )
            else:
                # Project-scoped grid: represent each stack by its lowest-positioned
                # member that is itself in this project scope, so a stack is not
                # dropped just because its global position-0 leader belongs to a
                # different project (e.g. a legacy stack whose membership predates
                # the stack-atomic invariant). Mirrors find_unassigned().
                sibling = aliased(cls)
                sibling_project = select(PictureProjectMember.picture_id).where(
                    PictureProjectMember.picture_id == sibling.id
                )
                if project_scope is None:
                    sibling_in_scope = ~exists(sibling_project)
                else:
                    sibling_in_scope = exists(
                        sibling_project.where(
                            PictureProjectMember.project_id == project_scope
                        )
                    )
                cur_pos = func.coalesce(cls.stack_position, 999999)
                sib_pos = func.coalesce(sibling.stack_position, 999999)
                has_higher_ranked_sibling = exists(
                    select(sibling.id).where(
                        sibling.stack_id == cls.stack_id,
                        sibling.deleted.is_(False),
                        sibling_in_scope,
                        or_(
                            sib_pos < cur_pos,
                            (sib_pos == cur_pos) & (sibling.id < cls.id),
                        ),
                    )
                )
                query = query.where(
                    or_(cls.stack_id.is_(None), ~has_higher_ranked_sibling)
                )
        if comfyui_self_parts:
            self_where = " OR ".join(comfyui_self_parts)
            if stack_leaders_only:
                # Restore the original behaviour: include a stack leader when
                # *any* member of the stack satisfies the ComfyUI filter, not
                # only when the leader row itself satisfies it.
                member_where = " OR ".join(comfyui_member_parts)
                comfyui_sql = (
                    f"({self_where})"
                    f" OR (picture.stack_id IS NOT NULL"
                    f" AND EXISTS ("
                    f"SELECT 1 FROM picture AS _m"
                    f" WHERE _m.stack_id = picture.stack_id"
                    f" AND ({member_where})"
                    f"))"
                )
                query = query.where(text(comfyui_sql).bindparams(**comfyui_bind_params))
            else:
                query = query.where(
                    text(f"({self_where})").bindparams(**comfyui_bind_params)
                )

        if sort_mech and not count_only:
            if sort_mech.key == SortMechanism.Keys.IMAGE_SIZE:
                # Sort by width * height
                if sort_mech.descending:
                    query = query.order_by(
                        (cls.width * cls.height).desc(), cls.id.desc()
                    )
                else:
                    query = query.order_by((cls.width * cls.height).asc(), cls.id.asc())
            elif sort_mech.key == SortMechanism.Keys.TEXT_CONTENT:
                if sort_mech.descending:
                    query = query.order_by(cls.text_score.desc(), cls.id.desc())
                else:
                    query = query.order_by(cls.text_score.asc(), cls.id.asc())
            elif sort_mech.key == SortMechanism.Keys.SCORE and guest_session_id:
                # Guest session: sort by the guest's own score, falling back to
                # picture.score when no guest_score row exists for this picture.
                gs_alias = aliased(GuestScore)
                join_cond = (gs_alias.picture_id == cls.id) & (
                    gs_alias.session_id == guest_session_id
                )
                if guest_token_id is not None:
                    join_cond = join_cond & (gs_alias.token_id == guest_token_id)
                query = query.outerjoin(gs_alias, join_cond)
                score_expr = func.coalesce(gs_alias.score, cls.score)
                if sort_mech.descending:
                    query = query.order_by(score_expr.desc(), cls.id.desc())
                else:
                    query = query.order_by(score_expr.asc(), cls.id.asc())
            else:
                field_name = sort_mech.field
                field = (
                    getattr(cls, field_name, None)
                    if isinstance(field_name, str)
                    else None
                )
                if field is not None:
                    if sort_mech.descending:
                        query = query.order_by(field.desc(), cls.id.desc())
                    else:
                        query = query.order_by(field.asc(), cls.id.asc())
        if not count_only and (offset > 0 or limit != sys.maxsize):
            query = query.offset(offset).limit(limit)

        if count_only:
            return session.execute(query).scalar_one()

        return session.exec(query).all()

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
            "smart_score",
            "created_at",
            "imported_at",
            "stack_id",
            "stack_position",
            "tag_uncertainty",
            "anomaly_tag_uncertainty",
            "text_score",
            "reference_folder_id",
            "file_path",
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
        stack_leaders_only: bool = False,
        min_score: Optional[int] = None,
        max_score: Optional[int] = None,
        smart_score_bucket: Optional[str] = None,
        resolution_bucket: Optional[str] = None,
        project_id: Optional[int] = None,
        only_unassigned_project: bool = False,
        tags_filter: Optional[List[str]] = None,
        tags_rejected_filter: Optional[List[str]] = None,
        tags_confidence_above_filter: Optional[List[str]] = None,
        tags_confidence_below_filter: Optional[List[str]] = None,
        hidden_tags_filter: Optional[List[str]] = None,
        face_filter: Optional[str] = None,
        impossible_sources: Optional[List[str]] = None,
        picture_ids: Optional[List[int]] = None,
        guest_session_id: Optional[str] = None,
        guest_token_id: Optional[int] = None,
        count_only: bool = False,
    ):
        # Imported lazily to avoid a circular import (predicate_filter imports Picture).
        from pixlstash.utils.query.predicate_filter import PredicateFilter

        query = select(func.count(cls.id)) if count_only else select(Picture)
        unassigned_conditions = cls.build_unassigned_conditions(
            enforce_stack_assignment=True,
            assignment_project_id=project_id,
            assignment_unassigned_project=only_unassigned_project,
        )
        query = query.where(
            *unassigned_conditions,
            Picture.deleted.is_(False),
        )

        if picture_ids is not None:
            query = query.where(Picture.id.in_(picture_ids))

        project_membership_query = select(PictureProjectMember.picture_id).where(
            PictureProjectMember.picture_id == Picture.id
        )
        if only_unassigned_project:
            query = query.where(~exists(project_membership_query))
        elif project_id is not None:
            query = query.where(
                exists(
                    project_membership_query.where(
                        PictureProjectMember.project_id == project_id
                    )
                )
            )

        # Intrinsic-attribute predicates via the shared compiler.  The unassigned /
        # project / deleted scoping is applied above; stack-leader collapsing stays
        # below.  find_unassigned never filters on comfyui / file-path / import-source
        # / import-excluded, so those fields are left unset.
        query = PredicateFilter(
            format=format,
            min_score=min_score,
            max_score=max_score,
            smart_score_bucket=smart_score_bucket,
            resolution_bucket=resolution_bucket,
            tags_filter=tags_filter,
            tags_rejected_filter=tags_rejected_filter,
            hidden_tags_filter=hidden_tags_filter,
            tags_confidence_above_filter=tags_confidence_above_filter,
            tags_confidence_below_filter=tags_confidence_below_filter,
            face_filter=face_filter,
            impossible_sources=impossible_sources,
            apply_deleted_filter=False,
        ).apply(query)

        if stack_leaders_only:
            project_scope_active = project_id is not None or only_unassigned_project
            if not project_scope_active:
                # Fast path: the stack leader is simply stack_position == 0,
                # backed by the partial ix_picture_grid_leaders_* indexes (0047).
                # Left unchanged so the common (unscoped) grid stays fast.
                query = query.where(
                    or_(Picture.stack_id.is_(None), Picture.stack_position == 0)
                )
            else:
                # Project-scoped grid: the global position-0 leader may belong to
                # a different project and be filtered out, which would wrongly drop
                # the whole stack. Represent each stack by its lowest-positioned
                # member that is itself in this project scope. This correlated check
                # runs only over the already project-narrowed candidate set, so the
                # common (unscoped) grid still hits the fast path above.
                sibling = aliased(Picture)
                sibling_project = select(PictureProjectMember.picture_id).where(
                    PictureProjectMember.picture_id == sibling.id
                )
                if only_unassigned_project:
                    sibling_in_scope = ~exists(sibling_project)
                else:
                    sibling_in_scope = exists(
                        sibling_project.where(
                            PictureProjectMember.project_id == project_id
                        )
                    )
                # NULL positions sort last, matching normalize_stack_positions and
                # the global "leader == position 0" convention.
                cur_pos = func.coalesce(Picture.stack_position, 999999)
                sib_pos = func.coalesce(sibling.stack_position, 999999)
                has_higher_ranked_sibling = exists(
                    select(sibling.id).where(
                        sibling.stack_id == Picture.stack_id,
                        sibling.deleted.is_(False),
                        sibling_in_scope,
                        or_(
                            sib_pos < cur_pos,
                            (sib_pos == cur_pos) & (sibling.id < Picture.id),
                        ),
                    )
                )
                query = query.where(
                    or_(Picture.stack_id.is_(None), ~has_higher_ranked_sibling)
                )

        select_fields = metadata_fields or cls.metadata_fields()
        if count_only:
            return session.execute(query).scalar_one()
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
            elif sort_mech.key == SortMechanism.Keys.TEXT_CONTENT:
                query = query.order_by(
                    Picture.text_score.desc()
                    if sort_mech.descending
                    else Picture.text_score.asc(),
                    Picture.id.desc() if sort_mech.descending else Picture.id.asc(),
                )
            elif sort_mech.key == SortMechanism.Keys.SCORE and guest_session_id:
                # Guest session: sort by the guest's own score, falling back to
                # picture.score when no guest_score row exists for this picture.
                gs_alias = aliased(GuestScore)
                join_cond = (gs_alias.picture_id == Picture.id) & (
                    gs_alias.session_id == guest_session_id
                )
                if guest_token_id is not None:
                    join_cond = join_cond & (gs_alias.token_id == guest_token_id)
                query = query.outerjoin(gs_alias, join_cond)
                score_expr = func.coalesce(gs_alias.score, Picture.score)
                query = query.order_by(
                    score_expr.desc() if sort_mech.descending else score_expr.asc(),
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

    @classmethod
    def build_unassigned_conditions(
        cls,
        *,
        enforce_stack_assignment: bool = False,
        assignment_project_id: Optional[int] = None,
        assignment_unassigned_project: bool = False,
    ) -> list:
        """Build SQL predicates for pictures that should count as unassigned.

        When enforce_stack_assignment is enabled, any stack with at least one
        assigned member (character face or set membership) is treated as assigned,
        so all pictures in that stack are excluded from unassigned queries.

        When assignment project scope is provided, assignment is evaluated only
        against characters/sets in that same project scope.
        """
        if assignment_unassigned_project:
            project_scope = Character.project_id.is_(None)
            set_scope = PictureSet.project_id.is_(None)
        elif assignment_project_id is not None:
            project_scope = Character.project_id == assignment_project_id
            set_scope = PictureSet.project_id == assignment_project_id
        else:
            project_scope = None
            set_scope = None

        assigned_face_query = select(Face.id).where(
            Face.picture_id == cls.id,
            Face.character_id.is_not(None),
        )
        if project_scope is not None:
            assigned_face_query = assigned_face_query.join(
                Character, Character.id == Face.character_id
            ).where(project_scope)

        assigned_set_query = select(PictureSetMember.picture_id).where(
            PictureSetMember.picture_id == cls.id
        )
        if set_scope is not None:
            assigned_set_query = assigned_set_query.join(
                PictureSet,
                PictureSet.id == PictureSetMember.set_id,
            ).where(set_scope)

        conditions = [~exists(assigned_face_query), ~exists(assigned_set_query)]

        if not enforce_stack_assignment:
            return conditions

        stack_picture = aliased(cls)
        stack_assigned_face_query = (
            select(Face.id)
            .join(stack_picture, stack_picture.id == Face.picture_id)
            .where(
                stack_picture.stack_id.is_not(None),
                stack_picture.stack_id == cls.stack_id,
                Face.character_id.is_not(None),
            )
        )
        if project_scope is not None:
            stack_assigned_face_query = stack_assigned_face_query.join(
                Character, Character.id == Face.character_id
            ).where(project_scope)

        stack_assigned_set_query = (
            select(PictureSetMember.picture_id)
            .join(
                stack_picture,
                stack_picture.id == PictureSetMember.picture_id,
            )
            .where(
                stack_picture.stack_id.is_not(None),
                stack_picture.stack_id == cls.stack_id,
            )
        )
        if set_scope is not None:
            stack_assigned_set_query = stack_assigned_set_query.join(
                PictureSet,
                PictureSet.id == PictureSetMember.set_id,
            ).where(set_scope)

        stack_has_assigned_face = exists(stack_assigned_face_query)
        stack_has_set_member = exists(stack_assigned_set_query)
        conditions.extend([~stack_has_assigned_face, ~stack_has_set_member])
        return conditions

    @staticmethod
    def fetch_features(session, picture_ids):
        """
        Fetch faces for a list of picture IDs.
        Returns:
            faces_by_pic: dict[picture_id, list[Face]]
        """
        faces = session.exec(select(Face).where(Face.picture_id.in_(picture_ids))).all()

        faces_by_pic = {}
        for face in faces:
            faces_by_pic.setdefault(face.picture_id, []).append(face)

        return (
            faces_by_pic,
            {},
            {},
            {},
        )

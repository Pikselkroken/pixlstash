import base64
import sqlite3
import json

from dataclasses import dataclass, field
from typing import Self, Union

from .logging import get_logger

# Configure logging for the module
logger = get_logger(__name__)


###################################
# Data models for database tables #
###################################


@dataclass
class PictureTagModel:
    """
    Database model for the picture_tags table.
    """

    __tablename__ = "picture_tags"
    picture_id: str = field(
        default=None, metadata={"foreign_key": "pictures(id)", "composite_key": True}
    )
    tag: str = field(default=None, metadata={"composite_key": True})


@dataclass
class PictureModel:
    """
    Database model for the pictures table.
    """

    __tablename__ = "pictures"
    id: str = field(default=None, metadata={"primary_key": True})
    character_id: str = field(default=None, metadata={"foreign_key": "characters(id)"})
    file_path: str = field(default=None)
    description: str = field(default=None, metadata={"include_in_embedding": True})
    format: str = field(default=None)
    width: int = field(default=None)
    height: int = field(default=None)
    size_bytes: int = field(default=None)
    created_at: str = field(default=None)
    is_reference: int = field(default=0)
    embedding: bytes = field(default=None)
    face_bbox: str = field(default=None)
    thumbnail: bytes = field(default=None)
    quality: str = field(default=None)
    face_quality: str = field(default=None)
    score: int = field(default=None)
    character_likeness: float = field(default=None)
    pixel_sha: str = field(default=None)
    tags: list[str] = field(
        default_factory=list, metadata={"db_ignore": True, "include_in_embedding": True}
    )

    def to_dict(self, include=None, exclude=None) -> dict:
        result = {
            "id": self.id,
            "character_id": self.character_id,
            "file_path": self.file_path,
            "description": self.description,
            "tags": self.tags,
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "is_reference": int(self.is_reference),
            "embedding": base64.b64encode(self.embedding).decode("ascii")
            if self.embedding is not None
            else None,
            "face_bbox": json.dumps(self.face_bbox) if self.face_bbox else None,
            "thumbnail": base64.b64encode(self.thumbnail).decode("ascii")
            if self.thumbnail is not None
            else None,
            "quality": self.quality,
            "face_quality": self.face_quality,
            "score": self.score,
            "character_likeness": self.character_likeness,
            "pixel_sha": self.pixel_sha,
        }
        if include:
            result = {k: v for k, v in result.items() if k in include}
        if exclude:
            for k in exclude:
                result.pop(k, None)
        return result

    @classmethod
    def from_dict(cls, row: Union[dict, sqlite3.Row]) -> Self:
        assert isinstance(row, dict) or isinstance(row, sqlite3.Row)

        assert "id" in row.keys(), "PictureModel.from_dict requires 'id' field in row"

        # Embedding and thumbnail are always stored as base64 strings in DB (from to_dict())
        # Decode them to bytes for internal use
        embedding = None
        if "embedding" in row.keys() and row["embedding"] is not None:
            embedding = base64.b64decode(row["embedding"])

        thumbnail = None
        if "thumbnail" in row.keys() and row["thumbnail"] is not None:
            thumbnail = base64.b64decode(row["thumbnail"])

        return cls(
            id=row["id"],
            character_id=row["character_id"] if "character_id" in row.keys() else None,
            file_path=row["file_path"] if "file_path" in row.keys() else None,
            description=row["description"] if "description" in row.keys() else None,
            tags=row["tags"] if "tags" in row.keys() else None,
            format=row["format"] if "format" in row.keys() else None,
            width=row["width"] if "width" in row.keys() else None,
            height=row["height"] if "height" in row.keys() else None,
            size_bytes=row["size_bytes"] if "size_bytes" in row.keys() else None,
            created_at=row["created_at"] if "created_at" in row.keys() else None,
            is_reference=row["is_reference"] == 1
            if "is_reference" in row.keys()
            else False,
            embedding=embedding,
            face_bbox=json.loads(row["face_bbox"])
            if "face_bbox" in row.keys() and row["face_bbox"]
            else None,
            thumbnail=thumbnail,
            quality=row["quality"] if "quality" in row.keys() else None,
            face_quality=row["face_quality"] if "face_quality" in row.keys() else None,
            score=row["score"] if "score" in row.keys() else None,
            character_likeness=row["character_likeness"]
            if "character_likeness" in row.keys()
            else None,
            pixel_sha=row["pixel_sha"] if "pixel_sha" in row.keys() else None,
        )

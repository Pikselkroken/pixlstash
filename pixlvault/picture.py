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
    tag: str = field(default=None, metadata={"composite_key": True, "index": True})


@dataclass
class PictureModel:
    """
    Database model for the pictures table.
    """

    __tablename__ = "pictures"
    id: str = field(default=None, metadata={"primary_key": True})
    file_path: str = field(default=None)
    description: str = field(default=None, metadata={"include_in_text_embedding": True})
    format: str = field(default=None)
    width: int = field(default=None)
    height: int = field(default=None)
    size_bytes: int = field(default=None)
    created_at: str = field(default=None)
    text_embedding: bytes = field(default=None)
    face_bbox: str = field(default=None)
    thumbnail: bytes = field(default=None)
    sharpness: float = field(default=None)
    edge_density: float = field(default=None)
    contrast: float = field(default=None)
    brightness: float = field(default=None)
    noise_level: float = field(default=None)
    face_sharpness: float = field(default=None)
    face_edge_density: float = field(default=None)
    face_contrast: float = field(default=None)
    face_brightness: float = field(default=None)
    face_noise_level: float = field(default=None)
    score: int = field(default=None, metadata={"index": True})
    character_likeness: float = field(default=None)
    facial_features: bytes = field(default=None)
    pixel_sha: str = field(
        default=None,
        metadata={"index": True, "unique_index": True},
    )
    primary_character_id: int = field(
        default=None, metadata={"foreign_key": "characters(id)", "index": True}
    )
    reference_picture_set_id: int = field(
        default=None, metadata={"foreign_key": "picture_sets(id)", "index": True}
    )
    tags: list[str] = field(
        default_factory=list,
        metadata={"db_ignore": True, "include_in_text_embedding": True},
    )
    character_ids: list[int] = field(default_factory=list, metadata={"db_ignore": True})

    __indexes__ = []

    @classmethod
    def metadata(cls):
        """
        Return a list of field names that are not type bytes (for lightweight/bulk queries).
        """
        return [
            f.name
            for f in cls.__dataclass_fields__.values()
            if f.type is not bytes and f.metadata.get("db_ignore") is not True
        ]

    def to_dict(self, include=None, exclude=None) -> dict:
        # Ensure no raw bytes are returned for any field
        def safe_float(val):
            if isinstance(val, bytes):
                # Log and convert to float if possible, else None
                import struct

                try:
                    # Try to decode as float32
                    return struct.unpack("f", val)[0]
                except Exception:
                    return None
            return float(val) if val is not None else None

        result = {
            "id": self.id,
            "primary_character_id": self.primary_character_id,
            "character_ids": self.character_ids,
            "file_path": self.file_path,
            "description": self.description,
            "tags": self.tags,
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "text_embedding": base64.b64encode(self.text_embedding).decode("ascii")
            if self.text_embedding is not None
            else None,
            "facial_features": base64.b64encode(self.facial_features).decode("ascii")
            if self.facial_features is not None
            else None,
            "face_bbox": json.dumps(self.face_bbox) if self.face_bbox else None,
            "thumbnail": base64.b64encode(self.thumbnail).decode("ascii")
            if self.thumbnail is not None
            else None,
            # Ensure metrics are always float, never bytes
            "sharpness": safe_float(self.sharpness),
            "edge_density": safe_float(self.edge_density),
            "contrast": safe_float(self.contrast),
            "brightness": safe_float(self.brightness),
            "noise_level": safe_float(self.noise_level),
            "face_sharpness": safe_float(self.face_sharpness),
            "face_edge_density": safe_float(self.face_edge_density),
            "face_contrast": safe_float(self.face_contrast),
            "face_brightness": safe_float(self.face_brightness),
            "face_noise_level": safe_float(self.face_noise_level),
            "score": self.score,
            "character_likeness": self.character_likeness,
            "pixel_sha": self.pixel_sha,
            "reference_picture_set_id": self.reference_picture_set_id,
        }
        # Assert no bytes in result
        for k, v in result.items():
            assert not isinstance(v, bytes), f"Field '{k}' is bytes in to_dict: {v!r}"
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

        text_embedding = None
        if "text_embedding" in row.keys() and row["text_embedding"] is not None:
            text_embedding = base64.b64decode(row["text_embedding"])

        facial_features = None
        if "facial_features" in row.keys() and row["facial_features"] is not None:
            facial_features = base64.b64decode(row["facial_features"])

        thumbnail = None
        if "thumbnail" in row.keys() and row["thumbnail"] is not None:
            thumbnail = base64.b64decode(row["thumbnail"])

        sharpness = row["sharpness"] if "sharpness" in row.keys() else None
        edge_density = row["edge_density"] if "edge_density" in row.keys() else None
        contrast = row["contrast"] if "contrast" in row.keys() else None
        brightness = row["brightness"] if "brightness" in row.keys() else None
        noise_level = row["noise_level"] if "noise_level" in row.keys() else None
        face_sharpness = (
            row["face_sharpness"] if "face_sharpness" in row.keys() else None
        )
        face_edge_density = (
            row["face_edge_density"] if "face_edge_density" in row.keys() else None
        )
        face_contrast = row["face_contrast"] if "face_contrast" in row.keys() else None
        face_brightness = (
            row["face_brightness"] if "face_brightness" in row.keys() else None
        )
        face_noise_level = (
            row["face_noise_level"] if "face_noise_level" in row.keys() else None
        )

        return cls(
            id=row["id"],
            file_path=row["file_path"] if "file_path" in row.keys() else None,
            description=row["description"] if "description" in row.keys() else None,
            tags=row["tags"] if "tags" in row.keys() else None,
            primary_character_id=row["primary_character_id"]
            if "primary_character_id" in row.keys()
            else None,
            character_ids=row["character_ids"] if "character_ids" in row.keys() else [],
            format=row["format"] if "format" in row.keys() else None,
            width=row["width"] if "width" in row.keys() else None,
            height=row["height"] if "height" in row.keys() else None,
            size_bytes=row["size_bytes"] if "size_bytes" in row.keys() else None,
            created_at=row["created_at"] if "created_at" in row.keys() else None,
            text_embedding=text_embedding,
            facial_features=facial_features,
            face_bbox=json.loads(row["face_bbox"])
            if "face_bbox" in row.keys() and row["face_bbox"]
            else None,
            thumbnail=thumbnail,
            sharpness=sharpness,
            edge_density=edge_density,
            contrast=contrast,
            brightness=brightness,
            noise_level=noise_level,
            face_sharpness=face_sharpness,
            face_edge_density=face_edge_density,
            face_contrast=face_contrast,
            face_brightness=face_brightness,
            face_noise_level=face_noise_level,
            score=row["score"] if "score" in row.keys() else None,
            character_likeness=row["character_likeness"]
            if "character_likeness" in row.keys()
            else None,
            pixel_sha=row["pixel_sha"] if "pixel_sha" in row.keys() else None,
            reference_picture_set_id=row["reference_picture_set_id"]
            if "reference_picture_set_id" in row.keys()
            else None,
        )

import base64
import sqlite3
import cv2
import json
import os
import uuid

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image
from typing import Optional, Self

from pixelurgy_vault.picture_utils import PictureUtils

from .logging import get_logger

# Configure logging for the module
logger = get_logger(__name__)


###################################
# Data models for database tables #
###################################
@dataclass
class PictureModel:
    """
    Database model for the pictures table.
    """

    __tablename__ = "pictures"
    id: str = field(default=None, metadata={"primary_key": True})
    character_id: str = field(default=None, metadata={"foreign_key": "characters(id)"})
    file_path: str = field(default=None)
    description: str = field(default=None)
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


class Picture:
    """Master asset representing a logical picture (stable UUID)."""

    def __init__(
        self,
        id: Optional[str] = None,
        character_id: Optional[str] = None,
        file_path: Optional[str] = None,
        description: Optional[str] = None,
        tags: list[str] = [],
        format: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        size_bytes: Optional[int] = None,
        created_at: Optional[str] = None,
        is_reference: bool = False,
        embedding: Optional[bytes] = None,
        face_bbox: Optional[list[float]] = None,
        thumbnail: Optional[bytes] = None,
        quality: Optional[str] = None,
        face_quality: Optional[str] = None,
        score: Optional[int] = None,
        character_likeness: Optional[float] = None,
        pixel_sha: Optional[str] = None,
    ):
        self.format = (
            format if format else file_path.split(".")[-1] if file_path else "png"
        )
        if id:
            self.id = id
        else:
            self.id = f"{uuid.uuid4().hex}.{self.format}"

        self.character_id = character_id
        self.file_path = file_path
        self.description = description
        self.tags = tags
        self.width = width
        self.height = height
        self.size_bytes = size_bytes

        self.created_at = created_at or datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        self.is_reference = is_reference
        self.embedding = embedding
        self.face_bbox = face_bbox
        self.thumbnail = thumbnail
        self.quality = quality
        self.face_quality = face_quality
        self.score = score
        self.character_likeness = character_likeness
        self.pixel_sha = pixel_sha
        if not self.pixel_sha and self.file_path and os.path.exists(self.file_path):
            self.pixel_sha = PictureUtils.calculate_hash_from_file_path(self.file_path)

    @staticmethod
    def create_from_file(
        image_root_path: str,
        source_file_path: str,
        picture_id: Optional[str] = None,
        character_id: Optional[str] = None,
        pixel_sha: Optional[str] = None,
    ) -> Self:
        """
        Create a Picture from a file path.
        Args:
            image_root_path (str): Root directory to store images.
            source_file_path (str): Path to the source image file.
            picture_id (str): Stable UUID for the picture.
            character_id (Optional[str]): Associated character ID.
            description (Optional[str]): Description of the picture.
        Returns:
            Picture: The created Picture object.
        """
        if not os.path.exists(source_file_path):
            raise ValueError(f"Source file path does not exist: {source_file_path}")
        with open(source_file_path, "rb") as f:
            image_bytes = f.read()
        return Picture.create_from_bytes(
            image_root_path=image_root_path,
            image_bytes=image_bytes,
            picture_id=picture_id,
            character_id=character_id,
            pixel_sha=pixel_sha,
        )

    @staticmethod
    def create_from_bytes(
        image_root_path: str,
        image_bytes: bytes,
        picture_id: Optional[str] = None,
        character_id: Optional[str] = None,
        pixel_sha: Optional[str] = None,
    ) -> Self:
        """
        Create a a Picture from raw bytes. Supports both images and videos.
        Args:
            image_root_path (str): Root directory to store images.
            image_bytes (bytes): Raw bytes of the image or video.
            picture_id (str): Stable UUID for the picture.
            character_id (Optional[str]): Associated character ID.
        Returns:
            Picture: The created Picture object.
        """

        if not pixel_sha:
            pixel_sha = PictureUtils.calculate_hash_from_bytes(image_bytes)

        # Try to detect if this is a video or image
        img_format = None
        width = height = None
        thumbnail_bytes = None
        is_video = False
        # Try image first
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                img_format = img.format or "PNG"
                width, height = img.size
                thumbnail_bytes = PictureUtils.generate_thumbnail_bytes(img)
        except Exception:
            # Not an image, try video
            is_video = True
        if is_video:
            # Write bytes to temp file to read with cv2
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name
            cap = cv2.VideoCapture(tmp_path)
            ret, frame = cap.read()
            if not ret:
                logger.error("Could not read first frame from video for thumbnail.")
            else:
                height, width = frame.shape[:2]
                thumbnail_bytes = PictureUtils.generate_thumbnail_bytes(frame)
            cap.release()
            img_format = "MP4"  # Default, could be improved by sniffing
            # Remove temp file
            os.remove(tmp_path)

        if not picture_id:
            picture_id = str(uuid.uuid4()) + f".{img_format.lower()}"

        file_path = os.path.join(image_root_path, picture_id)
        if os.path.exists(file_path):
            size_bytes = os.path.getsize(file_path)
        else:
            os.makedirs(image_root_path, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(image_bytes)
            size_bytes = len(image_bytes)

        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        pic = Picture(
            id=picture_id,
            file_path=file_path,
            format=img_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
            created_at=created_at,
            thumbnail=thumbnail_bytes,
            character_id=character_id,
            pixel_sha=pixel_sha,
        )
        return pic

    def to_dict(self, include=None, exclude=None):
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
            if self.embedding
            else None,
            "face_bbox": json.dumps(self.face_bbox) if self.face_bbox else None,
            "thumbnail": base64.b64encode(self.thumbnail).decode("ascii")
            if self.thumbnail
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
    def from_dict(cls, row):
        assert isinstance(row, dict) or isinstance(row, sqlite3.Row)
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
            embedding=base64.b64decode(row["embedding"])
            if "embedding" in row.keys() and row["embedding"]
            else None,
            face_bbox=json.loads(row["face_bbox"])
            if "face_bbox" in row.keys() and row["face_bbox"]
            else None,
            thumbnail=base64.b64decode(row["thumbnail"])
            if "thumbnail" in row.keys() and row["thumbnail"]
            else None,
            quality=row["quality"] if "quality" in row.keys() else None,
            face_quality=row["face_quality"] if "face_quality" in row.keys() else None,
            score=row["score"] if "score" in row.keys() else None,
            character_likeness=row["character_likeness"]
            if "character_likeness" in row.keys()
            else None,
            pixel_sha=row["pixel_sha"] if "pixel_sha" in row.keys() else None,
        )

import hashlib
import os
from typing import Optional, Tuple
from PIL import Image
from .picture_quality import PictureQuality
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime, timezone
from .logging import get_logger

# Configure logging for the module
logger = get_logger(__name__)


@dataclass
class PictureIteration:
    id: str
    picture_id: str
    file_path: str
    format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    created_at: Optional[str] = None
    is_master: int = 0
    derived_from: Optional[str] = None
    transform_metadata: Optional[str] = None
    thumbnail: Optional[bytes] = None
    quality: Optional[PictureQuality] = None
    score: Optional[int] = None
    pixel_sha: Optional[str] = None
    character_id: Optional[str] = None

    @staticmethod
    def _generate_thumbnail_bytes(
        pil_img: Image.Image, size=(256, 256)
    ) -> Optional[bytes]:
        try:
            img = pil_img.copy()
            img.thumbnail(size, resample=Image.LANCZOS)
            # Create a new square background
            thumb_bg = Image.new("RGBA", size, (255, 255, 255, 0))
            # Center the resized image
            offset_x = (size[0] - img.width) // 2
            offset_y = (size[1] - img.height) // 2
            thumb_bg.paste(img, (offset_x, offset_y))
            buf = BytesIO()
            thumb_bg.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Error generating thumbnail bytes: {e}")
            return None

    @staticmethod
    def create_from_bytes(
        image_root_path: str,
        image_bytes: bytes,
        picture_id: str,
        derived_from: Optional[str] = None,
        transform_metadata: Optional[str] = None,
        is_master: bool = False,
    ) -> Tuple[str, "PictureIteration"]:
        """Create an iteration from raw bytes. Returns (picture_uuid, PictureIteration)."""
        raw_sha = hashlib.sha256(image_bytes).hexdigest()
        if not picture_id:
            raise ValueError(
                "picture_uuid must be provided when creating a picture iteration."
            )

        # Determine format and metadata
        with Image.open(BytesIO(image_bytes)) as img:
            img_format = img.format or "PNG"
            width, height = img.size
            thumbnail_bytes = PictureIteration._generate_thumbnail_bytes(img)

        ext = f".{img_format.lower()}" if not img_format.startswith(".") else img_format
        file_path = os.path.join(image_root_path, f"{raw_sha}{ext}")
        if os.path.exists(file_path):
            # If file exists, we still return the iteration linked to the provided picture_uuid
            size_bytes = os.path.getsize(file_path)
        else:
            # Save bytes to disk
            os.makedirs(image_root_path, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(image_bytes)
            size_bytes = len(image_bytes)

        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        iteration = PictureIteration(
            id=raw_sha,
            picture_id=picture_id,
            file_path=file_path,
            format=img_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
            created_at=created_at,
            is_master=1 if is_master else 0,
            derived_from=derived_from,
            transform_metadata=transform_metadata,
            thumbnail=thumbnail_bytes,
        )
        return picture_id, iteration

    @staticmethod
    def create_from_file(
        image_root_path: str,
        source_file_path: str,
        picture_id: str,
        derived_from: Optional[str] = None,
        transform_metadata: Optional[str] = None,
        is_master: bool = False,
    ) -> Tuple[str, "PictureIteration"]:
        if not picture_id:
            raise ValueError(
                "picture_uuid must be provided when creating a picture iteration."
            )
        if not os.path.exists(source_file_path):
            raise ValueError(f"Source file path does not exist: {source_file_path}")
        with open(source_file_path, "rb") as f:
            image_bytes = f.read()
        return PictureIteration.create_from_bytes(
            image_root_path=image_root_path,
            image_bytes=image_bytes,
            picture_id=picture_id,
            derived_from=derived_from,
            transform_metadata=transform_metadata,
            is_master=is_master,
        )

    @staticmethod
    def calculate_sha256_from_file_path(file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()

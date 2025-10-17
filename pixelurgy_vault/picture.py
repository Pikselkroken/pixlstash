import hashlib
import logging
import shutil
from typing import Optional, List
import uuid
import numpy as np
import os
from PIL import Image
from .picture_quality import PictureQuality

# Configure logging for the module
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


class Picture:
    """
    Represents a digital picture with typical metadata and AI/Diffusion-friendly thumbnail storage as a NumPy array.
    """

    def __init__(
        self,
        id: str,
        file_path: str,
        character_id: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        format: Optional[str] = None,
        created_at: Optional[str] = None,
        thumbnail: Optional[np.ndarray] = None,
        score: Optional[int] = None,
        quality: Optional[PictureQuality] = None,
    ):
        if not os.path.exists(file_path):
            raise ValueError(f"File path does not exist: {file_path}")

        self.id = id  # Unique ID (SHA-256 hash of file)
        self.file_path = file_path  # Path to image file on disk
        self.character_id = character_id  # Reference to Character
        self.description = description
        self.tags = tags or []
        self.width = width
        self.height = height
        self.format = format
        self.created_at = created_at
        self._thumbnail_array = thumbnail  # NumPy array (H, W, C), dtype=uint8
        self.score = score
        self.quality = quality

    @staticmethod
    def create_picture_from_bytes(
        image_root_path: str,
        image_bytes: bytes,
        character_id: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> "Picture":
        """
        Factory method to create a Picture instance from raw image bytes.
        """
        # Compute SHA256 from raw bytes (single pass)
        id = hashlib.sha256(image_bytes).hexdigest()

        # Determine file extension/format by opening bytes once
        from io import BytesIO

        with Image.open(BytesIO(image_bytes)) as img:
            format = img.format or "PNG"
            width, height = img.size
            thumbnail = Picture.generate_thumbnail(img)

        ext = f".{format.lower()}" if not format.startswith(".") else format
        file_path = os.path.join(image_root_path, f"{id}{ext}")
        if os.path.exists(file_path):
            raise ValueError(f"Image file already exists: {file_path}")

        # Save original bytes to disk (no re-encoding)
        with open(file_path, "wb") as f:
            f.write(image_bytes)

        pic = Picture(
            id=id,
            file_path=file_path,
            character_id=character_id,
            description=description,
            tags=tags,
            width=width,
            height=height,
            format=format,
            thumbnail=thumbnail,
        )
        return pic

    @staticmethod
    def create_picture_from_file(
        image_root_path: str,
        file_path: str,
        character_id: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> "Picture":
        """
        Factory method to create a Picture instance from an image file path.
        """
        if not os.path.exists(file_path):
            raise ValueError(f"Source file path does not exist: {file_path}")

        id = Picture.calculate_sha256_from_file_path(file_path)

        destination_path = os.path.join(
            image_root_path, id + os.path.splitext(file_path)[1]
        )
        if os.path.exists(destination_path):
            raise ValueError(f"Image file already exists: {destination_path}")

        shutil.copy2(file_path, destination_path)
        with Image.open(destination_path) as img:
            width, height = img.size
            format = img.format
            thumbnail = Picture.generate_thumbnail(img)

        pic = Picture(
            id=id,
            file_path=destination_path,
            character_id=character_id,
            description=description,
            tags=tags,
            width=width,
            height=height,
            format=format,
            thumbnail=thumbnail,
        )
        return pic

    @property
    def thumbnail(self) -> Optional[Image.Image]:
        """
        Returns a PIL Image object for the thumbnail, or None if not available.
        """
        return Image.fromarray(self._thumbnail_array)

    @property
    def thumbnail_array(self) -> Optional[np.ndarray]:
        """
        Returns a NumPy array for the thumbnail, or None if not available.
        """
        return self._thumbnail_array

    @property
    def image(self) -> Optional[Image.Image]:
        """
        Returns a PIL Image object for the picture file, or None if not available.
        """
        try:
            return Image.open(self.file_path)
        except Exception:
            return None

    def calculate_quality_metrics(self):
        """
        Calculate and store quality metrics using the PictureQuality class.
        """
        try:
            image = Image.open(self.file_path)
            self.quality = np.array(PictureQuality.calculate_metrics(image))
        except Exception as e:
            logger.error(f"Error calculating quality metrics: {e}")

    @staticmethod
    def generate_thumbnail(image: Image.Image, size=(128, 128)) -> Optional[np.ndarray]:
        """
        Generate and store a thumbnail as a NumPy array.
        """
        try:
            image.thumbnail(size)
            return np.array(image)
        except Exception as e:
            logger.error(f"Error generating thumbnail: {e}")
            return None

    @staticmethod
    def calculate_sha256_from_file_path(file_path: str) -> str:
        """
        Calculate SHA-256 hash of the file for a unique ID.
        """
        import hashlib

        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def calculate_sha256_from_image(image: np.ndarray) -> str:
        """
        Calculate SHA-256 hash of the file for a unique ID.
        """
        import hashlib

        arr_bytes = image.tobytes()
        return hashlib.sha256(arr_bytes).hexdigest()

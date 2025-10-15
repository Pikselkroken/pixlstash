from typing import Optional, List
import uuid
import numpy as np
from PIL import Image
from .picture_quality import PictureQuality


class Picture:
    """
    Represents a digital picture with typical metadata and AI/Diffusion-friendly thumbnail storage as a NumPy array.
    """

    def __init__(
        self,
        file_path: str,
        character_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        format: Optional[str] = None,
        created_at: Optional[str] = None,
        thumbnail: Optional[np.ndarray] = None,
    ):
        self.id = str(uuid.uuid4())  # Unique ID not tied to path
        self.file_path = file_path  # Path to image file on disk
        self.character_id = character_id  # Reference to Character
        self.title = title
        self.description = description
        self.tags = tags or []
        self.width = width
        self.height = height
        self.format = format
        self.created_at = created_at
        self.thumbnail = thumbnail  # NumPy array (H, W, C), dtype=uint8
        self.quality = PictureQuality()

    def get_thumbnail_pil(self) -> Optional[Image.Image]:
        """
        Convert the thumbnail NumPy array to a PIL Image for display.
        """
        if self.thumbnail is not None:
            return Image.fromarray(self.thumbnail)
        return None

    def calculate_quality_metrics(self):
        """
        Calculate and store quality metrics using the PictureQuality class.
        """
        image = Image.open(self.file_path)
        if self.thumbnail is not None:
            self.quality = PictureQuality.calculate_metrics(image.toarray())

"""Stack ordering utilities for picture stacks."""

from typing import List

from pixlstash.db_models import Picture
from pixlstash.utils.image_processing.image_utils import ImageUtils


class StackUtils:
    """Ordering helpers for picture stacks."""

    @staticmethod
    def picture_order_key(pic: Picture, image_root: str = None):
        """Return a sort key for a picture within a likeness stack.

        Ordering priority:
        - Higher resolution (width × height) first
        - Higher sharpness first
        - Lower noise_level first
        """
        if not pic.height or not pic.width:
            file_path = ImageUtils.resolve_picture_path(image_root, pic.file_path)
            pic.width, pic.height, _ = ImageUtils.load_metadata(file_path)
        resolution = (pic.width * pic.height) if pic.width and pic.height else 0

        quality = pic.quality
        sharp = quality.sharpness if quality and quality.sharpness is not None else 0.0
        noise = (
            quality.noise_level if quality and quality.noise_level is not None else 1.0
        )

        return (-resolution, -sharp, noise)

    @staticmethod
    def order_stack_pictures(
        pictures: List[Picture], image_root: str = None
    ) -> List[Picture]:
        """Return pictures sorted best-to-worst by resolution, sharpness, and noise."""
        return sorted(
            pictures, key=lambda pic: StackUtils.picture_order_key(pic, image_root)
        )

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


def _deduplicate_by_stack(pics: list) -> list:
    """Keep first-seen member per stack; unstacked pictures pass through.

    The incoming list is expected to already be ordered by the active sort.
    For stack-collapsed views, the first member encountered for each stack is
    therefore the correct leader to preserve both ordering and displayed values.
    Works on both plain dicts and ORM-style objects with .get() / attribute access.
    """

    def _get(p, key):
        if isinstance(p, dict):
            return p.get(key)
        return getattr(p, key, None)

    # Emit one entry per stack in order of first appearance.
    seen: set = set()
    result = []
    for pic in pics:
        stack_id = _get(pic, "stack_id")
        if not stack_id:
            result.append(pic)
            continue
        sid = int(stack_id)
        if sid in seen:
            continue
        seen.add(sid)
        result.append(pic)
    return result

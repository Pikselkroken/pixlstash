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


def _better_stack_rank(a: dict, b: dict) -> bool:
    """Return True if picture dict 'a' should be the stack leader over 'b'.

    Ranking mirrors JS compareStackOrder in stack.js:
    1. Lowest stack_position (NULLs last)
    2. Highest score
    3. Newest created_at
    4. Lowest id (stable tiebreaker)
    """
    pos_a = a.get("stack_position")
    pos_b = b.get("stack_position")
    pa = 999999 if pos_a is None else int(pos_a)
    pb = 999999 if pos_b is None else int(pos_b)
    if pa != pb:
        return pa < pb
    sa = int(a.get("score") or 0)
    sb = int(b.get("score") or 0)
    if sa != sb:
        return sa > sb
    ca = str(a.get("created_at") or "")
    cb = str(b.get("created_at") or "")
    if ca != cb:
        return ca > cb
    return int(a.get("id") or 0) < int(b.get("id") or 0)


def _deduplicate_by_stack(pics: list) -> list:
    """Keep the best-ranked member per stack; unstacked pictures pass through.

    Preserves the order of first appearance of each stack in the input list.
    Works on both plain dicts and ORM-style objects with .get() / attribute access.
    """

    def _get(p, key):
        if isinstance(p, dict):
            return p.get(key)
        return getattr(p, key, None)

    # First pass: find best representative per stack_id
    best: dict = {}
    for pic in pics:
        stack_id = _get(pic, "stack_id")
        if not stack_id:
            continue
        sid = int(stack_id)
        if sid not in best:
            best[sid] = pic
        else:
            a_dict = (
                pic
                if isinstance(pic, dict)
                else {
                    k: _get(pic, k)
                    for k in ("stack_position", "score", "created_at", "id")
                }
            )
            b_dict = (
                best[sid]
                if isinstance(best[sid], dict)
                else {
                    k: _get(best[sid], k)
                    for k in ("stack_position", "score", "created_at", "id")
                }
            )
            if _better_stack_rank(a_dict, b_dict):
                best[sid] = pic

    # Second pass: emit one entry per stack in order of first appearance
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
        result.append(best[sid])
    return result

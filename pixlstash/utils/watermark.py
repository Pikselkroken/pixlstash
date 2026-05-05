"""Watermark compositing utilities shared by the share and pictures routes."""

import os
from io import BytesIO

from PIL import Image


def get_watermark_bytes(user_watermark_image: bytes | None) -> bytes | None:
    """Return *user_watermark_image* when set, the default Watermark.png otherwise.

    Returns ``None`` when no watermark asset can be found at all.
    """
    if user_watermark_image:
        return user_watermark_image
    return get_default_watermark_bytes()


def get_default_watermark_bytes() -> bytes | None:
    """Return the bytes of the default Watermark.png, searching dev then packaged paths."""
    here = os.path.dirname(__file__)
    candidates = [
        os.path.normpath(
            os.path.join(here, "..", "..", "frontend", "public", "Watermark.png")
        ),
        os.path.normpath(
            os.path.join(here, "..", "..", "frontend", "dist", "Watermark.png")
        ),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
    return None


def apply_watermark(pil_img: Image.Image, watermark_bytes: bytes) -> Image.Image:
    """Composite a watermark onto the bottom-right corner of *pil_img*.

    The watermark is scaled to 25 % of the image width and blended at 80 %
    opacity.  Returns a new RGB image.
    """
    wm = Image.open(BytesIO(watermark_bytes)).convert("RGBA")

    target_w = max(64, pil_img.width // 4)
    scale = target_w / wm.width
    target_h = max(1, int(wm.height * scale))
    wm = wm.resize((target_w, target_h), Image.LANCZOS)

    # Apply 80 % opacity to the watermark alpha channel.
    r, g, b, a = wm.split()
    a = a.point(lambda v: int(v * 0.80))
    wm = Image.merge("RGBA", (r, g, b, a))

    base = pil_img.convert("RGBA")
    padding = 16
    x = max(0, base.width - target_w - padding)
    y = max(0, base.height - target_h - padding)
    base.paste(wm, (x, y), wm)
    return base.convert("RGB")

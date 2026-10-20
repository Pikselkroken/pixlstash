"""Built-in crop plugin."""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixlstash.image_plugins.base import ImagePlugin


class CropPlugin(ImagePlugin):
    """Crop images to a box given as fractions of each picture.

    The Edit tab's crop tool runs this with the frame the owner chose, so a
    crop is the same small JSON as any other plugin run
    (``{"left": 0.1, "top": 0.05, "width": 0.6, "height": 0.8}``) and lands in
    the original's stack. Fractions rather than pixels, so the same box can be
    repeated on a resized copy.
    """

    name = "crop"
    display_name = "Crop"
    description = "Crop pictures to a box given as fractions of each picture."
    author = "Gaute Lindkvist <lindkvis@gmail.com>"
    license = "GPL-3.0-only"
    models = []
    supports_images = True
    supports_videos = False

    def parameter_schema(self) -> list[dict[str, Any]]:
        def fraction(name: str, label: str, default: float, what: str) -> dict:
            return {
                "name": name,
                "label": label,
                "type": "number",
                "default": default,
                "description": f"{what}, as a fraction of the picture (0-1).",
            }

        return [
            fraction("left", "Left", 0.05, "Where the box starts across"),
            fraction("top", "Top", 0.05, "Where the box starts down"),
            fraction("width", "Width", 0.9, "How wide the box is"),
            fraction("height", "Height", 0.9, "How tall the box is"),
        ]

    @staticmethod
    def box(parameters: dict[str, Any] | None, size: tuple[int, int]) -> tuple:
        """``(left, top, right, bottom)`` in pixels, clamped inside *size*."""
        params = parameters or {}
        width, height = size

        def read(name: str, default: float) -> float:
            try:
                return min(max(float(params.get(name, default)), 0.0), 1.0)
            except (TypeError, ValueError):
                return default

        left = int(round(read("left", 0.05) * width))
        top = int(round(read("top", 0.05) * height))
        right = int(round((read("left", 0.05) + read("width", 0.9)) * width))
        bottom = int(round((read("top", 0.05) + read("height", 0.9)) * height))
        right = min(max(right, left + 1), width)
        bottom = min(max(bottom, top + 1), height)
        return min(left, width - 1), min(top, height - 1), right, bottom

    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
        captions: list[str] | None = None,
    ) -> list[Image.Image]:
        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                out.append(image.crop(self.box(parameters, image.size)))
                self.report_progress(
                    progress_callback,
                    current=idx + 1,
                    total=total,
                    message=f"Cropped image {idx + 1}/{total}",
                )
            except Exception as exc:
                self.report_error(
                    error_callback,
                    index=idx,
                    message="Failed to crop image",
                    details={"error": str(exc)},
                )
                out.append(image.copy())
        return out

"""Built-in rotate plugin."""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixlstash.image_plugins.base import ImagePlugin


class RotatePlugin(ImagePlugin):
    """Rotate images or videos by 90° left, 90° right or 180°."""

    name = "rotate"
    display_name = "Rotate"
    description = "Rotate images or videos by 90° left, 90° right, or 180°."
    supports_images = True
    supports_videos = True

    MODES = {
        "90_left": "90° Left (counter-clockwise)",
        "90_right": "90° Right (clockwise)",
        "180": "180°",
    }

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "direction",
                "label": "Rotation",
                "type": "string",
                "default": "90_right",
                "enum": list(self.MODES.keys()),
                "enumLabels": self.MODES,
                "description": "Direction and amount of rotation.",
            }
        ]

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    def get_bbox_transform(self, parameters, source_size, output_size):
        """Transform bounding boxes according to the rotation direction.

        All four corners of the source bbox are rotated as points, then the
        axis-aligned bounding box of the rotated corners is returned.
        """
        params = parameters or {}
        direction = str(params.get("direction") or "90_right").strip().lower()
        if direction not in self.MODES:
            direction = "90_right"

        src_w, src_h = source_size

        def transform(bbox: list[int]) -> list[int]:
            x1, y1, x2, y2 = bbox
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            if direction == "90_left":  # CCW: (x, y) -> (y, src_w - x)
                rotated = [(y, src_w - x) for x, y in corners]
            elif direction == "90_right":  # CW: (x, y) -> (src_h - y, x)
                rotated = [(src_h - y, x) for x, y in corners]
            else:  # 180°: (x, y) -> (src_w - x, src_h - y)
                rotated = [(src_w - x, src_h - y) for x, y in corners]
            xs = [p[0] for p in rotated]
            ys = [p[1] for p in rotated]
            return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]

        return transform

    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
        captions: list[str] | None = None,
    ) -> list[Image.Image]:
        params = parameters or {}
        direction = str(params.get("direction") or "90_right").strip().lower()
        if direction not in self.MODES:
            direction = "90_right"

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                rotated = self._rotate_image(image, direction)
                out.append(rotated)
                self.report_progress(
                    progress_callback,
                    current=idx + 1,
                    total=total,
                    message=f"Rotated image {idx + 1}/{total}",
                )
            except Exception as exc:
                self.report_error(
                    error_callback,
                    index=idx,
                    message="Failed to rotate image",
                    details={"error": str(exc)},
                )
                out.append(image.copy())
        return out

    # ------------------------------------------------------------------
    # Videos
    # ------------------------------------------------------------------

    def run_video(
        self,
        source_path: str,
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
    ) -> tuple[bytes, str]:
        params = parameters or {}
        direction = str(params.get("direction") or "90_right").strip().lower()
        if direction not in self.MODES:
            direction = "90_right"

        return self.transform_video(
            source_path,
            lambda image: self._rotate_image(image, direction),
            progress_callback=progress_callback,
            error_callback=error_callback,
            error_message="Failed to rotate video",
            progress_verb="Rotated",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rotate_image(image: Image.Image, direction: str) -> Image.Image:
        rgb = image.convert("RGB")
        if direction == "90_left":
            # ROTATE_90 in PIL is counter-clockwise.
            return rgb.transpose(Image.Transpose.ROTATE_90)
        if direction == "90_right":
            # ROTATE_270 in PIL is clockwise.
            return rgb.transpose(Image.Transpose.ROTATE_270)
        # 180°
        return rgb.transpose(Image.Transpose.ROTATE_180)

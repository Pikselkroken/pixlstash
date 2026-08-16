"""Built-in brightness/contrast plugin."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageEnhance

from pixlstash.image_plugins.base import ImagePlugin


class BrightnessContrastPlugin(ImagePlugin):
    name = "brightness_contrast"
    display_name = "Brightness / Contrast"
    description = "Adjust brightness and contrast for images or videos."
    author = "Gaute Lindkvist <lindkvis@gmail.com>"
    license = "GPL-3.0-only"
    models = []
    supports_images = True
    supports_videos = True

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "brightness",
                "label": "Brightness",
                "type": "number",
                "default": 1.0,
                "description": "Brightness multiplier (1.0 = no change).",
            },
            {
                "name": "contrast",
                "label": "Contrast",
                "type": "number",
                "default": 1.0,
                "description": "Contrast multiplier (1.0 = no change).",
            },
        ]

    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
        captions: list[str] | None = None,
    ) -> list[Image.Image]:
        params = parameters or {}
        brightness = self._coerce_positive_number(params.get("brightness"), 1.0)
        contrast = self._coerce_positive_number(params.get("contrast"), 1.0)

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                filtered = self._apply_adjustments(image, brightness, contrast)
                out.append(filtered)
                self.report_progress(
                    progress_callback,
                    current=idx + 1,
                    total=total,
                    message=f"Processed image {idx + 1}/{total}",
                )
            except Exception as exc:
                self.report_error(
                    error_callback,
                    index=idx,
                    message="Failed to apply brightness/contrast",
                    details={"error": str(exc)},
                )
                out.append(image.copy())
        return out

    def run_video(
        self,
        source_path: str,
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
    ) -> tuple[bytes, str]:
        params = parameters or {}
        brightness = self._coerce_positive_number(params.get("brightness"), 1.0)
        contrast = self._coerce_positive_number(params.get("contrast"), 1.0)

        return self.transform_video(
            source_path,
            lambda image: self._apply_adjustments(image, brightness, contrast),
            progress_callback=progress_callback,
            error_callback=error_callback,
            error_message="Failed to apply brightness/contrast to video",
        )

    @staticmethod
    def _apply_adjustments(
        image: Image.Image,
        brightness: float,
        contrast: float,
    ) -> Image.Image:
        rgb = image.convert("RGB")
        bright = ImageEnhance.Brightness(rgb).enhance(brightness)
        return ImageEnhance.Contrast(bright).enhance(contrast)

"""Built-in image scaling plugin."""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixlvault.image_plugins.base import ImagePlugin


if hasattr(Image, "Resampling"):
    _RESAMPLING = Image.Resampling
else:
    _RESAMPLING = Image


class ScalingPlugin(ImagePlugin):
    name = "scaling"
    display_name = "Scaling"
    description = "Scale images up or down using common interpolation algorithms."
    supports_images = True
    supports_videos = False

    SCALE_FACTORS = ["0.25", "0.5", "1.25", "1.5", "2.0", "4.0"]
    RESAMPLERS = {
        "nearest_neighbour": _RESAMPLING.NEAREST,
        "bilinear": _RESAMPLING.BILINEAR,
        "bicubic": _RESAMPLING.BICUBIC,
        "lanczos": _RESAMPLING.LANCZOS,
        "box": _RESAMPLING.BOX,
        "hamming": _RESAMPLING.HAMMING,
    }

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "algorithm",
                "label": "Algorithm",
                "type": "string",
                "default": "lanczos",
                "enum": sorted(self.RESAMPLERS.keys()),
                "description": "Interpolation algorithm used during scaling.",
            },
            {
                "name": "scale_factor",
                "label": "Scale Factor",
                "type": "string",
                "default": "2.0",
                "enum": self.SCALE_FACTORS,
                "description": "Target scale multiplier.",
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
        algorithm = str(params.get("algorithm") or "lanczos").strip().lower()
        scale_factor_raw = str(params.get("scale_factor") or "2.0").strip()

        if algorithm not in self.RESAMPLERS:
            algorithm = "lanczos"

        if scale_factor_raw not in self.SCALE_FACTORS:
            scale_factor_raw = "2.0"

        resampler = self.RESAMPLERS[algorithm]
        scale_factor = float(scale_factor_raw)

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                width = max(1, int(round(image.width * scale_factor)))
                height = max(1, int(round(image.height * scale_factor)))
                out.append(image.resize((width, height), resample=resampler))
                self.report_progress(
                    progress_callback,
                    current=idx + 1,
                    total=total,
                    message=f"Scaled image {idx + 1}/{total}",
                )
            except Exception as exc:
                self.report_error(
                    error_callback,
                    index=idx,
                    message="Failed to scale image",
                    details={"error": str(exc)},
                )
                out.append(image.copy())
        return out

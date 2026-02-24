"""Built-in colour filter plugin.

How to create your own plugin from this skeleton:
1. Copy this file into `image-plugins/user/` and rename it, for example
   `my_plugin.py`.
2. Rename the class and set a unique `name` (plugin id), plus
   `display_name`/`description`.
3. Define your UI parameters in `parameter_schema()`.
4. Implement image processing in `run()` and return one output image for each
   input image, in the same order.
5. Use `self.report_progress(...)` while processing and
   `self.report_error(...)` if a single image fails.

Minimal skeleton:

    from typing import Any
    from PIL import Image
    from pixlvault.image_plugins.base import ImagePlugin

    class MyPlugin(ImagePlugin):
        name = "my_plugin"
        display_name = "My Plugin"
        description = "Describe what this plugin does."

        def parameter_schema(self) -> list[dict[str, Any]]:
            return [
                {
                    "name": "strength",
                    "type": "number",
                    "default": 1.0,
                }
            ]

        def run(self, images, parameters=None, progress_callback=None, error_callback=None):
            out = []
            total = len(images)
            for idx, image in enumerate(images):
                try:
                    # transform image here
                    out.append(image.copy())
                    self.report_progress(progress_callback, current=idx + 1, total=total, message="Processed")
                except Exception as exc:
                    self.report_error(error_callback, index=idx, message="Failed", details={"error": str(exc)})
                    out.append(image.copy())
            return out
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageEnhance, ImageOps

from pixlvault.image_plugins.base import ImagePlugin


class ColourFilterPlugin(ImagePlugin):
    name = "colour_filter"
    display_name = "Colour Filter"
    description = "Apply black & white, sepia, cool, warm, or vivid colour filters."

    FILTER_MODES = {
        "black_and_white",
        "sepia",
        "cool",
        "warm",
        "vivid",
    }

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "mode",
                "label": "Filter",
                "type": "string",
                "default": "black_and_white",
                "enum": sorted(self.FILTER_MODES),
                "description": "Which colour transform to apply.",
            }
        ]

    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
    ) -> list[Image.Image]:
        params = parameters or {}
        mode = str(params.get("mode") or "black_and_white").strip().lower()
        if mode not in self.FILTER_MODES:
            mode = "black_and_white"

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                rgb = image.convert("RGB")
                if mode == "black_and_white":
                    filtered = ImageOps.grayscale(rgb).convert("RGB")
                elif mode == "sepia":
                    filtered = ImageOps.colorize(
                        ImageOps.grayscale(rgb),
                        black="#2E1B0F",
                        white="#F2D8B5",
                    )
                elif mode == "cool":
                    r, g, b = rgb.split()
                    filtered = Image.merge(
                        "RGB",
                        (
                            r.point(lambda value: int(value * 0.9)),
                            g.point(lambda value: int(value * 1.0)),
                            b.point(lambda value: int(value * 1.1)),
                        ),
                    )
                elif mode == "warm":
                    r, g, b = rgb.split()
                    filtered = Image.merge(
                        "RGB",
                        (
                            r.point(lambda value: int(value * 1.1)),
                            g.point(lambda value: int(value * 1.0)),
                            b.point(lambda value: int(value * 0.9)),
                        ),
                    )
                elif mode == "vivid":
                    filtered = ImageEnhance.Color(rgb).enhance(1.35)
                else:
                    filtered = ImageOps.grayscale(rgb).convert("RGB")
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
                    message="Failed to apply colour filter",
                    details={"error": str(exc)},
                )
                out.append(image.copy())
        return out

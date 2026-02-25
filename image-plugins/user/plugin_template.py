"""PixlVault user plugin template.

How to use:
1. Copy this file to a new file in this folder, for example `my_plugin.py`.
2. Rename the class and set a unique `name`.
3. Define parameters in `parameter_schema()`.
4. Implement your transform in `run()`.
5. Keep the output list length equal to the input list length.

Notes:
- This template file is ignored by plugin discovery on purpose.
- New plugin files in this folder are auto-discovered.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixlvault.image_plugins.base import ImagePlugin


class MyPlugin(ImagePlugin):
    name = "my_plugin"
    display_name = "My Plugin"
    description = "Describe what this plugin does."
    supports_images = True
    supports_videos = False

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "strength",
                "label": "Strength",
                "type": "number",
                "default": 1.0,
                "description": "Example numeric parameter.",
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
        strength = float(params.get("strength", 1.0))

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                _ = strength
                transformed = image.copy()
                out.append(transformed)
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
                    message="Failed to process image",
                    details={"error": str(exc)},
                )
                out.append(image.copy())

        return out

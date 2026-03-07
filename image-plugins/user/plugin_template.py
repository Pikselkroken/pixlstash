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
    """Template plugin — rename this class and customise before use.

    Steps to create a real plugin from this template:

    1. Rename the class and set a unique ``name`` (snake_case).
    2. Update ``display_name`` and ``description``.
    3. Declare parameters in ``parameter_schema``.
    4. Implement your image transform in ``run``.
    5. Optionally implement ``run_video`` if ``supports_videos = True``.

    The output list from ``run`` must always be the same length as the
    input list. On per-image failures, fall back to a copy of the original
    and call ``self.report_error`` rather than raising.
    """

    name = "my_plugin"
    display_name = "My Plugin"
    description = "Describe what this plugin does."
    supports_images = True
    supports_videos = False

    def parameter_schema(self) -> list[dict[str, Any]]:
        """Return the parameter definitions exposed to the UI.

        Each dict must include ``name``, ``label``, ``type``, and ``default``.
        Supported types: ``"number"``, ``"string"``, ``"boolean"``, ``"select"``
        (requires an ``"options"`` list). Add or remove entries to match your
        plugin's needs.

        Returns:
            List of parameter definition dicts.
        """
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
        """Apply the plugin transform to each image in the batch.

        Replace the body of this method with your own image processing logic.
        Call ``self.report_progress`` after each image and
        ``self.report_error`` (with a fallback output) on failure.

        Args:
            images: Input images to process.
            parameters: Runtime parameter values from the UI.
            progress_callback: Optional progress reporting callback.
            error_callback: Optional error reporting callback.

        Returns:
            Transformed images, same length and order as ``images``.
        """
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

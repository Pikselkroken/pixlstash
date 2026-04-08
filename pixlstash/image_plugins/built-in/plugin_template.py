# Copyright 2026 Gaute Lindkvist
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Plugin template — copy this file to your user plugin directory to get started.

Licensing note:
This template and the plugin API base class are MIT-licensed so external/user
plugins can be authored under any license.

This file is excluded from plugin discovery and will never be loaded as-is.

Quick-start
-----------
1. Copy this file to your user plugin directory (PixlStash logs the path on startup).
2. Rename it (anything except ``plugin_template.py``).
3. Fill in your plugin name, display name, parameters, and ``run()`` logic.
4. Restart PixlStash Server — your plugin will appear in the Filters menu.

User plugin directories
-----------------------
  Linux   : ~/.local/share/pixlstash/image-plugins/user/
  macOS   : ~/Library/Application Support/pixlstash/image-plugins/user/
  Windows : %LOCALAPPDATA%\\pixlstash\\image-plugins\\user\\
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixlstash.image_plugins.base import ImagePlugin


class MyPlugin(ImagePlugin):
    """One-line description of what this plugin does."""

    # Unique snake_case identifier — must be distinct from all other plugins.
    name = "my_plugin"

    # Label shown in the Filters dropdown.
    display_name = "My Plugin"

    # Short description shown in the UI (optional).
    description = "Describe what your plugin does."

    # Set to True if this plugin can process still images (almost always True).
    supports_images = True

    # Set to True and implement run_video() if this plugin can process video files.
    supports_videos = False

    def parameter_schema(self) -> list[dict[str, Any]]:
        """Declare the parameters your plugin exposes in the UI.

        Supported types: "number", "string", "boolean", "select".
        For "select" type, include an "options" key listing the allowed values.
        """
        return [
            {
                "name": "strength",
                "label": "Strength",
                "type": "number",
                "default": 1.0,
                "description": "Effect strength (higher = stronger).",
            },
            # Add more parameters as needed, for example:
            # {
            #     "name": "mode",
            #     "label": "Mode",
            #     "type": "select",
            #     "default": "option_a",
            #     "options": ["option_a", "option_b"],
            #     "description": "Which mode to use.",
            # },
        ]

    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
        captions: list[str] | None = None,
    ) -> list[Image.Image]:
        """Transform a batch of images and return them in the same order.

        Args:
            images: Input PIL images.
            parameters: Values from the UI keyed by the ``name`` in
                ``parameter_schema``. Fall back to defaults for missing keys.
            progress_callback: Call ``self.report_progress(...)`` after each
                image to update the progress bar.
            error_callback: Call ``self.report_error(...)`` on per-image
                failures instead of raising, so the rest of the batch continues.
            captions: Per-image caption strings (one per image, same order).
                Each entry is the picture's stored description, or ``""`` if
                none. Use these to drive caption-conditioned transforms.
        """
        params = parameters or {}
        strength = float(params.get("strength") or 1.0)

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            caption = (captions[idx] if captions else None) or ""
            try:
                print(
                    "Processing image with strength =",
                    strength,
                    "and caption =",
                    repr(caption),
                )
                # ----------------------------------------------------------------
                # YOUR TRANSFORM GOES HERE
                # ----------------------------------------------------------------
                # Example: return the image unchanged.
                result = image.copy()
                # ----------------------------------------------------------------

                out.append(result)
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
                out.append(image.copy())  # fall back to original on failure
        return out

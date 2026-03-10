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

import os
import tempfile
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from pixlvault.image_plugins.base import ImagePlugin


class ColourFilterPlugin(ImagePlugin):
    name = "colour_filter"
    display_name = "Colour Filter"
    description = "Apply black & white, sepia, cool, warm, or vivid colour filters."
    supports_images = True
    supports_videos = True

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
        captions: list[str] | None = None,
    ) -> list[Image.Image]:
        params = parameters or {}
        mode = str(params.get("mode") or "black_and_white").strip().lower()
        if mode not in self.FILTER_MODES:
            mode = "black_and_white"

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                filtered = self._apply_mode(image, mode)
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

    def run_video(
        self,
        source_path: str,
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
    ) -> tuple[bytes, str]:
        params = parameters or {}
        mode = str(params.get("mode") or "black_and_white").strip().lower()
        if mode not in self.FILTER_MODES:
            mode = "black_and_white"

        cap = cv2.VideoCapture(source_path)
        if not cap.isOpened():
            raise ValueError(f"Failed to open video file: {source_path}")

        frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0:
            fps = 24.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            cap.release()
            raise ValueError(f"Invalid video dimensions for {source_path}")

        temp_path = ""
        writer = None
        output_ext = ".mp4"
        processed = 0
        try:
            source_ext = os.path.splitext(source_path)[1].lower()
            writer_candidates = [
                (".mp4", "avc1"),
                (".mp4", "H264"),
                (".webm", "VP80"),
                (".webm", "VP90"),
                (".mp4", "mp4v"),
            ]
            if source_ext == ".webm":
                writer_candidates = [
                    (".webm", "VP80"),
                    (".webm", "VP90"),
                    (".mp4", "avc1"),
                    (".mp4", "H264"),
                    (".mp4", "mp4v"),
                ]

            for candidate_ext, codec in writer_candidates:
                with tempfile.NamedTemporaryFile(
                    suffix=candidate_ext,
                    delete=False,
                ) as temp_file:
                    temp_path = temp_file.name
                candidate_writer = cv2.VideoWriter(
                    temp_path,
                    cv2.VideoWriter_fourcc(*codec),
                    fps,
                    (width, height),
                )
                if candidate_writer.isOpened():
                    writer = candidate_writer
                    output_ext = candidate_ext
                    break
                candidate_writer.release()
                try:
                    os.remove(temp_path)
                except OSError:
                    # Best-effort cleanup: continue if temp file deletion fails.
                    pass
                temp_path = ""

            if writer is None:
                raise ValueError("Failed to open output video writer")

            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                filtered = self._apply_mode(Image.fromarray(rgb_frame), mode)
                filtered_bgr = cv2.cvtColor(
                    np.array(filtered.convert("RGB")),
                    cv2.COLOR_RGB2BGR,
                )
                writer.write(filtered_bgr)

                processed += 1
                self.report_progress(
                    progress_callback,
                    current=processed,
                    total=frame_total if frame_total > 0 else processed,
                    message=f"Processed video frame {processed}",
                )

            if processed == 0:
                raise ValueError("No frames processed from video")

            writer.release()
            writer = None
            cap.release()

            with open(temp_path, "rb") as handle:
                return handle.read(), output_ext
        except Exception as exc:
            self.report_error(
                error_callback,
                index=0,
                message="Failed to apply colour filter to video",
                details={"error": str(exc), "source_path": source_path},
            )
            raise
        finally:
            if writer is not None:
                writer.release()
            cap.release()
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _apply_mode(self, image: Image.Image, mode: str) -> Image.Image:
        rgb = image.convert("RGB")
        if mode == "black_and_white":
            return ImageOps.grayscale(rgb).convert("RGB")
        if mode == "sepia":
            return ImageOps.colorize(
                ImageOps.grayscale(rgb),
                black="#2E1B0F",
                white="#F2D8B5",
            )
        if mode == "cool":
            r, g, b = rgb.split()
            return Image.merge(
                "RGB",
                (
                    r.point(lambda value: int(value * 0.9)),
                    g.point(lambda value: int(value * 1.0)),
                    b.point(lambda value: int(value * 1.1)),
                ),
            )
        if mode == "warm":
            r, g, b = rgb.split()
            return Image.merge(
                "RGB",
                (
                    r.point(lambda value: int(value * 1.1)),
                    g.point(lambda value: int(value * 1.0)),
                    b.point(lambda value: int(value * 0.9)),
                ),
            )
        if mode == "vivid":
            return ImageEnhance.Color(rgb).enhance(1.35)
        return ImageOps.grayscale(rgb).convert("RGB")

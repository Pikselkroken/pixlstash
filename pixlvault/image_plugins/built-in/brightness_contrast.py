"""Built-in brightness/contrast plugin."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance

from pixlvault.image_plugins.base import ImagePlugin


class BrightnessContrastPlugin(ImagePlugin):
    name = "brightness_contrast"
    display_name = "Brightness / Contrast"
    description = "Adjust brightness and contrast for images or videos."
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
                filtered = self._apply_adjustments(
                    Image.fromarray(rgb_frame),
                    brightness,
                    contrast,
                )
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
                message="Failed to apply brightness/contrast to video",
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

    @staticmethod
    def _coerce_positive_number(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed <= 0:
            return default
        return parsed

    @staticmethod
    def _apply_adjustments(
        image: Image.Image,
        brightness: float,
        contrast: float,
    ) -> Image.Image:
        rgb = image.convert("RGB")
        bright = ImageEnhance.Brightness(rgb).enhance(brightness)
        return ImageEnhance.Contrast(bright).enhance(contrast)

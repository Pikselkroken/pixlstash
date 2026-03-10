"""Built-in blur/sharpen plugin."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from pixlvault.image_plugins.base import ImagePlugin


class BlurSharpenPlugin(ImagePlugin):
    name = "blur_sharpen"
    display_name = "Blur / Sharpen"
    description = "Apply blur or sharpen effect to images or videos."
    supports_images = True
    supports_videos = True

    MODES = {"blur", "sharpen"}

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "mode",
                "label": "Mode",
                "type": "string",
                "default": "blur",
                "enum": sorted(self.MODES),
                "description": "Choose whether to blur or sharpen.",
            },
            {
                "name": "strength",
                "label": "Strength",
                "type": "number",
                "default": 1.0,
                "description": "Effect strength (higher means stronger).",
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
        mode = str(params.get("mode") or "blur").strip().lower()
        if mode not in self.MODES:
            mode = "blur"
        strength = self._coerce_positive_number(params.get("strength"), 1.0)

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                filtered = self._apply_mode(image, mode, strength)
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
                    message="Failed to apply blur/sharpen",
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
        mode = str(params.get("mode") or "blur").strip().lower()
        if mode not in self.MODES:
            mode = "blur"
        strength = self._coerce_positive_number(params.get("strength"), 1.0)

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
                    pass
                temp_path = ""

            if writer is None:
                raise ValueError("Failed to open output video writer")

            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                filtered = self._apply_mode(Image.fromarray(rgb_frame), mode, strength)
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
                message="Failed to apply blur/sharpen to video",
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
    def _apply_mode(image: Image.Image, mode: str, strength: float) -> Image.Image:
        rgb = image.convert("RGB")
        if mode == "sharpen":
            factor = 1.0 + (strength * 1.5)
            return ImageEnhance.Sharpness(rgb).enhance(factor)
        radius = max(0.1, strength * 1.2)
        return rgb.filter(ImageFilter.GaussianBlur(radius=radius))

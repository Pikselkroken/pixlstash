"""Built-in blur/sharpen plugin."""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from pixlstash.image_plugins.base import ImagePlugin


class BlurSharpenPlugin(ImagePlugin):
    name = "blur_sharpen"
    display_name = "Blur / Sharpen"
    description = "Apply blur or sharpen effect to images or videos."
    supports_images = True
    supports_videos = True

    MODES = {"blur", "sharpen", "motion_blur", "camera_shake"}

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "mode",
                "label": "Mode",
                "type": "string",
                "default": "blur",
                "enum": sorted(self.MODES),
                "description": (
                    "blur - Gaussian blur. "
                    "sharpen - Unsharp sharpen. "
                    "motion_blur - Linear directional smear (use 'angle' to set direction). "
                    "camera_shake - Curved arc blur that mimics hand-held camera shake."
                ),
            },
            {
                "name": "strength",
                "label": "Strength",
                "type": "number",
                "default": 1.0,
                "description": "Effect strength (higher means stronger).",
            },
            {
                "name": "angle",
                "label": "Angle (degrees)",
                "type": "number",
                "default": 0.0,
                "description": (
                    "Direction of motion for motion_blur (0 = horizontal right). "
                    "Ignored by other modes."
                ),
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
        angle = self._coerce_number(params.get("angle"), 0.0)

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                filtered = self._apply_mode(image, mode, strength, angle)
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
        angle = self._coerce_number(params.get("angle"), 0.0)

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
                with contextlib.suppress(OSError):
                    os.remove(temp_path)
                temp_path = ""

            if writer is None:
                raise ValueError("Failed to open output video writer")

            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                filtered = self._apply_mode(
                    Image.fromarray(rgb_frame), mode, strength, angle
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
                message="Failed to apply blur/sharpen to video",
                details={"error": str(exc), "source_path": source_path},
            )
            raise
        finally:
            if writer is not None:
                writer.release()
            cap.release()
            if temp_path and os.path.exists(temp_path):
                with contextlib.suppress(OSError):
                    os.remove(temp_path)

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
    def _coerce_number(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _motion_blur_kernel(length: int, angle_deg: float) -> np.ndarray:
        """Build a linear motion-blur kernel of the given length and angle.

        The kernel is a line of ones rotated to *angle_deg* (0 = horizontal).
        """
        length = max(3, length | 1)  # must be odd and ≥ 3
        kernel = np.zeros((length, length), dtype=np.float32)
        kernel[length // 2, :] = 1.0
        kernel /= kernel.sum()
        cx = cy = length / 2.0
        M = cv2.getRotationMatrix2D((cx, cy), angle_deg, 1.0)
        return cv2.warpAffine(kernel, M, (length, length))

    @staticmethod
    def _camera_shake_kernel(size: int, arc_fraction: float = 0.25) -> np.ndarray:
        """Build an arc-shaped camera-shake blur kernel.

        Simulates a slight rotational camera movement during exposure: the
        kernel traces a short arc around the image centre, giving a curved
        smear rather than a straight directional smear.

        Args:
            size: Kernel grid side length (must be odd, ≥ 3).
            arc_fraction: Fraction of a full 360° covered by the arc (0–1).
                          0.25 means 90°; smaller values = tighter shake.
        """
        size = max(3, size | 1)
        kernel = np.zeros((size, size), dtype=np.float32)
        cx = cy = (size - 1) / 2.0
        radius = cx * 0.85
        n_points = max(64, size * 4)
        arc_deg = 360.0 * max(0.01, min(1.0, arc_fraction))
        start_deg = -arc_deg / 2.0
        for i in range(n_points):
            angle_rad = np.deg2rad(start_deg + arc_deg * i / (n_points - 1))
            x = cx + radius * np.cos(angle_rad)
            y = cy + radius * np.sin(angle_rad)
            xi, yi = int(round(x)), int(round(y))
            if 0 <= xi < size and 0 <= yi < size:
                kernel[yi, xi] += 1.0
        total = kernel.sum()
        if total == 0:
            kernel[size // 2, size // 2] = 1.0
        else:
            kernel /= total
        return kernel

    @classmethod
    def _apply_mode(
        cls, image: Image.Image, mode: str, strength: float, angle: float = 0.0
    ) -> Image.Image:
        rgb = image.convert("RGB")
        if mode == "sharpen":
            factor = 1.0 + (strength * 1.5)
            return ImageEnhance.Sharpness(rgb).enhance(factor)
        if mode == "motion_blur":
            length = max(3, int(strength * 20))
            kernel = cls._motion_blur_kernel(length, angle)
            arr = np.array(rgb, dtype=np.float32)
            blurred = cv2.filter2D(arr, -1, kernel)
            return Image.fromarray(np.clip(blurred, 0, 255).astype(np.uint8))
        if mode == "camera_shake":
            # Size grows with strength; arc fraction stays small (realistic shake).
            size = max(3, int(strength * 30)) | 1
            arc_fraction = min(0.5, 0.08 + strength * 0.06)
            kernel = cls._camera_shake_kernel(size, arc_fraction)
            arr = np.array(rgb, dtype=np.float32)
            blurred = cv2.filter2D(arr, -1, kernel)
            return Image.fromarray(np.clip(blurred, 0, 255).astype(np.uint8))
        # default: Gaussian blur
        radius = max(0.1, strength * 1.2)
        return rgb.filter(ImageFilter.GaussianBlur(radius=radius))

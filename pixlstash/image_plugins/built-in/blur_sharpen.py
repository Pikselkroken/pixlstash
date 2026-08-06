"""Built-in blur/sharpen plugin."""

from __future__ import annotations

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

        return self.transform_video(
            source_path,
            lambda image: self._apply_mode(image, mode, strength, angle),
            progress_callback=progress_callback,
            error_callback=error_callback,
            error_message="Failed to apply blur/sharpen to video",
        )

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

"""Built-in subtle pixelation plugin."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PIL import Image

from pixlstash.image_plugins.base import ImagePlugin


class PixelatePlugin(ImagePlugin):
    """Simulate the aliasing artefacts produced by under-sampled rendering.

    Pipeline (applied only to high-texture regions such as hair):
    1. Nearest-neighbour downsample → randomly replace a fraction of pixels
       with a neighbour value → Lanczos upsample.  Each swapped pixel at the
       small scale becomes a smeared staircase streak at full size, mimicking
       the characteristic jagged edges and stray-colour specks produced by a
       low-step diffusion sampler or poor anti-aliasing.
    2. Local-variance map gates the effect: smooth areas (skin, plain
       backgrounds) keep the original; only fibrous / detailed regions get the
       aliased reconstruction.
    3. A light unsharp-mask pass restores perceived sharpness lost during the
       Lanczos interpolation.
    """

    name = "pixelate"
    display_name = "Pixelate"
    description = (
        "Simulate aliasing / under-sampling artefacts (staircase edges, stray "
        "colour pixels) in textured regions such as hair, while leaving smooth "
        "areas such as skin and plain backgrounds intact."
    )
    supports_images = True
    supports_videos = False

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "block_size",
                "label": "Block size",
                "type": "number",
                "default": 4,
                "description": (
                    "Downscale factor before Lanczos reconstruction (2-16). "
                    "Larger values produce coarser aliasing; 3-6 is a good range "
                    "for a realistic under-sampled look."
                ),
            },
            {
                "name": "error_rate",
                "label": "Error rate",
                "type": "number",
                "default": 0.08,
                "description": (
                    "Fraction of pixels at the downscaled resolution that are "
                    "randomly swapped with a direct neighbour (0.0-0.5). "
                    "0.05-0.15 gives scattered staircase specks; higher values "
                    "produce more widespread colour errors."
                ),
            },
            {
                "name": "strength",
                "label": "Strength",
                "type": "number",
                "default": 0.85,
                "description": (
                    "Blend strength in high-texture regions "
                    "(0.0 = no effect, 1.0 = fully aliased in textured areas). "
                    "0.7-0.95 gives a pronounced but realistic look."
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
        block_size = max(
            2, min(16, int(self._coerce_positive_number(params.get("block_size"), 4.0)))
        )
        error_rate = max(
            0.0, min(0.5, self._coerce_positive_number(params.get("error_rate"), 0.08))
        )
        strength = max(
            0.0, min(1.0, self._coerce_positive_number(params.get("strength"), 0.85))
        )

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                result = self._pixelate(image, block_size, error_rate, strength)
                out.append(result)
                self.report_progress(
                    progress_callback,
                    current=idx + 1,
                    total=total,
                    message=f"Processed image {idx + 1}/{total}",
                )
            except Exception as exc:  # noqa: BLE001
                self.report_error(
                    error_callback,
                    index=idx,
                    message="Failed to apply pixelation",
                    details={"error": str(exc)},
                )
                out.append(image.copy())
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pixelate(
        image: Image.Image, block_size: int, error_rate: float, strength: float
    ) -> Image.Image:
        """Simulate aliasing artefacts in high-texture regions.

        Steps:
        1. Nearest-neighbour downsample the image by `block_size`.
        2. Randomly replace `error_rate` fraction of pixels at that small
           scale with a direct neighbour; this injects the stray-colour errors
           that become visible staircase smears after upsampling.
        3. Lanczos upsample back to original size; each swapped pixel spreads
           into a small directional streak replicating badly anti-aliased edges.
        4. Compute a local-variance map; blend the aliased reconstruction into
           the original only where variance is high (hair, fine detail).
        5. Apply a light unsharp mask to restore sharpness.
        """
        orig_mode = image.mode
        rgb = image.convert("RGB")
        orig_arr = np.array(rgb, dtype=np.float32)
        h, w = orig_arr.shape[:2]
        orig_u8 = orig_arr.clip(0, 255).astype(np.uint8)

        # --- 1. Nearest-neighbour downsample ---------------------------------
        small_w = max(1, w // block_size)
        small_h = max(1, h // block_size)
        small = cv2.resize(orig_u8, (small_w, small_h), interpolation=cv2.INTER_NEAREST)

        # --- 2. Inject random pixel errors -----------------------------------
        if error_rate > 0.0:
            rng = np.random.default_rng()
            error_mask = rng.random((small_h, small_w)) < error_rate
            # Swap each flagged pixel with one of its ±1 neighbours.
            dy = rng.integers(-1, 2, size=(small_h, small_w))
            dx = rng.integers(-1, 2, size=(small_h, small_w))
            src_y = np.clip(
                np.arange(small_h)[:, np.newaxis] + dy, 0, small_h - 1
            )
            src_x = np.clip(
                np.arange(small_w)[np.newaxis, :] + dx, 0, small_w - 1
            )
            small_err = small.copy()
            small_err[error_mask] = small[src_y[error_mask], src_x[error_mask]]
            small = small_err

        # --- 3. Lanczos upsample ---------------------------------------------
        upsampled = np.array(
            Image.fromarray(small, "RGB").resize((w, h), Image.LANCZOS),
            dtype=np.float32,
        )

        # --- 4. Local-variance texture mask ----------------------------------
        gray = cv2.cvtColor(orig_u8, cv2.COLOR_RGB2GRAY).astype(np.float32)
        ksize = max(3, (block_size * 2) | 1)
        mean_local = cv2.boxFilter(gray, cv2.CV_32F, (ksize, ksize))
        mean_sq = cv2.boxFilter(gray * gray, cv2.CV_32F, (ksize, ksize))
        variance = np.maximum(0.0, mean_sq - mean_local**2)
        p85 = float(np.percentile(variance, 85))
        texture_mask = (
            np.clip(variance / p85, 0.0, 1.0) if p85 > 0 else np.zeros_like(variance)
        )

        blend_w = np.clip(texture_mask * strength, 0.0, 1.0)[:, :, np.newaxis]
        result_arr = np.clip(
            orig_arr * (1.0 - blend_w) + upsampled * blend_w, 0, 255
        ).astype(np.uint8)

        # --- 5. Light unsharp mask -------------------------------------------
        blurred = cv2.GaussianBlur(result_arr, (0, 0), sigmaX=0.8)
        result_arr = np.clip(
            result_arr.astype(np.float32)
            + 0.5 * (result_arr.astype(np.float32) - blurred.astype(np.float32)),
            0,
            255,
        ).astype(np.uint8)

        result = Image.fromarray(result_arr, "RGB")
        return result.convert(orig_mode) if orig_mode != "RGB" else result

    @staticmethod
    def _coerce_positive_number(value: Any, default: float) -> float:
        try:
            v = float(value)
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default

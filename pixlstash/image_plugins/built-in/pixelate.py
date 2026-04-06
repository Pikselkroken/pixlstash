"""Built-in subtle pixelation plugin."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PIL import Image

from pixlstash.image_plugins.base import ImagePlugin


class PixelatePlugin(ImagePlugin):
    """Simulate the aliasing artefacts produced by under-sampled rendering.

    Pipeline:
    1. Compute a Sobel edge-magnitude map on the greyscale image.  Edges are
       where aliasing artefacts actually live in a real under-sampled render:
       the hair/background boundary, individual strand boundaries, etc.
    2. Nearest-neighbour downsample the image by `block_size`, then inject a
       fraction of directional pixel errors at that small scale.  Each flagged
       pixel is swapped with a neighbour in the direction *perpendicular* to
       the local gradient (i.e. across the edge), so after Lanczos upsampling
       every error becomes a staircase step that correctly crosses rather than
       runs along the strand.
    3. A smooth cluster-noise field (secondary modulation) groups nearby errors
       into natural-looking clumps rather than an even scatter.
    4. Lanczos upsample back to full resolution.
    5. Blend the aliased reconstruction into the original using the edge mask;
       smooth interior regions of skin and background are left unchanged.
    6. Light unsharp-mask to restore perceived crispness.
    """

    name = "pixelate"
    display_name = "Pixelate"
    description = (
        "Simulate aliasing / under-sampling artefacts (staircase edges at "
        "hair boundaries, stray colour pixels) while leaving smooth skin and "
        "plain backgrounds intact."
    )
    supports_images = True
    supports_videos = False

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "block_size",
                "label": "Block size",
                "type": "number",
                "default": 2,
                "description": (
                    "Downscale factor before NN reconstruction (2-8). "
                    "2 produces the finest staircase steps closest to the "
                    "diffusion under-sampling look; larger values give "
                    "coarser, more visible blocks."
                ),
            },
            {
                "name": "error_rate",
                "label": "Error rate",
                "type": "number",
                "default": 0.9,
                "description": (
                    "Fraction of edge pixels (at the downscaled resolution) that "
                    "receive a directional swap (0.0-1.0). "
                    "0.8-0.95 gives dense, realistic diffusion-style artefacts; "
                    "lower values produce a sparser, more scattered look."
                ),
            },
            {
                "name": "strength",
                "label": "Strength",
                "type": "number",
                "default": 0.95,
                "description": (
                    "Blend strength along detected hair edges "
                    "(0.0 = no effect, 1.0 = fully aliased at edges). "
                    "0.7-0.95 works well since the mask is selective."
                ),
            },
            {
                "name": "blur_strength",
                "label": "Blur",
                "type": "number",
                "default": 0.5,
                "description": (
                    "Gaussian blur sigma applied after the aliasing effect "
                    "(0.0 = none, 1.0 = strong). Softens the hard NN block "
                    "boundaries slightly, mimicking the blurriness in under-"
                    "sampled renders. 0.3-0.6 is a natural range."
                ),
            },
            {
                "name": "sharpen_strength",
                "label": "Sharpen",
                "type": "number",
                "default": 0.4,
                "description": (
                    "Unsharp-mask strength applied after the blur step "
                    "(0.0 = none, 1.0 = strong). Restores perceived crispness "
                    "without removing the aliasing artefacts. 0.3-0.5 pairs "
                    "well with a blur of 0.4-0.6."
                ),
            },
            {
                "label": "Debug mask",
                "type": "boolean",
                "default": False,
                "description": (
                    "Save the edge/hair mask as a greyscale PNG to "
                    "/tmp/pixelate_debug_mask_<n>.png for each image. "
                    "Use this to verify that the mask is correctly targeting hair."
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
            2, min(8, int(self._coerce_positive_number(params.get("block_size"), 2.0)))
        )
        error_rate = max(
            0.0, min(1.0, self._coerce_positive_number(params.get("error_rate"), 0.9))
        )
        strength = max(
            0.0, min(1.0, self._coerce_positive_number(params.get("strength"), 0.95))
        )
        blur_strength = max(
            0.0,
            min(2.0, self._coerce_positive_number(params.get("blur_strength"), 0.5)),
        )
        sharpen_strength = max(
            0.0,
            min(2.0, self._coerce_positive_number(params.get("sharpen_strength"), 0.4)),
        )
        debug = bool(params.get("debug", False))

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                result = self._pixelate(
                    image,
                    block_size,
                    error_rate,
                    strength,
                    blur_strength,
                    sharpen_strength,
                    debug,
                    idx,
                )
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
        image: Image.Image,
        block_size: int,
        error_rate: float,
        strength: float,
        blur_strength: float = 0.5,
        sharpen_strength: float = 0.4,
        debug: bool = False,
        debug_idx: int = 0,
    ) -> Image.Image:
        """Simulate aliasing artefacts concentrated at edges (e.g. hair boundaries).

        Steps:
        1. Compute Sobel edge magnitude on greyscale; normalise to [0,1] as the
           primary blend/error gate.  This focuses all changes on boundaries
           (hair-vs-background, strand edges) rather than the flat interior.
        2. Nearest-neighbour downsample by `block_size`.
        3. Inject directional pixel errors at the small scale, gated by the
           edge mask + a smooth cluster-noise field:
           - swap direction is *perpendicular* to the local gradient angle so
             each error crosses the edge (producing a staircase step) rather
             than running along it.
        4. Lanczos upsample back to full resolution.
        5. Blend using the edge mask; smooth skin/background untouched.
        6. Light unsharp mask for crispness.
        """
        orig_mode = image.mode
        rgb = image.convert("RGB")
        orig_arr = np.array(rgb, dtype=np.float32)
        h, w = orig_arr.shape[:2]
        orig_u8 = orig_arr.clip(0, 255).astype(np.uint8)

        # --- 1. Local-variance texture mask ----------------------------------
        # Local variance is high in fine-detail regions (hair, fabric weave,
        # foliage) and near-zero in smooth areas (bare skin, plain walls).
        # This naturally produces a non-uniform, content-driven mask that
        # concentrates the effect where detail is finest.
        gray = cv2.cvtColor(orig_u8, cv2.COLOR_RGB2GRAY).astype(np.float32)
        # Kernel sized to capture a few hair strands worth of neighbourhood.
        ksize = max(5, (block_size * 3) | 1)
        mean_local = cv2.boxFilter(gray, cv2.CV_32F, (ksize, ksize))
        mean_sq = cv2.boxFilter(gray * gray, cv2.CV_32F, (ksize, ksize))
        variance = np.maximum(0.0, mean_sq - mean_local**2)
        # Normalise against the 80th percentile so hair-level variance
        # reaches ~1.0 while smooth skin stays near 0.  Cube the result for
        # a steep falloff: mid-variance regions (blurred background, flat
        # clothing) are suppressed strongly, only the finest texture peaks.
        p80 = float(np.percentile(variance, 80))
        edge_mask = (
            np.clip(variance / p80, 0.0, 1.0) ** 3
            if p80 > 0
            else np.zeros_like(variance)
        )
        # Also compute Sobel gradient for directional error injection.
        gray_blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=0.8)
        gx = cv2.Sobel(gray_blur, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray_blur, cv2.CV_32F, 0, 1, ksize=3)

        if debug:
            debug_path = f"/tmp/pixelate_debug_mask_{debug_idx}.png"
            mask_u8 = (edge_mask * 255).clip(0, 255).astype(np.uint8)
            cv2.imwrite(debug_path, mask_u8)
            # Also save a colour overlay: mask tinted red over the original.
            overlay = orig_u8.copy()
            overlay[:, :, 0] = np.clip(
                overlay[:, :, 0].astype(np.float32) + mask_u8 * 0.8, 0, 255
            ).astype(np.uint8)
            overlay[:, :, 1] = np.clip(
                overlay[:, :, 1].astype(np.float32) - mask_u8 * 0.4, 0, 255
            ).astype(np.uint8)
            overlay[:, :, 2] = np.clip(
                overlay[:, :, 2].astype(np.float32) - mask_u8 * 0.4, 0, 255
            ).astype(np.uint8)
            cv2.imwrite(
                f"/tmp/pixelate_debug_overlay_{debug_idx}.png",
                cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR),
            )

        # --- 2. Nearest-neighbour downsample ---------------------------------
        small_w = max(1, w // block_size)
        small_h = max(1, h // block_size)
        small = cv2.resize(orig_u8, (small_w, small_h), interpolation=cv2.INTER_NEAREST)

        # --- 3. Directional errors gated by edge mask + cluster noise --------
        if error_rate > 0.0:
            rng = np.random.default_rng()

            # Gradient angle at full resolution → downsample to small scale.
            # atan2 gives angle in [-π, π]; we want the perpendicular direction
            # (across the edge) so add π/2.
            grad_angle = np.arctan2(gy, gx) + np.pi / 2
            angle_small = cv2.resize(
                grad_angle, (small_w, small_h), interpolation=cv2.INTER_NEAREST
            )
            # Convert angle to integer ±1 step in x/y (8-directional snap).
            perp_dx = np.round(np.cos(angle_small)).astype(np.int32)
            perp_dy = np.round(np.sin(angle_small)).astype(np.int32)

            # Variance weight at the small scale — errors concentrate where
            # detail is finest.  Use error_rate as a global scale factor
            # rather than a flat percentile cut, so the distribution mirrors
            # the variance map (non-uniform, content-driven).
            tex_small = cv2.resize(
                edge_mask, (small_w, small_h), interpolation=cv2.INTER_AREA
            )

            # Combined probability: variance weight × cluster blob.
            # No percentile threshold — just compare against uniform random
            # so each pixel's chance of being flagged is proportional to its
            # variance score × error_rate.
            rand_field = rng.random((small_h, small_w)).astype(np.float32)
            error_flag = rand_field < (tex_small * error_rate)

            # Directional swap: move perpendicular to the local edge normal.
            src_y = np.clip(np.arange(small_h)[:, np.newaxis] + perp_dy, 0, small_h - 1)
            src_x = np.clip(np.arange(small_w)[np.newaxis, :] + perp_dx, 0, small_w - 1)
            small_err = small.copy()
            small_err[error_flag] = small[src_y[error_flag], src_x[error_flag]]
            small = small_err

        # --- 4. Nearest-neighbour upsample -----------------------------------
        # INTER_NEAREST preserves the hard pixel steps created by NN downscale
        # and error injection.  Lanczos would smooth them away, which is the
        # opposite of the aliasing look we want.
        upsampled = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST).astype(
            np.float32
        )

        # --- 5. Blend using edge mask ----------------------------------------
        blend_w = np.clip(edge_mask * strength, 0.0, 1.0)[:, :, np.newaxis]
        result_arr = np.clip(
            orig_arr * (1.0 - blend_w) + upsampled * blend_w, 0, 255
        ).astype(np.uint8)

        # --- 6. Blur then sharpen -------------------------------------------
        if blur_strength > 0.0:
            sigma = blur_strength * 2.0  # map 0-1 → 0-2 sigma
            result_arr = cv2.GaussianBlur(result_arr, (0, 0), sigmaX=sigma)
        if sharpen_strength > 0.0:
            blurred = cv2.GaussianBlur(result_arr, (0, 0), sigmaX=1.0)
            result_arr = np.clip(
                result_arr.astype(np.float32)
                + sharpen_strength
                * (result_arr.astype(np.float32) - blurred.astype(np.float32)),
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

import cv2
import numpy as np
import time

from sqlmodel import Column, ForeignKey, Integer, SQLModel, Field, Relationship, Session
from typing import List, Optional, TYPE_CHECKING

from scipy.ndimage import median_filter

from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from .picture import Picture

logger = get_logger(__name__)


class Quality(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    picture_id: Optional[int] = Field(
        sa_column=Column(
            Integer, ForeignKey("picture.id", ondelete="CASCADE"), index=True
        ),
        default=None,
    )
    sharpness: Optional[float] = Field(default=None, index=True)
    edge_density: Optional[float] = Field(default=None, index=True)
    contrast: Optional[float] = Field(default=None, index=True)
    brightness: Optional[float] = Field(default=None, index=True)
    noise_level: Optional[float] = Field(default=None, index=True)
    colorfulness: Optional[float] = Field(default=None, index=True)
    luminance_entropy: Optional[float] = Field(default=None, index=True)
    dominant_hue: Optional[float] = Field(default=None, index=True)

    # Relationships
    picture: Optional["Picture"] = Relationship(back_populates="quality")

    def calculate_quality_score(self) -> float:
        # Calculate heuristic quality score (1-10)
        # Sharpness: typically 0-0.5. Normalize * 2.0 -> 0-1
        s = min(1.0, (self.sharpness or 0.0) * 2.0)
        # Contrast: typically 0-0.3. Normalize * 3.0 -> 0-1
        c = min(1.0, (self.contrast or 0.0) * 3.0)
        # Brightness: ideal ~0.5.
        b_val = self.brightness or 0.5
        b = 1.0 - abs(b_val - 0.5) * 2.0  # 1.0 at 0.5, 0.0 at 0 or 1

        # Weights: Sharpness (40%), Contrast (40%), Exposure/Brightness (20%)
        heuristic = (s * 0.4 + c * 0.4 + b * 0.2) * 9.0 + 1.0
        return round(heuristic, 2)

    @staticmethod
    def batch_likeness_scores(features_a, features_b):
        """
        Given two lists of facial feature arrays (np.ndarray), compute cosine similarity for each pair.
        Returns a numpy array of likeness scores (shape: [len(features_a)]).
        """
        import numpy as np

        X_a = np.stack(features_a, axis=0)
        X_b = np.stack(features_b, axis=0)
        norms_a = np.linalg.norm(X_a, axis=1, keepdims=True)
        norms_b = np.linalg.norm(X_b, axis=1, keepdims=True)
        X_a_norm = X_a / (norms_a + 1e-8)
        X_b_norm = X_b / (norms_b + 1e-8)
        return np.sum(X_a_norm * X_b_norm, axis=1)

    @staticmethod
    def calculate_quality_batch(
        images: np.ndarray,
    ) -> List["Quality"]:
        """Calculate quality metrics for a batch of images.

        Accepts a 4D np.ndarray (batch, H, W, C) and returns a list of Quality
        instances.  Metrics are vectorised as much as possible:

        - brightness, contrast, colorfulness — fully vectorised (single NumPy op)
        - edge_density — batched L1 gradient (pure NumPy, replaces per-image Canny)
        - luminance_entropy — offset-bincount trick (single C call for whole batch)
        - dominant_hue — vectorised NumPy RGB→hue; per-image only for argmax
        - sharpness, noise_level — fully vectorised discrete Laplacian
        - text_score — stored on Picture; not computed here
        """
        batch_size = images.shape[0]
        if images.ndim != 4:
            raise ValueError(
                "Input must be a 4D array: (batch, height, width, channels)"
            )

        # --- Fully vectorised: brightness, contrast ---
        brightness = images.mean(axis=(1, 2, 3)) / 255.0
        contrast = images.std(axis=(1, 2, 3)) / 255.0

        # --- Fully vectorised: colorfulness (Hasler & Süsstrunk) ---
        # rgb is shared for edge_density, grayscale, and hue below — single float32 alloc.
        rgb = images.astype(np.float32) / 255.0  # (B, H, W, C)
        if images.shape[3] == 3:
            rg = rgb[:, :, :, 0] - rgb[:, :, :, 1]
            yb = 0.5 * (rgb[:, :, :, 0] + rgb[:, :, :, 1]) - rgb[:, :, :, 2]
            colorfulness = np.clip(
                np.sqrt(rg.std(axis=(1, 2)) ** 2 + yb.std(axis=(1, 2)) ** 2)
                + 0.3 * np.sqrt(rg.mean(axis=(1, 2)) ** 2 + yb.mean(axis=(1, 2)) ** 2),
                0.0,
                1.0,
            )
        else:
            colorfulness = np.zeros(batch_size, dtype=np.float32)

        # Grayscale for the metrics below — derived from rgb to avoid a second cast.
        # Multiply by 255 so gray_uint8 clip/cast and Laplacian inputs are correct.
        gray = rgb.mean(axis=3) * 255.0  # (B, H, W), float32, values in [0, 255]

        # --- Batched L1 gradient as edge_density (replaces per-image cv2.Canny) ---
        # Mean absolute first-order gradient across all channels/spatial dims,
        # normalised to [0, 1].  Fully vectorised across the whole batch — no
        # Python loop, no OpenCV.  Reuses rgb (0..1) — no second astype needed.
        dy = np.abs(rgb[:, 1:, :, :] - rgb[:, :-1, :, :])
        dx = np.abs(rgb[:, :, 1:, :] - rgb[:, :, :-1, :])
        edge_density = (dy.mean(axis=(1, 2, 3)) + dx.mean(axis=(1, 2, 3))) / 2.0

        # --- Luminance entropy: offset-bincount trick (single C call) ---
        gray_uint8 = np.clip(gray, 0, 255).astype(np.uint8)  # (B, H, W)
        gray_flat = gray_uint8.reshape(batch_size, -1)  # (B, H*W)
        offsets = (np.arange(batch_size, dtype=np.int64) * 256)[:, None]
        all_hists = (
            np.bincount(
                (gray_flat.astype(np.int64) + offsets).ravel(),
                minlength=batch_size * 256,
            )
            .reshape(batch_size, 256)
            .astype(np.float64)
        )
        totals = all_hists.sum(axis=1, keepdims=True)
        probs = all_hists / np.maximum(totals, 1.0)
        log_probs = np.where(probs > 0.0, np.log2(np.maximum(probs, 1e-12)), 0.0)
        luminance_entropy = (-np.sum(probs * log_probs, axis=1) / np.log2(256)).astype(
            np.float32
        )

        # --- Dominant hue: vectorised NumPy RGB→hue, per-image argmax only ---
        # cv2.cvtColor has no batch API; we compute hue for the entire batch as
        # a single NumPy expression and only loop for the final histogram argmax.
        if images.shape[3] == 3:
            R, G, B_ch = rgb[:, :, :, 0], rgb[:, :, :, 1], rgb[:, :, :, 2]
            Cmax = np.maximum(np.maximum(R, G), B_ch)
            Cmin = np.minimum(np.minimum(R, G), B_ch)
            delta = Cmax - Cmin
            eps = 1e-7
            h = np.zeros_like(delta)
            mr = (Cmax == R) & (delta > eps)
            h = np.where(mr, ((G - B_ch) / (delta + eps)) % 6.0, h)
            mg = (Cmax == G) & (delta > eps)
            h = np.where(mg, (B_ch - R) / (delta + eps) + 2.0, h)
            mb = (Cmax == B_ch) & (delta > eps)
            h = np.where(mb, (R - G) / (delta + eps) + 4.0, h)
            # Scale to OpenCV HSV hue range (0–179)
            hue_int = np.clip(h * (180.0 / 6.0), 0, 179).astype(np.int32)
            sat = np.where(Cmax > eps, delta / (Cmax + eps), 0.0)
            sat_val_mask = (sat > 20.0 / 255.0) & (Cmax > 20.0 / 255.0)  # (B, H, W)
            # Fully vectorised histogram — same offset-bincount trick as entropy.
            # sat_val_mask used as weights: 0.0 for masked-out pixels, 1.0 for valid,
            # so they contribute nothing to the per-image hue counts.
            hue_flat = hue_int.reshape(batch_size, -1).astype(np.int64)  # (B, H*W)
            mask_flat = sat_val_mask.reshape(batch_size, -1).astype(
                np.float64
            )  # weights
            hue_offsets = (np.arange(batch_size, dtype=np.int64) * 180)[:, None]
            hue_hists = np.bincount(
                (hue_flat + hue_offsets).ravel(),
                weights=mask_flat.ravel(),
                minlength=batch_size * 180,
            ).reshape(batch_size, 180)
            has_valid = hue_hists.sum(axis=1) > 0
            dominant_hue_idx = hue_hists.argmax(axis=1).astype(np.float32)
            dominant_hue = np.where(
                has_valid, (dominant_hue_idx + 0.5) / 180.0, 0.0
            ).astype(np.float32)
        else:
            dominant_hue = np.zeros(batch_size, dtype=np.float32)

        # --- Fully vectorised: sharpness + noise_level via discrete Laplacian ---
        # 5-point stencil (identical to cv2.Laplacian ksize=1) applied to the
        # whole batch in one NumPy expression — no Python loop, no OpenCV call,
        # GIL released for the entire computation.
        lap = (
            4.0 * gray[:, 1:-1, 1:-1]
            - gray[:, :-2, 1:-1]
            - gray[:, 2:, 1:-1]
            - gray[:, 1:-1, :-2]
            - gray[:, 1:-1, 2:]
        )  # (B, H-2, W-2), float32

        # noise_level: mean |Laplacian| normalised to [0, 1]
        noise_level = np.clip(np.abs(lap).mean(axis=(1, 2)) / 255.0, 0.0, 1.0)

        # sharpness: vectorised _cell_sharpness — rewards a sharp subject region.
        # Divide each frame's Laplacian into a 4×4 grid, take the top-4 cell
        # variances, normalise.  Equivalent to calling _cell_sharpness per image.
        _GRID, _TOP_K, _NORM = 4, 4, 500.0
        B_l, H_l, W_l = lap.shape
        cell_h = max(1, H_l // _GRID)
        cell_w = max(1, W_l // _GRID)
        lap_c = lap[:, : cell_h * _GRID, : cell_w * _GRID]
        lc = lap_c.reshape(B_l, _GRID, cell_h, _GRID, cell_w)
        cell_var = lc.var(axis=(2, 4))  # (B, _GRID, _GRID)
        cell_var_flat = cell_var.reshape(B_l, -1)  # (B, _GRID²)
        n_cells = cell_var_flat.shape[1]
        k = min(_TOP_K, n_cells)
        if k > 0:
            top_vals = np.partition(cell_var_flat, n_cells - k, axis=1)[
                :, n_cells - k :
            ]
            sharpness = np.clip(top_vals.mean(axis=1) / _NORM, 0.0, 1.0)
        else:
            sharpness = np.zeros(batch_size, dtype=np.float32)

        results = []
        for i in range(batch_size):
            results.append(
                Quality(
                    sharpness=float(sharpness[i]),
                    edge_density=float(edge_density[i]),
                    contrast=float(contrast[i]),
                    brightness=float(brightness[i]),
                    noise_level=float(noise_level[i]),
                    colorfulness=float(colorfulness[i]),
                    luminance_entropy=float(luminance_entropy[i]),
                    dominant_hue=float(dominant_hue[i]),
                    text_score=None,  # Filled in separately by TextScoreTask
                )
            )
        return results

    """
    Stores subjective and objective quality metrics for an image.
    Fractional parameters can be calculated automatically.
    """

    @staticmethod
    def calculate_quality(
        image: np.ndarray, face_crop: Optional[np.ndarray] = None
    ) -> "Quality":
        """
        Calculate objective metrics from a NumPy image array.
        Logs timing for each metric calculation.
        If any metric is None, set to -1.0.
        """
        timings = {}
        t0 = time.time()
        sharpness = Quality._calculate_sharpness(image)
        timings["sharpness"] = time.time() - t0
        t0 = time.time()
        edge_density = Quality._calculate_edge_density(image)
        timings["edge_density"] = time.time() - t0
        t0 = time.time()
        contrast = Quality._calculate_contrast(image)
        timings["contrast"] = time.time() - t0
        t0 = time.time()
        brightness = Quality._calculate_brightness(image)
        timings["brightness"] = time.time() - t0
        t0 = time.time()
        noise_level = Quality._calculate_noise_level(image)
        timings["noise_level"] = time.time() - t0
        t0 = time.time()
        colorfulness = Quality._calculate_colorfulness(image)
        timings["colorfulness"] = time.time() - t0
        t0 = time.time()
        luminance_entropy = Quality._calculate_luminance_entropy(image)
        timings["luminance_entropy"] = time.time() - t0
        t0 = time.time()
        dominant_hue = Quality._calculate_dominant_hue(image)
        timings["dominant_hue"] = time.time() - t0

        # Post-calc None checks
        sharpness = -1.0 if sharpness is None else sharpness
        edge_density = -1.0 if edge_density is None else edge_density
        contrast = -1.0 if contrast is None else contrast
        brightness = -1.0 if brightness is None else brightness
        noise_level = -1.0 if noise_level is None else noise_level
        colorfulness = -1.0 if colorfulness is None else colorfulness
        luminance_entropy = -1.0 if luminance_entropy is None else luminance_entropy
        dominant_hue = -1.0 if dominant_hue is None else dominant_hue
        return Quality(
            sharpness=float(sharpness),
            edge_density=float(edge_density),
            contrast=float(contrast),
            brightness=float(brightness),
            noise_level=float(noise_level),
            colorfulness=float(colorfulness),
            luminance_entropy=float(luminance_entropy),
            dominant_hue=float(dominant_hue),
        )

    @staticmethod
    def _cell_sharpness(
        lap: np.ndarray,
        grid: int = 4,
        top_k: int = 4,
        norm: float = 500.0,
    ) -> float:
        """Subject sharpness: [0, 1]. High if at least one region of the image is sharp.

        Divides the Laplacian image into a grid of cells and returns the mean
        Laplacian variance of the top-k sharpest cells, normalised to [0, 1].
        This rewards images with a sharp subject (face, object, etc.) regardless
        of overall depth-of-field, and penalises images that are uniformly blurry.

        Args:
            lap: 2-D Laplacian array (float32).
            grid: Number of rows/columns in the cell grid.
            top_k: Number of sharpest cells to average.
            norm: Normalisation constant — cell variance above this maps to 1.0.

        Returns:
            Score in [0, 1].
        """
        h, w = lap.shape
        cell_h = max(1, h // grid)
        cell_w = max(1, w // grid)
        variances = []
        for row in range(grid):
            for col in range(grid):
                y0 = row * cell_h
                y1 = min(h, y0 + cell_h)
                x0 = col * cell_w
                x1 = min(w, x0 + cell_w)
                cell = lap[y0:y1, x0:x1]
                if cell.size > 0:
                    variances.append(float(cell.var()))
        if not variances:
            return 0.0
        variances.sort(reverse=True)
        top_mean = float(np.mean(variances[: min(top_k, len(variances))]))
        return float(min(top_mean / norm, 1.0))

    @staticmethod
    def _calculate_sharpness(image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F)
        return Quality._cell_sharpness(lap)

    @staticmethod
    def _calculate_edge_density(image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        return float(np.count_nonzero(edges)) / edges.size

    @staticmethod
    def _calculate_contrast(image: np.ndarray) -> float:
        gray = image.mean(axis=2) if image.ndim == 3 else image
        contrast = gray.std() / 255.0
        return min(contrast, 1.0)

    @staticmethod
    def _calculate_brightness(image: np.ndarray) -> float:
        gray = image.mean(axis=2) if image.ndim == 3 else image
        brightness = gray.mean() / 255.0
        return min(brightness, 1.0)

    @staticmethod
    def _calculate_noise_level(image: np.ndarray) -> float:
        # Optimised: grayscale and quarter resolution

        # Convert to grayscale
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image

        # Downscale to quarter resolution
        h, w = gray.shape
        gray_small = cv2.resize(gray, (w // 2, h // 2), interpolation=cv2.INTER_AREA)

        # Apply median filter
        filtered = median_filter(gray_small, size=3)
        diff = np.abs(gray_small - filtered)
        noise = diff.mean() / 255.0
        return min(noise, 1.0)

    @staticmethod
    def _calculate_colorfulness(image: np.ndarray) -> float:
        if image.ndim != 3 or image.shape[2] != 3:
            return 0.0
        rgb = image.astype(np.float32) / 255.0
        rg = rgb[:, :, 0] - rgb[:, :, 1]
        yb = 0.5 * (rgb[:, :, 0] + rgb[:, :, 1]) - rgb[:, :, 2]
        std_rg = rg.std()
        std_yb = yb.std()
        mean_rg = rg.mean()
        mean_yb = yb.mean()
        colorfulness = np.sqrt(std_rg**2 + std_yb**2) + 0.3 * np.sqrt(
            mean_rg**2 + mean_yb**2
        )
        return float(min(max(colorfulness, 0.0), 1.0))

    @staticmethod
    def _calculate_luminance_entropy(image: np.ndarray) -> float:
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image
        hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
        total = hist.sum()
        if total == 0:
            return 0.0
        probs = hist / total
        probs = probs[probs > 0]
        entropy = -np.sum(probs * np.log2(probs))
        return float(min(max(entropy / np.log2(256), 0.0), 1.0))

    @staticmethod
    def _calculate_dominant_hue(image: np.ndarray) -> float:
        if image.ndim != 3 or image.shape[2] != 3:
            return 0.0
        hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
        hue = hsv[:, :, 0]
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        mask = (sat > 20) & (val > 20)
        if not np.any(mask):
            return 0.0
        hist = np.bincount(hue[mask].ravel(), minlength=180).astype(np.float64)
        if hist.sum() == 0:
            return 0.0
        bin_idx = int(hist.argmax())
        return float(min(max((bin_idx + 0.5) / 180.0, 0.0), 1.0))

    @staticmethod
    def _calculate_text_score(image: np.ndarray) -> float:
        """Estimate the likelihood that an image is a text document (receipt, invoice, etc.).

        Two independent signals are combined with a geometric mean so that *both* must
        be strong to produce a high score:

        1. **Background uniformity** – documents have a strongly dominant background
           colour (white paper, dark board) covering most of the image.  Natural photos
           have a much more spread-out pixel distribution.  A blank white sheet scores
           0 on signal 2 and therefore still returns 0.

        2. **Character-component density** – after Otsu binarisation against the detected
           background polarity, connected components that fall within the size range of
           individual printed characters are counted.  Receipts produce hundreds of such
           components; a T-shirt with a slogan produces only a handful.

        The geometric mean ensures both signals must be present: a blank page gets 0
        (no components), a textured photo gets 0 (no dominant background), and only
        true text documents score high.

        Args:
            image: RGB or grayscale image as a numpy array.

        Returns:
            Score in [0, 1].
        """
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image.copy()
        h, w = gray.shape
        area = h * w
        if area == 0:
            return 0.0

        # --- Signal 1: background uniformity ---
        # Find the dominant grey level by smoothing the histogram and finding its peak.
        hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
        # Box-smooth over ±15 levels to find the broad peak (background colour).
        kernel = np.ones(31) / 31.0
        hist_smooth = np.convolve(hist, kernel, mode="same")
        bg_level = int(np.argmax(hist_smooth))
        # Fraction of pixels within ±25 levels of the dominant background peak.
        margin = 25
        lo = max(0, bg_level - margin)
        hi = min(255, bg_level + margin)
        bg_fraction = float(hist[lo : hi + 1].sum()) / area
        # Ramp from 0 below 0.60 to 1.0 at 0.90.  Textured natural photos (stone walls,
        # foliage, fabric) typically land at 0.35–0.58; a scanned document at 0.70–0.92.
        # The higher lower bound is critical: stone/brick walls produce a moderately
        # dominant background colour but not as tight a peak as white paper.
        bg_score = float(np.clip((bg_fraction - 0.60) / 0.30, 0.0, 1.0))

        # Early exit for light scenes: if the dominant background isn't document-like
        # there is no point proceeding.  The paper_score boost is only meaningful in
        # the dark-scene path below (bright document on dark backdrop).

        # --- Signal 2: character-component density ---
        # The binarisation strategy depends on whether the document background is light
        # (scanned / held-up receipt) or whether a bright paper sits on a dark scene
        # (receipt laid on a table and photographed from above).
        if bg_level > 128:
            # Light background, dark text — standard case.
            # Run Otsu first: the threshold value itself is the strongest discriminant.
            thresh_val, binary = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
            )
            # Genuine ink-on-white documents split at 130–190: large white spike (~240)
            # vs dark ink / UI chrome (~0–80).  Mid-grey natural scenes (stone walls,
            # pool decks) split at 80–125 — the two "halves" are just texture, not ink.
            if thresh_val < 130:
                return 0.0
            # A very high Otsu split (≥ 140) is strong evidence of document bimodality
            # — e.g. a PDF screenshot where phone chrome squeezes bg_fraction below 0.60.
            # In that case waive the bg_score requirement; otherwise require it.
            if bg_score == 0.0 and thresh_val < 140:
                return 0.0
            fg_fraction = float(np.count_nonzero(binary)) / area
            # Sparse ink on paper: 2–40 % foreground.
            # Textured photos split ~50/50 (no true bimodal distribution) → reject.
            if fg_fraction > 0.40 or fg_fraction < 0.02:
                return 0.0
            analysis_binary = binary
        else:
            # Dark scene with a bright document region.
            # Check for a large near-white area — the paper is brighter than the backdrop.
            # Ramp: 20 % paper coverage → 0; 55 % coverage → 1.
            paper_fraction = float((gray > 160).sum()) / area
            paper_score = float(np.clip((paper_fraction - 0.20) / 0.35, 0.0, 1.0))
            bg_score = max(bg_score, paper_score)
            if bg_score == 0.0:
                return 0.0
            # Step 1: isolate the paper (bright) region.
            _, paper_mask = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            paper_px = int(np.count_nonzero(paper_mask))
            paper_frac = float(paper_px) / area
            # Require the paper region to cover 15–70 % of the frame.
            if paper_frac < 0.15 or paper_frac > 0.70:
                return 0.0
            # Step 2: re-binarise *within* the paper region to find dark text on white.
            # Non-paper pixels are set to white so they don't confuse Otsu.
            paper_gray = np.where(paper_mask > 0, gray, 255).astype(np.uint8)
            _, analysis_binary = cv2.threshold(
                paper_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
            )
            # Text should cover 1–35 % of the paper area (not 50 %, which is texture).
            text_px = int(np.count_nonzero(analysis_binary))
            fg_fraction = float(text_px) / max(paper_px, 1)
            if fg_fraction > 0.35 or fg_fraction < 0.01:
                return 0.0

        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(
            analysis_binary, connectivity=8
        )

        # Character-size bounds scaled to actual image resolution.
        # At 512 px, a small printed character is roughly 3–30 px on a side.
        min_char_area = max(8, area // 4000)  # lower bound scales with resolution
        max_char_area = area // 20  # upper bound: nothing bigger than 5 % of frame

        char_count = 0
        for i in range(1, num_labels):  # label 0 is the background
            ca = int(stats[i, cv2.CC_STAT_AREA])
            if ca < min_char_area or ca > max_char_area:
                continue
            cw = int(stats[i, cv2.CC_STAT_WIDTH])
            ch = int(stats[i, cv2.CC_STAT_HEIGHT])
            if cw == 0 or ch == 0:
                continue
            # Reject very elongated blobs (scan lines, borders, stray marks).
            if max(cw, ch) / float(min(cw, ch)) > 6.0:
                continue
            char_count += 1

        # Normalise: ~250 character-like components = full score.
        # A dense receipt at 512 px yields 200–500; a T-shirt slogan yields < 30.
        cc_score = float(min(1.0, char_count / 250.0))

        # Geometric mean: both signals must be strong for a high overall score.
        return float(np.sqrt(bg_score * cc_score))

    @classmethod
    def quality_metric_fields(cls) -> set[str]:
        """
        Return list of quality metric field names common for pictures and picture faces
        """
        return [
            "sharpness",
            "edge_density",
            "contrast",
            "brightness",
            "noise_level",
            "colorfulness",
            "luminance_entropy",
            "dominant_hue",
        ]

    @classmethod
    def quality_read_for_picture(
        cls, session: Session, picture_id: int
    ) -> Optional["Quality"]:
        """
        Load quality record for given picture ID.
        """
        return (
            session.query(cls)
            .filter(cls.picture_id == picture_id, cls.face_id.is_(None))
            .first()
        )

    @classmethod
    def quality_read_for_face(
        cls, session: Session, face_id: int
    ) -> Optional["Quality"]:
        """
        Load quality record for given face ID.
        """
        return session.query(cls).filter(cls.face_id == face_id).first()

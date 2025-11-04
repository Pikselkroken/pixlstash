from typing import Optional
import numpy as np

from pixelurgy_vault.logging import get_logger

logger = get_logger(__name__)


class PictureQuality:
    """
    Stores subjective and objective quality metrics for an image.
    Fractional parameters can be calculated automatically.
    """

    def __init__(
        self,
        sharpness: Optional[float] = None,
        edge_density: Optional[float] = None,
        contrast: Optional[float] = None,
        brightness: Optional[float] = None,
        noise_level: Optional[float] = None,
    ):
        self.sharpness = sharpness  # Objective sharpness metric (0.0-1.0)
        self.edge_density = edge_density  # Fraction of edge pixels (0.0-1.0)
        self.contrast = contrast  # Normalized contrast (0.0-1.0)
        self.brightness = brightness  # Normalized brightness (0.0-1.0)
        self.noise_level = noise_level  # Estimated noise (0.0-1.0)

    @staticmethod
    def calculate_metrics(
        image: np.ndarray, face_crop: Optional[np.ndarray] = None
    ) -> "PictureQuality":
        """
        Calculate objective metrics from a NumPy image array.
        Logs timing for each metric calculation.
        """
        import time

        timings = {}

        t0 = time.time()
        sharpness = PictureQuality._calculate_sharpness(image)
        timings["sharpness"] = time.time() - t0

        t0 = time.time()
        edge_density = PictureQuality._calculate_edge_density(image)
        timings["edge_density"] = time.time() - t0

        t0 = time.time()
        contrast = PictureQuality._calculate_contrast(image)
        timings["contrast"] = time.time() - t0

        t0 = time.time()
        brightness = PictureQuality._calculate_brightness(image)
        timings["brightness"] = time.time() - t0

        t0 = time.time()
        noise_level = PictureQuality._calculate_noise_level(image)
        timings["noise_level"] = time.time() - t0

        return PictureQuality(
            sharpness=sharpness,
            edge_density=edge_density,
            contrast=contrast,
            brightness=brightness,
            noise_level=noise_level,
        )

    @staticmethod
    def calculate_face_quality(image_np, face_bbox):
        """
        Calculate the quality score for the face region in the image.
        """
        x1, y1, x2, y2 = [int(round(v)) for v in face_bbox]
        h, w = image_np.shape[:2]
        # Clamp bbox to image bounds
        x1_clamped = max(0, min(w, x1))
        x2_clamped = max(0, min(w, x2))
        y1_clamped = max(0, min(h, y1))
        y2_clamped = max(0, min(h, y2))
        if x2_clamped > x1_clamped and y2_clamped > y1_clamped:
            face_crop = image_np[y1_clamped:y2_clamped, x1_clamped:x2_clamped]
            if face_crop.size == 0:
                logger.error(
                    f"Face crop is empty after clamping bbox: {face_bbox}, clamped: {(x1_clamped, y1_clamped, x2_clamped, y2_clamped)}"
                )
                return None
            else:
                return PictureQuality.calculate_metrics(face_crop)

        logger.error(
            f"Invalid bbox after clamping: {face_bbox}, clamped: {(x1_clamped, y1_clamped, x2_clamped, y2_clamped)}"
        )
        return None

    @staticmethod
    def _calculate_sharpness(image: np.ndarray) -> float:
        # Example: variance of Laplacian
        import cv2

        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        return min(laplacian_var / 100.0, 1.0)

    @staticmethod
    def _calculate_edge_density(image: np.ndarray) -> float:
        import cv2

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
        # Optimized: grayscale and quarter resolution
        from scipy.ndimage import median_filter
        import cv2

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

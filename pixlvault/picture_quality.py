import cv2
import numpy as np
import time


from scipy.ndimage import median_filter
from typing import Optional

from pixlvault.logging import get_logger

logger = get_logger(__name__)


class PictureQuality:
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
        likeness_values = np.sum(X_a_norm * X_b_norm, axis=1)
        return likeness_values

    @staticmethod
    def calculate_quality_batch(images: np.ndarray) -> list:
        """
        Calculate quality metrics for a batch of images.
        Accepts a 4D np.ndarray (batch, height, width, channels) and returns a list of PictureQuality instances.
        All metrics are vectorized for speed. If any metric is None, set to -1.0.
        """
        batch_size = images.shape[0]
        if images.ndim != 4:
            raise ValueError(
                "Input must be a 4D array: (batch, height, width, channels)"
            )
        # Vectorized brightness and contrast
        brightness = images.mean(axis=(1, 2, 3)) / 255.0
        contrast = images.std(axis=(1, 2, 3)) / 255.0
        if images.shape[3] == 3:
            gray = images.mean(axis=3)
        else:
            gray = images.squeeze(axis=3) if images.shape[3] == 1 else images
        laplacians = np.array(
            [
                cv2.Laplacian(gray[i].astype(np.float32), cv2.CV_32F)
                for i in range(batch_size)
            ]
        )
        noise_level = np.clip(np.abs(laplacians).mean(axis=(1, 2)) / 255.0, 0, 1)
        sharpness = np.clip(laplacians.var(axis=(1, 2)) / 100.0, 0, 1)
        edges = np.array(
            [cv2.Canny(gray[i].astype(np.uint8), 100, 200) for i in range(batch_size)]
        )
        edge_density = (edges > 0).sum(axis=(1, 2)) / edges[0].size

        # Post-calc None checks
        def fix_none(arr):
            arr = np.array(arr)
            arr[np.equal(arr, None)] = -1.0
            return arr

        sharpness = fix_none(sharpness)
        edge_density = fix_none(edge_density)
        contrast = fix_none(contrast)
        brightness = fix_none(brightness)
        noise_level = fix_none(noise_level)
        results = []
        for i in range(batch_size):
            results.append(
                PictureQuality(
                    sharpness=sharpness[i],
                    edge_density=edge_density[i],
                    contrast=contrast[i],
                    brightness=brightness[i],
                    noise_level=noise_level[i],
                )
            )
        return results

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

    def to_dict(self):
        """Convert PictureQuality instance to dictionary."""
        return {
            "sharpness": self.sharpness,
            "edge_density": self.edge_density,
            "contrast": self.contrast,
            "brightness": self.brightness,
            "noise_level": self.noise_level,
        }

    @staticmethod
    def from_db_columns(
        sharpness=None,
        edge_density=None,
        contrast=None,
        brightness=None,
        noise_level=None,
    ):
        return PictureQuality(
            sharpness=sharpness,
            edge_density=edge_density,
            contrast=contrast,
            brightness=brightness,
            noise_level=noise_level,
        )

    @staticmethod
    def from_dict(data: dict) -> "PictureQuality":
        """Create PictureQuality instance from dictionary."""
        return PictureQuality(
            sharpness=data.get("sharpness"),
            edge_density=data.get("edge_density"),
            contrast=data.get("contrast"),
            brightness=data.get("brightness"),
            noise_level=data.get("noise_level"),
        )

    @staticmethod
    def calculate_quality(
        image: np.ndarray, face_crop: Optional[np.ndarray] = None
    ) -> "PictureQuality":
        """
        Calculate objective metrics from a NumPy image array.
        Logs timing for each metric calculation.
        If any metric is None, set to -1.0.
        """
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
        # Post-calc None checks
        sharpness = -1.0 if sharpness is None else sharpness
        edge_density = -1.0 if edge_density is None else edge_density
        contrast = -1.0 if contrast is None else contrast
        brightness = -1.0 if brightness is None else brightness
        noise_level = -1.0 if noise_level is None else noise_level
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
                return PictureQuality.calculate_quality(face_crop)

        logger.error(
            f"Invalid bbox after clamping: {face_bbox}, clamped: {(x1_clamped, y1_clamped, x2_clamped, y2_clamped)}"
        )
        return None

    @staticmethod
    def _calculate_sharpness(image: np.ndarray) -> float:
        # Example: variance of Laplacian
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        return min(laplacian_var / 100.0, 1.0)

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
        # Optimized: grayscale and quarter resolution

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

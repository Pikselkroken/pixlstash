from typing import Optional
import numpy as np


class PictureQuality:
    """
    Stores subjective and objective quality metrics for an image.
    Fractional parameters can be calculated automatically.
    """

    def __init__(
        self,
        subjective_score: Optional[float] = None,
        sharpness: Optional[float] = None,
        edge_density: Optional[float] = None,
        contrast: Optional[float] = None,
        brightness: Optional[float] = None,
        noise_level: Optional[float] = None,
    ):
        self.subjective_score = subjective_score  # User or model rating (0.0-1.0)
        self.sharpness = sharpness  # Objective sharpness metric (0.0-1.0)
        self.edge_density = edge_density  # Fraction of edge pixels (0.0-1.0)
        self.contrast = contrast  # Normalized contrast (0.0-1.0)
        self.brightness = brightness  # Normalized brightness (0.0-1.0)
        self.noise_level = noise_level  # Estimated noise (0.0-1.0)

    @staticmethod
    def calculate_metrics(image: np.ndarray) -> "PictureQuality":
        """
        Calculate objective metrics from a NumPy image array.
        """
        sharpness = PictureQuality._calculate_sharpness(image)
        edge_density = PictureQuality._calculate_edge_density(image)
        contrast = PictureQuality._calculate_contrast(image)
        brightness = PictureQuality._calculate_brightness(image)
        noise_level = PictureQuality._calculate_noise_level(image)
        return PictureQuality(
            sharpness=sharpness,
            edge_density=edge_density,
            contrast=contrast,
            brightness=brightness,
            noise_level=noise_level,
        )

    @staticmethod
    def _calculate_sharpness(image: np.ndarray) -> float:
        # Example: variance of Laplacian
        import cv2

        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        return min(laplacian_var / 1000.0, 1.0)

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
        # Simple noise estimate: difference between image and median-filtered image
        from scipy.ndimage import median_filter

        if image.ndim == 3:
            diff = np.abs(image - median_filter(image, size=(3, 3, 1)))
        else:
            diff = np.abs(image - median_filter(image, size=3))
        noise = diff.mean() / 255.0
        return min(noise, 1.0)

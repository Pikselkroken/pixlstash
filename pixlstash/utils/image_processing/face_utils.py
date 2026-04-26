"""Face cropping, detection helpers, and face-weighted thumbnail generation."""

import cv2
import numpy as np
from typing import List, Optional

from PIL import Image

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class FaceUtils:
    """Utility methods for face cropping, detection, and face-weighted thumbnails."""

    @staticmethod
    def crop_face_from_frame(frame, bbox):
        """
        Crop a face region from a video frame (numpy array) using bbox [x1, y1, x2, y2].

        Clamps bbox to frame bounds. Returns cropped region or None if invalid.
        """
        if frame is None or bbox is None or len(bbox) != 4:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        x1 = int(max(0, min(w - 1, round(x1))))
        y1 = int(max(0, min(h - 1, round(y1))))
        x2 = int(max(0, min(w, round(x2))))
        y2 = int(max(0, min(h, round(y2))))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or crop.shape[0] == 0 or crop.shape[1] == 0:
            return None
        return crop

    @staticmethod
    def generate_face_weighted_thumbnail_bytes(
        img,
        face_bboxes: List[List[int]],
        min_side: int = 256,
        output_size: tuple = (256, 256),
    ) -> Optional[bytes]:
        """
        Generate a face-weighted square thumbnail and return the bytes.

        Wraps :meth:`generate_face_weighted_thumbnail_with_crop` and discards
        the crop metadata.
        """
        thumbnail_bytes, _ = FaceUtils.generate_face_weighted_thumbnail_with_crop(
            img,
            face_bboxes,
            min_side=min_side,
            output_size=output_size,
        )
        return thumbnail_bytes

    @staticmethod
    def generate_face_weighted_thumbnail_with_crop(
        img,
        face_bboxes: List[List[int]],
        min_side: int = 256,
        output_size: tuple = (256, 256),
    ) -> tuple:
        """
        Generate a face-weighted square thumbnail, returning ``(bytes, crop_dict)``.

        The crop is centred on the area-weighted centroid of all face bounding boxes.
        Returns ``(None, None)`` on failure.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        if img is None or not face_bboxes:
            return None, None
        try:
            if isinstance(img, Image.Image):
                pil_img = img.copy()
            else:
                pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

            w, h = pil_img.size
            side_max = min(w, h)
            if side_max <= 0:
                return None, None

            clamped = []
            for bbox in face_bboxes:
                if not bbox or len(bbox) != 4:
                    continue
                x1, y1, x2, y2 = [int(round(v)) for v in bbox]
                x1 = max(0, min(w, x1))
                x2 = max(0, min(w, x2))
                y1 = max(0, min(h, y1))
                y2 = max(0, min(h, y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                area = float((x2 - x1) * (y2 - y1))
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                clamped.append((x1, y1, x2, y2, area, cx, cy))

            if not clamped:
                return None, None

            min_x = min(b[0] for b in clamped)
            min_y = min(b[1] for b in clamped)
            max_x = max(b[2] for b in clamped)
            max_y = max(b[3] for b in clamped)

            total_weight = sum(b[4] ** 0.5 for b in clamped)
            if total_weight > 0:
                cx = sum(b[4] ** 0.5 * b[5] for b in clamped) / total_weight
                cy = sum(b[4] ** 0.5 * b[6] for b in clamped) / total_weight
            else:
                cx = (min_x + max_x) / 2.0
                cy = (min_y + max_y) / 2.0

            side = side_max

            span_x = max_x - min_x
            span_y = max_y - min_y
            lower_left = max_x - side
            upper_left = min_x
            lower_top = max_y - side
            upper_top = min_y

            if span_x <= side:
                left = min(max(cx - side / 2.0, lower_left), upper_left)
            else:
                left = cx - side / 2.0
            if span_y <= side:
                top = min(max(cy - side / 2.0, lower_top), upper_top)
            else:
                top = cy - side / 2.0

            left = max(0, min(w - side, left))
            top = max(0, min(h - side, top))

            left = int(round(left))
            top = int(round(top))
            side = int(round(side))

            square_img = pil_img.crop((left, top, left + side, top + side))
            if output_size and square_img.size != output_size:
                square_img = square_img.resize(output_size, resample=Image.LANCZOS)
            crop = {
                "left": left,
                "top": top,
                "side": side,
            }
            thumbnail_bytes = ImageUtils._encode_thumbnail(square_img)
            if thumbnail_bytes is None:
                return None, None
            return thumbnail_bytes, crop
        except Exception as e:
            logger.error(f"Error generating face-weighted thumbnail: {e}")
            return None, None

    @staticmethod
    def load_and_crop_square_image_with_face(file_path, bbox):
        """
        Load an image or video file and return a square crop that always includes the
        face bbox.

        The crop is not tight to the face but is as large as possible while still
        containing it.

        Args:
            file_path: Path to image or video file.
            bbox: [x1, y1, x2, y2]
        """
        x1, y1, x2, y2 = [int(round(v)) for v in bbox]
        img = None
        try:
            img = Image.open(file_path)
        except Exception:
            img = None

        if img is None:
            try:
                cap = cv2.VideoCapture(file_path)
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    img = frame
            except Exception:
                img = None

        if img is None:
            return None

        if hasattr(img, "size") and callable(getattr(img, "crop", None)):
            w, h = img.size
            x1c = max(0, min(w, x1))
            x2c = max(0, min(w, x2))
            y1c = max(0, min(h, y1))
            y2c = max(0, min(h, y2))
            face_cx = (x1c + x2c) // 2
            face_cy = (y1c + y2c) // 2
            face_w = x2c - x1c
            face_h = y2c - y1c
            min_side = max(face_w, face_h)
            max_side = min(w, h)
            side = max(min_side, min(max_side, max(w, h)))
            left = max(0, min(w - side, face_cx - side // 2))
            top = max(0, min(h - side, face_cy - side // 2))
            return img.crop((left, top, left + side, top + side))
        h, w = img.shape[:2]
        x1c = max(0, min(w, x1))
        x2c = max(0, min(w, x2))
        y1c = max(0, min(h, y1))
        y2c = max(0, min(h, y2))
        face_cx = (x1c + x2c) // 2
        face_cy = (y1c + y2c) // 2
        face_w = x2c - x1c
        face_h = y2c - y1c
        min_side = max(face_w, face_h)
        max_side = min(w, h)
        side = max(min_side, min(max_side, max(w, h)))
        left = max(0, min(w - side, face_cx - side // 2))
        top = max(0, min(h - side, face_cy - side // 2))
        left = int(left)
        top = int(top)
        side = int(side)
        return img[top : top + side, left : left + side]

    @staticmethod
    def batch_facial_likeness(facial_features_list: list) -> np.ndarray:
        """
        Given a list of facial feature arrays (all same shape), compute a cosine
        similarity matrix.

        Each entry ``[i, j]`` is the cosine similarity between
        ``facial_features_list[i]`` and ``facial_features_list[j]``.

        Returns an ``(N, N)`` numpy array.
        """
        X = np.stack(facial_features_list, axis=0)
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        X_norm = X / (norms + 1e-8)
        return np.dot(X_norm, X_norm.T)

    @staticmethod
    def batch_face_likeness(face_crops: list) -> np.ndarray:
        """
        Given a list of lists of face crops (as numpy arrays), compute an
        ``(N, N)`` likeness matrix.

        Each entry ``[i, j]`` is the best cosine similarity between any crop in
        group ``i`` and any crop in group ``j``.
        """
        N = len(face_crops)
        likeness_matrix = np.zeros((N, N), dtype=np.float32)
        flat_crops = [
            [
                crop.flatten().astype(np.float32)
                / (np.linalg.norm(crop.flatten().astype(np.float32)) + 1e-8)
                for crop in crops
            ]
            for crops in face_crops
        ]
        for i in range(N):
            for j in range(N):
                if i == j or not flat_crops[i] or not flat_crops[j]:
                    likeness_matrix[i, j] = 0.0
                else:
                    sims = [
                        float(np.dot(c1, c2))
                        for c1 in flat_crops[i]
                        for c2 in flat_crops[j]
                    ]
                    likeness_matrix[i, j] = max(sims) if sims else 0.0
        return likeness_matrix

    @staticmethod
    def crop_face_bbox_exact(file_path, bbox):
        """
        Load an image or video file and return a crop exactly matching the face bbox
        as a PIL Image.

        Args:
            file_path: Path to image or video file.
            bbox: [x1, y1, x2, y2]

        Returns:
            Cropped PIL Image, or None on error.
        """
        x1, y1, x2, y2 = [int(round(v)) for v in bbox]
        img = None

        try:
            img = Image.open(file_path)
        except Exception:
            logger.error(f"Failed to open image for cropping: {file_path}")
            img = None

        if img is None:
            try:
                cap = cv2.VideoCapture(file_path)
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_rgb)
            except Exception:
                img = None

        if img is None:
            return None

        w, h = img.size
        x1c = max(0, min(w, x1))
        x2c = max(0, min(w, x2))
        y1c = max(0, min(h, y1))
        y2c = max(0, min(h, y2))
        return img.crop((x1c, y1c, x2c, y2c))

    @staticmethod
    def softmax_weighted_average(scores, alpha: float = 5.0) -> float:
        """
        Compute a softmax-weighted average of likeness scores.

        Args:
            scores: List or np.ndarray of likeness scores (floats between 0 and 1).
            alpha: Controls sharpness; higher alpha makes the max more dominant.

        Returns:
            Softmax-weighted average likeness as a float.
        """
        scores = np.array(scores)
        weights = np.exp(alpha * scores)
        return float(np.sum(weights * scores) / np.sum(weights))

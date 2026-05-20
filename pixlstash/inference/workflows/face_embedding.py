"""Face embedding workflow: CLIP-based facial feature extraction."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.face_utils import FaceUtils

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine

logger = get_logger(__name__)

_INSIGHTFACE_VRAM_MB = 400  # RetinaFace + ArcFace models via CUDA provider


class FaceEmbeddingWorkflow:
    """Extracts CLIP image embeddings for face crops.

    Given a picture file path and a list of bounding boxes, this workflow
    loads the image (or the first video frame), crops each face to a square
    centred on the bounding box, and returns a CLIP embedding per crop.

    Args:
        engine: The :class:`~pixlstash.inference.engine.InferenceEngine` that
            holds the already-constructed service instances.
    """

    def __init__(self, engine: "InferenceEngine") -> None:
        self._engine = engine

    def encode_face_crops(
        self,
        file_path: str,
        face_bboxes: list,
        description: str | None = None,
    ) -> list[np.ndarray | None]:
        """Generate CLIP embeddings for a list of face bounding boxes.

        Args:
            file_path: Absolute path to the image or video file.
            face_bboxes: Sequence of bounding boxes, each accepted by
                :meth:`FaceUtils.load_and_crop_square_image_with_face` or
                :meth:`FaceUtils.crop_face_from_frame`.
            description: Optional human-readable label used as a debugging hint
                during CLIP encoding.  Falls back to *file_path*.

        Returns:
            A list of ``np.ndarray`` embeddings (or ``None`` for failed crops)
            in the same order as *face_bboxes*.
        """
        import cv2
        from PIL import Image

        ext = os.path.splitext(file_path)[1].lower()
        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        face_crops: list = []

        if ext in video_exts:
            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            cap.release()
            frame = frame if ret else None
            for bbox in face_bboxes:
                if frame is not None:
                    crop = FaceUtils.crop_face_from_frame(frame, bbox)
                    if crop is not None and isinstance(crop, np.ndarray):
                        crop = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                    face_crops.append(crop)
                else:
                    face_crops.append(None)
        else:
            for bbox in face_bboxes:
                face_crops.append(
                    FaceUtils.load_and_crop_square_image_with_face(file_path, bbox)
                )

        pic_desc = description or file_path
        return self._engine.clip_service.encode_image_crops(
            face_crops, pic_desc=pic_desc
        )

    def estimated_vram_mb(self) -> int:
        """Flat VRAM estimate for face extraction (InsightFace model + inference)."""
        if self._engine.device != "cuda":
            return 0
        return _INSIGHTFACE_VRAM_MB

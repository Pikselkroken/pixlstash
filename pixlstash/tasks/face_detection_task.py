"""Lightweight interactive task for detecting faces in BGR images.

Unlike :class:`~pixlstash.tasks.face_extraction_task.FaceExtractionTask`,
which processes stored :class:`~pixlstash.db_models.Picture` objects in bulk,
this task accepts raw BGR numpy arrays and returns face-detection results
immediately.  It is designed for interactive search endpoints that need face
embeddings from uploaded query images without storing anything in the database.

It runs on the GPU queue at ``URGENT`` priority so it skips ahead of all
background batch work, and supports CPU spillover in the same way as the bulk
extractor.
"""

from __future__ import annotations

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class FaceDetectionTask(BaseTask):
    """Detect faces in a list of BGR images and return per-image results.

    Args:
        engine: :class:`~pixlstash.inference.engine.InferenceEngine` used to
            determine GPU vs CPU execution and model settings.
        bgr_images: List of BGR uint8 numpy arrays to run detection on.
    """

    def __init__(self, engine, bgr_images: list[np.ndarray]):
        super().__init__(
            task_type="FaceDetectionTask",
            params={"n_images": len(bgr_images)},
        )
        self._engine = engine
        self._bgr_images = bgr_images
        self._cpu_spillover_enabled = False

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.URGENT

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    def _run_task(self) -> list[list]:
        """Run face detection and return results.

        Returns:
            A list parallel to *bgr_images* where each element is itself a
            list of :class:`~pixlstash.tasks.face_extraction_task.FaceResult`
            objects (empty list when no face was detected in that image).
        """
        from pixlstash.tasks.face_extraction_task import FaceExtractionTask

        app = FaceExtractionTask.get_or_init_insightface(
            self._engine, cpu_spillover=self._cpu_spillover_enabled
        )
        return FaceExtractionTask.detect_faces_in_images(app, self._bgr_images)

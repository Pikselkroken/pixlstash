from __future__ import annotations

import os
import threading
import traceback
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlmodel import Session

from pixlstash.database import DBPriority
from pixlstash.db_models import Detection
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine


logger = get_logger(__name__)

# Still-image extensions Florence-2 detection supports. Videos are skipped in
# the MVP (phase 2 may sample frames, mirroring FaceExtractionTask).
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".avif"}


class DetectionTask(BaseTask):
    """User-triggered object detection over a batch of pictures.

    Mirrors :class:`~pixlstash.tasks.face_extraction_task.FaceExtractionTask`'s
    GPU-queue / high-priority shape, but runs Florence-2 grounding/OD and stores
    labelled boxes in the :class:`~pixlstash.db_models.detection.Detection`
    table. Unlike face extraction there is **no WorkFinder** — detection only
    runs when the user asks for it (the Segment action), so it is not part of
    the NULL-column reprocessing pattern.

    Args:
        database: Vault database instance.
        engine: :class:`~pixlstash.inference.engine.InferenceEngine` holding the
            shared Florence-2 service.
        pictures: Pictures included in this detection batch.
        prompt: Optional grounding phrase; empty → dense ``<OD>`` detection.
    """

    def __init__(
        self,
        database,
        engine: "InferenceEngine",
        pictures: list,
        prompt: str | None = None,
        origin_client_id: str | None = None,
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="DetectionTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
                "prompt": (prompt or "").strip(),
                # Echoed back in the completion event so the originating tab can
                # attribute the change to itself (no "view changed" pill).
                "origin_client_id": origin_client_id,
            },
        )
        self._db = database
        self._engine = engine
        self._pictures = pictures or []
        self._prompt = (prompt or "").strip()
        self._stop_event = threading.Event()
        # Live progress, read by Vault.get_worker_progress for the task manager.
        self._total_count = len(picture_ids)
        self._processed_count = 0

    @property
    def priority(self) -> TaskPriority:
        # User-initiated, so it should preempt background tagging/embeddings.
        return TaskPriority.HIGH

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def on_cancel(self) -> None:
        self._stop_event.set()

    def estimated_vram_mb(self) -> int:
        # Detection shares Florence-2 with captioning; reuse the description
        # workflow's VRAM estimate (model weights dominate; detection's extra
        # output tokens are negligible activation scratch).
        try:
            return max(
                0,
                self._engine.description_workflow.estimate_vram_mb(len(self._pictures)),
            )
        except Exception:
            return 0

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "picture_ids": []}

        # Resolve absolute paths for still images only (videos unsupported in MVP).
        path_to_pic: dict[str, int] = {}
        image_paths: list[str] = []
        for pic in self._pictures:
            if pic.id is None or not getattr(pic, "file_path", None):
                continue
            file_path = str(
                ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            )
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in _IMAGE_EXTS:
                logger.debug("DetectionTask: skipping non-image %s", file_path)
                continue
            image_paths.append(file_path)
            path_to_pic[file_path] = pic.id

        if not image_paths:
            return {"changed_count": 0, "picture_ids": []}

        # Only the still images actually drive progress (videos were skipped).
        self._total_count = len(image_paths)
        self._processed_count = 0

        # Process in modest chunks so VRAM stays bounded (detection decodes more
        # tokens than captioning) and the task manager shows incremental
        # progress instead of one all-or-nothing jump.
        try:
            chunk_size = max(
                1, min(16, int(self._engine.florence_service.description_batch_size()))
            )
        except Exception:
            chunk_size = 8

        source = "florence2:grounding" if self._prompt else "florence2:od"
        detections_by_path: dict = {}
        for i in range(0, len(image_paths), chunk_size):
            if self._stop_event.is_set():
                break
            chunk = image_paths[i : i + chunk_size]
            try:
                chunk_results = self._engine.detect_objects(chunk, prompt=self._prompt)
                detections_by_path.update(chunk_results or {})
            except Exception as exc:
                logger.error(
                    "DetectionTask chunk failed for ids=%s: %s\n%s",
                    [path_to_pic.get(p) for p in chunk],
                    exc,
                    traceback.format_exc(),
                )
            self._processed_count = min(
                self._total_count, self._processed_count + len(chunk)
            )

        # Build Detection rows per picture. A picture present in path_to_pic but
        # absent from the model output (or with no boxes) still gets an entry so
        # a re-run clears its prior rows even when nothing is detected.
        rows_by_pic: dict[int, list[Detection]] = {
            pid: [] for pid in path_to_pic.values()
        }
        for path, detections in detections_by_path.items():
            pic_id = path_to_pic.get(path)
            if pic_id is None:
                continue
            # Stable ordering (top-to-bottom, then left-to-right) for the index.
            ordered = sorted(detections, key=lambda d: (d[1][1], d[1][0]))
            rows_by_pic[pic_id] = [
                Detection(
                    picture_id=pic_id,
                    frame_index=0,
                    detection_index=det_index,
                    label=label,
                    bbox=bbox,
                    score=score,
                    source=source,
                )
                for det_index, (label, bbox, score) in enumerate(ordered)
            ]

        changed_ids = self._db.run_task(
            self._replace_detections, rows_by_pic, priority=DBPriority.HIGH
        )

        return {
            "changed_count": len(changed_ids or []),
            "picture_ids": changed_ids or [],
        }

    @staticmethod
    def _replace_detections(session: Session, rows_by_pic: dict) -> list:
        """Replace each picture's detection rows with the freshly-detected set."""
        changed_ids: list[int] = []
        for pic_id, rows in rows_by_pic.items():
            session.exec(delete(Detection).where(Detection.picture_id == pic_id))
            for row in rows:
                session.add(row)
            changed_ids.append(pic_id)
        session.commit()
        return changed_ids

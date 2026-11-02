"""Task that finds and embeds the faces in one training run's new previews (#2522).

Handed out by :class:`~pixlstash.tasks.training_preview_faces_finder.TrainingPreviewFacesFinder`;
the scoring against the character is done by the live view, from what this
writes (:mod:`pixlstash.training.preview_likeness`).
"""

from __future__ import annotations

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.training import preview_likeness
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.insightface_model_utils import DEFAULT_MODEL_PACK
from pixlstash.utils.vram_utils import is_vram_oom

logger = get_logger(__name__)


class TrainingPreviewFacesTask(BaseTask):
    """Embed the faces of a batch of one run's previews.

    Runs on the CPU queue with the CPU InsightFace app: the previews land while
    the trainer holds the GPU, and a few dozen small pictures do not need it.
    """

    def __init__(self, engine, run_uuid: str, work_dir: str, previews: list):
        """Bind the task to one run's previews.

        Args:
            engine: The vault's inference engine, for its InsightFace pack.
            run_uuid: The run's uuid, which the finder claims while this runs.
            work_dir: The run's work dir, holding its preview faces.
            previews: ``(filename, path)`` of each preview to look at.
        """
        super().__init__(
            task_type="TrainingPreviewFacesTask",
            params={"run_uuid": run_uuid, "previews": len(previews)},
        )
        self._engine = engine
        self._work_dir = work_dir
        self._previews = previews
        self._total_count = len(previews)
        self._processed_count = 0

    def _run_task(self) -> dict:
        model_pack = getattr(self._engine, "insightface_model_pack", DEFAULT_MODEL_PACK)
        app = FaceExtractionTask.get_or_init_insightface(
            self._engine, cpu_spillover=True
        )
        found: dict = {}
        for filename, path in self._previews:
            img, _ = ImageUtils.load_image_bgr_reduced(
                path, FaceExtractionTask.INFERENCE_MAX_SIDE
            )
            if img is None:
                # Kept as unreadable rather than "no face": it says nothing
                # about the step, and is not handed out again.
                logger.warning(
                    "Could not read training preview %s; it gets no likeness.", path
                )
                found[filename] = None
            else:
                try:
                    faces = FaceExtractionTask.detect_faces_in_images(app, [img])[0]
                except Exception as exc:
                    if is_vram_oom(exc):
                        raise
                    # One preview that breaks the detector is that preview's
                    # problem, not the rest of the run's.
                    logger.warning(
                        "Face detection failed on training preview %s; it gets "
                        "no likeness: %s",
                        path,
                        exc,
                    )
                    found[filename] = None
                else:
                    found[filename] = [
                        face.embedding.astype(np.float32)
                        for face in faces
                        if face.embedding is not None
                    ]
            self._processed_count += 1
        preview_likeness.write_faces(self._work_dir, model_pack, found)
        logger.info(
            "Looked for faces in %d preview(s) of training run %s: %d with a face.",
            len(found),
            self.params["run_uuid"],
            sum(1 for faces in found.values() if faces),
        )
        return {"previews": len(found)}

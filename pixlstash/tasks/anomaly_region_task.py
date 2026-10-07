"""Interactive task that runs the anomaly tagger's Grad-CAM for one picture.

``GET /pictures/{id}/anomaly_region`` used to load the tagger and run Grad-CAM
on its request thread, beside the GPU worker. On Metal that is two threads on
the device at once, which kills the process (``docs/apple-metal-thread-safety.md``).
On every host it also raced tag batches on the shared model: Grad-CAM casts that
model to fp32 in place for its backward pass, so a batch running at that moment
met fp32 weights. Running it here, on the single GPU worker, serialises it with
everything else that touches the device or the model.

It runs at ``URGENT`` priority, so it skips background batches and waits for
the task the worker is running plus any interactive work queued before it
(an interactive retag is ``URGENT`` too, and the queue is FIFO within a
priority).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from PIL import Image

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.pixlstash_tagger import UnknownAnomalyLabel
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class AnomalyRegionOutcome(str, Enum):
    """How an :class:`AnomalyRegionTask` ended, for the route to map to a status."""

    OK = "ok"
    TAGGER_UNAVAILABLE = "tagger_unavailable"
    UNKNOWN_LABEL = "unknown_label"


@dataclass
class AnomalyRegionResult:
    """What an :class:`AnomalyRegionTask` returns.

    Attributes:
        outcome: Whether the region was computed, and if not, why.
        region: ``{"boxes", "diffuse", "heatmap"}`` when ``outcome`` is ``OK``.
        version: The tagger version read after the load, for the cache key.
    """

    outcome: AnomalyRegionOutcome
    region: Optional[dict] = None
    version: int = 0


class AnomalyRegionTask(BaseTask):
    """Load the anomaly tagger if needed and localise one label in one image.

    Args:
        engine: :class:`~pixlstash.inference.engine.InferenceEngine` holding the
            tagger service.
        rgb_image: The decoded picture, or ``None`` when it is not a still image
            PIL can decode. The label is still validated then, and the region
            comes back diffuse.
        label: The anomaly tag to localise.
    """

    def __init__(self, engine, rgb_image: Optional[Image.Image], label: str):
        super().__init__(task_type="AnomalyRegionTask", params={"label": label})
        self._engine = engine
        self._rgb_image = rgb_image
        self._label = label

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.URGENT

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def _run_task(self) -> AnomalyRegionResult:
        service = self._engine.pixlstash_tagger_service
        # The tagger is idle-unloaded between tagging runs, so during review it
        # is usually not resident. Loading it here keeps the load on the worker.
        if not service.is_loaded():
            ensure = getattr(self._engine, "ensure_pixlstash_tagger_ready", None)
            if ensure is None or not ensure():
                logger.warning(
                    "anomaly_region: the anomaly tagger could not be loaded for "
                    "label '%s'",
                    self._label,
                )
                return AnomalyRegionResult(AnomalyRegionOutcome.TAGGER_UNAVAILABLE)

        version = int(service.version())
        if service.resolve_label_index(self._label) is None:
            return AnomalyRegionResult(
                AnomalyRegionOutcome.UNKNOWN_LABEL, version=version
            )
        if self._rgb_image is None:
            return AnomalyRegionResult(
                AnomalyRegionOutcome.OK,
                {"boxes": [], "diffuse": True, "heatmap": None},
                version,
            )
        try:
            region = service.localize_anomaly(self._rgb_image, self._label)
        except UnknownAnomalyLabel:
            # Defensive: resolve_label_index already validated above.
            return AnomalyRegionResult(
                AnomalyRegionOutcome.UNKNOWN_LABEL, version=version
            )
        return AnomalyRegionResult(AnomalyRegionOutcome.OK, region, version)

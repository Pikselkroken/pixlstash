"""Background rebuild of the tag_health cache table.

User-triggered from ``POST /tag_health/rebuild``; runs on the shared task
runner's CPU queue so the request returns immediately while the board shows a
progress bar (tags processed / total, published via
:func:`pixlstash.services.tag_health_service.get_status`).
"""

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class TagHealthRebuildTask(BaseTask):
    """Recompute every tag's health signals and replace the cache rows.

    Pure SQL aggregation (no models, no embeddings) - CPU queue. HIGH priority:
    user-triggered, but a batch job that shouldn't preempt interactive tasks.
    """

    def __init__(self, vault):
        super().__init__(task_type="TagHealthRebuildTask", params={})
        self._vault = vault

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

    @property
    def queue_type(self) -> QueueType:
        return QueueType.CPU

    def _run_task(self) -> dict:
        # Late import to avoid a service<->task import cycle at module load.
        from pixlstash.services import tag_health_service

        result = tag_health_service._run_rebuild_guarded(self._vault)
        logger.info("tag_health rebuild completed: %s tags", result.get("tags"))
        return result

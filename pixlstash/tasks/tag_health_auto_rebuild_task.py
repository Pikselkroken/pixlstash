"""Dispatcher task: kicks a tag_health rebuild via the safe, idempotent path.

Created by :class:`~pixlstash.tasks.tag_health_auto_rebuild_finder.
TagHealthAutoRebuildFinder` when ``GET /tag_health`` would report
``stale=true`` and no rebuild is already running (Spec B,
``docs/reviews/tag-review-board-redesign-ux-spec.md`` §4). Delegates to
:func:`pixlstash.services.tag_health_service.start_rebuild` - the exact same
lock-protected, idempotent entry point ``POST /tag_health/rebuild`` uses - so
an auto-trigger racing a concurrent manual click can never double-rebuild.
This task's own body is a thin dispatch (submit the real
:class:`~pixlstash.tasks.tag_health_rebuild_task.TagHealthRebuildTask` and
return its status); the actual aggregate recompute runs there, not here.
"""

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class TagHealthAutoRebuildTask(BaseTask):
    """Trigger a tag_health rebuild through ``tag_health_service.start_rebuild``.

    LOW priority and CPU queue: this task itself does no aggregation work, it
    only submits ``TagHealthRebuildTask`` (which runs at HIGH priority, same
    as a manual click) and returns immediately.

    Attributes:
        _vault: The owning Vault, used to reach ``tag_health_service``.
    """

    def __init__(self, vault) -> None:
        """Initialise the task.

        Args:
            vault: The owning Vault instance.
        """
        super().__init__(task_type="TagHealthAutoRebuildTask", params={})
        self._vault = vault

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def queue_type(self) -> QueueType:
        return QueueType.CPU

    def _run_task(self) -> dict:
        # Late import to avoid a service<->task import cycle at module load
        # (same reasoning as TagHealthRebuildTask).
        from pixlstash.services import tag_health_service

        status = tag_health_service.start_rebuild(self._vault)
        logger.info(
            "TagHealthAutoRebuildTask: dispatched rebuild (building=%s)",
            status.get("building"),
        )
        return status

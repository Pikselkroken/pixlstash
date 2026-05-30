"""One-shot task that takes a DAILY snapshot for today's slot."""

from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class EnsureDailySnapshotTask(BaseTask):
    """Create a DAILY snapshot for the current calendar date.

    This task is created by ``EnsureDailySnapshotFinder`` and runs at LOW
    priority so it does not starve other background work.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the task.

        Args:
            vault: The owning Vault, used to access SnapshotService.
        """
        super().__init__(task_type="EnsureDailySnapshotTask")
        self._vault = vault

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        try:
            cp = self._vault.snapshot_service.create_snapshot("DAILY")
            logger.info("EnsureDailySnapshotTask: DAILY snapshot id=%d created", cp.id)
            return {"snapshot_id": cp.id}
        except Exception as exc:
            logger.error(
                "EnsureDailySnapshotTask: failed to create DAILY snapshot: %s",
                exc,
                exc_info=True,
            )
            raise

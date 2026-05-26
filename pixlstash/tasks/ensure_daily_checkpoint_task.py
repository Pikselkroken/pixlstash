"""One-shot task that takes a DAILY checkpoint for today's slot."""

from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class EnsureDailyCheckpointTask(BaseTask):
    """Create a DAILY checkpoint for the current calendar date.

    This task is created by ``EnsureDailyCheckpointFinder`` and runs at LOW
    priority so it does not starve other background work.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the task.

        Args:
            vault: The owning Vault, used to access CheckpointService.
        """
        super().__init__(task_type="EnsureDailyCheckpointTask")
        self._vault = vault

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        try:
            cp = self._vault.checkpoint_service.create_checkpoint("DAILY")
            logger.info(
                "EnsureDailyCheckpointTask: DAILY checkpoint id=%d created", cp.id
            )
            return {"checkpoint_id": cp.id}
        except Exception as exc:
            logger.error(
                "EnsureDailyCheckpointTask: failed to create DAILY checkpoint: %s",
                exc,
                exc_info=True,
            )
            raise

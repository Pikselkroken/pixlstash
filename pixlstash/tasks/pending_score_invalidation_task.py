"""Apply smart-score invalidations that were recorded but not yet applied."""

from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.service.smart_score_invalidation import (
    apply_pending_invalidations,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class PendingScoreInvalidationTask(BaseTask):
    """Drain ``pending_score_invalidation`` in one vault transaction.

    Created by :class:`~pixlstash.tasks.pending_score_invalidation_finder.PendingScoreInvalidationFinder`.
    Normally there is nothing to do: the settings handler applies its own
    invalidation immediately after recording it. This exists for the case that
    handler cannot cover, where the process died between the record and the
    application, and for a failure that left the record behind.

    Runs at HIGH priority despite being background work, because until it runs
    the affected pictures carry scores computed under the old weights and the
    grid sorts by them.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the task.

        Args:
            vault: The owning Vault, whose database holds the pending records.
        """
        super().__init__(task_type="PendingScoreInvalidationTask")
        self._vault = vault

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

    def _run_task(self):
        cleared = self._vault.db.run_task(apply_pending_invalidations)
        if cleared:
            logger.info(
                "PendingScoreInvalidationTask: cleared %d stale smart score(s)",
                cleared,
            )
        return {"cleared": cleared}

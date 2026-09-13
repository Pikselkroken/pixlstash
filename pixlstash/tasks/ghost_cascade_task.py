"""Settle queued instance hashes against the hub's picture ghosts."""

from typing import TYPE_CHECKING

from pixlstash.services.workflow_ghost_service import drain_ghost_cascade
from pixlstash.tasks.base_task import BaseTask, TaskPriority

if TYPE_CHECKING:
    from pixlstash.vault import Vault


class GhostCascadeTask(BaseTask):
    """Run one batch of the covered-ghost cascade.

    Created by :class:`~pixlstash.tasks.ghost_cascade_finder.GhostCascadeFinder`.
    HIGH priority: until it runs, a ghost kept only because a picture covered it
    can outlive that picture, which is the privacy promise ``covered`` makes.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the task.

        Args:
            vault: The owning Vault.
        """
        super().__init__(task_type="GhostCascadeTask")
        self._vault = vault

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

    def _run_task(self):
        evaluated, destroyed = drain_ghost_cascade(
            self._vault.db,
            self._vault.hub,
            self._vault.library_uuid,
            self._vault.ghost_retention,
        )
        return {"evaluated": evaluated, "destroyed": destroyed}

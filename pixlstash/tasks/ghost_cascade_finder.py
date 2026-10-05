"""Find instance hashes picture rows gave up, for the covered-ghost cascade."""

from typing import TYPE_CHECKING

from sqlalchemy import text

from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.ghost_cascade_task import GhostCascadeTask

if TYPE_CHECKING:
    from pixlstash.vault import Vault

# How often to look. This is the normal path for every hard delete except the
# scrapheap purge, so an uncovered ghost outlives its cover by about this long
# plus the time the task takes to drain the whole queue.
# The probe is one indexed read of a table that is almost always empty.
_CHECK_INTERVAL_S: float = 10.0


class GhostCascadeFinder(BaseTaskFinder):
    """Queue a drain whenever ``pending_ghost_cascade`` holds anything.

    The vault's triggers fill the queue on every picture delete and re-hash,
    whatever path issued it; see
    :func:`~pixlstash.services.workflow_ghost_service.drain_ghost_cascade`.
    Registered only on a vault opened through a hub, because ghosts live in the
    hub. A hubless vault keeps its queue for the next time a hub opens it.

    Attributes:
        _vault: The owning Vault.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault, for its database, hub, library and the
                current retention position.
        """
        super().__init__()
        self._vault = vault

    def finder_name(self) -> str:
        return "GhostCascadeFinder"

    def find_task(self):
        if not self._due(_CHECK_INTERVAL_S):
            return None

        outstanding = self._vault.db.run_immediate_read_task(
            lambda session: session.execute(
                text("SELECT 1 FROM pending_ghost_cascade LIMIT 1")
            ).first()
        )
        if outstanding is None:
            return None
        return GhostCascadeTask(vault=self._vault)

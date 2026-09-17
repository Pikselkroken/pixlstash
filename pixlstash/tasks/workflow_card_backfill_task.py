"""Give the variants already in the hub the card they belong to (v1.12 B2).

**The hub alone.** Every input is a stored document, so this reads no picture,
opens no image file and needs no library to be attached. That matters twice: a
rescan of a library the owner has since trimmed could not recover a recipe whose
pictures are gone, and a pass over the pictures would cost hours where this
costs one reduction per recipe.

Re-runnable by construction. :func:`~pixlstash.hub.workflow_cards.record_identity`
is keyed by content and every write is a REPLACE of the identical row or an
IGNORE, so a second pass over the same hub leaves the same rows.

The owner gate's grouping report is the finder's
(``on_all_tasks_complete``), not this task's: it is one number for the whole
hub, and computing it per batch is a full scan and a log line per fifty
variants for a figure that is only true once the drain is over.
"""

from __future__ import annotations

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_cards import record_identity
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import WorkflowGraphError
from pixlstash.tasks.base_task import BaseTask, TaskPriority

logger = get_logger(__name__)


class WorkflowCardBackfillTask(BaseTask):
    """Derive the card for a batch of filed variants."""

    BATCH_SIZE = 50

    def __init__(self, hub: HubDatabase, structural_hashes: list[str]):
        """Initialise the task.

        Args:
            hub: The hub database holding the workflow tables.
            structural_hashes: The variants to key, from the finder.
        """
        super().__init__(
            task_type="WorkflowCardBackfillTask",
            params={"structural_hashes": list(structural_hashes)},
        )
        self._hub = hub
        self._structural_hashes = list(structural_hashes)

    @property
    def priority(self) -> TaskPriority:
        """LOW: the Workflows view is the better for it, nothing waits on it."""
        return TaskPriority.LOW

    def _run_task(self):
        identified = 0
        deferred: list[str] = []
        for structural_hash in self._structural_hashes:
            try:
                key = record_identity(self._hub, structural_hash)
            except WorkflowGraphError as exc:
                # A stored document that will not reduce, or one with nothing
                # left once the stack strip runs. Deferred rather than retried:
                # the document does not change by itself, so the finder would
                # otherwise hand it back on every sweep forever. Narrow on
                # purpose - anything else (a locked hub) is the batch's error
                # and stays retryable.
                logger.warning(
                    "Workflow variant %s gets no card, its stored document "
                    "cannot be keyed: %s",
                    structural_hash,
                    exc,
                )
                deferred.append(structural_hash)
                continue
            if key is None:
                deferred.append(structural_hash)
            else:
                identified += 1
        return {"identified": identified, "deferred": deferred}

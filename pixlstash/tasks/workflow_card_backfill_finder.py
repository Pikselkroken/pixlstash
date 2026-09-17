"""Finder for filed workflow variants that have no card yet."""

from __future__ import annotations

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_cards import unidentified_variants, variant_counts
from pixlstash.pixl_logging import get_logger
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.workflow_card_backfill_task import WorkflowCardBackfillTask

logger = get_logger(__name__)


class WorkflowCardBackfillFinder(BaseTaskFinder):
    """Hand out variants whose card is missing or keyed by a superseded rule.

    This is the whole once-only-ness of the backfill, and it is why the pass
    needs no version counter of its own: work exists exactly while a stored
    document has no current card, so the first pass drains it, a re-run finds
    nothing, and a later ``WORKFLOW_KEY_VERSION`` or ``CORE_VERSION`` bump
    re-fills it without anybody remembering to run anything. A
    ``CURRENT_DATA_VERSION`` step would be the wrong shape twice over: it cannot
    see the recipes that arrive after it ran, and it would be skipped on the one
    hub that needed it most - the one whose first pass was interrupted.

    A variant the task could not key is *deferred* for the life of the process
    rather than handed out again: a stored document does not change by itself,
    so without that a single unkeyable graph keeps the planner awake forever.
    """

    def __init__(self, hub: HubDatabase) -> None:
        """Initialise the finder.

        Args:
            hub: The hub database holding the workflow tables.
        """
        super().__init__()
        self._hub = hub
        self._deferred: set[str] = set()
        # Handed out and not yet reported. The planner frees the inflight slot
        # before it tells the finder how the task went, so without this the
        # identical batch can be issued twice (see MissingCheckpointHashFinder).
        self._handed_out: set[str] = set()

    def finder_name(self) -> str:
        return "WorkflowCardBackfillFinder"

    def progress(self) -> tuple[int, int]:
        """``(variants with a stored document, how many still need a card)``.

        Deferred variants still count as pending: they are work this session
        refuses to retry, not work that finished.
        """
        return variant_counts(self._hub)

    def find_task(self):
        skip = self._deferred | self._handed_out
        batch = [
            structural_hash
            for structural_hash in unidentified_variants(
                self._hub, WorkflowCardBackfillTask.BATCH_SIZE + len(skip)
            )
            if structural_hash not in skip
        ][: WorkflowCardBackfillTask.BATCH_SIZE]
        if not batch:
            return None
        self._handed_out.update(batch)
        return WorkflowCardBackfillTask(hub=self._hub, structural_hashes=batch)

    def on_task_complete(self, task, error) -> None:
        """Record which variants must not be handed out again this session."""
        hashes = (getattr(task, "params", None) or {}).get("structural_hashes") or []
        self._handed_out.difference_update(hashes)
        if isinstance(error, TaskCancelledError):
            # Never ran, so nothing was learned about these rows: a planner stop
            # or a queue drain must not strand them.
            logger.debug(
                "Workflow card backfill was cancelled before it ran: %s. Those "
                "variants stay eligible.",
                error,
            )
            return
        if error is not None:
            logger.warning(
                "Workflow card backfill failed for %d variants: %s. Deferring "
                "them for the rest of this session.",
                len(hashes),
                error,
            )
            self._deferred.update(hashes)
            return
        self._deferred.update((getattr(task, "result", None) or {}).get("deferred", []))

"""Offers a pull of ComfyUI's saved workflows at most once a minute.

The finder decides only *whether*: a ComfyUI address is saved, the owner's
"Pull workflows from ComfyUI" setting is on, and no pull is queued or running
(:class:`~pixlstash.services.comfyui_workflow_pulls.WorkflowPulls` holds that
gate for the poll, the Link and the route alike). The pull itself makes a quiet
poll one request: an unchanged file is not read.
"""

from __future__ import annotations

from pixlstash.pixl_logging import get_logger
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseTaskFinder

logger = get_logger(__name__)

# How often the poll asks ComfyUI while it answers.
POLL_INTERVAL_S: float = 60.0

# How often it asks after a pull failed (ComfyUI not running is the usual
# reason), or stopped at its write budget: each logs, and a minute of those is
# noise - and a ComfyUI that fills the budget every minute fills the hub.
FAILED_POLL_INTERVAL_S: float = 600.0


class ComfyUIWorkflowPollFinder(BaseTaskFinder):
    """Hand the planner a background pull when one is due."""

    def __init__(self, pulls) -> None:
        """Initialise the finder.

        Args:
            pulls: The server's
                :class:`~pixlstash.services.comfyui_workflow_pulls.WorkflowPulls`.
        """
        super().__init__()
        self._pulls = pulls
        self._interval_s = POLL_INTERVAL_S

    def finder_name(self) -> str:
        return "ComfyUIWorkflowPollFinder"

    def find_task(self):
        if not self._due(self._interval_s):
            return None
        comfyui_url = self._pulls.owner_wants_pulls()
        if comfyui_url is None:
            return None
        task, new = self._pulls.claim(comfyui_url, background=True)
        return task if new else None

    def on_task_complete(self, task, error) -> None:
        """Slow down while pulls fail, stop at their budget or meet the card
        cap, and free a gate a dropped task held."""
        if isinstance(error, TaskCancelledError):
            # Dropped before it ran (the planner stopping): it never will.
            self._pulls.release(task)
            return
        result = getattr(task, "result", None)
        stopped = isinstance(result, dict) and (
            result.get("budget_exhausted") or result.get("card_cap_reached")
        )
        self._interval_s = (
            POLL_INTERVAL_S if error is None and not stopped else FAILED_POLL_INTERVAL_S
        )

"""Finder for saved recipes that name a card and no workflow yet (#1623)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from pixlstash.pixl_logging import get_logger
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.saved_recipe_convert_task import SavedRecipeConvertTask

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class MissingSavedRecipeWorkflowFinder(BaseTaskFinder):
    """Hand out ``saved_recipe`` rows whose ``workflow_id`` is NULL.

    Registered only on a vault opened through a hub: the successor rows it
    reads live there. A recipe with no successor is *deferred* for the life of
    the process rather than handed out again, as
    ``WorkflowCardBackfillFinder`` defers a graph that will not key: nothing
    changes it until the hub does, and a restart retries it.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault, for its database and its hub.
        """
        super().__init__()
        self._vault = vault
        self._deferred: set[int] = set()
        # Handed out and not yet reported: the planner frees the inflight slot
        # before it reports, so without this one batch could go out twice.
        self._handed_out: set[int] = set()

    def finder_name(self) -> str:
        return "MissingSavedRecipeWorkflowFinder"

    def find_task(self):
        skip = self._deferred | self._handed_out
        limit = SavedRecipeConvertTask.BATCH_SIZE + len(skip)
        ids = self._vault.db.run_immediate_read_task(
            lambda session: [
                row[0]
                for row in session.execute(
                    text(
                        "SELECT id FROM saved_recipe WHERE workflow_id IS NULL "
                        "ORDER BY id LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ]
        )
        batch = [i for i in ids if i not in skip][: SavedRecipeConvertTask.BATCH_SIZE]
        if not batch:
            return None
        self._handed_out.update(batch)
        return SavedRecipeConvertTask(vault=self._vault, recipe_ids=batch)

    def on_task_complete(self, task, error) -> None:
        """Record which recipes must not be handed out again this session."""
        ids = (getattr(task, "params", None) or {}).get("recipe_ids") or []
        self._handed_out.difference_update(ids)
        if isinstance(
            error,
            (TaskCancelledError, sqlite3.OperationalError, SQLAlchemyOperationalError),
        ):
            # Never ran, or a busy hub (sqlite3) or vault (through SQLAlchemy):
            # nothing was learned, so they stay eligible.
            logger.info(
                "Saved-recipe conversion did not run for %d recipe(s): %s. They "
                "stay eligible.",
                len(ids),
                error,
            )
            return
        if error is not None:
            logger.warning(
                "Saved-recipe conversion failed for %d recipe(s): %s. Deferring "
                "them for the rest of this session.",
                len(ids),
                error,
            )
            self._deferred.update(ids)
            return
        self._deferred.update((getattr(task, "result", None) or {}).get("deferred", []))

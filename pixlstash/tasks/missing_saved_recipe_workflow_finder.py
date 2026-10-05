"""Finder for saved recipes that name a card and no workflow yet (#1623), or a
workflow that has since been retired (``workflow_id_successor``), or a card
moved out of a workflow that lives on (``workflow_card_move``)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from pixlstash.hub.workflow_card_reads import (
    moved_card_workflows,
    workflow_id_successors,
)
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseDeferringFinder
from pixlstash.tasks.saved_recipe_convert_task import (
    PENDING_RECIPE_WHERE,
    SavedRecipeConvertTask,
    pending_recipe_binds,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault


class MissingSavedRecipeWorkflowFinder(BaseDeferringFinder):
    """Hand out ``saved_recipe`` rows whose ``workflow_id`` is NULL.

    Registered only on a vault opened through a hub: the successor rows it
    reads live there. A recipe with no successor is *deferred* for the life of
    the process rather than handed out again, as
    ``WorkflowCardBackfillFinder`` defers a graph that will not key: nothing
    changes it until the hub does, and a restart retries it.
    """

    _IDS_PARAM = "recipe_ids"
    # Never ran, or a busy hub (sqlite3) or vault (through SQLAlchemy): nothing
    # was learned, so they stay eligible.
    _TRANSIENT = (
        TaskCancelledError,
        sqlite3.OperationalError,
        SQLAlchemyOperationalError,
    )
    _WHAT = "Saved-recipe conversion"

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault, for its database and its hub.
        """
        super().__init__()
        self._vault = vault

    def finder_name(self) -> str:
        return "MissingSavedRecipeWorkflowFinder"

    def find_task(self):
        binds = pending_recipe_binds(
            workflow_id_successors(self._vault.hub),
            moved_card_workflows(self._vault.hub),
        )
        batch = self._take(
            lambda limit: self._vault.db.run_immediate_read_task(
                lambda session: [
                    row[0]
                    for row in session.execute(
                        text(
                            f"SELECT id FROM saved_recipe WHERE {PENDING_RECIPE_WHERE} "
                            "ORDER BY id LIMIT :limit"
                        ),
                        {**binds, "limit": limit},
                    )
                ]
            ),
            SavedRecipeConvertTask.BATCH_SIZE,
        )
        if not batch:
            return None
        return SavedRecipeConvertTask(vault=self._vault, recipe_ids=batch)

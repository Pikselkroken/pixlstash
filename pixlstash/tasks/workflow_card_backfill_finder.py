"""Finder for filed workflow variants that have no card yet."""

from __future__ import annotations

import sqlite3
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_cards import (
    card_grouping,
    shelf_family_signature,
    unidentified_variants,
    variant_counts,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_events import announce_to_vault
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.base_task_finder import BaseDeferringFinder
from pixlstash.tasks.workflow_card_backfill_task import (
    FamilyReidentifyTask,
    WorkflowCardBackfillTask,
)

logger = get_logger(__name__)


class WorkflowCardBackfillFinder(BaseDeferringFinder):
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
    Cancelled or a busy hub stays eligible: the hub is shared with a second
    process under a 5 s timeout, and deriving a card is cheap to retry, so
    retiring fifty variants until the next restart over a lock is the wrong
    trade - unlike the checkpoint hasher, where a retry is 24 GB of reading.
    """

    _IDS_PARAM = "structural_hashes"
    _TRANSIENT = (TaskCancelledError, sqlite3.OperationalError)
    _WHAT = "Workflow card backfill"

    def __init__(self, hub: HubDatabase, vault=None) -> None:
        """Initialise the finder.

        Args:
            hub: The hub database holding the workflow tables.
            vault: Where a family pass that retired workflow ids announces
                them (``workflows_changed``, reason ``regrouped``). ``None``
                announces nothing.
        """
        super().__init__()
        self._hub = hub
        self._vault = vault
        # The shelf's base models (and the known families of cores holding an
        # unresolved variant) as of the last family pass: only a change can
        # identify an unknown family. Persisted in the hub, so a start (or a
        # library switch) with an unchanged shelf runs nothing; None while an
        # unknown family is filed and the shelf moved since the last pass, so
        # a change the last session never passed over is not lost with it.
        current = shelf_family_signature(hub)
        unknown = hub.fetchone(
            "SELECT 1 FROM workflow_variant_family WHERE families LIKE '%asset:%' "
            "OR families LIKE '%unresolved%' LIMIT 1"
        )
        passed = hub.fetchone("SELECT signature FROM workflow_family_pass")
        self._family_signature: Optional[str] = (
            None if unknown and (passed is None or passed[0] != current) else current
        )

    def finder_name(self) -> str:
        return "WorkflowCardBackfillFinder"

    def progress(self) -> tuple[int, int]:
        """``(variants with a stored document, how many still need a card)``.

        Deferred variants still count as pending: they are work this session
        refuses to retry, not work that finished.
        """
        return variant_counts(self._hub)

    def find_task(self):
        batch = self._take(
            lambda limit: unidentified_variants(self._hub, limit),
            WorkflowCardBackfillTask.BATCH_SIZE,
        )
        if not batch:
            return self._reidentify_task()
        return WorkflowCardBackfillTask(hub=self._hub, structural_hashes=batch)

    def _reidentify_task(self):
        """A pass moving unknown base-model families that are now known.

        Asked only once the backfill is drained, and only when the shelf's
        base models changed since the last pass (a scan or the owner setting a
        base model), or a core holding an unresolved variant gained a known
        family set (``shelf_family_signature``).
        """
        signature = shelf_family_signature(self._hub)
        if signature == self._family_signature:
            return None
        self._family_signature = signature
        return FamilyReidentifyTask(hub=self._hub, signature=signature)

    def on_all_tasks_complete(self) -> None:
        """Report the grouping once the hub is drained: the owner gate reads it.

        Here rather than in the task, because it is one number for the whole hub
        and a per-batch report is a full scan and a log line per fifty variants
        for a figure that is only true at the end.
        """
        grouping = card_grouping(self._hub)
        if not grouping["variants"]:
            return
        logger.info(
            "Workflow cards: %d variants over %d topologies make %d cards, "
            "%d automatic stacks holding %d of them, %d one-offs and %d not "
            "grouped yet. If the stacks swallow everything, "
            "STRIP_LORAS_FOR_STACKS is the default to flip.",
            grouping["variants"],
            grouping["topologies"],
            grouping["cards"],
            grouping["stacks"],
            grouping["stacked_cards"],
            grouping["one_offs"],
            grouping["ungrouped"],
        )

    def on_task_complete(self, task, error) -> None:
        """Record which variants must not be handed out again this session."""
        if isinstance(task, FamilyReidentifyTask) and error is None:
            self._family_pass_done(task)
            return
        if isinstance(task, FamilyReidentifyTask):
            if isinstance(error, (TaskCancelledError, sqlite3.OperationalError)):
                # Never ran, or a busy hub: nothing was learned, so the next
                # sweep asks again.
                logger.warning("Base-model family pass did not run: %s", error)
                self._family_signature = None
                return
            # A real failure keeps the signature `_reidentify_task` set, so
            # the pass is asked again only once the shelf changes: handing it
            # out every sweep would keep the planner awake forever.
            logger.warning(
                "Base-model family pass failed: %s. It runs again when the "
                "shelf's base models change.",
                error,
            )
            return
        super().on_task_complete(task, error)

    def _family_pass_done(self, task: FamilyReidentifyTask) -> None:
        """Remember the shelf the pass ran against, and announce what it moved."""
        signature = task.params.get("signature")
        if signature is not None:
            with self._hub.transaction() as conn:
                conn.execute("DELETE FROM workflow_family_pass")
                conn.execute(
                    "INSERT INTO workflow_family_pass (signature) VALUES (?)",
                    (signature,),
                )
        result = getattr(task, "result", None) or {}
        if not result.get("moved") or self._vault is None:
            return
        # Retired ids are gone from GET /workflows, so a tab holding one (a
        # selection, an open inspector) needs to be told where it went.
        announce_to_vault(
            self._vault,
            result.get("keys") or [],
            "regrouped",
            renamed=result.get("renamed") or {},
        )

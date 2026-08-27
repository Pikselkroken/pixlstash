"""Scheduled task that permanently purges scrapheap pictures past their deadline.

Created by :class:`~pixlstash.tasks.scrapheap_retention_purge_finder.ScrapheapRetentionPurgeFinder`,
which has already computed the due ids from ``deleted_at`` + the configured
retention window (including the reduction grace floor). This task hands those ids
to the ONE destruction path,
:func:`pixlstash.services.scrapheap_service.purge_scrapheap_pictures`, with
``include_protected=False`` - so a protected reference-folder original can never
be destroyed by a timer, only by an explicit human confirmation - together with a
:class:`~pixlstash.services.scrapheap_service.RetentionGuard` that re-checks the
deadline and the locked-set freeze against current state.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import scrapheap_service
from pixlstash.tasks.base_task import BaseTask, TaskPriority

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class ScrapheapRetentionPurgeTask(BaseTask):
    """Purge one batch of retention-expired, UNPROTECTED scrapheap pictures.

    Attributes:
        _vault: The owning Vault (DB work-queue, image root, event bus).
        _picture_ids: Ids the finder determined are past their purge deadline.
    """

    # Cap on ids per task so one sweep cannot monopolise the DB queue; the
    # finder simply schedules another task on its next cycle.
    BATCH_SIZE = 200

    def __init__(self, vault: "Vault", picture_ids: list[int]) -> None:
        """Initialise the task.

        Args:
            vault: The owning Vault.
            picture_ids: Picture ids that are past their retention deadline.
        """
        ids = [int(pid) for pid in (picture_ids or []) if pid is not None]
        super().__init__(
            task_type="ScrapheapRetentionPurgeTask",
            params={"picture_ids": ids, "batch_size": len(ids)},
        )
        self._vault = vault
        self._picture_ids = ids

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        if not self._picture_ids:
            return {"purged": 0, "skipped": 0, "skipped_locked": 0, "retained": 0}

        logger.info(
            "ScrapheapRetentionPurgeTask: auto-purging %d retention-expired "
            "picture(s): %s",
            len(self._picture_ids),
            self._picture_ids,
        )
        # Second-layer deadline guard, re-evaluated HERE rather than reusing the
        # finder's verdict. This task runs at LOW priority and may sit queued
        # behind other work, so a picture restored and re-deleted in the meantime
        # would carry a brand-new deleted_at; recomputing the deadline against
        # current state is what stops that TOCTOU from destroying an in-window
        # picture. (The locked-set freeze is enforced unconditionally inside the
        # purge plan, on every path, so it is not part of this guard.)
        guard = scrapheap_service.RetentionGuard(
            now=datetime.now(timezone.utc),
            retention_days=self._vault.scrapheap_retention_days,
            reduced_at=self._vault.scrapheap_retention_reduced_at,
        )
        outcome = scrapheap_service.purge_scrapheap_pictures(
            self._vault,
            self._picture_ids,
            # NEVER true here. The retention timer governs unprotected (managed)
            # pictures only; protected reference originals are exempt and are
            # destroyed solely by the consent-gated manual delete-forever.
            include_protected=False,
            retention_guard=guard,
        )
        if outcome.skipped_locked:
            logger.info(
                "ScrapheapRetentionPurgeTask: skipped %d picture(s) frozen by a "
                "locked picture-set: %s",
                len(outcome.skipped_locked),
                outcome.skipped_locked,
            )
        if outcome.retained_count:
            logger.info(
                "ScrapheapRetentionPurgeTask: retained %d picture(s) that no "
                "longer qualified at purge time (restored/re-deleted, locked, or "
                "the window changed between planning and execution)",
                outcome.retained_count,
            )
        if outcome.skipped_restored:
            # Not an error: the re-check inside the purge caught a picture that
            # had left the scrapheap (a restore that landed mid-purge) and spared
            # its row, file and ledger entry. Recorded so an unattended sweep
            # never silently drops work.
            logger.info(
                "ScrapheapRetentionPurgeTask: spared %d picture(s) that left the "
                "scrapheap between planning and deletion: %s",
                len(outcome.skipped_restored),
                outcome.skipped_restored,
            )
        if outcome.skipped_count:
            # The finder already excludes protected rows, so a non-zero skip
            # means the protection changed between planning and execution. The
            # picture was correctly left intact; record it as an anomaly.
            logger.warning(
                "ScrapheapRetentionPurgeTask: %d selected picture(s) turned out "
                "to be protected and were skipped (protection changed between "
                "planning and purge)",
                outcome.skipped_count,
            )
        if outcome.purged_ids:
            self._vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": list(outcome.purged_ids),
                    "change_kind": "removed",
                },
            )
        logger.info(
            "ScrapheapRetentionPurgeTask: purged %d picture(s)",
            outcome.deleted_count,
        )
        return {
            "purged": outcome.deleted_count,
            "skipped": outcome.skipped_count,
            "skipped_locked": len(outcome.skipped_locked),
            "retained": outcome.retained_count,
        }

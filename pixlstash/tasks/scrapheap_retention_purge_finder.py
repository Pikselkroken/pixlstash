"""Scheduled finder that drives the scrapheap retention auto-purge.

Every :data:`_CHECK_INTERVAL_S` seconds it asks the DB for UNPROTECTED
soft-deleted pictures whose retention deadline has passed and, if there are any,
schedules one :class:`ScrapheapRetentionPurgeTask` for that batch.

Deadline = ``max(deleted_at + scrapheap_retention_days,
scrapheap_retention_reduced_at + 1 day)`` - the second term is a FLOOR measured
from the last window *lowering*, so after a reduction nothing is purgeable for a
day regardless of how long it has been in the scrapheap. The finder returns no
work at all when:

* ``scrapheap_retention_days`` is ``None`` ("Never" - auto-purge disabled), or
* nothing is past its deadline.

``None`` is the DEFAULT, so on an install where nobody has turned auto-empty on
this finder never schedules a task and no file is ever removed from disk by the
timer. The check is here, in the finder, not merely in the UI.

Protected reference-folder originals (``allow_delete_file=False``) are excluded
from the candidate query itself and are exempt from any timer; only the manual,
consent-gated ``include_protected=true`` delete-forever can destroy them.

A config save NEVER triggers a purge: the only trigger is this finder's own
timer, so lowering the window takes effect on the next cycle at the earliest -
and, thanks to the grace floor, no earlier than a day after the save - never
synchronously inside the PATCH request.

Pictures frozen by a locked picture-set are excluded from the candidate query
(skip-and-log, never raise: one frozen member must not abort a background
sweep). ``DELETE /pictures/{id}`` already refuses them with 423.
"""

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.services import scrapheap_service
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.scrapheap_retention_purge_task import ScrapheapRetentionPurgeTask

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# How often (seconds) to re-check for expired scrapheap pictures. Retention is
# measured in days, so a 15-minute cadence is far finer than the policy needs
# while keeping the DB scan rare.
_CHECK_INTERVAL_S: float = 900.0


class ScrapheapRetentionPurgeFinder(BaseTaskFinder):
    """Periodically schedule the auto-purge of retention-expired scrapheap items.

    Attributes:
        _vault: The owning Vault (retention config + DB access).
        _last_check_at: Monotonic timestamp of the last DB check, or ``None``
            when no check has happened yet.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault instance, used for the retention settings
                and DB access.
        """
        super().__init__()
        self._vault = vault
        # ``None``, NOT 0.0. ``time.monotonic()``'s reference point is undefined
        # (CPython docs: "only the difference between the results of two calls
        # is valid"); on Linux it is seconds since BOOT. So 0.0 is not a "never
        # checked" sentinel - it is an absolute instant, and on a host that
        # booted less than _CHECK_INTERVAL_S ago ``now - 0.0`` is *below* the
        # interval, which reads as "checked recently" and silently suppresses
        # the first sweep. That is real on a fresh container/VM (and is exactly
        # how this surfaced: a CI runner whose uptime was under 15 minutes).
        self._last_check_at: Optional[float] = None

    def finder_name(self) -> str:
        return "ScrapheapRetentionPurgeFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        retention_days = self._vault.scrapheap_retention_days
        if retention_days is None:
            # "Never" - auto-purge is disabled entirely. Nothing is ever
            # destroyed by the timer while this is set.
            return None

        now_mono = time.monotonic()
        if (
            self._last_check_at is not None
            and now_mono - self._last_check_at < _CHECK_INTERVAL_S
        ):
            return None
        self._last_check_at = now_mono

        try:
            due_ids = scrapheap_service.find_due_retention_picture_ids(
                self._vault,
                datetime.now(timezone.utc),
                retention_days,
                self._vault.scrapheap_retention_reduced_at,
                ScrapheapRetentionPurgeTask.BATCH_SIZE,
            )
        except Exception as exc:
            logger.error(
                "ScrapheapRetentionPurgeFinder: failed to query due scrapheap "
                "pictures (retention_days=%s, reduced_at=%s): %s",
                retention_days,
                self._vault.scrapheap_retention_reduced_at,
                exc,
                exc_info=True,
            )
            return None

        if not due_ids:
            return None

        logger.info(
            "ScrapheapRetentionPurgeFinder: %d scrapheap picture(s) past the "
            "%s-day retention window - scheduling auto-purge",
            len(due_ids),
            retention_days,
        )
        return ScrapheapRetentionPurgeTask(self._vault, due_ids)

"""Periodically checks tag_health cache staleness and dispatches a rebuild.

Closes Spec B's loop (``docs/reviews/tag-review-board-redesign-ux-spec.md``
§4): the persistent header rebuild control (frontend) is the manual escape
hatch; this finder is what makes the board catch up to review activity / new
pictures / new tagger runs without anyone clicking it. Shape mirrors
:class:`~pixlstash.tasks.ensure_gfs_snapshot_finder.EnsureGfsSnapshotFinder`'s
monotonic-clock check-interval gate - the established precedent in this
codebase for "cheap periodic condition check, act at most every N seconds".
"""

import time
from typing import TYPE_CHECKING, Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# Minimum time between staleness checks / auto-rebuild dispatches, seconds.
# 5 minutes: matches this codebase's other periodic-finder cadences
# (ReferenceFolderScanFinder._RESCAN_INTERVAL_S, EnsureGfsSnapshotFinder.
# _CHECK_INTERVAL_S) - frequent enough that the board catches up well within
# a normal review session, infrequent enough that a burst of accept/dismiss
# clicks can't retrigger a rebuild every few seconds. This is the spec's
# required debounce; tune here only.
AUTO_REBUILD_CHECK_INTERVAL_S: float = 300.0


class TagHealthAutoRebuildFinder(BaseTaskFinder):
    """Check ``tag_health_service.is_stale`` at most every 5 minutes.

    When stale and no rebuild is already running, queues one
    :class:`~pixlstash.tasks.tag_health_auto_rebuild_task.
    TagHealthAutoRebuildTask`, which dispatches through the same idempotent
    ``start_rebuild`` path the manual button uses.

    Attributes:
        _vault: The owning Vault.
        _last_check_at: Monotonic timestamp of the last staleness check,
            or ``None`` when no check has happened yet.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault instance, used to reach
                ``tag_health_service``.
        """
        super().__init__()
        self._vault = vault
        # ``None``, NOT 0.0 - ``time.monotonic()``'s reference point is undefined
        # (on Linux it is seconds since BOOT), so 0.0 is an absolute instant, not
        # a "never checked" sentinel. On a host that booted less than the check
        # interval ago, ``now - 0.0`` falls BELOW the interval and silently
        # suppresses the first run. Same defect as
        # ScrapheapRetentionPurgeFinder; see its comment.
        self._last_check_at: Optional[float] = None

    def finder_name(self) -> str:
        return "TagHealthAutoRebuildFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        now_mono = time.monotonic()
        if (
            self._last_check_at is not None
            and now_mono - self._last_check_at < AUTO_REBUILD_CHECK_INTERVAL_S
        ):
            return None
        self._last_check_at = now_mono

        # Late import: avoids a service<->task import cycle at module load.
        from pixlstash.services import tag_health_service

        if tag_health_service.get_status(self._vault)["building"]:
            return None
        if not tag_health_service.is_stale(self._vault):
            return None

        logger.info("TagHealthAutoRebuildFinder: cache stale - dispatching rebuild")
        from pixlstash.tasks.tag_health_auto_rebuild_task import (
            TagHealthAutoRebuildTask,
        )

        return TagHealthAutoRebuildTask(self._vault)

"""Find recorded-but-unapplied smart-score invalidations."""

import time
from typing import TYPE_CHECKING, Optional

from sqlmodel import select

from pixlstash.db_models.pending_score_invalidation import PendingScoreInvalidation
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.pending_score_invalidation_task import PendingScoreInvalidationTask

if TYPE_CHECKING:
    from pixlstash.vault import Vault

# How often to look. The settings handler applies its own invalidation inline,
# so this is the safety net for a crash in the window between recording and
# applying, not the normal path. Checking often would be pointless polling;
# checking rarely would leave scores visibly stale after a crash, so this sits
# deliberately shorter than the GFS finder's five minutes.
_CHECK_INTERVAL_S: float = 60.0


class PendingScoreInvalidationFinder(BaseTaskFinder):
    """Queue a drain task whenever a pending invalidation is outstanding.

    The record exists because the setting that caused it lives in the hub while
    the scores live in the vault, so the two writes cannot share a transaction
    (see
    :class:`~pixlstash.db_models.pending_score_invalidation.PendingScoreInvalidation`).
    This finder is what turns a lost step into a delayed one: it runs shortly
    after startup, so a crash in the unsafe window is repaired on the next run
    rather than leaving scores wrong indefinitely.

    Attributes:
        _vault: The owning Vault.
        _last_check_at: Monotonic timestamp of the last check, or ``None`` when
            no check has happened yet.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault, used to reach the database and build tasks.
        """
        super().__init__()
        self._vault = vault
        # ``None``, not 0.0: ``time.monotonic()``'s reference point is undefined,
        # so 0.0 is an absolute instant rather than a "never checked" sentinel,
        # and on a host that booted recently it would suppress the first run.
        # Same defect as EnsureGfsSnapshotFinder; see its comment.
        self._last_check_at: Optional[float] = None

    def finder_name(self) -> str:
        return "PendingScoreInvalidationFinder"

    def max_inflight_tasks(self) -> int:
        # Draining is a single transaction over the whole table, so a second
        # concurrent task would only contend for the same rows.
        return 1

    def find_task(self):
        now_mono = time.monotonic()
        if (
            self._last_check_at is not None
            and now_mono - self._last_check_at < _CHECK_INTERVAL_S
        ):
            return None
        self._last_check_at = now_mono

        outstanding = self._vault.db.run_immediate_read_task(
            lambda session: session.exec(select(PendingScoreInvalidation)).first()
        )
        if outstanding is None:
            return None
        return PendingScoreInvalidationTask(vault=self._vault)

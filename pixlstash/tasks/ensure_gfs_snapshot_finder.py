"""Scheduled finder that drives the GFS snapshot schedule.

Implements the Grandfather-Father-Son rotation: one automatic snapshot per
day, tiered so each day's snapshot is promoted to the highest period it opens.
On any given check the finder schedules at most one snapshot, of the highest
tier that is *due*:

* ``MONTHLY`` — no MONTHLY snapshot exists for the current calendar month.
* ``WEEKLY``  — no WEEKLY-or-MONTHLY snapshot exists for the current ISO week.
* ``DAILY``   — no automatic snapshot at all exists for today (UTC).

Because a higher tier also satisfies the lower slots (a MONTHLY counts as this
week's WEEKLY and today's DAILY), an aligned boundary day produces a single
MONTHLY rather than three near-identical snapshots. Retention
(``SnapshotService._apply_gfs_retention``) then prunes each tier to its keep
count independently (7 daily / 4 weekly / 12 monthly).
"""

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlmodel import select

from pixlstash.db_models.snapshot import Snapshot
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# How often (seconds) to re-check whether a snapshot tier is due.
_CHECK_INTERVAL_S: float = 300.0  # 5 minutes

# Automatic (GFS-tiered) snapshot kinds.
_AUTO_KINDS: tuple[str, ...] = ("DAILY", "WEEKLY", "MONTHLY")


class EnsureGfsSnapshotFinder(BaseTaskFinder):
    """Periodically ensure the due GFS snapshot tier has been taken.

    The check runs at most once every ``_CHECK_INTERVAL_S`` seconds so it does
    not busy-poll the DB.

    Attributes:
        _vault: The owning Vault.
        _last_check_at: Monotonic timestamp of the last DB check.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault instance used to access the DB and
                SnapshotService.
        """
        super().__init__()
        self._vault = vault
        self._last_check_at: float = 0.0

    def finder_name(self) -> str:
        return "EnsureGfsSnapshotFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        if not self._vault.daily_snapshots_enabled:
            return None
        now_mono = time.monotonic()
        if now_mono - self._last_check_at < _CHECK_INTERVAL_S:
            return None
        self._last_check_at = now_mono

        now = datetime.now(timezone.utc)
        auto_snapshots = self._vault.db.run_immediate_read_task(
            lambda session: [
                (kind, created)
                for kind, created in session.exec(
                    select(Snapshot.kind, Snapshot.created_at)
                ).all()
                if kind in _AUTO_KINDS
            ]
        )

        kind = self._due_kind(now, auto_snapshots)
        if kind is None:
            return None

        logger.info("EnsureGfsSnapshotFinder: %s snapshot due — scheduling one", kind)
        from pixlstash.tasks.ensure_gfs_snapshot_task import EnsureGfsSnapshotTask

        return EnsureGfsSnapshotTask(self._vault, kind)

    @staticmethod
    def _due_kind(now: datetime, auto_snapshots: list) -> Optional[str]:
        """Return the highest snapshot tier that is currently due, or None.

        Args:
            now: Current UTC datetime.
            auto_snapshots: List of ``(kind, created_at)`` for existing
                automatic snapshots (DAILY / WEEKLY / MONTHLY). ``created_at``
                may be naive; it is treated as UTC.

        Returns:
            ``"MONTHLY"``, ``"WEEKLY"``, ``"DAILY"``, or ``None`` when every
            tier's slot for the current period is already filled.
        """
        today = now.date()
        week_key = today.isocalendar()[:2]  # (ISO year, ISO week)
        month_key = (now.year, now.month)

        has_monthly_this_month = False
        has_weekly_or_higher_this_week = False
        has_any_auto_today = False

        for kind, created in auto_snapshots:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            c_date = created.astimezone(timezone.utc).date()

            if kind == "MONTHLY" and (c_date.year, c_date.month) == month_key:
                has_monthly_this_month = True
            if kind in ("WEEKLY", "MONTHLY") and c_date.isocalendar()[:2] == week_key:
                has_weekly_or_higher_this_week = True
            if c_date == today:
                has_any_auto_today = True

        if not has_monthly_this_month:
            return "MONTHLY"
        if not has_weekly_or_higher_this_week:
            return "WEEKLY"
        if not has_any_auto_today:
            return "DAILY"
        return None

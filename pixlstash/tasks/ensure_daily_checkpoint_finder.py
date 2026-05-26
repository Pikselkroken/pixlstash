"""Scheduled finder that ensures a DAILY checkpoint exists for today's slot."""

import time
from datetime import datetime, timezone, date
from typing import TYPE_CHECKING

from sqlmodel import select

from pixlstash.db_models.checkpoint import Checkpoint
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# How often (seconds) to re-check whether today's slot is empty.
_CHECK_INTERVAL_S: float = 300.0  # 5 minutes


class EnsureDailyCheckpointFinder(BaseTaskFinder):
    """Periodically check whether a DAILY checkpoint exists for today.

    If no DAILY checkpoint has been taken on the current calendar date (UTC)
    the finder triggers ``CheckpointService.create_checkpoint('DAILY')``.
    The check runs at most once every ``_CHECK_INTERVAL_S`` seconds so it
    does not busy-poll the DB.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault instance used to access the DB and
                CheckpointService.
        """
        super().__init__()
        self._vault = vault
        self._last_check_at: float = 0.0

    def finder_name(self) -> str:
        return "EnsureDailyCheckpointFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        now = time.monotonic()
        if now - self._last_check_at < _CHECK_INTERVAL_S:
            return None
        self._last_check_at = now

        today: date = datetime.now(timezone.utc).date()

        already_done = self._vault.db.run_immediate_read_task(
            lambda session: self._today_checkpoint_exists(session, today)
        )
        if already_done:
            return None

        logger.info(
            "EnsureDailyCheckpointFinder: no DAILY checkpoint for %s — scheduling one",
            today.isoformat(),
        )
        from pixlstash.tasks.ensure_daily_checkpoint_task import (
            EnsureDailyCheckpointTask,
        )

        return EnsureDailyCheckpointTask(self._vault)

    def _today_checkpoint_exists(self, session, today: date) -> bool:
        """Return True if a DAILY checkpoint was created on *today* (UTC).

        Args:
            session: Read-only database session.
            today: The UTC calendar date to check.

        Returns:
            True if a matching checkpoint row exists.
        """
        rows = session.exec(select(Checkpoint).where(Checkpoint.kind == "DAILY")).all()
        for cp in rows:
            cp_date = cp.created_at
            if cp_date.tzinfo is None:
                from datetime import timezone as _tz

                cp_date = cp_date.replace(tzinfo=_tz.utc)
            if cp_date.date() == today:
                return True
        return False

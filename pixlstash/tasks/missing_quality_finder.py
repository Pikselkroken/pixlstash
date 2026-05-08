from sqlmodel import Session
import time

from pixlstash.worker_config import QUALITY_MAX_INFLIGHT
from .base_task_finder import BaseTaskFinder
from .quality_task import QualityTask


class MissingQualityFinder(BaseTaskFinder):
    """Find missing full-image quality work and create a QualityTask.

    Batch size is chosen dynamically based on the observed preload rate from
    the previous task.  The target invariant is:

        preload_time(N) ≤ _PRELOAD_BUDGET_S

    i.e. N = preload_rate × _PRELOAD_BUDGET_S (clamped to [MIN, MAX]).

    This decouples from compute time entirely — compute time scales with N and
    would create a collapsed feedback loop if used as the budget.  The preload
    rate (images/s) is an independent property of disk+PIL speed that responds
    directly to whether the OS page cache is cold or warm.

    Cold disk  → low rate → small N → cache warms after a few tasks → rate
    climbs → N grows towards MAX.  No fixed ramp schedule needed.
    """

    _FETCH_LIMIT = QualityTask.BATCH_SIZE * 4
    _MAX_BATCH_SIZE = 256
    _MIN_BATCH_SIZE = 16
    _INITIAL_BATCH_SIZE = 32

    # Target wall time for the preload thread.  With INFLIGHT=3 a task computes
    # for ~0.5–4 s; targeting 3 s means the preload should finish comfortably
    # before that task acquires the compute semaphore.
    _PRELOAD_BUDGET_S = 2.0

    # EMA smoothing — lower = smoother but slower to adapt.
    _EMA_ALPHA = 0.25

    # Maximum ratio by which the batch size may grow in a single step.
    # Prevents a single warm-cache task from jumping 32 → 256 instantly.
    _MAX_STEP_UP = 1.5

    # Reset to initial batch if idle for this many seconds (cold disk cache).
    _IDLE_RESET_S = 5.0

    def __init__(self, database):
        super().__init__()
        self._db = database
        self._ema_preload_rate: float | None = None  # images/s (with parallelism)
        self._prev_batch_size: int = self._INITIAL_BATCH_SIZE
        self._last_find_task_at: float = 0.0

    def finder_name(self) -> str:
        return "MissingQualityFinder"

    def max_inflight_tasks(self) -> int:
        return QUALITY_MAX_INFLIGHT

    def _next_batch_size(self) -> int:
        rate = self._ema_preload_rate
        if rate is None or rate <= 0.0:
            return self._INITIAL_BATCH_SIZE
        target = int(rate * self._PRELOAD_BUDGET_S)
        # Cap growth to _MAX_STEP_UP× the previous batch to prevent a single
        # warm-cache task from jumping the size from 32 → 256 in one step.
        max_allowed = int(self._prev_batch_size * self._MAX_STEP_UP)
        target = min(target, max_allowed)
        return max(self._MIN_BATCH_SIZE, min(self._MAX_BATCH_SIZE, target))

    def _update_ema(self) -> None:
        with QualityTask._feedback_lock:
            preload_s = QualityTask._last_preload_s
            batch_size = QualityTask._last_batch_size

        if batch_size <= 0 or preload_s <= 0.0:
            return

        raw_rate = batch_size / preload_s
        if self._ema_preload_rate is None:
            self._ema_preload_rate = raw_rate
        else:
            a = self._EMA_ALPHA
            self._ema_preload_rate = a * raw_rate + (1 - a) * self._ema_preload_rate

    def find_task(self):
        pictures = self._db.run_immediate_read_task(
            QualityTask._find_pictures_missing_quality,
            self._FETCH_LIMIT * 8,
        )
        if not pictures:
            self._ema_preload_rate = None
            self._prev_batch_size = self._INITIAL_BATCH_SIZE
            self._last_find_task_at = 0.0
            return None

        now = time.monotonic()
        if now - self._last_find_task_at > self._IDLE_RESET_S:
            self._ema_preload_rate = None  # disk cache cold — start conservative
            self._prev_batch_size = self._INITIAL_BATCH_SIZE
        self._last_find_task_at = now

        self._update_ema()
        claim_limit = self._next_batch_size()
        self._prev_batch_size = claim_limit

        selected = self._filter_and_claim(pictures, claim_limit)
        if not selected:
            return None

        return QualityTask(database=self._db, pictures=selected)

    @staticmethod
    def _count_missing_quality(session: Session) -> int:
        return QualityTask.count_missing_quality(session)

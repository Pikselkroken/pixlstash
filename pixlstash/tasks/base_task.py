import threading
import uuid

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.vram_utils import is_vram_oom

logger = get_logger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    """Task execution priority. Lower value = higher priority (min-heap ordering)."""

    URGENT = 0  # user-triggered interactive tasks — skip ahead of everything
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class QueueType(str, Enum):
    """Which worker pool a task should run in."""

    CPU = "cpu"
    GPU = "gpu"


class BaseTask(ABC):
    """In-memory task unit executed by the TaskRunner.

    Attributes:
        id: Unique task identifier.
        type: Task type label.
        params: Input parameters used by task logic.
        result: Optional task result.
        error: Optional task error string.
        status: Current task status.
        created_at: Task creation time.
        started_at: Task execution start time.
        completed_at: Task completion time.
        vram_oom_attempts: How many attempts a GPU out-of-memory failure cost
            this task. ``0`` when it never hit one.
    """

    #: Total attempts a task gets when it fails with a GPU out-of-memory error.
    #: VRAM pressure is usually another process (ComfyUI, a second model) that
    #: frees up shortly, so the same batch is worth retrying rather than losing.
    VRAM_OOM_ATTEMPTS = 3

    def __init__(self, task_type: str, params: Optional[dict[str, Any]] = None):
        self.id = str(uuid.uuid4())
        self.type = task_type
        self.params = params or {}
        self.result: Any = None
        self.error: Optional[str] = None
        self.status = TaskStatus.PENDING
        self.created_at = datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.vram_oom_attempts = 0
        self._done_event = threading.Event()

    def run(
        self,
        on_vram_oom: Optional[Callable[["BaseTask", int, BaseException], None]] = None,
    ) -> Any:
        """Execute the task, retrying a GPU out-of-memory failure.

        The retry lives here rather than in the runner so that the task's
        completion bookkeeping (``_done_event``, ``completed_at``) fires once,
        after the last attempt — a waiter in ``submit_and_wait`` must not see a
        task settle and then start running again.

        Args:
            on_vram_oom: Called as ``(task, attempt, exc)`` after each failed
                attempt that is *going* to be retried, never after the last one.
                It owns freeing the GPU, pausing before the retry, and telling
                the user; see ``TaskRunner._pause_and_report_vram_oom``. It may
                raise to abandon the remaining attempts — the runner does that
                when it is shutting down — and the task then fails with what it
                raised.

        Returns:
            Whatever ``_run_task`` returned.
        """
        self.started_at = datetime.utcnow()
        self.status = TaskStatus.RUNNING
        try:
            for attempt in range(1, self.VRAM_OOM_ATTEMPTS + 1):
                try:
                    self.result = self._run_task()
                    self.status = TaskStatus.COMPLETED
                    return self.result
                except Exception as exc:
                    # GPU-queue tasks only. Re-running ``_run_task`` from the top
                    # is safe for an inference pass that failed before it wrote
                    # anything; it is not something to hand to the CPU queue's
                    # import/purge tasks, which move and delete files.
                    if self.queue_type != QueueType.GPU or not is_vram_oom(exc):
                        raise
                    self.vram_oom_attempts = attempt
                    if attempt >= self.VRAM_OOM_ATTEMPTS:
                        raise
                    logger.warning(
                        "Task %s (%s) ran out of GPU memory on attempt %d of %d; "
                        "retrying: %s",
                        self.id,
                        self.type,
                        attempt,
                        self.VRAM_OOM_ATTEMPTS,
                        exc,
                    )
                    if on_vram_oom is not None:
                        on_vram_oom(self, attempt, exc)
        except Exception as exc:
            self.error = str(exc)
            self.status = TaskStatus.FAILED
            raise
        finally:
            self.completed_at = datetime.utcnow()
            self._done_event.set()

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.MEDIUM

    @property
    def queue_type(self) -> QueueType:
        """Which worker pool this task runs in.  Override to return ``QueueType.GPU``
        for tasks that require the GPU so they are serialised on the GPU worker."""
        return QueueType.CPU

    def on_queued(self) -> None:
        return None

    def estimated_vram_mb(self) -> int:
        return 0

    def allow_cpu_spillover(self) -> bool:
        return False

    def enable_cpu_spillover(self) -> None:
        return None

    def on_cancel(self) -> None:
        return None

    @abstractmethod
    def _run_task(self) -> Any:
        raise NotImplementedError

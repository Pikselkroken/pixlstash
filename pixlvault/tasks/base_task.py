import uuid

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    """Task execution priority. Lower value = higher priority (min-heap ordering)."""

    HIGH = 1
    MEDIUM = 2
    LOW = 3


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
    """

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

    def run(self) -> Any:
        self.started_at = datetime.utcnow()
        self.status = TaskStatus.RUNNING
        try:
            self.result = self._run_task()
            self.status = TaskStatus.COMPLETED
            return self.result
        except Exception as exc:
            self.error = str(exc)
            self.status = TaskStatus.FAILED
            raise
        finally:
            self.completed_at = datetime.utcnow()

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.MEDIUM

    def on_queued(self) -> None:
        return None

    def estimated_vram_mb(self) -> int:
        return 0

    def allow_cpu_spillover(self) -> bool:
        return False

    def enable_cpu_spillover(self) -> None:
        return None

    @abstractmethod
    def _run_task(self) -> Any:
        raise NotImplementedError

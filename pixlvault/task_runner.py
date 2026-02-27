import queue
import threading
import uuid
import traceback

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

from .pixl_logging import get_logger


logger = get_logger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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

    @abstractmethod
    def _run_task(self) -> Any:
        raise NotImplementedError


class CallableTask(BaseTask):
    """Task wrapper for running callables in the TaskRunner."""

    def __init__(
        self,
        task_type: str,
        func: Callable[..., Any],
        params: Optional[dict[str, Any]] = None,
    ):
        super().__init__(task_type=task_type, params=params)
        self._func = func

    def _run_task(self) -> Any:
        return self._func()


class TaskRunner:
    """Single-thread in-memory task orchestrator.

    Tasks are executed serially by one background thread.
    """

    def __init__(self, name: str = "TaskRunner"):
        self._name = name
        self._queue: queue.Queue[BaseTask] = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._on_task_complete_callbacks: list[
            Callable[[BaseTask, Optional[BaseException]], None]
        ] = []

    def set_task_complete_callback(
        self, callback: Callable[[BaseTask, Optional[BaseException]], None]
    ):
        self._on_task_complete_callbacks = [callback]

    def add_task_complete_callback(
        self, callback: Callable[[BaseTask, Optional[BaseException]], None]
    ):
        self._on_task_complete_callbacks.append(callback)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._queue.put(_StopTask())
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("TaskRunner %s did not stop within timeout.", self._name)

    def submit(self, task: BaseTask) -> str:
        self._queue.put(task)
        return task.id

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        logger.info("TaskRunner %s started.", self._name)
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if isinstance(task, _StopTask):
                continue

            error: Optional[BaseException] = None
            try:
                task.run()
            except Exception as exc:
                error = exc
                tb = traceback.extract_tb(exc.__traceback__)
                if tb:
                    last = tb[-1]
                    logger.warning(
                        "Task %s (%s) failed at %s:%s in %s: %s | code=%s",
                        task.id,
                        task.type,
                        last.filename,
                        last.lineno,
                        last.name,
                        exc,
                        (last.line or "").strip(),
                    )
                else:
                    logger.warning("Task %s (%s) failed: %s", task.id, task.type, exc)
            finally:
                callbacks = list(self._on_task_complete_callbacks)
                for callback in callbacks:
                    try:
                        callback(task, error)
                    except Exception as callback_exc:
                        logger.warning(
                            "Task completion callback failed for %s: %s",
                            task.id,
                            callback_exc,
                        )
        logger.info("TaskRunner %s stopped.", self._name)


class _StopTask(BaseTask):
    def __init__(self):
        super().__init__(task_type="_stop")

    def _run_task(self) -> Any:
        return None

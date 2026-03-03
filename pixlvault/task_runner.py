import queue
import threading
import traceback
import subprocess
import os
import time

from typing import Any, Callable, Optional

from .pixl_logging import get_logger
from .tasks.base_task import BaseTask


logger = get_logger(__name__)


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

    SPILLOVER_GRACE_SECONDS = 1.5
    SPILLOVER_TOLERANCE_MB = 256

    def __init__(self, name: str = "TaskRunner"):
        self._name = name
        self._queue: queue.Queue[BaseTask] = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._closed = False
        self._on_task_complete_callbacks: list[
            Callable[[BaseTask, Optional[BaseException]], None]
        ] = []
        self._max_vram_usage_mb: Optional[int] = None

    def set_max_vram_usage_gb(self, max_vram_gb: Optional[float]):
        if max_vram_gb is None:
            self._max_vram_usage_mb = None
            return
        try:
            requested_mb = int(float(max_vram_gb) * 1024)
        except Exception:
            self._max_vram_usage_mb = None
            return
        if requested_mb <= 0:
            self._max_vram_usage_mb = None
            return
        total_mb = self._get_total_vram_mb()
        if total_mb > 0:
            self._max_vram_usage_mb = max(1, min(requested_mb, total_mb))
        else:
            self._max_vram_usage_mb = requested_mb

    @staticmethod
    def _get_total_vram_mb() -> int:
        try:
            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            totals = []
            for line in output.splitlines():
                value = line.strip()
                if not value:
                    continue
                totals.append(int(float(value)))
            return sum(totals)
        except Exception:
            return 0

    @staticmethod
    def _get_process_vram_mb() -> int:
        pid = os.getpid()
        try:
            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            used_mb = 0
            for line in output.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    line_pid = int(parts[0])
                    line_used_mb = int(float(parts[1]))
                except Exception:
                    continue
                if line_pid == pid:
                    used_mb += line_used_mb
            return used_mb
        except Exception:
            return 0

    def _wait_for_vram_budget(self, task: BaseTask) -> None:
        budget_mb = self._max_vram_usage_mb
        if not budget_mb:
            return
        estimated_mb = max(0, int(getattr(task, "estimated_vram_mb", lambda: 0)()))
        if estimated_mb <= 0:
            return
        if estimated_mb > budget_mb:
            logger.warning(
                "Task %s (%s) estimated VRAM %sMB exceeds configured budget %sMB; running anyway.",
                task.id,
                task.type,
                estimated_mb,
                budget_mb,
            )
            return

        wait_started_at = time.perf_counter()
        spillover_allowed = bool(getattr(task, "allow_cpu_spillover", lambda: False)())
        spillover_applied = False
        while not self._stop.is_set():
            used_mb = self._get_process_vram_mb()
            if used_mb <= 0:
                return
            required_mb = used_mb + estimated_mb
            overflow_mb = required_mb - budget_mb
            if overflow_mb <= 0:
                waited_s = time.perf_counter() - wait_started_at
                if waited_s > 0.01:
                    logger.debug(
                        "Task %s (%s) VRAM gate released after %.3fs (used=%sMB estimated=%sMB budget=%sMB)",
                        task.id,
                        task.type,
                        waited_s,
                        used_mb,
                        estimated_mb,
                        budget_mb,
                    )
                return

            if overflow_mb <= self.SPILLOVER_TOLERANCE_MB:
                logger.debug(
                    "Task %s (%s) VRAM gate allowing small overflow (used=%sMB estimated=%sMB overflow=%sMB tolerance=%sMB budget=%sMB)",
                    task.id,
                    task.type,
                    used_mb,
                    estimated_mb,
                    overflow_mb,
                    self.SPILLOVER_TOLERANCE_MB,
                    budget_mb,
                )
                return

            waited_s = time.perf_counter() - wait_started_at
            if spillover_allowed and waited_s < self.SPILLOVER_GRACE_SECONDS:
                time.sleep(0.1)
                continue

            if spillover_allowed and not spillover_applied:
                try:
                    getattr(task, "enable_cpu_spillover", lambda: None)()
                    spillover_applied = True
                    logger.info(
                        "Task %s (%s) switched to CPU spillover (used=%sMB estimated=%sMB budget=%sMB)",
                        task.id,
                        task.type,
                        used_mb,
                        estimated_mb,
                        budget_mb,
                    )
                    return
                except Exception as exc:
                    logger.warning(
                        "Task %s (%s) CPU spillover hook failed: %s",
                        task.id,
                        task.type,
                        exc,
                    )
            time.sleep(0.1)

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
        with self._lock:
            self._closed = False
            self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
        self._queue.put(_StopTask())
        if self._thread is not None:
            self._thread.join(timeout=60)
            if self._thread.is_alive():
                logger.warning("TaskRunner %s did not stop within timeout.", self._name)

    def submit(self, task: BaseTask) -> str:
        if self._closed or self._stop.is_set():
            raise RuntimeError(f"TaskRunner {self._name} is stopped.")
        try:
            task.on_queued()
        except Exception as exc:
            logger.warning(
                "Task %s (%s) queue hook failed: %s",
                task.id,
                task.type,
                exc,
            )
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

            self._wait_for_vram_budget(task)

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

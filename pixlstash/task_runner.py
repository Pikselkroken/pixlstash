import ctypes
import gc
import itertools
import platform
import queue
import threading
import traceback
import subprocess
import os
import time
import torch

from typing import Any, Callable, Optional
from datetime import datetime, UTC

from .pixl_logging import get_logger
from .tasks.base_task import BaseTask, QueueType, TaskPriority, TaskStatus


logger = get_logger(__name__)


class TaskCancelledError(RuntimeError):
    """Raised by ``TaskRunner.submit_and_wait`` when a task is cancelled
    before it had a chance to complete (e.g. the runner was stopped)."""


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
    """Multi-thread in-memory task orchestrator.

    Tasks are dequeued by priority and executed concurrently across
    *num_workers* background threads.  A single shared PriorityQueue
    ensures correct ordering while all threads remain busy.
    """

    SPILLOVER_GRACE_SECONDS = 1.5
    SPILLOVER_TOLERANCE_MB = 256

    # Cache nvidia-smi results: (timestamp, value). A fresh query is only made
    # if the cached value is older than this many seconds, preventing all 4
    # worker threads from spawning simultaneous nvidia-smi subprocesses.
    _VRAM_CACHE_TTL_S = 1.5
    _vram_cache_lock = threading.Lock()
    _vram_cache_value: int = 0
    _vram_cache_ts: float = 0.0
    # Timeout for nvidia-smi calls: prevents workers from hanging indefinitely
    # when nvidia-smi stalls under heavy GPU load.
    _NVIDIA_SMI_TIMEOUT_S = 5

    def __init__(self, name: str = "TaskRunner", num_workers: int = 1):
        self._name = name
        self._num_workers = max(1, int(num_workers))
        # CPU queue: serviced by num_workers threads.
        self._queue: queue.PriorityQueue[tuple[int, int, BaseTask]] = (
            queue.PriorityQueue()
        )
        # GPU queue: serviced by exactly ONE dedicated thread so GPU tasks are
        # never concurrent.  Priority ordering ensures high-priority tasks
        # (e.g. face extraction) always run before lower-priority ones.
        self._gpu_queue: queue.PriorityQueue[tuple[int, int, BaseTask]] = (
            queue.PriorityQueue()
        )
        self._queue_seq = itertools.count()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._vram_gate_lock = threading.Lock()
        self._active_task_lock = threading.Lock()
        self._active_tasks: dict[int, BaseTask] = {}  # thread ident -> running task
        self._vram_reserved_mb: int = 0
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
        # Store the user's requested budget exactly and never silently reduce it.
        total_mb = self._get_total_vram_mb()
        self._max_vram_usage_mb = requested_mb
        if total_mb > 0 and requested_mb > total_mb:
            logger.warning(
                "Configured task-runner VRAM budget %.2f GB exceeds detected GPU total %.2f GB; keeping configured budget as requested.",
                requested_mb / 1024.0,
                total_mb / 1024.0,
            )

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
                timeout=TaskRunner._NVIDIA_SMI_TIMEOUT_S,
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

    @classmethod
    def _get_process_vram_mb(cls) -> int:
        """Return this process's VRAM usage in MB.

        Results are cached for ``_VRAM_CACHE_TTL_S`` seconds so that
        concurrent worker threads don't all spawn nvidia-smi at once, and so
        that a stalled nvidia-smi (which can happen under heavy GPU load) only
        blocks one thread rather than all of them.
        """
        now = time.perf_counter()
        with cls._vram_cache_lock:
            if now - cls._vram_cache_ts < cls._VRAM_CACHE_TTL_S:
                return cls._vram_cache_value
            # Set the timestamp far enough into the future to cover the full
            # subprocess timeout, so concurrent threads don't race to spawn
            # additional nvidia-smi processes while one is already in-flight.
            cls._vram_cache_ts = now + cls._NVIDIA_SMI_TIMEOUT_S

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
                timeout=cls._NVIDIA_SMI_TIMEOUT_S,
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
        except subprocess.TimeoutExpired:
            logger.warning(
                "nvidia-smi timed out after %ss; reusing last VRAM reading.",
                cls._NVIDIA_SMI_TIMEOUT_S,
            )
            with cls._vram_cache_lock:
                return cls._vram_cache_value
        except Exception:
            with cls._vram_cache_lock:
                return cls._vram_cache_value

        with cls._vram_cache_lock:
            cls._vram_cache_value = used_mb
            cls._vram_cache_ts = time.perf_counter()
        return used_mb

    def _wait_for_vram_budget(self, task: BaseTask) -> int:
        """Wait until VRAM budget allows the task and return the MB reserved.

        The caller must release the reservation (subtract from
        ``self._vram_reserved_mb``) once the task finishes.
        """
        budget_mb = self._max_vram_usage_mb
        if not budget_mb:
            logger.debug(
                "Task %s (%s) VRAM gate: no budget configured, running immediately.",
                task.id,
                task.type,
            )
            return 0
        estimated_mb = max(0, int(getattr(task, "estimated_vram_mb", lambda: 0)()))
        if estimated_mb <= 0:
            logger.debug(
                "Task %s (%s) VRAM gate: no VRAM estimate, running immediately (budget=%sMB).",
                task.id,
                task.type,
                budget_mb,
            )
            return 0
        if estimated_mb > budget_mb:
            logger.warning(
                "Task %s (%s) estimated VRAM %sMB exceeds configured budget %sMB; running anyway.",
                task.id,
                task.type,
                estimated_mb,
                budget_mb,
            )
            return 0

        wait_started_at = time.perf_counter()
        last_log_s = -1.0
        LOG_INTERVAL_S = 5.0
        spillover_allowed = bool(getattr(task, "allow_cpu_spillover", lambda: False)())
        while not self._stop.is_set():
            used_mb = self._get_process_vram_mb()
            waited_s = time.perf_counter() - wait_started_at
            if used_mb <= 0:
                logger.debug(
                    "Task %s (%s) VRAM gate: nvidia-smi reports 0 MB used, running immediately "
                    "(estimated=%sMB budget=%sMB waited=%.3fs).",
                    task.id,
                    task.type,
                    estimated_mb,
                    budget_mb,
                    waited_s,
                )
                with self._vram_gate_lock:
                    self._vram_reserved_mb += estimated_mb
                return estimated_mb
            # Include VRAM already committed by other in-flight tasks that have
            # passed the gate but may not yet be visible to nvidia-smi.
            with self._vram_gate_lock:
                reserved_mb = self._vram_reserved_mb
            required_mb = used_mb + reserved_mb + estimated_mb
            overflow_mb = required_mb - budget_mb
            if overflow_mb <= 0:
                if waited_s > 0.01:
                    logger.debug(
                        "Task %s (%s) VRAM gate released after %.3fs "
                        "(used=%sMB reserved=%sMB estimated=%sMB budget=%sMB).",
                        task.id,
                        task.type,
                        waited_s,
                        used_mb,
                        reserved_mb,
                        estimated_mb,
                        budget_mb,
                    )
                else:
                    logger.debug(
                        "Task %s (%s) VRAM gate passed immediately "
                        "(used=%sMB reserved=%sMB estimated=%sMB budget=%sMB).",
                        task.id,
                        task.type,
                        used_mb,
                        reserved_mb,
                        estimated_mb,
                        budget_mb,
                    )
                with self._vram_gate_lock:
                    self._vram_reserved_mb += estimated_mb
                return estimated_mb

            if overflow_mb <= self.SPILLOVER_TOLERANCE_MB:
                logger.debug(
                    "Task %s (%s) VRAM gate allowing small overflow "
                    "(used=%sMB reserved=%sMB estimated=%sMB overflow=%sMB tolerance=%sMB budget=%sMB waited=%.3fs).",
                    task.id,
                    task.type,
                    used_mb,
                    reserved_mb,
                    estimated_mb,
                    overflow_mb,
                    self.SPILLOVER_TOLERANCE_MB,
                    budget_mb,
                    waited_s,
                )
                with self._vram_gate_lock:
                    self._vram_reserved_mb += estimated_mb
                return estimated_mb

            if waited_s - last_log_s >= LOG_INTERVAL_S:
                logger.debug(
                    "Task %s (%s) VRAM gate waiting: used=%sMB reserved=%sMB estimated=%sMB "
                    "required=%sMB budget=%sMB overflow=%sMB waited=%.1fs spillover_allowed=%s.",
                    task.id,
                    task.type,
                    used_mb,
                    reserved_mb,
                    estimated_mb,
                    required_mb,
                    budget_mb,
                    overflow_mb,
                    waited_s,
                    spillover_allowed,
                )
                last_log_s = waited_s

            # Escape hatch: if nothing is currently in flight (reserved_mb==0),
            # waiting longer cannot help — the overflow comes from loaded models
            # or an external process (e.g. ComfyUI) that won't be freed.
            # If the task supports CPU spillover, try that first so we don't
            # pile more GPU work onto an already-full device.
            if reserved_mb == 0 and waited_s >= self.SPILLOVER_GRACE_SECONDS:
                if spillover_allowed:
                    try:
                        getattr(task, "enable_cpu_spillover", lambda: None)()
                        logger.warning(
                            "Task %s (%s) VRAM gate escape (external VRAM pressure): "
                            "enabling CPU spillover (used=%sMB estimated=%sMB budget=%sMB overflow=%sMB).",
                            task.id,
                            task.type,
                            used_mb,
                            estimated_mb,
                            budget_mb,
                            overflow_mb,
                        )
                        return estimated_mb
                    except Exception as exc:
                        logger.warning(
                            "Task %s (%s) CPU spillover hook failed during escape: %s",
                            task.id,
                            task.type,
                            exc,
                        )
                logger.warning(
                    "Task %s (%s) VRAM gate escape: no tasks in flight after %.1fs; "
                    "running despite overflow (used=%sMB estimated=%sMB budget=%sMB overflow=%sMB). "
                    "VRAM baseline exceeds budget — models likely loaded into memory.",
                    task.id,
                    task.type,
                    waited_s,
                    used_mb,
                    estimated_mb,
                    budget_mb,
                    overflow_mb,
                )
                with self._vram_gate_lock:
                    self._vram_reserved_mb += estimated_mb
                return estimated_mb

            if spillover_allowed and waited_s < self.SPILLOVER_GRACE_SECONDS:
                time.sleep(0.1)
                continue

            if spillover_allowed:
                try:
                    getattr(task, "enable_cpu_spillover", lambda: None)()
                    logger.debug(
                        "Task %s (%s) switched to CPU spillover (used=%sMB reserved=%sMB estimated=%sMB budget=%sMB).",
                        task.id,
                        task.type,
                        used_mb,
                        reserved_mb,
                        estimated_mb,
                        budget_mb,
                    )
                    return estimated_mb
                except Exception as exc:
                    logger.warning(
                        "Task %s (%s) CPU spillover hook failed: %s",
                        task.id,
                        task.type,
                        exc,
                    )
            time.sleep(0.1)
        return 0

    def set_task_complete_callback(
        self, callback: Callable[[BaseTask, Optional[BaseException]], None]
    ):
        self._on_task_complete_callbacks = [callback]

    def add_task_complete_callback(
        self, callback: Callable[[BaseTask, Optional[BaseException]], None]
    ):
        self._on_task_complete_callbacks.append(callback)

    def cancel_pending_tasks(self) -> int:
        """Drain and cancel all tasks waiting in both CPU and GPU queues.

        Tasks that are already executing are not interrupted.

        Returns:
            Number of tasks cancelled.
        """
        cancelled = 0
        for q in (self._queue, self._gpu_queue):
            while True:
                try:
                    _priority, _seq, queued_task = q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(queued_task, _StopTask):
                    continue
                try:
                    queued_task.on_cancel()
                except Exception as exc:
                    logger.warning(
                        "Task %s (%s) cancel hook failed: %s",
                        queued_task.id,
                        queued_task.type,
                        exc,
                    )
                queued_task.status = TaskStatus.CANCELLED
                queued_task.completed_at = datetime.now(UTC)
                cancelled += 1
        logger.debug(
            "TaskRunner %s: cancelled %d pending task(s).", self._name, cancelled
        )
        return cancelled

    def cancel_pending_tasks_for_pictures(self, picture_ids: set) -> int:
        """Drain and cancel queued tasks whose ``params['picture_ids']`` overlap *picture_ids*.

        Tasks that are already executing are not interrupted.

        Args:
            picture_ids: Set of Picture IDs to match against task params.

        Returns:
            Number of tasks cancelled.
        """
        cancelled = 0
        for q in (self._queue, self._gpu_queue):
            kept: list[tuple] = []
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    break
                _priority, _seq, queued_task = item
                if isinstance(queued_task, _StopTask):
                    kept.append(item)
                    continue
                task_pids = set((queued_task.params or {}).get("picture_ids") or [])
                if task_pids & picture_ids:
                    try:
                        queued_task.on_cancel()
                    except Exception as exc:
                        logger.warning(
                            "Task %s (%s) cancel hook failed: %s",
                            queued_task.id,
                            queued_task.type,
                            exc,
                        )
                    queued_task.status = TaskStatus.CANCELLED
                    queued_task.completed_at = datetime.now(UTC)
                    cancelled += 1
                else:
                    kept.append(item)
            for item in kept:
                q.put(item)
        logger.debug(
            "TaskRunner %s: cancelled %d pending task(s) for picture ids %s.",
            self._name,
            cancelled,
            picture_ids,
        )
        return cancelled

    def has_active_task_of_type(self, task_type: str) -> bool:
        """Return True if any task of the given type is currently executing."""
        with self._active_task_lock:
            return any(t.type == task_type for t in self._active_tasks.values())

    def get_active_tasks_of_type(self, task_type: str) -> list:
        """Return a list of currently executing task instances of the given type."""
        with self._active_task_lock:
            return [t for t in self._active_tasks.values() if t.type == task_type]

    def start(self):
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            if self._threads:
                return
            self._closed = False
            self._stop.clear()
        for i in range(self._num_workers):
            t = threading.Thread(
                target=self._run,
                args=(self._queue,),
                name=f"{self._name}-cpu-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        # Single dedicated GPU worker — one task at a time, priority-ordered.
        gpu_worker = threading.Thread(
            target=self._run,
            args=(self._gpu_queue,),
            name=f"{self._name}-gpu",
            daemon=True,
        )
        gpu_worker.start()
        self._threads.append(gpu_worker)

    def stop(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()

        # Cancel tasks still waiting in both queues so task-specific background
        # resources (such as preload threads) can be released deterministically.
        for q in (self._queue, self._gpu_queue):
            while True:
                try:
                    _priority, _seq, queued_task = q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(queued_task, _StopTask):
                    continue
                try:
                    queued_task.on_cancel()
                except Exception as exc:
                    logger.warning(
                        "Task %s (%s) cancel hook failed: %s",
                        queued_task.id,
                        queued_task.type,
                        exc,
                    )
                queued_task.status = TaskStatus.CANCELLED
                queued_task.completed_at = datetime.now(UTC)
                queued_task._done_event.set()

        # Cancel tasks that are currently executing so their loops can exit early.
        with self._active_task_lock:
            active = list(self._active_tasks.values())
        for active_task in active:
            try:
                active_task.on_cancel()
            except Exception as exc:
                logger.warning(
                    "Task %s (%s) cancel hook failed (active): %s",
                    active_task.id,
                    active_task.type,
                    exc,
                )

        # Unblock CPU workers + the single GPU worker with stop sentinels.
        for _ in range(self._num_workers):
            self._queue.put((TaskPriority.HIGH, next(self._queue_seq), _StopTask()))
        self._gpu_queue.put((TaskPriority.HIGH, next(self._queue_seq), _StopTask()))
        for t in self._threads:
            t.join(timeout=60)
            if t.is_alive():
                logger.warning(
                    "TaskRunner %s worker %s did not stop within timeout.",
                    self._name,
                    t.name,
                )

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
        target_queue = (
            self._gpu_queue if task.queue_type == QueueType.GPU else self._queue
        )
        target_queue.put((task.priority, next(self._queue_seq), task))
        qsize = target_queue.qsize()
        logger.debug(
            "TaskRunner %s: submitted task id=%s type=%s queue=%s queue_depth=%s",
            self._name,
            task.id,
            task.type,
            task.queue_type,
            qsize,
        )
        return task.id

    def submit_and_wait(self, task: BaseTask, timeout_s: float = 60.0) -> Any:
        """Submit *task* and block until it completes, then return its result.

        This is intended for interactive, user-triggered tasks that need a
        result before the caller can continue (e.g. face detection during a
        search request).  It should be called from a background thread (e.g.
        via ``asyncio.run_in_executor``) so the event loop is not blocked.

        Args:
            task: The task to submit and wait for.
            timeout_s: Maximum seconds to wait before raising ``TimeoutError``.

        Returns:
            The value stored in ``task.result`` after successful completion.

        Raises:
            TimeoutError: The task did not complete within *timeout_s* seconds.
            TaskCancelledError: The task was cancelled before completing
                (e.g. the runner was stopped).
            RuntimeError: The task failed; the original error message is included.
        """
        self.submit(task)
        if not task._done_event.wait(timeout=timeout_s):
            raise TimeoutError(
                f"Task {task.id} ({task.type}) did not complete within {timeout_s}s"
            )
        if task.status == TaskStatus.CANCELLED:
            raise TaskCancelledError(
                f"Task {task.id} ({task.type}) was cancelled before completion"
            )
        if task.status == TaskStatus.FAILED:
            raise RuntimeError(f"Task {task.id} ({task.type}) failed: {task.error}")
        return task.result

    def is_running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def _run(self, work_queue: queue.PriorityQueue):
        logger.debug("TaskRunner %s worker started.", self._name)
        while not self._stop.is_set():
            try:
                _priority, _seq, task = work_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if isinstance(task, _StopTask):
                continue

            if self._stop.is_set():
                try:
                    task.on_cancel()
                except Exception as exc:
                    logger.warning(
                        "Task %s (%s) cancel hook failed after stop: %s",
                        task.id,
                        task.type,
                        exc,
                    )
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(UTC)
                task._done_event.set()
                continue

            logger.debug(
                "TaskRunner %s: dequeued task id=%s type=%s queue=%s queue_depth=%s.",
                self._name,
                task.id,
                task.type,
                task.queue_type,
                work_queue.qsize(),
            )

            # GPU-queue tasks are physically serialised by the single GPU worker
            # thread — only one runs at a time, so there is no concurrent GPU
            # usage to gate against.  Skipping the VRAM gate avoids the
            # spillover escape that fires when loaded-model baseline VRAM
            # already exceeds the configured budget.
            if task.queue_type == QueueType.GPU:
                vram_reserved_mb = 0
            else:
                vram_reserved_mb = self._wait_for_vram_budget(task)

            task_start = time.perf_counter()
            logger.debug(
                "TaskRunner %s: starting task id=%s type=%s.",
                self._name,
                task.id,
                task.type,
            )
            error: Optional[BaseException] = None
            thread_ident = threading.current_thread().ident
            with self._active_task_lock:
                self._active_tasks[thread_ident] = task
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
                with self._active_task_lock:
                    self._active_tasks.pop(thread_ident, None)
                # Always flush PyTorch's CUDA allocator cache after a GPU-queue
                # task so that activation tensors (data, not models) are returned
                # promptly.  CPU-queue tasks only flush when they held a VRAM
                # reservation.
                if task.queue_type == QueueType.GPU:
                    try:
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                            with TaskRunner._vram_cache_lock:
                                TaskRunner._vram_cache_ts = 0.0
                    except Exception:
                        logger.warning(
                            "Failed to flush CUDA cache after task %s (%s): %s",
                            task.id,
                            task.type,
                            traceback.format_exc(),
                        )
                    # Collect Python objects freed during inference (e.g. preloaded
                    # image dicts) and trim glibc's malloc arena so that resident
                    # set size drops back towards the true working set.
                    gc.collect()
                    if platform.system().lower().startswith("linux"):
                        try:
                            trim = getattr(
                                ctypes.CDLL("libc.so.6"), "malloc_trim", None
                            )
                            if trim is not None:
                                trim(0)
                        except Exception:
                            logger.warning(
                                "Failed to trim malloc arena after task %s (%s): %s",
                                task.id,
                                task.type,
                                traceback.format_exc(),
                            )
                elif vram_reserved_mb > 0:
                    with self._vram_gate_lock:
                        self._vram_reserved_mb = max(
                            0, self._vram_reserved_mb - vram_reserved_mb
                        )
                    try:
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                            with TaskRunner._vram_cache_lock:
                                TaskRunner._vram_cache_ts = 0.0
                    except Exception:
                        logger.warning(
                            "Failed to flush CUDA cache after task %s (%s): %s",
                            task.id,
                            task.type,
                            traceback.format_exc(),
                        )
                elapsed_s = time.perf_counter() - task_start
                logger.debug(
                    "TaskRunner %s: finished task id=%s type=%s status=%s elapsed=%.3fs.",
                    self._name,
                    task.id,
                    task.type,
                    task.status,
                    elapsed_s,
                )
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
        logger.debug("TaskRunner %s stopped.", self._name)


class _StopTask(BaseTask):
    def __init__(self):
        super().__init__(task_type="_stop")

    def _run_task(self) -> Any:
        return None

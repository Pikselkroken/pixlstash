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

from typing import Any, Callable, Optional
from datetime import datetime, UTC

from .event_types import EventType
from .pixl_logging import get_logger
from .tasks.base_task import BaseTask, QueueType, TaskPriority, TaskStatus
from .utils.vram_utils import empty_cuda_cache


logger = get_logger(__name__)


class TaskCancelledError(RuntimeError):
    """Raised by ``TaskRunner.submit_and_wait`` when a task is cancelled
    before it had a chance to complete (e.g. the runner was stopped)."""


class TaskRunner:
    """Multi-thread in-memory task orchestrator.

    Tasks are dequeued by priority and executed concurrently across
    *num_workers* background threads.  A single shared PriorityQueue
    ensures correct ordering while all threads remain busy.
    """

    SPILLOVER_GRACE_SECONDS = 1.5
    SPILLOVER_TOLERANCE_MB = 256

    # Pause between attempts after a GPU out-of-memory failure. Long enough to
    # be worth waiting for - whatever else is holding the card has to give some
    # back - and short enough that the single GPU worker is not parked on it:
    # an interactive ``submit_and_wait`` (face detection, character likeness)
    # queues behind this and has a 60 s budget. Two pauses is the worst case.
    VRAM_OOM_RETRY_PAUSE_S = 5.0

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

    def __init__(
        self,
        name: str = "TaskRunner",
        num_workers: int = 1,
        notifier: Optional[Callable[[EventType, Any], None]] = None,
    ):
        self._name = name
        # Bound ``Vault.notify``, so a GPU out-of-memory retry can reach the
        # user's screen. Passed as the bound method rather than the vault for
        # the same reason the work finders take one: this is all the runner
        # needs from it.
        self._notifier = notifier
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
            # nvidia-smi absent/failing is normal on CPU-only hosts; 0 (no VRAM)
            # IS the documented answer, so logging it would be routine noise.
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
            used_mb = sum(
                row_mb
                for row_pid, _name, row_mb in cls._query_compute_apps()
                if row_pid == pid
            )
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

    def _pause_and_report_vram_oom(
        self, task: BaseTask, attempt: int, error: BaseException
    ) -> None:
        """Give the GPU back, tell the user, and wait before the next attempt.

        Called by :meth:`BaseTask.run` between attempts. Flushing the allocator
        cache first is what makes the retry worth making: the failed attempt's
        partial allocations are still reserved by PyTorch until it is.

        Args:
            task: The task that failed.
            attempt: How many attempts have been used, 1-based.
            error: The out-of-memory error, for the log line.
        """
        try:
            if empty_cuda_cache():
                with TaskRunner._vram_cache_lock:
                    TaskRunner._vram_cache_ts = 0.0
        except Exception:
            logger.warning(
                "Failed to flush CUDA cache before retrying task %s (%s): %s",
                task.id,
                task.type,
                traceback.format_exc(),
            )
        self._report_vram_oom(task, attempt, final=False)
        # ``_stop.wait`` rather than ``sleep`` so a shutdown does not have to sit
        # out the pause.
        self._stop.wait(self.VRAM_OOM_RETRY_PAUSE_S)
        if self._stop.is_set():
            # The runner is shutting down: raising abandons the remaining
            # attempts rather than starting an inference pass nobody is waiting
            # for. ``run()`` records it as the task's failure, as it would have
            # done had the last attempt failed.
            raise error

    @classmethod
    def _query_compute_apps(cls) -> list[tuple[int, str, int]]:
        """Every process on the card as nvidia-smi sees it: ``(pid, name, MB)``.

        The one nvidia-smi call behind both the VRAM gate (which wants our own
        total) and the OOM notice (which wants everyone else's name). Raises
        what ``subprocess`` raises; the callers decide what a failure means.
        """
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=cls._NVIDIA_SMI_TIMEOUT_S,
        )
        rows: list[tuple[int, str, int]] = []
        for line in output.splitlines():
            # rsplit, because a process name is a path and may hold commas.
            parts = [part.strip() for part in line.rsplit(",", 1)]
            head = parts[0].split(",", 1) if parts else []
            if len(parts) < 2 or len(head) < 2:
                continue
            try:
                rows.append((int(head[0]), head[1].strip(), int(float(parts[1]))))
            except ValueError as exc:
                logger.debug("Skipping an unparseable nvidia-smi row %r: %s", line, exc)
        return rows

    @classmethod
    def gpu_tenants(cls, limit: int = 3) -> list[dict]:
        """The other processes holding the card, largest first, for the OOM notice.

        "Another program is probably holding the card" was the toast's best
        guess; this names it. The name is nvidia-smi's process path reduced to
        its basename - enough to recognise ``lms`` or ``ComfyUI``'s python -
        and never sent anywhere but this owner's own browser.
        """
        pid = os.getpid()
        try:
            rows = cls._query_compute_apps()
        except Exception as exc:
            # No nvidia-smi, or it stalled: the notice simply names nobody.
            logger.debug("Could not list GPU tenants for the OOM notice: %s", exc)
            return []
        others = sorted(
            (
                {"name": os.path.basename(name) or name, "used_mb": mb}
                for row_pid, name, mb in rows
                if row_pid != pid and mb > 0
            ),
            key=lambda row: -row["used_mb"],
        )
        return others[:limit]

    def _report_vram_oom(
        self, task: BaseTask, attempt: int, final: bool, recovered: bool = False
    ) -> None:
        """Emit the VRAM_OOM event the SPA turns into a toast.

        Every retry sequence ends with one closing frame - ``recovered`` or
        ``gave_up`` - because the SPA coalesces them all onto one card, and a
        card whose last word is "retrying…" describes a state that is over.
        """
        if self._notifier is None:
            return
        try:
            self._notifier(
                EventType.VRAM_OOM,
                {
                    # Diagnostic only, like the envelope's ``event`` field: the
                    # SPA's sentence is about the GPU, not about a task class.
                    "task_type": task.type,
                    "attempt": attempt,
                    "max_attempts": task.VRAM_OOM_ATTEMPTS,
                    "gave_up": final,
                    "recovered": recovered,
                    # Who else is on the card, so the toast can say "LM Studio
                    # is holding 18 GB" instead of guessing. Not on the
                    # recovery frame: the contention is over.
                    "other_processes": [] if recovered else self.gpu_tenants(),
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to announce the GPU out-of-memory retry for task %s (%s): %s",
                task.id,
                task.type,
                exc,
            )

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
            # A single task larger than the whole budget can never satisfy the
            # gate, but it should still not stampede over tasks already in flight.
            # Fall through into the wait loop instead of returning immediately:
            # the loop serializes it (waits for reserved_mb to reach 0) and then
            # runs it alone via the escape hatch, reserving its estimate so later
            # tasks queue behind it rather than piling on top.
            logger.warning(
                "Task %s (%s) estimated VRAM %sMB exceeds configured budget %sMB; "
                "will run alone once the GPU is otherwise idle.",
                task.id,
                task.type,
                estimated_mb,
                budget_mb,
            )

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
            # waiting longer cannot help - the overflow comes from loaded models
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
                    "VRAM baseline exceeds budget - models likely loaded into memory.",
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
                self._cancel_queued_task(queued_task, "drained from the queue")
                cancelled += 1
        logger.debug(
            "TaskRunner %s: cancelled %d pending task(s).", self._name, cancelled
        )
        return cancelled

    def get_active_tasks_of_type(self, task_type: str) -> list:
        """Return a list of currently executing task instances of the given type."""
        with self._active_task_lock:
            return [t for t in self._active_tasks.values() if t.type == task_type]

    def has_active_gpu_tasks(self) -> bool:
        """True while the GPU worker is executing a task."""
        with self._active_task_lock:
            return any(
                t.queue_type == QueueType.GPU for t in self._active_tasks.values()
            )

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
        # Single dedicated GPU worker - one task at a time, priority-ordered.
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
                self._cancel_queued_task(queued_task, "runner stopped")

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
                self._cancel_queued_task(task, "runner stopped before the task ran")
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
            # thread - only one runs at a time, so there is no concurrent GPU
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
                task.run(on_vram_oom=self._pause_and_report_vram_oom)
                # ``vram_oom_attempts`` decides whether the user has a card open
                # about this task; ``attempts_used`` is what that card counts -
                # the attempt that actually finished the work, not the last one
                # that OOMed.
                if task.vram_oom_attempts:
                    # It got there in the end - say so, rather than leaving the
                    # user's last card reading "retrying".
                    self._report_vram_oom(
                        task, task.attempts_used, final=False, recovered=True
                    )
            except Exception as exc:
                error = exc
                # Same split: a task that OOMed twice and then died of something
                # else still has a card open, and it closes naming the attempt
                # that died - as does one abandoned at shutdown, which used one
                # attempt, not three.
                if task.vram_oom_attempts:
                    self._report_vram_oom(task, task.attempts_used, final=True)
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
                        if empty_cuda_cache():
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
                        if empty_cuda_cache():
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
                self._fire_task_complete_callbacks(task, error)
        logger.debug("TaskRunner %s stopped.", self._name)

    def _fire_task_complete_callbacks(
        self, task: BaseTask, error: Optional[BaseException]
    ):
        for callback in list(self._on_task_complete_callbacks):
            try:
                callback(task, error)
            except Exception as callback_exc:
                logger.warning(
                    "Task completion callback failed for %s (%s): %s",
                    task.id,
                    task.type,
                    callback_exc,
                )

    def _cancel_queued_task(self, task: BaseTask, reason: str):
        """Cancel a task that will never run and release everything it holds.

        The completion callbacks are the only path that gives a task's resources
        back: ``BaseTaskFinder.on_task_complete`` discards its claimed picture
        ids and ``WorkPlanner.on_task_complete`` frees its in-flight slot. They
        used to fire only from the worker's ``finally``, so a task cancelled off
        the queue kept both for the life of the process - the finder then sat at
        max in-flight for ever, every finder that ``depends_on()`` it starved,
        and the claimed pictures could never be selected again.

        Args:
            task: The task being cancelled; it has been taken off its queue.
            reason: Why it was cancelled, for the log and the callback error.
        """
        try:
            task.on_cancel()
        except Exception as exc:
            logger.warning(
                "Task %s (%s) cancel hook failed (%s): %s",
                task.id,
                task.type,
                reason,
                exc,
            )
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(UTC)
        task._done_event.set()
        self._fire_task_complete_callbacks(
            task,
            TaskCancelledError(f"Task {task.id} ({task.type}) cancelled: {reason}"),
        )


class _StopTask(BaseTask):
    def __init__(self):
        super().__init__(task_type="_stop")

    def _run_task(self) -> Any:
        return None

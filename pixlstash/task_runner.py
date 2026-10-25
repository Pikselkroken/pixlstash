import ctypes
import gc
import itertools
import platform
import queue
import threading
import traceback
import time

from typing import Any, Callable, Optional
from datetime import datetime, UTC

from .event_types import EventType
from .pixl_logging import get_logger
from .resources.arbiter import Lease, ResourceArbiter, arbiter
from .resources.ledger import ledger
from .tasks.base_task import BaseTask, QueueType, TaskPriority, TaskStatus
from .utils.vram_utils import empty_device_cache


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

    # Pause between attempts after a GPU out-of-memory failure. Long enough to
    # be worth waiting for - whatever else is holding the card has to give some
    # back - and short enough that the single GPU worker is not parked on it:
    # an interactive ``submit_and_wait`` (face detection, character likeness)
    # queues behind this and has a 60 s budget. Two pauses is the worst case.
    VRAM_OOM_RETRY_PAUSE_S = 5.0

    # A task the arbiter refuses GPU memory, and that cannot spill to the CPU,
    # goes back on its queue after this long, doubling to the cap. The worker
    # never waits on it: it takes the next task meanwhile.
    VRAM_REQUEUE_BASE_S = 1.0
    VRAM_REQUEUE_MAX_S = 15.0

    def __init__(
        self,
        name: str = "TaskRunner",
        num_workers: int = 1,
        notifier: Optional[Callable[[EventType, Any], None]] = None,
        resource_arbiter: Optional[ResourceArbiter] = None,
    ):
        self._name = name
        # Bound ``Vault.notify``, so a GPU out-of-memory retry can reach the
        # user's screen. Passed as the bound method rather than the vault for
        # the same reason the work finders take one: this is all the runner
        # needs from it.
        self._notifier = notifier
        # Every task that may touch the GPU asks it for a LIGHT lease first.
        self._arbiter = resource_arbiter or arbiter()
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
        self._active_task_lock = threading.Lock()
        self._active_tasks: dict[int, BaseTask] = {}  # thread ident -> running task
        # Running CPU-queue tasks that were granted GPU memory. Under
        # ``_active_task_lock``.
        self._leased_on_gpu: set[str] = set()
        # Tasks refused GPU memory, waiting out their backoff off the queue:
        # task id -> (timer, task). Under ``_active_task_lock``.
        self._requeued: dict[str, tuple[threading.Timer, BaseTask]] = {}
        self._requeue_counts: dict[str, int] = {}
        # Task id -> why it was stopped to make room, for the ones preempted.
        self._preempted: dict[str, str] = {}
        self._closed = False
        self._on_task_complete_callbacks: list[
            Callable[[BaseTask, Optional[BaseException]], None]
        ] = []

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
            empty_device_cache()
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
                    "other_processes": [] if recovered else ledger().other_processes(),
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to announce the GPU out-of-memory retry for task %s (%s): %s",
                task.id,
                task.type,
                exc,
            )

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
        cancelled += self._cancel_requeued("drained from the queue")
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
            return any(self._on_gpu(t) for t in self._active_tasks.values())

    def preempt_gpu(self, reason: str) -> list[str]:
        """Stop PixlStash's GPU work to make room, at a task boundary.

        Running GPU tasks are asked to stop (``on_cancel``): the ones that can
        stop early do, the rest finish their batch. Queued GPU tasks are
        cancelled; the CPU queue, imports included, is not touched. Interactive
        tasks - URGENT, or waited on by ``submit_and_wait`` - are never stopped.

        Everything stopped ends as ``TaskCancelledError``, the one ending the
        finders do not defer pictures over, so the pictures it did not process
        are found again once the planner runs.

        Args:
            reason: Said in the cancellation and the log ("preempted by ...").

        Returns:
            The worker types that were stopped, for the snapshot's
            ``preempted`` entries.
        """
        stopped: list[str] = []
        with self._active_task_lock:
            running = [
                task
                for task in self._active_tasks.values()
                if self._on_gpu(task) and not self._interactive(task)
            ]
            for task in running:
                self._preempted[task.id] = reason
        for task in running:
            try:
                task.on_cancel()
            except Exception as exc:
                logger.warning(
                    "Task %s (%s) cancel hook failed while making room: %s",
                    task.id,
                    task.type,
                    exc,
                )
                # Not asked to stop, so its ending is not a preemption.
                with self._active_task_lock:
                    self._preempted.pop(task.id, None)
                continue
            stopped.append(task.type)
        kept = []
        while True:
            try:
                item = self._gpu_queue.get_nowait()
            except queue.Empty:
                break
            queued_task = item[2]
            if isinstance(queued_task, _StopTask) or self._interactive(queued_task):
                kept.append(item)
                continue
            self._cancel_queued_task(queued_task, reason)
            stopped.append(queued_task.type)
        for item in kept:
            self._gpu_queue.put(item)
        with self._active_task_lock:
            waiting = [
                task_id
                for task_id, (_timer, task) in self._requeued.items()
                if task.queue_type == QueueType.GPU and not self._interactive(task)
            ]
        for task_id in waiting:
            task = self._take_requeued(task_id)
            if task is not None:
                self._cancel_queued_task(task, reason)
                stopped.append(task.type)
        if stopped:
            logger.warning(
                "TaskRunner %s: stopped %d GPU task(s) (%s): %s",
                self._name,
                len(stopped),
                ", ".join(sorted(set(stopped))),
                reason,
            )
        return stopped

    def wait_for_gpu_idle(self, timeout_s: float = 60.0) -> bool:
        """Block until no GPU task runs, interactive ones included; True if so.

        Interactive tasks are never stopped, but they are short, and unloading
        a model under one makes it return nothing: what this waits for is the
        moment models can be unloaded.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._active_task_lock:
                busy = any(self._on_gpu(t) for t in self._active_tasks.values())
            if not busy:
                return True
            if self._stop.wait(0.1):
                return False
        return False

    def _on_gpu(self, task: BaseTask) -> bool:
        """*task* is running on the card; call under ``_active_task_lock``.

        Every GPU-queue task counts, even one running on the CPU: it holds a
        model, and ``unload_models`` under its batch makes it return nothing.
        """
        return task.queue_type == QueueType.GPU or task.id in self._leased_on_gpu

    @staticmethod
    def _interactive(task: BaseTask) -> bool:
        return task.priority == TaskPriority.URGENT or getattr(task, "_awaited", False)

    def _admit(
        self, task: BaseTask, work_queue: queue.PriorityQueue
    ) -> tuple[bool, Optional[Lease]]:
        """Ask the arbiter for the GPU memory *task* needs, without blocking.

        Every GPU-queue task asks, even one with no estimate, so an exclusive
        lease elsewhere can keep it off the card; a CPU-queue task asks only
        when it says it needs VRAM. Refused, the task spills to the CPU where
        it can, or goes back on its queue after a backoff while this worker
        takes the next one.

        Returns:
            ``(run_now, lease)``. ``run_now`` False means the task was put
            aside and must not run on this pass.
        """
        try:
            need_mb = max(0, int(task.estimated_vram_mb()))
        except Exception as exc:
            logger.warning(
                "Task %s (%s) VRAM estimate failed; asking for none: %s",
                task.id,
                task.type,
                exc,
            )
            need_mb = 0
        if task.queue_type != QueueType.GPU and need_mb <= 0:
            return True, None
        try:
            lease = self._arbiter.try_light(
                task.type,
                need_mb=need_mb,
                worker_type=task.type,
                urgent=self._interactive(task),
            )
        except Exception as exc:
            # Raised here, it would end the worker thread and every task
            # queued behind this one. Taken as a refusal instead: it spills
            # to the CPU or is tried again after the backoff.
            logger.error(
                "Task %s (%s) could not be checked against the GPU's memory: %s",
                task.id,
                task.type,
                exc,
                exc_info=True,
            )
            lease = None
        if lease is not None:
            self._requeue_counts.pop(task.id, None)
            return True, lease
        if task.allow_cpu_spillover():
            try:
                task.enable_cpu_spillover()
                logger.info(
                    "Task %s (%s) moved to the CPU: the GPU has no room for %s MB",
                    task.id,
                    task.type,
                    need_mb,
                )
                self._requeue_counts.pop(task.id, None)
                return True, self._arbiter.note_on_cpu(task.type, task.type)
            except Exception as exc:
                logger.warning(
                    "Task %s (%s) could not move to the CPU, so it waits for "
                    "the GPU instead: %s",
                    task.id,
                    task.type,
                    exc,
                )
        attempts = self._requeue_counts.get(task.id, 0)
        self._requeue_counts[task.id] = attempts + 1
        delay = min(self.VRAM_REQUEUE_MAX_S, self.VRAM_REQUEUE_BASE_S * (2**attempts))
        logger.debug(
            "Task %s (%s) waits %.0fs for GPU memory (%s MB)",
            task.id,
            task.type,
            delay,
            need_mb,
        )
        timer = threading.Timer(delay, self._requeue, args=(task.id, work_queue))
        timer.daemon = True
        with self._active_task_lock:
            self._requeued[task.id] = (timer, task)
        timer.start()
        return False, None

    def _take_requeued(self, task_id: str) -> Optional[BaseTask]:
        with self._active_task_lock:
            entry = self._requeued.pop(task_id, None)
        self._requeue_counts.pop(task_id, None)
        if entry is None:
            return None
        entry[0].cancel()
        return entry[1]

    def _requeue(self, task_id: str, work_queue: queue.PriorityQueue) -> None:
        with self._active_task_lock:
            entry = self._requeued.pop(task_id, None)
        if entry is None:
            return
        task = entry[1]
        if self._stop.is_set():
            self._cancel_queued_task(task, "runner stopped")
            return
        work_queue.put((task.priority, next(self._queue_seq), task))
        if self._stop.is_set():
            # stop() may have drained the queue between the check and the put.
            self._drain(work_queue, "runner stopped")

    def _drain(self, work_queue: queue.PriorityQueue, reason: str) -> None:
        """Cancel every task still waiting in *work_queue*."""
        while True:
            try:
                _priority, _seq, queued_task = work_queue.get_nowait()
            except queue.Empty:
                return
            if not isinstance(queued_task, _StopTask):
                self._cancel_queued_task(queued_task, reason)

    def _cancel_requeued(self, reason: str) -> int:
        with self._active_task_lock:
            task_ids = list(self._requeued)
        cancelled = 0
        for task_id in task_ids:
            task = self._take_requeued(task_id)
            if task is not None:
                self._cancel_queued_task(task, reason)
                cancelled += 1
        return cancelled

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
            self._drain(q, "runner stopped")
        self._cancel_requeued("runner stopped")

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
                It is cancelled, so if it had not started it never runs.
            TaskCancelledError: The task was cancelled before completing
                (e.g. the runner was stopped).
            RuntimeError: The task failed; the original error message is included.
        """
        # Somebody is waiting on it: it is never stopped to make room for
        # other work, and a waiting heavy lease does not hold it back.
        task._awaited = True
        self.submit(task)
        if not task._done_event.wait(timeout=timeout_s):
            # Nobody is waiting any more: a task still queued is skipped when the
            # worker reaches it, rather than running for no one, and one
            # waiting out a GPU backoff is not put back at all.
            task.on_cancel()
            requeued = self._take_requeued(task.id)
            if requeued is not None:
                self._cancel_queued_task(requeued, "nobody is waiting for it any more")
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

            run_now, lease = self._admit(task, work_queue)
            if not run_now:
                continue

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
                if lease is not None and not lease.on_cpu:
                    self._leased_on_gpu.add(task.id)
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
                preempted_by = self._preempted.get(task.id)
                if preempted_by is not None:
                    # Stopped to make room, whatever the task raised on the
                    # way out. A finder defers a batch for the session on any
                    # other error; this one says nothing about the pictures.
                    task.status = TaskStatus.CANCELLED
                    error = TaskCancelledError(
                        f"Task {task.id} ({task.type}) {preempted_by}"
                    )
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
                    self._leased_on_gpu.discard(task.id)
                    self._preempted.pop(task.id, None)
                if lease is not None:
                    lease.release()
                # Always flush PyTorch's CUDA allocator cache after a GPU-queue
                # task so that activation tensors (data, not models) are returned
                # promptly.  CPU-queue tasks only flush when they held a VRAM
                # reservation.
                if task.queue_type == QueueType.GPU:
                    try:
                        empty_device_cache()
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
                elif lease is not None and not lease.on_cpu:
                    try:
                        empty_device_cache()
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
        self._requeue_counts.pop(task.id, None)
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

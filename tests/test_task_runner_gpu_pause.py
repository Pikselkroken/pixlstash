"""Tests for the TaskRunner GPU-queue pause/resume mechanism (GPU lease).

These exercise the pause gate the single GPU worker checks *between* tasks:
pausing must stop NEW GPU tasks from starting (without touching the CPU queue),
must not drop queued tasks, and stop() must stay deadlock-free while paused.
"""

import threading
import time

from pixlstash.task_runner import TaskRunner
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority


class _RecordingTask(BaseTask):
    """Records when it runs and (optionally) blocks until released."""

    def __init__(self, name, queue_type, started_log, gate=None, priority=None):
        super().__init__(task_type=name)
        self._queue_type = queue_type
        self._started_log = started_log
        self._gate = gate
        self._priority = priority or TaskPriority.MEDIUM
        self.finished = threading.Event()

    @property
    def queue_type(self):
        return self._queue_type

    @property
    def priority(self):
        return self._priority

    def _run_task(self):
        self._started_log.append(self.type)
        if self._gate is not None:
            # Block so the test can observe "in-flight" state and drain.
            self._gate.wait(timeout=5.0)
        self.finished.set()
        return None


def _wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_pause_blocks_new_gpu_tasks_but_not_cpu():
    runner = TaskRunner(name="test", num_workers=2)
    runner.start()
    try:
        started: list[str] = []

        # Pause first, then submit a GPU task: it must NOT start while paused.
        runner.pause_gpu_queue()
        assert runner.is_gpu_queue_paused() is True

        gpu_task = _RecordingTask("gpu-1", QueueType.GPU, started)
        cpu_task = _RecordingTask("cpu-1", QueueType.CPU, started)
        runner.submit(gpu_task)
        runner.submit(cpu_task)

        # CPU task still runs while the GPU queue is paused.
        assert cpu_task.finished.wait(timeout=5.0) is True
        assert "cpu-1" in started

        # Give the GPU worker ample time to (wrongly) start the GPU task.
        time.sleep(0.4)
        assert "gpu-1" not in started, "GPU task ran while the queue was paused"
        assert gpu_task.finished.is_set() is False

        # Resume: the queued GPU task must now run (no task loss).
        runner.resume_gpu_queue()
        assert runner.is_gpu_queue_paused() is False
        assert gpu_task.finished.wait(timeout=5.0) is True
        assert "gpu-1" in started
    finally:
        runner.stop()


def test_pause_wait_drains_in_flight_gpu_task():
    runner = TaskRunner(name="test", num_workers=1)
    runner.start()
    try:
        started: list[str] = []
        gate = threading.Event()
        in_flight = _RecordingTask("gpu-busy", QueueType.GPU, started, gate=gate)
        runner.submit(in_flight)

        # Wait until the task is actually executing.
        assert _wait_until(lambda: "gpu-busy" in started) is True

        # pause(wait=True) must NOT return while the task is still running.
        pause_returned = threading.Event()
        pause_result = {}

        def _do_pause():
            pause_result["drained"] = runner.pause_gpu_queue(wait=True, timeout=5.0)
            pause_returned.set()

        t = threading.Thread(target=_do_pause)
        t.start()

        # The pause call should block on the in-flight task.
        assert pause_returned.wait(timeout=0.3) is False
        assert runner.is_gpu_queue_paused() is True  # gate cleared immediately

        # Release the in-flight task; pause(wait=True) should now drain + return.
        gate.set()
        assert pause_returned.wait(timeout=5.0) is True
        assert pause_result["drained"] is True
        t.join(timeout=5.0)
    finally:
        gate.set()
        runner.stop()


def test_pause_wait_times_out_when_task_hangs():
    runner = TaskRunner(name="test", num_workers=1)
    runner.start()
    try:
        started: list[str] = []
        gate = threading.Event()  # never set until cleanup
        hung = _RecordingTask("gpu-hang", QueueType.GPU, started, gate=gate)
        runner.submit(hung)
        assert _wait_until(lambda: "gpu-hang" in started) is True

        # Short timeout: the task never finishes, so drain must report False.
        drained = runner.pause_gpu_queue(wait=True, timeout=0.2)
        assert drained is False
        assert runner.is_gpu_queue_paused() is True
    finally:
        gate.set()
        runner.stop()


def test_no_task_loss_priority_preserved_across_pause():
    runner = TaskRunner(name="test", num_workers=1)
    runner.start()
    try:
        started: list[str] = []

        runner.pause_gpu_queue()
        low = _RecordingTask(
            "gpu-low", QueueType.GPU, started, priority=TaskPriority.LOW
        )
        high = _RecordingTask(
            "gpu-high", QueueType.GPU, started, priority=TaskPriority.HIGH
        )
        runner.submit(low)
        runner.submit(high)

        time.sleep(0.3)
        assert started == [], "no GPU task should run while paused"

        runner.resume_gpu_queue()
        assert high.finished.wait(timeout=5.0) is True
        assert low.finished.wait(timeout=5.0) is True
        # Higher priority (lower enum value) runs first; nothing dropped.
        assert started == ["gpu-high", "gpu-low"]
    finally:
        runner.stop()


def test_stop_does_not_deadlock_while_paused():
    runner = TaskRunner(name="test", num_workers=2)
    runner.start()
    try:
        started: list[str] = []
        runner.pause_gpu_queue()
        # Queue a GPU task that will never get to run because we stop while paused.
        runner.submit(_RecordingTask("gpu-queued", QueueType.GPU, started))
        time.sleep(0.2)
        assert started == []
    finally:
        # stop() must return promptly even though the GPU worker is parked.
        t0 = time.perf_counter()
        runner.stop()
        elapsed = time.perf_counter() - t0
        assert elapsed < 30.0, f"stop() took too long while paused: {elapsed:.1f}s"
    assert runner.is_running() is False


def test_resume_is_idempotent():
    runner = TaskRunner(name="test", num_workers=1)
    runner.start()
    try:
        # Resuming when already running is a harmless no-op.
        runner.resume_gpu_queue()
        runner.resume_gpu_queue()
        assert runner.is_gpu_queue_paused() is False

        started: list[str] = []
        task = _RecordingTask("gpu-x", QueueType.GPU, started)
        runner.submit(task)
        assert task.finished.wait(timeout=5.0) is True
    finally:
        runner.stop()

"""``TaskRunner.submit_and_wait``: a request that gives up leaves nothing behind.

No ``Server`` here: a bare runner with fake tasks reaches everything under test.
"""

import threading

import pytest

from pixlstash.task_runner import TaskRunner
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskStatus


class _GpuTask(BaseTask):
    """Blocks on *gate* if given, and records whether it ran."""

    def __init__(self, gate=None):
        super().__init__(task_type="GpuTask")
        self._gate = gate
        self.ran = threading.Event()

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def _run_task(self):
        self.ran.set()
        if self._gate is not None:
            assert self._gate.wait(timeout=30), "the test never opened the gate"
        return "done"


def test_a_timed_out_task_is_skipped_when_the_worker_reaches_it():
    runner = TaskRunner(name="test-runner")
    runner.start()
    gate = threading.Event()
    blocker = _GpuTask(gate)
    try:
        runner.submit(blocker)
        assert blocker.ran.wait(timeout=30), "the blocker never started"
        late = _GpuTask()
        with pytest.raises(TimeoutError):
            runner.submit_and_wait(late, timeout_s=0.05)

        gate.set()
        assert late._done_event.wait(timeout=30), "the worker never reached it"
        assert not late.ran.is_set(), "a task nobody waits for still ran"
        assert late.status == TaskStatus.CANCELLED
    finally:
        gate.set()
        runner.stop()

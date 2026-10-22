"""PixlStash's GPU work, stopped to make room for heavy work, loses nothing.

The way this loses data silently is the finders' deferral path: a finder that
sees a task end in any error other than ``TaskCancelledError`` defers its
pictures for the rest of the session (``MissingOcrFinder``,
``MissingPromptMatchFinder``). So every preempted task, running or queued,
must end as exactly ``TaskCancelledError``, whatever it raised on the way out.

A real ``TaskRunner`` with plain fake tasks: no ``Server``, nothing to load.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from pixlstash.resources.arbiter import LeaseState, ResourceArbiter
from pixlstash.task_runner import TaskCancelledError, TaskRunner
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority
from pixlstash.tasks.missing_ocr_finder import MissingOcrFinder
from pixlstash.vault import Vault


class _NoCard:
    def devices(self, max_age_s=1.0):
        return []

    def device(self, uuid):
        return None

    def pixlstash_device(self):
        return None

    def other_processes(self, limit=3):
        return []


class _GpuTask(BaseTask):
    """Runs until cancelled, then raises: the worst case for the deferral path."""

    def __init__(self, name, priority=TaskPriority.MEDIUM):
        super().__init__(task_type=name, params={"picture_ids": [1, 2, 3]})
        self._priority = priority
        self.started = threading.Event()

    @property
    def priority(self):
        return self._priority

    @property
    def queue_type(self):
        return QueueType.GPU

    def _run_task(self):
        self.started.set()
        deadline = time.monotonic() + 5
        while not self._cancel_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._cancel_event.is_set():
            raise RuntimeError("model unloaded under the batch")
        return "done"


class _QuickGpuTask(BaseTask):
    def __init__(self):
        super().__init__(task_type="TagTask")

    @property
    def queue_type(self):
        return QueueType.GPU

    def _run_task(self):
        return "done"


@pytest.fixture
def runner():
    runner = TaskRunner(
        name="preempt-test",
        resource_arbiter=ResourceArbiter(_NoCard(), usage=lambda: None),
    )
    endings = {}
    runner.add_task_complete_callback(
        lambda task, error: endings.__setitem__(task.type, error)
    )
    runner.start()
    yield SimpleNamespace(runner=runner, endings=endings)
    runner.stop()


def _until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_preempted_running_and_queued_tasks_end_as_cancelled(runner):
    running = _GpuTask("OcrTask")
    queued = _GpuTask("TagTask")
    runner.runner.submit(running)
    assert running.started.wait(5)
    runner.runner.submit(queued)

    stopped = runner.runner.preempt_gpu("preempted by ComfyUI 'Portrait'")

    assert sorted(stopped) == ["OcrTask", "TagTask"]
    assert _until(lambda: {"OcrTask", "TagTask"} <= set(runner.endings))
    for name in ("OcrTask", "TagTask"):
        error = runner.endings[name]
        assert type(error) is TaskCancelledError, (name, error)
    assert "preempted by ComfyUI 'Portrait'" in str(runner.endings["OcrTask"])
    assert runner.runner.wait_for_gpu_idle(5)


def test_a_preempted_batch_is_found_again_rather_than_deferred(runner):
    """What the finder makes of the ending: its pictures stay eligible."""
    finder = MissingOcrFinder(database=None, engine_getter=lambda: None)
    running = _GpuTask("OcrTask")
    runner.runner.add_task_complete_callback(
        lambda task, error: finder.on_task_complete(task, error)
    )
    runner.runner.submit(running)
    assert running.started.wait(5)
    runner.runner.preempt_gpu("preempted by Training")
    assert _until(lambda: "OcrTask" in runner.endings)
    assert finder._deferred == set(), "a preempted batch was deferred for the session"


def test_interactive_work_is_never_preempted(runner):
    urgent = _GpuTask("FaceDetectionTask", priority=TaskPriority.URGENT)
    runner.runner.submit(urgent)
    assert urgent.started.wait(5)
    assert runner.runner.preempt_gpu("preempted by Training") == []
    assert not urgent._cancel_event.is_set()
    # Never stopped, but waited for: models are not unloaded under it.
    assert runner.runner.wait_for_gpu_idle(0.2) is False
    urgent.on_cancel()
    assert _until(lambda: "FaceDetectionTask" in runner.endings)


def test_a_task_refused_gpu_memory_waits_off_the_queue_and_runs_later():
    """Refused and unable to spill, the task is put back after a backoff; the
    worker is free meanwhile."""

    class _Refusing(ResourceArbiter):
        refusals = 1

        def try_light(self, *args, **kwargs):
            if self.refusals:
                self.refusals -= 1
                return None
            return super().try_light(*args, **kwargs)

    runner = TaskRunner(
        name="refused-test", resource_arbiter=_Refusing(_NoCard(), usage=lambda: None)
    )
    runner.VRAM_REQUEUE_BASE_S = 0.05
    done = threading.Event()
    runner.add_task_complete_callback(lambda task, error: done.set())
    runner.start()
    try:
        task = _QuickGpuTask()
        runner.submit(task)
        assert done.wait(5), "the refused task never ran"
        assert task.status.value == "completed"
    finally:
        runner.stop()


def test_a_requeued_task_cancelled_by_stop_leaves_no_attempt_count():
    class _Refusing(ResourceArbiter):
        def try_light(self, *args, **kwargs):
            return None

    runner = TaskRunner(
        name="refused-stop", resource_arbiter=_Refusing(_NoCard(), usage=lambda: None)
    )
    runner.VRAM_REQUEUE_BASE_S = 30
    runner.start()
    task = _QuickGpuTask()
    try:
        runner.submit(task)
        assert _until(lambda: task.id in runner._requeued)
        assert task.id in runner._requeue_counts
    finally:
        runner.stop()
    assert task.id not in runner._requeue_counts


@pytest.mark.parametrize(
    ("meanwhile", "stopped"),
    [
        (LeaseState.WAITING, ["preempted by Portrait", "unload"]),
        # Granted or withdrawn while the hold was taken: nothing needs the room.
        (LeaseState.GRANTED, []),
        (LeaseState.RELEASED, []),
    ],
)
def test_pixlstash_yields_only_for_a_lease_that_still_waits(meanwhile, stopped):
    done = []
    lease = SimpleNamespace(id="lease-1", label="Portrait", state=LeaseState.WAITING)

    class _Planner:
        def hold(self, owner, seconds):
            lease.state = meanwhile

        def release(self, owner):
            done.append(f"release {owner}")

    vault = SimpleNamespace(
        _task_runner=SimpleNamespace(
            preempt_gpu=lambda reason: done.append(reason) or [],
            wait_for_gpu_idle=lambda: True,
        ),
        _closed=False,
        _work_planner=_Planner(),
        _yield_holds=set(),
        unload_models=lambda: done.append("unload"),
    )
    vault._release_yield_hold = lambda lease_id: Vault._release_yield_hold(
        vault, lease_id
    )
    Vault._yield_gpu(vault, lease)
    assert [step for step in done if not step.startswith("release")] == stopped
    # The hold ends with the yield it was for, or stays for the lease's grant.
    assert (vault._yield_holds == set()) is (meanwhile is not LeaseState.WAITING)

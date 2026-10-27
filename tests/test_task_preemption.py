"""PixlStash's GPU work, stopped to make room for heavy work, loses nothing.

The way this loses data silently is the finders' deferral path: a finder that
sees a task end in any error other than ``TaskCancelledError`` defers its
pictures for the rest of the session (``MissingOcrFinder``,
``MissingPromptMatchFinder``). So every preempted task, running or queued,
must end as exactly ``TaskCancelledError``, whatever it raised on the way out.

A real ``TaskRunner`` with plain fake tasks: no ``Server``, nothing to load.
"""

import queue
import threading
import time
from types import SimpleNamespace

import pytest

from pixlstash.resources.arbiter import LeaseState, ResourceArbiter
from pixlstash.resources.ledger import DeviceReading
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


class _OneCard(_NoCard):
    CARD = "GPU-00000000-0000-0000-0000-000000000001"

    def devices(self, max_age_s=1.0):
        return [
            DeviceReading(
                uuid=self.CARD,
                name="Test GPU",
                total_mb=24576,
                used_mb=0,
                free_mb=24576,
                measurable=True,
            )
        ]

    def device(self, uuid):
        return next((d for d in self.devices() if d.uuid == uuid), None)

    def pixlstash_device(self):
        return self.CARD


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


def test_a_task_whose_cancel_hook_fails_is_not_reported_stopped(runner):
    """Nothing stopped, so the snapshot must not say it was paused."""

    class _Stubborn(_GpuTask):
        def on_cancel(self):
            raise RuntimeError("cannot stop")

    task = _Stubborn("OcrTask")
    runner.runner.submit(task)
    assert task.started.wait(5)
    assert runner.runner.preempt_gpu("preempted by Training") == []
    assert task.id not in runner.runner._preempted, "its ending would read as preempted"
    task._cancel_event.set()
    assert _until(lambda: "OcrTask" in runner.endings)


def test_a_cpu_queue_task_granted_gpu_memory_counts_as_gpu_work():
    """``_admit`` leases a CPU-queue task that says it needs VRAM; it then runs
    on the card, so it is stopped to make room and models wait for it."""
    runner = SimpleNamespace(
        runner=TaskRunner(
            name="leased-test",
            resource_arbiter=ResourceArbiter(_OneCard(), usage=lambda: None),
        ),
        endings={},
    )
    runner.runner.add_task_complete_callback(
        lambda task, error: runner.endings.__setitem__(task.type, error)
    )
    runner.runner.start()

    class _CpuTaskOnTheCard(_GpuTask):
        @property
        def queue_type(self):
            return QueueType.CPU

        def estimated_vram_mb(self):
            return 512

    try:
        task = _CpuTaskOnTheCard("PluginTask")
        runner.runner.submit(task)
        assert task.started.wait(5)
        assert runner.runner.has_active_gpu_tasks()
        assert runner.runner.wait_for_gpu_idle(0.2) is False
        assert runner.runner.preempt_gpu("preempted by Training") == ["PluginTask"]
        assert _until(lambda: "PluginTask" in runner.endings)
        assert type(runner.endings["PluginTask"]) is TaskCancelledError
        assert runner.runner.wait_for_gpu_idle(1)
    finally:
        runner.runner.stop()


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


def test_only_the_batch_refused_gpu_memory_runs_on_the_cpu():
    """CPU fallback is per batch: one refusal, say during an image generation,
    must not keep the rest of a long tagging run on the CPU."""

    class _RefusingOnce(ResourceArbiter):
        refusals = 1

        def try_light(self, *args, **kwargs):
            if self.refusals:
                self.refusals -= 1
                return None
            return super().try_light(*args, **kwargs)

    class _SpillingTask(_QuickGpuTask):
        def __init__(self):
            super().__init__()
            self.on_cpu = False

        def allow_cpu_spillover(self):
            return True

        def enable_cpu_spillover(self):
            self.on_cpu = True

    runner = TaskRunner(
        name="spill-test",
        resource_arbiter=_RefusingOnce(_OneCard(), usage=lambda: None),
    )
    finished = []
    runner.add_task_complete_callback(lambda task, error: finished.append(task))
    runner.start()
    try:
        batches = [_SpillingTask() for _ in range(3)]
        for batch in batches:
            runner.submit(batch)
        assert _until(lambda: len(finished) == 3)
        assert [batch.on_cpu for batch in batches] == [True, False, False]
    finally:
        runner.stop()


def test_an_admission_that_raises_does_not_end_the_worker():
    class _RaisesOnce(ResourceArbiter):
        raised = False

        def try_light(self, *args, **kwargs):
            if not self.raised:
                self.raised = True
                raise RuntimeError("test-ledger-failure")
            return super().try_light(*args, **kwargs)

    runner = TaskRunner(
        name="admit-raises", resource_arbiter=_RaisesOnce(_NoCard(), usage=lambda: None)
    )
    runner.VRAM_REQUEUE_BASE_S = 0.05
    done = threading.Event()
    runner.add_task_complete_callback(lambda task, error: done.set())
    runner.start()
    try:
        task = _QuickGpuTask()
        runner.submit(task)
        assert done.wait(5), "the worker died on the admission error"
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


def test_a_queued_task_cancelled_after_a_requeue_leaves_no_attempt_count():
    """Back on the queue, then cancelled there (preempted, drained)."""
    runner = TaskRunner(
        name="requeued-cancel", resource_arbiter=ResourceArbiter(_NoCard())
    )
    task = _QuickGpuTask()
    runner._requeue_counts[task.id] = 3
    runner._cancel_queued_task(task, "preempted by test")
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

    vault = _vault_on(
        SimpleNamespace(
            preempt_gpu=lambda reason: done.append(reason) or [],
            gpu_worker_alive=lambda: False,
        ),
        lambda: done.append("unload"),
        _work_planner=_Planner(),
    )
    vault._release_yield_hold = lambda lease_id: Vault._release_yield_hold(
        vault, lease_id
    )
    Vault._yield_gpu(vault, lease)
    assert [step for step in done if not step.startswith("release")] == stopped
    # The hold ends with the yield it was for, or stays for the lease's grant.
    assert (vault._yield_holds == set()) is (meanwhile is not LeaseState.WAITING)


def test_a_grant_landing_before_the_hold_does_not_leave_the_planner_held():
    """The listener ends the hold between ``_yield_holds.add`` and ``hold``."""
    lease = SimpleNamespace(id="lease-1", label="Portrait", state=LeaseState.WAITING)
    holds = set()

    class _Planner:
        def hold(self, owner, seconds):
            lease.state = LeaseState.GRANTED
            Vault._on_resource_lease(vault, {"change": "granted", "lease_id": owner})
            holds.add(owner)

        def release(self, owner):
            holds.discard(owner)

    vault = SimpleNamespace(
        _task_runner=SimpleNamespace(preempt_gpu=lambda reason: []),
        _closed=False,
        _work_planner=_Planner(),
        _yield_holds=set(),
        notify=lambda *args: None,
    )
    vault._release_yield_hold = lambda lease_id: Vault._release_yield_hold(
        vault, lease_id
    )
    assert Vault._yield_gpu(vault, lease) == []
    assert holds == set(), "the planner stays held for the rest of the session"


class _HeldGpuTask(BaseTask):
    """A batch that ignores cancellation and ends only when released."""

    def __init__(self):
        super().__init__(task_type="CaptionTask")
        self.started = threading.Event()
        self.release = threading.Event()

    @property
    def queue_type(self):
        return QueueType.GPU

    def _run_task(self):
        self.started.set()
        self.release.wait(5)
        return "done"


def _vault_on(task_runner, unload_models, **fields):
    """The slice of a ``Vault`` that unloading and making room read."""
    vault = SimpleNamespace(
        **{
            "_task_runner": task_runner,
            "_closed": False,
            "_work_planner": None,
            "_yield_holds": set(),
            "_keep_models_in_memory": False,
            "_engine": object(),
            "_last_aggressive_unload_at": 0.0,
            "AGGRESSIVE_UNLOAD_INTERVAL": Vault.AGGRESSIVE_UNLOAD_INTERVAL,
            "_aggressive_unload_lock": threading.Lock(),
            "unload_models": unload_models,
            **fields,
        }
    )
    vault._unload_models_on_worker = lambda when=None: Vault._unload_models_on_worker(
        vault, when
    )
    return vault


def _record_thread(into):
    return lambda: into.append(threading.current_thread().name)


# ── device-cache flushes run on the GPU worker (#1775) ─────────────────────
# Unloading ends in ``torch.mps.empty_cache()`` on Metal, which kills the
# process when another thread runs a model at that moment. So every unload is
# a task on the GPU worker, behind the batch in flight, never beside it.


def test_making_room_unloads_on_the_gpu_worker_once_the_batch_ends(runner, monkeypatch):
    """The arbiter waits briefly; the unload lands after the batch, not under it."""
    monkeypatch.setattr("pixlstash.vault.YIELD_WAIT_S", 0.2)
    unloaded = []
    batch = _HeldGpuTask()
    runner.runner.submit(batch)
    assert batch.started.wait(5)
    lease = SimpleNamespace(id="lease-1", label="Portrait", state=LeaseState.WAITING)
    vault = _vault_on(runner.runner, _record_thread(unloaded))

    began = time.monotonic()
    Vault._yield_gpu(vault, lease)
    assert time.monotonic() - began < 2, "the arbiter's thread waited out the batch"
    assert unloaded == [], "the models were unloaded under the running batch"

    batch.release.set()
    assert _until(lambda: unloaded), "the models were never unloaded"
    assert unloaded == ["preempt-test-gpu"]


def test_a_lease_granted_during_the_wait_unloads_nothing(runner, monkeypatch):
    """The room is there; unloading now would only cost the next batch a reload."""
    monkeypatch.setattr("pixlstash.vault.YIELD_WAIT_S", 0.1)
    unloaded = []
    batch = _HeldGpuTask()
    runner.runner.submit(batch)
    assert batch.started.wait(5)
    lease = SimpleNamespace(id="lease-1", label="Portrait", state=LeaseState.WAITING)
    vault = _vault_on(runner.runner, _record_thread(unloaded))

    Vault._yield_gpu(vault, lease)
    lease.state = LeaseState.GRANTED
    batch.release.set()
    assert _until(lambda: "UnloadModels" in runner.endings)
    assert unloaded == []


def test_the_idle_sweep_unloads_on_the_gpu_worker_once(runner):
    """Reached from a request thread and the event loop: it queues, never flushes.

    Two progress polls at once used to sweep twice; the second one here enters
    while the first is still reading the snapshot.
    """
    unloaded = []
    vault = _vault_on(runner.runner, _record_thread(unloaded))
    reading = threading.Event()

    class _SlowSnapshot(dict):
        def values(self):
            reading.set()
            time.sleep(0.3)
            return super().values()

    first = threading.Thread(
        target=Vault._maybe_aggressive_unload, args=(vault, _SlowSnapshot())
    )
    first.start()
    assert reading.wait(5)
    Vault._maybe_aggressive_unload(vault, {})
    first.join(5)

    assert _until(lambda: unloaded)
    time.sleep(0.2)
    assert unloaded == ["preempt-test-gpu"]


def test_an_unload_queued_before_the_setting_is_switched_back_on_is_skipped(runner):
    """The sweep queues and returns; the owner's later "keep" wins on the worker."""
    unloaded = []
    vault = _vault_on(runner.runner, _record_thread(unloaded))
    vault._task_runner = SimpleNamespace(
        has_active_gpu_tasks=lambda: False,
        gpu_worker_alive=runner.runner.gpu_worker_alive,
        submit=runner.runner.submit,
    )
    batch = _HeldGpuTask()
    runner.runner.submit(batch)
    assert batch.started.wait(5)

    Vault._maybe_aggressive_unload(vault, {})
    vault._keep_models_in_memory = True
    batch.release.set()
    assert _until(lambda: "UnloadModels" in runner.endings)
    assert unloaded == []


def test_stop_leaves_the_models_to_a_worker_that_outlived_the_wait(monkeypatch):
    """``TaskRunner.stop()`` gives up after 60 s; a flush beside it would crash."""
    released = []
    monkeypatch.setattr(
        "pixlstash.vault.FaceExtractionTask.release_detection_models",
        lambda: released.append("faces"),
    )
    for alive in (True, False):
        vault = SimpleNamespace(
            _changed_tags_notify_lock=threading.Lock(),
            _changed_tags_flush_timer=None,
            _changed_tags_pending_ids=set(),
            _started=False,
            _task_runner=SimpleNamespace(gpu_worker_alive=lambda alive=alive: alive),
            _disable_background_workers=False,
            _engine=SimpleNamespace(close=lambda: released.append("engine")),
            db=None,
        )
        Vault.stop(vault)
        assert vault._engine is None
        if alive:
            assert released == [], "flushed beside a worker that is still running"
    assert released == ["faces", "engine"], "a stopped runner releases as before"


def test_a_task_requeued_as_the_runner_stops_is_cancelled_not_stranded():
    """stop() can drain the queue between _requeue's check and its put."""
    runner = TaskRunner(
        name="requeue-stop",
        resource_arbiter=ResourceArbiter(_NoCard(), usage=lambda: None),
    )
    endings = []
    runner.add_task_complete_callback(lambda task, error: endings.append(error))

    class _StopsOnPut(queue.PriorityQueue):
        def put(self, item, *args, **kwargs):
            runner._stop.set()
            super().put(item, *args, **kwargs)

    work_queue = _StopsOnPut()
    task = _QuickGpuTask()
    runner._requeued[task.id] = (threading.Timer(60, lambda: None), task)
    runner._requeue(task.id, work_queue)
    assert work_queue.empty()
    assert task.status.value == "cancelled"
    assert len(endings) == 1


def test_a_task_nobody_waits_for_any_more_leaves_the_gpu_backoff():
    class _Refusing(ResourceArbiter):
        def try_light(self, *args, **kwargs):
            return None

    runner = TaskRunner(
        name="refused-timeout",
        resource_arbiter=_Refusing(_NoCard(), usage=lambda: None),
    )
    runner.VRAM_REQUEUE_BASE_S = 30
    runner.start()
    task = _QuickGpuTask()
    try:
        with pytest.raises(TimeoutError):
            runner.submit_and_wait(task, timeout_s=0.5)
        assert task.id not in runner._requeued
        assert task.status.value == "cancelled"
    finally:
        runner.stop()

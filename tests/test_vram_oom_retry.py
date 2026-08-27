"""GPU out-of-memory handling: retry the batch, say so, change nothing.

The failure this pins is a user plugin whose model load OOMs because something
else (a ComfyUI run, a second model) is holding the card. That used to lose the
whole batch on the first try - and, for descriptions, clear the caption of every
picture in it. See ``BaseTask.run`` and ``DescriptionTask``.

No ``Server`` here on purpose: every piece under test is reachable with plain
fakes, and standing an environment up would cost ~1.35 s per test for nothing.
"""

import asyncio
import os
import threading
from types import SimpleNamespace

import pytest

from pixlstash.event_types import EventType
from pixlstash.task_runner import TaskRunner
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskStatus
from pixlstash.tasks.description_task import DescriptionTask
from pixlstash.utils.vram_utils import is_vram_oom
from pixlstash.ws.broadcaster import WsBroadcasterMixin


class _FakeOom(RuntimeError):
    """Stands in for ``torch.OutOfMemoryError``.

    Deliberately not a torch type: a plugin may run its model through a runtime
    that raises its own class, and the message is what identifies those.
    """

    def __init__(self):
        super().__init__("CUDA out of memory. Tried to allocate 24.00 MiB.")


class _OomTask(BaseTask):
    """Fails with an OOM for the first *fail_times* attempts."""

    def __init__(self, fail_times: int):
        super().__init__(task_type="OomTask")
        self._fail_times = fail_times
        self.attempts = 0

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def _run_task(self):
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise _FakeOom()
        return "done"


def test_is_vram_oom_reads_the_message_when_the_type_is_not_from_torch():
    assert is_vram_oom(_FakeOom())
    assert not is_vram_oom(RuntimeError("model file is corrupt"))
    assert not is_vram_oom(FileNotFoundError("no such file"))


def test_is_vram_oom_ignores_an_out_of_memory_that_is_not_the_gpus():
    # SQLITE_NOMEM says "out of memory" too, and its task must not be re-run.
    assert not is_vram_oom(RuntimeError("out of memory"))
    assert not is_vram_oom(MemoryError("Unable to allocate 4.00 GiB for an array"))


def test_is_vram_oom_recognises_onnx_runtimes_full_arena():
    """ORT says neither "out of memory" nor a device word; this is the message
    a full card produced on 2026-08-27 with an LLM holding the VRAM."""
    assert is_vram_oom(
        RuntimeError(
            "[ONNXRuntimeError] : 1 : FAIL : Non-zero status code returned while "
            "running Conv node. Name:'Conv_3' Status Message: bfc_arena.cc:359 "
            "void* onnxruntime::BFCArena::AllocateRawInternal(size_t, bool, "
            "onnxruntime::Stream*) Failed to allocate memory for requested "
            "buffer of size 90063104"
        )
    )


def test_is_vram_oom_looks_through_a_plugin_wrapper():
    # `raise RuntimeError(...) from oom` is how a plugin reports one.
    wrapper = RuntimeError("plugin 'example-plugin' failed to load its model")
    wrapper.__cause__ = _FakeOom()
    assert is_vram_oom(wrapper)


def test_a_cpu_queue_task_is_never_retried():
    class _CpuOomTask(_OomTask):
        @property
        def queue_type(self) -> QueueType:
            return QueueType.CPU

    task = _CpuOomTask(fail_times=99)
    with pytest.raises(_FakeOom):
        task.run(on_vram_oom=lambda *_: pytest.fail("CPU-queue work is not re-run"))
    # Import and purge tasks live on the CPU queue and move real files; a blind
    # second pass over one is not a retry, it is a second effect.
    assert task.attempts == 1


def test_a_transient_oom_is_retried_and_the_batch_survives():
    task = _OomTask(fail_times=2)
    reported = []

    assert task.run(on_vram_oom=lambda t, attempt, exc: reported.append(attempt)) == (
        "done"
    )

    assert task.attempts == 3
    assert task.status == TaskStatus.COMPLETED
    # Reported between attempts only - never after the one that succeeded.
    assert reported == [1, 2]


def test_a_permanent_oom_fails_after_three_attempts():
    task = _OomTask(fail_times=99)
    reported = []

    with pytest.raises(_FakeOom):
        task.run(on_vram_oom=lambda t, attempt, exc: reported.append(attempt))

    assert task.attempts == BaseTask.VRAM_OOM_ATTEMPTS == 3
    assert task.status == TaskStatus.FAILED
    assert task.vram_oom_attempts == 3
    # The last attempt does not announce a retry it is not going to make; the
    # runner announces the give-up instead (test below).
    assert reported == [1, 2]
    # Settled exactly once, at the end: a `submit_and_wait` caller must not be
    # woken by a failed attempt that is about to be retried.
    assert task._done_event.is_set()


def test_a_non_oom_failure_is_not_retried():
    class _BrokenTask(BaseTask):
        def __init__(self):
            super().__init__(task_type="BrokenTask")
            self.attempts = 0

        def _run_task(self):
            self.attempts += 1
            raise RuntimeError("model file is corrupt")

    task = _BrokenTask()
    with pytest.raises(RuntimeError):
        task.run(on_vram_oom=lambda *_: pytest.fail("must not retry a real failure"))
    assert task.attempts == 1
    assert task.vram_oom_attempts == 0


def _run_through_the_worker(task, monkeypatch):
    """Submit *task* to a real ``TaskRunner`` and return the events it emitted.

    Through the worker on purpose: everything the SPA ever sees is produced by
    ``TaskRunner._run``, so a test that calls ``task.run()`` by hand can pass
    with the whole reporting path deleted.
    """
    monkeypatch.setattr(TaskRunner, "VRAM_OOM_RETRY_PAUSE_S", 0.0)
    events = []
    runner = TaskRunner(
        name="test-runner",
        notifier=lambda event_type, data: events.append((event_type, data)),
    )
    finished = threading.Event()
    runner.add_task_complete_callback(lambda *_: finished.set())
    runner.start()
    try:
        runner.submit(task)
        assert finished.wait(timeout=30), "the worker never finished the task"
    finally:
        runner.stop()
    return events


def test_the_runner_reports_every_attempt_and_the_give_up(monkeypatch):
    task = _OomTask(fail_times=99)

    events = _run_through_the_worker(task, monkeypatch)

    assert task.attempts == 3
    assert [event_type for event_type, _ in events] == [EventType.VRAM_OOM] * 3
    # The toast counts the attempts used, so all three frames have to differ.
    assert [data["attempt"] for _, data in events] == [1, 2, 3]
    assert [data["gave_up"] for _, data in events] == [False, False, True]
    assert {data["max_attempts"] for _, data in events} == {3}


def test_the_runner_closes_the_notice_when_a_retry_succeeds(monkeypatch):
    task = _OomTask(fail_times=1)

    events = _run_through_the_worker(task, monkeypatch)

    assert task.status == TaskStatus.COMPLETED
    # Two frames: the retry, then the recovery. Without the second one the
    # user's last word on work that succeeded is "Retrying".
    assert [data["recovered"] for _, data in events] == [False, True]
    # Attempt 1 OOMed, attempt 2 did the work - so the closing frame names 2.
    # Repeating the last *failed* attempt here would have the card claim the
    # work finished on the attempt that did not finish it.
    assert [data["attempt"] for _, data in events] == [1, 2]


def test_a_non_oom_death_after_an_oom_still_closes_the_notice(monkeypatch):
    class _ThenBrokenTask(_OomTask):
        def _run_task(self):
            self.attempts += 1
            if self.attempts == 1:
                raise _FakeOom()
            raise RuntimeError("model file is corrupt")

    events = _run_through_the_worker(_ThenBrokenTask(fail_times=0), monkeypatch)

    # The card is open because of the first attempt, so something has to close
    # it even though the exception that ended the task is not an OOM. It closes
    # naming attempt 2 - the one that died - and short of the 3 the task was
    # entitled to, which is what tells the SPA not to promise a later retry.
    assert [data["gave_up"] for _, data in events] == [False, True]
    assert [data["attempt"] for _, data in events] == [1, 2]
    assert events[-1][1]["max_attempts"] == 3


def test_the_wire_payload_carries_the_attempt_count():
    class _FakeWs:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

    class _Broadcaster(WsBroadcasterMixin):
        def __init__(self, ws):
            self._ws_clients = [{"ws": ws, "owner": True, "filters": {}}]
            self._ws_clients_lock = threading.Lock()

    ws = _FakeWs()
    asyncio.run(
        _Broadcaster(ws)._broadcast_ws_event(
            EventType.VRAM_OOM,
            {
                "task_type": "DescriptionTask",
                "attempt": 2,
                "max_attempts": 3,
                "gave_up": False,
                "recovered": False,
            },
        )
    )

    assert ws.sent == [
        {
            "type": "vram_oom",
            "event": "VRAM_OOM",
            "task_type": "DescriptionTask",
            "attempt": 2,
            "max_attempts": 3,
            "gave_up": False,
            "recovered": False,
            "source": "external",
            "origin_client_id": None,
        }
    ]


def test_the_event_is_not_filtered_out_by_a_clients_grid_filters():
    # It describes the machine, so no grid filter may suppress it - and a
    # non-owner socket must not receive vault-wide activity at all.
    assert WsBroadcasterMixin._should_send_ws_update(
        None, EventType.VRAM_OOM, {"selected_character": 7}
    )


def test_a_shutdown_abandons_the_remaining_attempts():
    runner = TaskRunner(name="test-runner")
    runner._stop.set()  # as ``stop()`` does, while a task is mid-flight
    task = _OomTask(fail_times=99)

    with pytest.raises(_FakeOom):
        task.run(on_vram_oom=runner._pause_and_report_vram_oom)

    # One attempt, not three: nobody is waiting for the second inference pass.
    assert task.attempts == 1


class _OomWorkflow:
    def generate_batch(self, pictures, engine_override=None, stop_event=None):
        raise _FakeOom()


class _RefusingDb:
    """A database that fails the test if the task tries to write."""

    image_root = "/home/me/pictures"

    def run_immediate_read_task(self, func, *args, **kwargs):
        return set()  # nothing locked

    def run_task(self, *args, **kwargs):
        raise AssertionError("a VRAM OOM must not write anything to the database")


def test_an_oom_leaves_existing_descriptions_alone():
    pictures = [
        SimpleNamespace(id=1, description="a cat on a wall"),
        SimpleNamespace(id=2, description="a dog in a hat"),
    ]
    task = DescriptionTask(
        database=_RefusingDb(),
        workflow=_OomWorkflow(),
        pictures=pictures,
        engine_override="moondream2",
    )

    with pytest.raises(_FakeOom):
        task.run()

    # Not "" - the whole point. The captions are untouched and the picture is
    # left for the finder to pick up again once the GPU has room.
    assert [pic.description for pic in pictures] == [
        "a cat on a wall",
        "a dog in a hat",
    ]
    assert task.status == TaskStatus.FAILED


def test_the_notice_names_the_other_process_on_the_card(monkeypatch):
    """ "Another program is probably holding the card" was a guess; name it."""
    monkeypatch.setattr(
        TaskRunner,
        "_query_compute_apps",
        classmethod(
            lambda cls: [
                (os.getpid(), "/usr/bin/python3", 4338),
                (566190, "/opt/LM Studio/lms", 18432),
                (566191, "/usr/lib/xorg/Xorg", 210),
            ]
        ),
    )
    task = _OomTask(fail_times=1)

    events = _run_through_the_worker(task, monkeypatch)

    retry, recovery = (data for _, data in events)
    assert retry["other_processes"] == [
        {"name": "lms", "used_mb": 18432},
        {"name": "Xorg", "used_mb": 210},
    ], "largest first, ourselves excluded, basename only"
    assert recovery["other_processes"] == [], "the contention is over"


def test_the_notice_names_nobody_without_nvidia_smi(monkeypatch):
    def missing(cls):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(TaskRunner, "_query_compute_apps", classmethod(missing))
    task = _OomTask(fail_times=99)

    events = _run_through_the_worker(task, monkeypatch)

    assert all(data["other_processes"] == [] for _, data in events)

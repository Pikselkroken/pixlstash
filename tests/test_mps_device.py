"""Apple Metal (MPS) support, and the device handling around it on every platform.

Stand-ins replace torch's device probes and the models, so these run on any
host. The tests that need a real Apple Metal GPU are marked and skip without one.
"""

import contextlib
import gc
import logging
import sys
import threading
import types
import weakref

import pytest

import pixlstash.inference.model_lifecycle as model_lifecycle_module
from pixlstash.inference.model_lifecycle import ModelLifecycleManager
from pixlstash.task_runner import (
    TaskRunner,
)
from pixlstash.tasks.base_task import (
    BaseTask,
    QueueType,
    TaskInterruptedError,
    TaskStatus,
)
from pixlstash.utils.device_utils import (
    detect_device,
    empty_device_cache,
    is_accelerator,
)


def _fake_torch(*, cuda=False, mps=False, cuda_raises=None, mps_raises=None):
    """A stand-in torch exposing only the two availability probes."""

    def cuda_available():
        if cuda_raises is not None:
            raise cuda_raises
        return cuda

    def mps_available():
        if mps_raises is not None:
            raise mps_raises
        return mps

    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=cuda_available,
            empty_cache=lambda: None,
        ),
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=mps_available)
        ),
        mps=types.SimpleNamespace(empty_cache=lambda: None),
    )


@pytest.fixture
def fake_torch(monkeypatch):
    """Install a stand-in torch in ``sys.modules``.

    Both ``detect_device`` (which imports torch) and ``empty_device_cache``
    (which reads ``sys.modules``) resolve it from there, so seeding the module
    table covers both without either touching real hardware.
    """

    def apply(mod):
        monkeypatch.setitem(sys.modules, "torch", mod)
        return mod

    return apply


# --------------------------------------------------------------------------- #
# detect_device
# --------------------------------------------------------------------------- #


def test_cpu_when_neither_available(fake_torch):
    fake_torch(_fake_torch(cuda=False, mps=False))
    assert detect_device() == "cpu"


@pytest.mark.parametrize(
    "failure",
    [ImportError("no torch"), OSError("dlopen(libtorch_cpu.dylib): image not found")],
    ids=["import-error", "shared-library"],
)
def test_missing_torch_reports_cpu(monkeypatch, failure):
    # An import that raises must answer "cpu", not abort the caller. A torch
    # whose shared libraries will not load raises OSError, not ImportError.
    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def boom(name, *args, **kwargs):
        if name == "torch":
            raise failure
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr("builtins.__import__", boom)
    assert detect_device() == "cpu"


def _device_warnings(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "pixlstash.utils.device_utils"
        and record.levelno == logging.WARNING
    ]


@pytest.mark.parametrize(
    "torch_mod,expected,named",
    [
        (None, "cpu", "torch could not be imported"),
    ],
    ids=["import"],
)
def test_a_broken_torch_is_logged_at_warning(
    monkeypatch, caplog, torch_mod, expected, named
):
    """A device lost to a broken install has to say why at the default level.

    detect_device never raises, so its callers - the engine, JoyCaption,
    ``plugins test`` - cannot report the reason themselves. ``None`` in
    ``sys.modules`` makes ``import torch`` raise.
    """
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    with caplog.at_level(logging.WARNING):
        assert detect_device() == expected

    warnings_logged = _device_warnings(caplog)
    assert len(warnings_logged) == 1, warnings_logged
    assert named in warnings_logged[0]


@pytest.mark.parametrize("cuda,mps", [(False, False), (True, False), (False, True)])
def test_a_working_torch_detects_without_a_warning(fake_torch, caplog, cuda, mps):
    # The control for the test above: a CPU-only host answers False from both
    # probes, so the warnings must not fire on every ordinary machine.
    fake_torch(_fake_torch(cuda=cuda, mps=mps))
    with caplog.at_level(logging.WARNING):
        detect_device()

    assert _device_warnings(caplog) == []


# --------------------------------------------------------------------------- #
# is_accelerator
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "device,expected",
    [
        ("cuda", True),
        ("mps", True),
        ("cuda:0", True),
        ("mps:0", True),
        ("MPS", True),
        ("cpu", False),
        (None, False),
    ],
)
def test_is_accelerator(device, expected):
    assert is_accelerator(device) is expected


def test_is_accelerator_accepts_torch_device_objects():
    # Call sites hold both a string and a torch.device; both must answer.
    # Building a torch.device touches no hardware.
    import torch

    assert is_accelerator(torch.device("mps")) is True
    assert is_accelerator(torch.device("cuda", 1)) is True
    assert is_accelerator(torch.device("cpu")) is False


@pytest.mark.parametrize("device", ["mps:0", "cuda:1", "MPS:0"])
def test_is_accelerator_ignores_the_device_index(device):
    """``str(torch.device("mps:0"))`` carries an index; the type alone does not.

    Both spellings reach these call sites, so the index has to be stripped
    before the lookup. Without this the string form of an indexed device
    reads as "not an accelerator" and skips the retry path entirely.
    """
    assert is_accelerator(device) is True


# --------------------------------------------------------------------------- #
# empty_device_cache
# --------------------------------------------------------------------------- #


def test_empty_device_cache_flushes_mps(fake_torch):
    calls = []
    mod = _fake_torch(cuda=False, mps=True)
    mod.mps.empty_cache = lambda: calls.append("mps")
    mod.cuda.empty_cache = lambda: calls.append("cuda")
    fake_torch(mod)

    assert empty_device_cache() is True
    assert calls == ["mps"]


@pytest.mark.parametrize(
    "device,expected",
    [("mps", ["mps"]), ("cuda", ["cuda"]), (None, ["cuda", "mps"])],
    ids=["mps-only", "cuda-only", "both"],
)
def test_empty_device_cache_flushes_only_the_named_backend(
    fake_torch, device, expected
):
    """A named device flushes that backend alone; no device flushes both.

    Both backends are available here, so a filter that let every backend
    through would flush both for a named device.
    """
    flushed = []
    torch = _fake_torch(cuda=True, mps=True)
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    torch.mps.empty_cache = lambda: flushed.append("mps")
    fake_torch(torch)

    assert empty_device_cache(device) is True
    assert flushed == expected


@pytest.mark.parametrize(
    "cuda,mps,expected",
    [(True, False, ["cuda"]), (False, False, [])],
    ids=["cuda-only-host", "cpu-only-host"],
)
def test_empty_device_cache_skips_a_backend_that_is_not_available(
    fake_torch, cuda, mps, expected
):
    """A host without Metal never calls torch.mps.empty_cache, and the reverse.

    Flushing an absent backend raises, and every teardown on a CUDA host would
    then log a flush failure.
    """
    flushed = []
    torch = _fake_torch(cuda=cuda, mps=mps)
    torch.cuda.empty_cache = lambda: flushed.append("cuda")
    torch.mps.empty_cache = lambda: flushed.append("mps")
    fake_torch(torch)

    assert empty_device_cache() is bool(expected)
    assert flushed == expected


def test_empty_device_cache_is_a_noop_without_torch(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert empty_device_cache() is False


@pytest.mark.parametrize(
    "backend,label",
    [("cuda", "CUDA"), ("mps", "Apple Metal")],
    ids=["cuda", "mps"],
)
def test_a_failing_flush_is_logged_at_warning(fake_torch, caplog, backend, label):
    """A flush that raises has to be visible at the default log level.

    task_runner wraps every flush in a WARNING handler, but the helper catches
    the exception itself, so that handler never fires; this log line is the
    only place the failure can show.
    """
    torch = _fake_torch(cuda=backend == "cuda", mps=backend == "mps")

    def _raise():
        raise RuntimeError("allocator is wedged")

    getattr(torch, backend).empty_cache = _raise
    fake_torch(torch)

    with caplog.at_level(logging.WARNING, logger="pixlstash.utils.device_utils"):
        assert empty_device_cache(backend) is False

    warnings_logged = _device_warnings(caplog)
    assert len(warnings_logged) == 1, caplog.text
    assert label in warnings_logged[0]
    assert "allocator is wedged" in warnings_logged[0]


class _CyclicModel:
    """A stand-in model in a reference cycle, as the loaded LLaVA model is."""

    def __init__(self):
        self.self_reference = self


@contextlib.contextmanager
def _only_explicit_collections():
    """Turn automatic garbage collection off, so only an explicit one frees a cycle.

    Proves it took effect first: a cycle dropped inside must still be alive.
    """
    collecting = gc.isenabled()
    gc.disable()
    try:
        probe = _CyclicModel()
        probe_ref = weakref.ref(probe)
        del probe
        assert probe_ref() is not None, (
            "a dropped cycle was freed without a collection, so a test cannot tell "
            "a flush before the collection from one after it"
        )
        yield
    finally:
        if collecting:
            gc.enable()


class _CyclicModelService:
    """A service whose ``unload`` drops a model held in a reference cycle."""

    def __init__(self):
        self.model = _CyclicModel()

    def unload(self):
        self.model = None


@pytest.mark.parametrize("unload", ["aggressive_unload", "safe_idle_unload"])
def test_a_lifecycle_unload_collects_the_models_before_flushing(unload, monkeypatch):
    """The engine's unloads flush after the collection that frees the models.

    Same reason as JoyCaption's unload above: an unloaded model in a reference
    cycle is freed only by ``gc.collect()``.
    """
    service = _CyclicModelService()
    model_ref = weakref.ref(service.model)
    alive_at_flush = []
    monkeypatch.setattr(
        model_lifecycle_module,
        "empty_cuda_cache",
        lambda: alive_at_flush.append(model_ref() is not None) or True,
    )

    with _only_explicit_collections():
        getattr(ModelLifecycleManager(device="mps"), unload)(clip_service=service)

    assert service.model is None
    assert alive_at_flush == [False], (
        f"{unload} flushed the device cache while the unloaded model was alive"
    )


@pytest.fixture
def gpu_runner():
    """A started ``TaskRunner``, stopped when the test ends.

    Per test, not per module: start plus stop costs about 0.1 ms, and
    ``stop()`` joins the GPU worker. Its post-task cache flush reads
    ``sys.modules["torch"]``, so a runner outliving its test could flush
    against the stand-in torch a later test installs.
    """
    runner = TaskRunner(name="gpu-call-test")
    runner.start()
    try:
        yield runner
    finally:
        runner.stop()


class _ThreadProbeTask(BaseTask):
    """An ordinary task that returns the thread it ran on.

    Args:
        queue: The queue it runs on, the GPU queue unless given.
    """

    def __init__(self, queue=QueueType.GPU):
        super().__init__(task_type="ThreadProbeTask")
        self._queue = queue

    @property
    def queue_type(self) -> QueueType:
        return self._queue

    def _run_task(self):
        return threading.current_thread()


class _ExitingTask(BaseTask):
    """A task whose ``_run_task`` raises *interrupt* on the queue named.

    Args:
        queue: The queue the task runs on.
        interrupt: The ``BaseException`` to raise.

    Attributes:
        ran_on: The worker thread that ran it.
    """

    def __init__(self, queue, interrupt):
        super().__init__(task_type="ExitingTask")
        self._queue = queue
        self._interrupt = interrupt
        self.ran_on = None

    @property
    def queue_type(self) -> QueueType:
        return self._queue

    def _run_task(self):
        self.ran_on = threading.current_thread()
        raise self._interrupt


@pytest.mark.parametrize("queue", [QueueType.CPU, QueueType.GPU], ids=["cpu", "gpu"])
@pytest.mark.parametrize(
    "interrupt", [SystemExit(3), KeyboardInterrupt()], ids=["exit", "interrupt"]
)
def test_a_task_that_raises_an_exit_fails_and_its_worker_runs_on(
    gpu_runner, caplog, queue, interrupt
):
    """A ``sys.exit()`` inside a task fails that task and ends no worker.

    A third-party tagger plugin runs inside ``TagTask``, an ordinary GPU task.
    Each queue of this runner has one worker, so the next task on the queue
    running on the same thread is what shows the thread survived.
    """
    settled = threading.Event()
    errors = []
    task = _ExitingTask(queue, interrupt)

    def on_complete(completed, error):
        if completed is task:
            errors.append(error)
            settled.set()

    gpu_runner.add_task_complete_callback(on_complete)
    with caplog.at_level(logging.WARNING, logger="pixlstash.tasks.base_task"):
        with pytest.raises(RuntimeError, match="failed"):
            gpu_runner.submit_and_wait(task, timeout_s=10)
        assert settled.wait(10), "the runner never reported the task settled"

    assert task.status == TaskStatus.FAILED
    assert len(errors) == 1
    assert isinstance(errors[0], TaskInterruptedError), errors
    assert errors[0].__cause__ is interrupt
    assert gpu_runner.submit_and_wait(_ThreadProbeTask(queue), timeout_s=5) is (
        task.ran_on
    ), "the next task did not run on the same worker"
    logged = [
        record.getMessage()
        for record in caplog.records
        if record.name == "pixlstash.tasks.base_task"
        and record.levelno == logging.WARNING
    ]
    assert len(logged) == 1, logged
    assert task.id in logged[0]
    assert "ExitingTask" in logged[0]
    assert type(interrupt).__name__ in logged[0]

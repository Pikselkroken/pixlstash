"""Where a search encodes its query, when the copies load, and what it costs.

A search encodes on the thread handling it - the text path inside the database
task, likeness search inline in its async handler - while the GPU worker runs
the embedding and tagging batches. Torch's Metal backend cannot take two
threads, and it does not raise when it gets them: the process dies or hangs, so
nothing downstream can recover. ``InferenceEngine.create`` therefore builds CPU
copies of the query encoders when, and only when, its device is Metal, and the
two workflow entry points route to them.

Three directions are asserted, because each is its own regression:

* a query that reaches the accelerator is the crash;
* a **picture** embedding pushed onto the CPU would quietly move the whole
  library's indexing off the GPU - the mistake a blanket "send everything to
  the CPU copies" fix makes;
* and loading the copies in ``create`` costs 7.3 s of every Metal boot, because
  every other engine service is lazy and these would be the first models in the
  process. That one was shipped and caught only by timing the real application,
  so it is pinned here.

No weights load. The services are stubs: what is under test is when the load is
asked for and who serves the query, not open_clip or sentence-transformers.
"""

import logging
import threading
import types

import pytest

from pixlstash.inference.cpu_query_encoders import (
    CpuQueryEncoders,
    CpuQueryEncodersNotReadyError,
    build_cpu_query_encoders,
)
from pixlstash.inference.workflows.clip_embedding import ClipEmbeddingWorkflow
from pixlstash.inference.workflows.text_embedding import TextEmbeddingWorkflow
from pixlstash.tasks.base_task import QueueType, TaskPriority, TaskStatus
from pixlstash.tasks.cpu_query_encoder_load_task import CpuQueryEncoderLoadTask


class _RecordingService:
    """Stands in for ClipService/SBertService and records what reached it."""

    def __init__(self, label, loads=True):
        self.label = label
        self.loads = loads
        self.loaded = False
        self.calls = []
        self.unloads = 0
        self.unload_raises = False

    def unload(self):
        self.unloads += 1
        if self.unload_raises:
            raise RuntimeError(f"{self.label}: unload failed")
        self.loaded = False

    def is_loaded(self):
        return self.loaded

    def ensure_ready(self):
        self.calls.append(("ensure_ready", None))
        if not self.loads:
            raise RuntimeError(f"{self.label}: no weights on disk")
        self.loaded = True

    def encode(self, texts):
        self.calls.append(("encode", tuple(texts)))
        return [f"{self.label}-sbert"]

    def encode_text(self, query):
        self.calls.append(("encode_text", query))
        return f"{self.label}-clip-text"

    def encode_image_batch(self, images, tensors=None):
        self.calls.append(("encode_image_batch", len(images)))
        return f"{self.label}-clip-image"


def _pair(clip_loads=True, sbert_loads=True):
    return CpuQueryEncoders(
        _RecordingService("cpu", loads=clip_loads),
        _RecordingService("cpu", loads=sbert_loads),
    )


def _loaded_pair():
    """A pair that has already served its load, as it is after start-up."""
    pair = _pair()
    pair.load()
    assert pair.is_loaded()
    return pair


def _engine(*, with_cpu_copies):
    """An engine stub with the accelerator's services, and optionally the copies."""
    return types.SimpleNamespace(
        clip_service=_RecordingService("device"),
        sbert_service=_RecordingService("device"),
        query_encoders=_loaded_pair() if with_cpu_copies else None,
        device="mps" if with_cpu_copies else "cuda",
    )


# ---------------------------------------------------------------------------
# The query paths: off the accelerator wherever the copies exist
# ---------------------------------------------------------------------------


def test_a_text_query_is_encoded_on_the_cpu_copy_when_there_is_one():
    """The crash this exists to stop: this call runs on the search thread."""
    engine = _engine(with_cpu_copies=True)

    assert TextEmbeddingWorkflow(engine).encode_query("A Cat") == ["cpu-sbert"]
    assert engine.sbert_service.calls == [], "the query reached the accelerator"


def test_a_clip_text_query_is_encoded_on_the_cpu_copy_when_there_is_one():
    engine = _engine(with_cpu_copies=True)

    assert TextEmbeddingWorkflow(engine).encode_clip_query("a cat") == "cpu-clip-text"
    assert engine.clip_service.calls == [], "the query reached the accelerator"


def test_a_likeness_query_image_is_encoded_on_the_cpu_copy_when_there_is_one():
    """The one likeness-search path that runs on a request thread. The stored
    picture embeddings it is compared against stay on the worker."""
    engine = _engine(with_cpu_copies=True)

    result = ClipEmbeddingWorkflow(engine).encode_query_image(object())

    assert result == "cpu-clip-image"
    assert engine.clip_service.calls == [], "the query image reached the accelerator"


# ---------------------------------------------------------------------------
# The other direction: hosts that need no protection keep their accelerator
# ---------------------------------------------------------------------------


def test_without_cpu_copies_a_text_query_uses_the_engines_own_service():
    """CUDA and CPU hosts have ``query_encoders is None`` and must not pay for
    a second model or a slower encode."""
    engine = _engine(with_cpu_copies=False)

    assert TextEmbeddingWorkflow(engine).encode_query("A Cat") == ["device-sbert"]
    assert engine.sbert_service.calls == [("encode", ("a cat",))]


def test_without_cpu_copies_a_clip_text_query_uses_the_engines_own_service():
    engine = _engine(with_cpu_copies=False)

    assert (
        TextEmbeddingWorkflow(engine).encode_clip_query("a cat") == "device-clip-text"
    )
    assert engine.clip_service.calls == [("encode_text", "a cat")]


def test_without_cpu_copies_a_likeness_query_image_uses_the_engines_own_service():
    engine = _engine(with_cpu_copies=False)

    result = ClipEmbeddingWorkflow(engine).encode_query_image(object())

    assert result == "device-clip-image"
    assert engine.clip_service.calls == [("encode_image_batch", 1)]


# ---------------------------------------------------------------------------
# What must NOT move: the worker's own picture embedding
# ---------------------------------------------------------------------------


def test_picture_text_embedding_stays_on_the_accelerator():
    """``encode`` is the GPU worker indexing the library. Routing it to the CPU
    copies would be a silent, library-wide throughput regression, and it is the
    mistake a blanket reroute makes."""
    engine = _engine(with_cpu_copies=True)
    picture = types.SimpleNamespace(text_embedding_data=lambda: {"caption": "a cat"})

    TextEmbeddingWorkflow(engine).encode([picture])

    assert engine.sbert_service.calls, "picture embedding was moved off the device"
    assert engine.query_encoders._sbert_service.calls == [("ensure_ready", None)]


def test_picture_image_embedding_stays_on_the_accelerator():
    """The same for images: only the likeness *query* goes to the CPU."""
    engine = _engine(with_cpu_copies=True)

    ClipEmbeddingWorkflow(engine).encode_images([object(), object()])

    assert engine.clip_service.calls == [("encode_image_batch", 2)]
    assert engine.query_encoders._clip_service.calls == [("ensure_ready", None)]


# ---------------------------------------------------------------------------
# Who gets the copies, and what building them is allowed to cost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("device", ["cuda", "cpu", "cuda:0", None])
def test_a_host_that_cannot_hit_the_race_builds_no_copies(device):
    """Both directions, and the one that costs money: a second copy of CLIP and
    SBERT is ~630 MB, and CUDA tolerates two threads, so only Metal pays."""
    assert build_cpu_query_encoders(device) is None


@pytest.mark.parametrize("device", ["mps", "mps:0"])
def test_a_metal_host_gets_copies(device):
    """``mps:0`` matters: the engine's device string carries an index in some
    paths, and a bare ``!= "mps"`` would silently skip the protection."""
    assert build_cpu_query_encoders(device) is not None


def test_building_the_copies_loads_no_weights():
    """The regression that shipped and was caught only by timing the real app.

    Every engine service is lazy, so ``InferenceEngine.create`` reads no
    weights and returns in milliseconds. Loading these two inline made it take
    7.3 s, because they were then the first models in the process and paid the
    whole cold-import cost - measured boot went 1.95 s to 9.11 s on a real
    library. The load belongs on the GPU worker, queued by ``Vault.start``.
    """
    encoders = build_cpu_query_encoders("mps")

    assert encoders is not None
    # The real services, not stubs: nothing has read a weight off disk.
    assert not encoders._clip_service.is_loaded(), "create() loaded CLIP weights"
    assert not encoders._sbert_service.is_loaded(), "create() loaded SBERT weights"
    assert not encoders.is_loaded()


def test_create_really_does_put_both_copies_on_the_cpu():
    """Exercises the real ``create()``. Without this, changing
    ``ClipService(device=CPU)`` to ``ClipService(device=device)`` - the exact
    regression this module exists to prevent - passes the whole suite green."""
    encoders = CpuQueryEncoders.create()

    assert encoders._clip_service.device == "cpu"
    assert encoders._sbert_service._device == "cpu"
    assert encoders.device == "cpu"


# ---------------------------------------------------------------------------
# Loading: both copies or neither
# ---------------------------------------------------------------------------


def test_load_readies_both_copies():
    pair = _pair()

    pair.load()

    assert pair.is_loaded()
    assert pair._clip_service.calls == [("ensure_ready", None)]
    assert pair._sbert_service.calls == [("ensure_ready", None)]


@pytest.mark.parametrize(
    "clip_loads, sbert_loads",
    [(True, False), (False, True), (False, False)],
    ids=["sbert-missing", "clip-missing", "both-missing"],
)
def test_a_half_loaded_pair_never_reports_itself_ready(clip_loads, sbert_loads, caplog):
    """Worse than having no copies at all, and the reason is not obvious.

    A caller only checks that it has a pair, and the services call
    ``ensure_ready()`` *outside* their own ``try`` (``clip_service.encode_text``,
    ``SBertService.encode``). So serving with one model missing turns "search
    encodes on the accelerator", which works, into "every search raises for the
    life of the process", which does not.
    """
    pair = _pair(clip_loads=clip_loads, sbert_loads=sbert_loads)

    with caplog.at_level(logging.ERROR):
        pair.load()

    assert not pair.is_loaded()
    assert any(record.levelno >= logging.ERROR for record in caplog.records), (
        "a copy failed to load and nothing was logged"
    )


def test_one_broken_copy_does_not_stop_the_other_loading(caplog):
    """So the log names which half is missing, rather than only the first."""
    pair = _pair(clip_loads=False)

    with caplog.at_level(logging.ERROR):
        pair.load()

    assert pair._sbert_service.calls == [("ensure_ready", None)]


# ---------------------------------------------------------------------------
# A search that arrives before the load has finished
# ---------------------------------------------------------------------------


def test_a_loaded_pair_serves_without_queueing_anything():
    """The ordinary case, and the one that must not touch the runner at all."""
    pair = _loaded_pair()
    pair.bind_loader(lambda: pytest.fail("queued a load for an already-loaded pair"))

    pair.ensure_serving()


def test_with_no_worker_the_caller_is_told_the_device_is_safe():
    """``False`` is permission to encode on the accelerator, not a failure.

    The crash needs *two* threads on Metal. With no task runner there is no GPU
    worker doing Metal work, so nothing is there to collide with. Refusing here
    instead broke search in every configuration that has an engine but no
    running worker - the e2e backend, a runner stopped for a library switch,
    and the multi-project authz suite, which is what caught it.
    """
    pair = _pair()

    assert pair.ensure_serving(timeout_s=0.1) is False


def test_a_search_that_arrives_first_queues_the_load_and_waits_for_it():
    """The start-up window: the boot queue is in flight or about to be."""
    pair = _pair()
    queued = []

    def loader():
        task = CpuQueryEncoderLoadTask(pair)
        queued.append(task)
        threading.Thread(target=task.run, daemon=True).start()
        return task

    pair.bind_loader(loader)
    pair.ensure_serving(timeout_s=5)

    assert len(queued) == 1
    assert pair.is_loaded()


def test_a_load_that_never_finishes_refuses_the_search_instead_of_hanging():
    """A search must not block a request thread forever; ``worker_running`` is
    True here, so the owner is told to retry rather than to restart."""
    pair = _pair()
    pair.bind_loader(lambda: types.SimpleNamespace(status=TaskStatus.RUNNING))

    with pytest.raises(CpuQueryEncodersNotReadyError) as excinfo:
        pair.ensure_serving(timeout_s=0.1)

    assert excinfo.value.worker_running is True


def test_a_second_search_does_not_queue_a_second_load():
    """The load is URGENT on the GPU queue; queueing one per waiting search
    would push the worker's real work behind a pile of duplicates."""
    pair = _pair()
    queued = []
    pair.bind_loader(
        lambda: queued.append(1) or types.SimpleNamespace(status=TaskStatus.RUNNING)
    )

    for _ in range(3):
        with pytest.raises(CpuQueryEncodersNotReadyError):
            pair.ensure_serving(timeout_s=0.01)

    assert len(queued) == 1


@pytest.mark.parametrize(
    "status", [TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.COMPLETED]
)
def test_a_finished_load_that_did_not_load_is_queued_again(status):
    """A full restore cancels pending tasks. Without this the pair waits on a
    task that will never run again and search stays broken until a restart."""
    pair = _pair()
    queued = []
    pair.bind_loader(lambda: queued.append(1) or types.SimpleNamespace(status=status))

    for _ in range(2):
        with pytest.raises(CpuQueryEncodersNotReadyError):
            pair.ensure_serving(timeout_s=0.01)

    assert len(queued) == 2, "a settled load was not re-queued"


def test_a_loader_that_cannot_queue_lets_the_caller_use_the_device():
    """``TaskRunner.submit`` raises when the runner is stopped. Waiting 60 s for
    a load nothing will run is the wrong answer, and so is refusing: a stopped
    runner is precisely the case where the accelerator has no other user."""
    pair = _pair()

    def refusing_loader():
        raise RuntimeError("task runner is not running")

    pair.bind_loader(refusing_loader)

    assert pair.ensure_serving(timeout_s=0.1) is False


# ---------------------------------------------------------------------------
# The load task itself
# ---------------------------------------------------------------------------


def test_the_load_task_runs_urgently_on_the_gpu_queue():
    """The GPU queue although the load is onto the CPU: that queue is what
    serialises it against the worker's own model loads, which is what keeps it
    clear of the transformers/accelerate import race. URGENT so a search in the
    first seconds does not queue behind the planner's tagging batches."""
    task = CpuQueryEncoderLoadTask(_pair())

    assert task.queue_type == QueueType.GPU
    assert task.priority == TaskPriority.URGENT


def test_the_load_task_loads_the_copies_and_reports_the_outcome():
    pair = _pair()
    task = CpuQueryEncoderLoadTask(pair)

    task.run()

    assert pair.is_loaded()
    assert task.result is True
    assert task.status == TaskStatus.COMPLETED


def test_a_partial_load_completes_the_task_reporting_false():
    """Not a failure: the task would then be retried three times for a GPU
    out-of-memory error a CPU load cannot have. The pair refuses to serve on
    its own account."""
    pair = _pair(clip_loads=False)
    task = CpuQueryEncoderLoadTask(pair)

    task.run()

    assert task.status == TaskStatus.COMPLETED
    assert task.result is False
    assert not pair.is_loaded()


# ---------------------------------------------------------------------------
# Getting the load queued at all: neither Vault hook is always the later one
# ---------------------------------------------------------------------------


def test_start_loading_queues_without_waiting():
    """The boot path: queue it and carry on, so nothing blocks on the load."""
    pair = _pair()
    queued = []
    pair.bind_loader(
        lambda: queued.append(1) or types.SimpleNamespace(status=TaskStatus.RUNNING)
    )

    assert pair.start_loading() is True
    assert len(queued) == 1
    assert not pair.is_loaded(), "start_loading must not block on the load"


def test_start_loading_reports_failure_instead_of_raising():
    """Called from ``Vault.start`` before the engine exists and from
    ``Vault.ensure_ready`` before the runner starts - whichever runs first has
    nothing to do, and must not take start-up down with it."""
    pair = _pair()

    assert pair.start_loading() is False

    def refusing_loader():
        raise RuntimeError("task runner is not running")

    pair.bind_loader(refusing_loader)
    assert pair.start_loading() is False


def test_calling_start_loading_twice_queues_one_load():
    """The Vault calls it from both hooks on purpose, so it has to be
    idempotent or every boot queues the load twice."""
    pair = _pair()
    queued = []
    pair.bind_loader(
        lambda: queued.append(1) or types.SimpleNamespace(status=TaskStatus.RUNNING)
    )

    pair.start_loading()
    pair.start_loading()

    assert len(queued) == 1


def test_start_loading_does_nothing_once_the_copies_are_loaded():
    pair = _loaded_pair()
    pair.bind_loader(lambda: pytest.fail("queued a load for an already-loaded pair"))

    assert pair.start_loading() is True


# ---------------------------------------------------------------------------
# The Vault hook itself, which is where this went wrong
# ---------------------------------------------------------------------------


def _vault_hook(engine, runner):
    """Call ``Vault._queue_cpu_query_encoder_load`` with a stand-in ``self``.

    The real method, without standing up a Vault: the bug it guards against is
    an ordering one, and the method is where the ordering lands.
    """
    from pixlstash.vault import Vault

    return Vault._queue_cpu_query_encoder_load(
        types.SimpleNamespace(_engine=engine, _task_runner=runner)
    )


def test_the_vault_hook_survives_being_called_before_the_engine_exists():
    """``Server.__init__`` calls ``Vault.start()`` before ``app`` builds the
    engine, so this runs with ``_engine`` unset on every boot. Returning
    quietly is what lets the ``ensure_ready`` call do the work instead."""
    _vault_hook(engine=None, runner=None)
    _vault_hook(engine=types.SimpleNamespace(query_encoders=None), runner=None)


def test_the_vault_hook_queues_the_load_once_both_halves_exist():
    """The bug this pins: with the hook only on ``start()``, the engine did not
    exist yet, no loader was ever bound, and the first search answered 503
    'restart PixlStash' for the life of the process."""
    pair = _pair()
    submitted = []
    runner = types.SimpleNamespace(submit=lambda task: submitted.append(task))

    _vault_hook(engine=types.SimpleNamespace(query_encoders=pair), runner=runner)

    assert len(submitted) == 1
    assert isinstance(submitted[0], CpuQueryEncoderLoadTask)


def test_the_vault_hook_can_be_called_from_both_places_without_double_queueing():
    """It is called from ``ensure_ready`` and from ``start`` because neither is
    reliably the later one."""
    pair = _pair()
    submitted = []
    runner = types.SimpleNamespace(submit=lambda task: submitted.append(task))
    engine = types.SimpleNamespace(query_encoders=pair)

    _vault_hook(engine=engine, runner=runner)
    _vault_hook(engine=engine, runner=runner)

    assert len(submitted) == 1


def test_the_vault_hook_does_not_raise_when_the_runner_refuses():
    """A stopped runner must not take start-up down; the first search re-queues."""
    pair = _pair()

    def refusing_submit(task):
        raise RuntimeError("task runner is not running")

    _vault_hook(
        engine=types.SimpleNamespace(query_encoders=pair),
        runner=types.SimpleNamespace(submit=refusing_submit),
    )


def test_ensure_ready_queues_the_load():
    """Pins the *call site*, not the method. The hook lived only on ``start()``
    once, which runs inside ``Server.__init__`` before the engine exists - so
    nothing was ever queued and the first search answered 503 forever. A test
    that only calls the hook directly cannot see that."""
    from pixlstash.vault import Vault

    calls = []
    Vault.ensure_ready(
        types.SimpleNamespace(
            _disable_background_workers=False,
            _engine=object(),  # truthy, so no engine is built here
            _queue_cpu_query_encoder_load=lambda: calls.append("queued"),
        )
    )

    assert calls == ["queued"], "ensure_ready no longer queues the encoder load"


def test_start_queues_the_load_before_the_planner_can_fill_the_gpu_queue():
    """Order matters as much as presence. URGENT heads the queue but cannot
    preempt a running task, and the planner queues tagging and description work
    the moment it starts - one such batch held the load over 86 s on a real
    library, long enough for a search to give up and answer 503."""
    from pixlstash.vault import Vault

    order = []
    Vault.start(
        types.SimpleNamespace(
            _disable_background_workers=False,
            _started=False,
            _task_runner=types.SimpleNamespace(start=lambda: order.append("runner")),
            _work_planner=types.SimpleNamespace(start=lambda: order.append("planner")),
            _ref_folder_watcher=types.SimpleNamespace(start=lambda: None),
            _start_existing_folder_watches=lambda: None,
            _queue_cpu_query_encoder_load=lambda: order.append("encoder-load"),
        )
    )

    assert "encoder-load" in order, "start() no longer queues the encoder load"
    assert order.index("encoder-load") > order.index("runner"), "queued before a runner"
    assert order.index("encoder-load") < order.index("planner"), (
        "queued after the planner had already filled the GPU queue"
    )


# ---------------------------------------------------------------------------
# Giving the memory back
# ---------------------------------------------------------------------------


def test_unload_releases_both_copies():
    """On unified memory these sit in the same pool the accelerator uses, so
    leaving ~630 MB here defeats a reclaim the owner asked for."""
    pair = _loaded_pair()

    pair.unload()

    assert pair._clip_service.unloads == 1
    assert pair._sbert_service.unloads == 1


def test_an_unloaded_pair_reloads_instead_of_encoding_on_nothing():
    """The load-bearing half of ``unload``. Without clearing the flag,
    ``ensure_serving`` returns at once for a pair whose models have gone and
    the encode fails on the search thread instead of queueing a reload."""
    pair = _loaded_pair()
    queued = []
    pair.bind_loader(
        lambda: queued.append(1) or types.SimpleNamespace(status=TaskStatus.RUNNING)
    )

    pair.unload()

    assert not pair.is_loaded()
    with pytest.raises(CpuQueryEncodersNotReadyError):
        pair.ensure_serving(timeout_s=0.01)
    assert queued == [1], "an unloaded pair did not queue a reload"


def test_a_broken_unload_does_not_stop_the_other_copy(caplog):
    pair = _loaded_pair()
    pair._clip_service.unload_raises = True

    with caplog.at_level(logging.ERROR):
        pair.unload()

    assert pair._sbert_service.unloads == 1
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


# ---------------------------------------------------------------------------
# No GPU worker: the accelerator has no other user, so it is safe
# ---------------------------------------------------------------------------


def test_a_text_query_falls_back_to_the_device_when_no_worker_is_running():
    """The regression the multi-project authz suite caught.

    An engine can exist with no running task runner - the e2e backend, a runner
    stopped for a library switch, and any test that builds a Server without
    starting workers. Refusing there turned a working search into a permanent
    503. It is safe because the crash needs a *second* thread on Metal, and a
    stopped worker is not one.
    """
    engine = _engine(with_cpu_copies=True)
    engine.query_encoders = _pair()  # unloaded, and no loader ever bound

    assert TextEmbeddingWorkflow(engine).encode_query("A Cat") == ["device-sbert"]
    assert engine.sbert_service.calls == [("encode", ("a cat",))]


def test_a_likeness_query_falls_back_to_the_device_when_no_worker_is_running():
    engine = _engine(with_cpu_copies=True)
    engine.query_encoders = _pair()

    assert ClipEmbeddingWorkflow(engine).encode_query_image(object()) == (
        "device-clip-image"
    )
    assert engine.clip_service.calls == [("encode_image_batch", 1)]


def test_a_running_worker_makes_the_caller_wait_instead_of_falling_back():
    """The other direction, and the one that must never become a fallback:
    with a worker running, encoding on the device IS the crash."""
    engine = _engine(with_cpu_copies=True)
    pair = _pair()
    pair.bind_loader(lambda: types.SimpleNamespace(status=TaskStatus.RUNNING))
    pair.DEFAULT_WAIT_S = 0.05
    engine.query_encoders = pair

    with pytest.raises(CpuQueryEncodersNotReadyError):
        TextEmbeddingWorkflow(engine).encode_query("a cat")

    assert engine.sbert_service.calls == [], "fell back to the device mid-crash-window"


def test_the_lazy_engine_build_also_queues_the_load():
    """``Vault.get_worker_future`` is the third place an engine is built, and
    the one I missed: without the hook the copies exist with no loader bound and
    every later search reports "no GPU worker" for the life of the process."""
    import inspect

    from pixlstash.vault import Vault

    source = inspect.getsource(Vault.get_worker_future)
    assert "_queue_cpu_query_encoder_load()" in source, (
        "the lazy engine build no longer queues the encoder load"
    )

"""Who encodes a search query, when the CPU copies load, and one at a time.

A search encodes on a request thread - text search before its database task,
likeness search on a threadpool worker - while the GPU worker runs the
embedding and tagging batches. Torch's Metal backend cannot take two threads,
and it does not raise when it gets them, so on Metal the query goes to CPU
copies of CLIP and SBERT (``InferenceEngine.query_services``).

Directions asserted, because each is its own regression:

* a query that reaches the accelerator on Metal is the crash;
* a **picture** embedding pushed onto the CPU would quietly move the whole
  library's indexing off the GPU;
* a CUDA or CPU host must not pay for a second model;
* the copies load before the Vault publishes its engine, so no other model
  load runs beside them;
* two query encodes never run at once, on any host.

No weights load. The services are stubs, except where a test says otherwise.
"""

import threading
import types

import pytest

from pixlstash.inference.cpu_query_encoders import (
    LOAD_FAILED_DETAIL,
    CpuQueryEncoders,
    CpuQueryEncodersNotReadyError,
    build_cpu_query_encoders,
)
from pixlstash.inference.engine import InferenceEngine
from pixlstash.inference.workflows.clip_embedding import ClipEmbeddingWorkflow
from pixlstash.inference.workflows.text_embedding import TextEmbeddingWorkflow
from pixlstash.tasks.task_type import TaskType
from pixlstash.vault import Vault
import pixlstash.vault as vault_module


class _RecordingService:
    """Stands in for ClipService/SBertService and records what reached it."""

    def __init__(self, label, loads=True):
        self.label = label
        self.loads = loads
        self.loaded = False
        self.calls = []

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


def _engine(*, with_cpu_copies):
    """A real ``InferenceEngine`` carrying only what the query path reads."""
    engine = InferenceEngine.__new__(InferenceEngine)
    engine.clip_service = _RecordingService("device")
    engine.sbert_service = _RecordingService("device")
    engine.query_encoders = None
    engine._query_lock = threading.Lock()
    if with_cpu_copies:
        engine.query_encoders = _pair()
        engine.query_encoders.load()
    return engine


# ---------------------------------------------------------------------------
# On Metal the query goes to the CPU copies
# ---------------------------------------------------------------------------


def test_a_text_query_is_encoded_on_the_cpu_copy_when_there_is_one():
    """The crash this exists to stop: this call runs on the search thread."""
    engine = _engine(with_cpu_copies=True)

    assert TextEmbeddingWorkflow(engine).encode_query("A Cat") == ["cpu-sbert"]
    assert engine.sbert_service.calls == [], "the query reached the accelerator"
    assert engine.query_encoders._sbert_service.calls[-1] == ("encode", ("a cat",))


def test_a_clip_text_query_is_encoded_on_the_cpu_copy_when_there_is_one():
    engine = _engine(with_cpu_copies=True)

    assert TextEmbeddingWorkflow(engine).encode_clip_query("a cat") == "cpu-clip-text"
    assert engine.clip_service.calls == [], "the query reached the accelerator"


def test_a_likeness_query_image_is_encoded_on_the_cpu_copy_when_there_is_one():
    engine = _engine(with_cpu_copies=True)

    result = ClipEmbeddingWorkflow(engine).encode_query_image(object())

    assert result == "cpu-clip-image"
    assert engine.clip_service.calls == [], "the query image reached the accelerator"


# ---------------------------------------------------------------------------
# Hosts that need no protection keep their own services
# ---------------------------------------------------------------------------


def test_without_cpu_copies_every_query_uses_the_engines_own_services():
    engine = _engine(with_cpu_copies=False)

    assert TextEmbeddingWorkflow(engine).encode_query("A Cat") == ["device-sbert"]
    assert TextEmbeddingWorkflow(engine).encode_clip_query("a cat") == (
        "device-clip-text"
    )
    assert ClipEmbeddingWorkflow(engine).encode_query_image(object()) == (
        "device-clip-image"
    )
    assert engine.sbert_service.calls == [("encode", ("a cat",))]
    assert engine.clip_service.calls == [
        ("encode_text", "a cat"),
        ("encode_image_batch", 1),
    ]


# ---------------------------------------------------------------------------
# What must NOT move: the worker's own picture embedding
# ---------------------------------------------------------------------------


def test_picture_embeddings_stay_on_the_accelerator():
    """``encode``/``encode_images`` are the GPU worker indexing the library.
    Routing them to the CPU copies would be a silent, library-wide throughput
    regression, and it is the mistake a blanket reroute makes."""
    engine = _engine(with_cpu_copies=True)
    picture = types.SimpleNamespace(text_embedding_data=lambda: {"caption": "a cat"})

    TextEmbeddingWorkflow(engine).encode([picture])
    ClipEmbeddingWorkflow(engine).encode_images([object(), object()])

    assert engine.sbert_service.calls, "picture text embedding moved off the device"
    assert engine.clip_service.calls == [("encode_image_batch", 2)]
    assert engine.query_encoders._sbert_service.calls == [("ensure_ready", None)]
    assert engine.query_encoders._clip_service.calls == [("ensure_ready", None)]


# ---------------------------------------------------------------------------
# Who gets the copies, and loading them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("device", ["cuda", "cpu", "cuda:0", None])
def test_a_host_that_cannot_hit_the_race_builds_no_copies(device):
    """A second CLIP and SBERT is ~0.7 GB, and CUDA tolerates two threads."""
    assert build_cpu_query_encoders(device) is None


@pytest.mark.parametrize("device", ["mps", "mps:0"])
def test_a_metal_host_gets_copies_on_the_cpu_without_loading_them(device):
    """The real ``create()``: changing ``ClipService(device=CPU)`` to the
    engine's device is the exact regression this module exists to prevent.
    Building reads no weights; the Vault loads them."""
    encoders = build_cpu_query_encoders(device)

    assert encoders is not None
    assert encoders._clip_service.device == "cpu"
    assert encoders._sbert_service._device == "cpu"
    assert not encoders.is_loaded(), "building the copies loaded weights"


@pytest.mark.parametrize(
    "clip_loads,sbert_loads",
    [(False, True), (True, False)],
    ids=["clip-missing", "sbert-missing"],
)
def test_a_half_loaded_pair_refuses_every_search(clip_loads, sbert_loads, caplog):
    """Both or neither. A copy with no model would load lazily on the search
    thread, and falling back to the Metal services is the crash, so the search
    is refused - and the log names the half that failed."""
    engine = _engine(with_cpu_copies=False)
    engine.query_encoders = _pair(clip_loads=clip_loads, sbert_loads=sbert_loads)
    engine.query_encoders.load()

    with pytest.raises(CpuQueryEncodersNotReadyError, match=LOAD_FAILED_DETAIL):
        TextEmbeddingWorkflow(engine).encode_query("a cat")
    assert engine.sbert_service.calls == [], "fell back to the accelerator"
    assert ("CLIP" if not clip_loads else "SBERT") in caplog.text


# ---------------------------------------------------------------------------
# One query encode at a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("with_cpu_copies", [False, True], ids=["cuda-cpu", "metal"])
def test_query_encodes_take_turns(with_cpu_copies):
    """Query encodes used to run inside the database task, so the single DB
    writer serialised them. Moving them out put concurrent searches on one
    shared service, which reads its model and tokenizer without a lock."""
    engine = _engine(with_cpu_copies=with_cpu_copies)
    sbert = (
        engine.query_encoders._sbert_service
        if with_cpu_copies
        else engine.sbert_service
    )
    inside = threading.Semaphore(0)
    release = threading.Event()
    real_encode = sbert.encode

    def slow_encode(texts):
        inside.release()
        release.wait(timeout=10)
        return real_encode(texts)

    sbert.encode = slow_encode
    workflow = TextEmbeddingWorkflow(engine)
    threads = [
        threading.Thread(target=workflow.encode_query, args=(q,)) for q in ("a", "b")
    ]
    try:
        threads[0].start()
        assert inside.acquire(timeout=5), "the first encode never started"
        threads[1].start()
        assert not inside.acquire(timeout=0.5), "two query encodes ran at once"
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)
    assert inside.acquire(timeout=5), "the second encode never ran"


# ---------------------------------------------------------------------------
# The Vault loads the copies before it publishes the engine
# ---------------------------------------------------------------------------


def _stand_in_vault(monkeypatch, seen):
    """A stand-in ``self`` whose engine build records what was published when
    the copies loaded. ``seen`` collects ``self._engine`` at load time."""
    stand_in = types.SimpleNamespace(
        _engine=None,
        _disable_background_workers=False,
        image_root="unused",
        _force_cpu=False,
        _fast_captions=True,
        _max_vram_gb=None,
        _wd14_tagger_enabled=False,
        _pixlstash_tagger_enabled=True,
        _wd14_threshold=None,
        _pixlstash_tagger_threshold_offset=0.0,
        _keep_models_in_memory=False,
        _insightface_model_pack=None,
        _tagger_settings={},
        _bind_engine_services=lambda: None,
    )
    encoders = types.SimpleNamespace(load=lambda: seen.append(stand_in._engine))
    engine = types.SimpleNamespace(query_encoders=encoders)
    monkeypatch.setattr(
        vault_module,
        "InferenceEngine",
        types.SimpleNamespace(create=lambda **_: engine),
    )
    stand_in._create_engine = lambda: Vault._create_engine(stand_in)
    return stand_in, engine


def test_ensure_ready_loads_the_copies_before_publishing_the_engine(monkeypatch):
    """The planner's model finders queue nothing while ``Vault._engine`` is
    ``None``, and at boot the planner is already running. Loading after
    publishing would let a tagging or embedding load start beside this one,
    which races transformers' and accelerate's imports."""
    seen = []
    stand_in, engine = _stand_in_vault(monkeypatch, seen)

    Vault.ensure_ready(stand_in)

    assert seen == [None], "the copies did not load, or loaded after publishing"
    assert stand_in._engine is engine


def test_the_lazy_engine_build_also_loads_the_copies_first(monkeypatch):
    """``get_worker_future`` is the other place an engine is built."""

    class _Built(Exception):
        """Stops the call once the engine is built."""

    seen = []
    stand_in, engine = _stand_in_vault(monkeypatch, seen)

    def stop():
        raise _Built

    stand_in._bind_engine_services = stop

    with pytest.raises(_Built):
        Vault.get_worker_future(stand_in, TaskType.TAGGER, object, 1, "tags")
    assert seen == [None], "the copies did not load, or loaded after publishing"
    assert stand_in._engine is engine


def test_closing_the_engine_keeps_the_copies_loaded():
    """They load once, before the engine is published, and nothing reloads them
    afterwards: releasing them under the idle sweep would leave every later
    search refused (#1774 tracks doing that properly)."""
    engine = _engine(with_cpu_copies=True)
    engine.wd14_service = engine.pixlstash_tagger_service = None
    engine.florence_service = None
    engine.lifecycle = types.SimpleNamespace(aggressive_unload=lambda **_: None)

    engine.close()

    assert engine.query_encoders.is_loaded()
    assert TextEmbeddingWorkflow(engine).encode_query("a cat") == ["cpu-sbert"]

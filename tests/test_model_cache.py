"""Contract tests for the process-wide model cache.

Deliberately free of the ML stack: the cache is plain memoisation, and the
properties that matter (pass-through when off, one load per key, device
isolation) are the ones a wrong implementation would break silently and
expensively. A regression here does not fail a test somewhere else — it just
makes the suite slow again, or, worse, hands a CUDA model to a CPU caller.
"""

import threading

import pytest

from pixlstash.utils import model_cache


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Give every test a clean, disabled cache and restore the session state.

    tests/conftest.py enables the cache process-wide, so without this the
    "disabled by default" assertions below would read the session's state
    rather than their own setup.
    """
    was_enabled = model_cache.is_enabled()
    model_cache._enabled = False
    model_cache.clear()
    yield
    model_cache.clear()
    model_cache._enabled = was_enabled


class _CountingLoader:
    """A loader that records how many times it actually ran."""

    def __init__(self, value="model"):
        self.calls = 0
        self.value = value

    def __call__(self):
        self.calls += 1
        return self.value


def test_disabled_cache_is_a_pass_through():
    """Production default: every call loads, nothing is retained.

    This is the property that keeps `unload()` and `keep_models_in_memory`
    honest in a real server — a cached strong reference would make freeing
    memory impossible.
    """
    loader = _CountingLoader()

    first = model_cache.get_or_load("k", loader)
    second = model_cache.get_or_load("k", loader)

    assert (first, second) == ("model", "model")
    assert loader.calls == 2, "Disabled cache must not memoise"
    assert not model_cache.is_enabled()


def test_enabled_cache_loads_once_per_key():
    model_cache.enable()
    loader = _CountingLoader()

    results = [model_cache.get_or_load("k", loader) for _ in range(5)]

    assert results == ["model"] * 5
    assert loader.calls == 1, f"Expected a single load, got {loader.calls}"


def test_distinct_keys_do_not_share_an_entry():
    model_cache.enable()
    cpu = _CountingLoader("cpu-model")
    cuda = _CountingLoader("cuda-model")

    assert model_cache.get_or_load(("clip", "ViT-B-32", "w", "cpu"), cpu) == "cpu-model"
    assert (
        model_cache.get_or_load(("clip", "ViT-B-32", "w", "cuda"), cuda) == "cuda-model"
    )
    # Re-request the first key: it must still be the CPU object, not the CUDA
    # one that was cached after it. `_load` mutates models in place with
    # `.to(device)` and `.half()`, so a key that ignored device would hand a
    # half-precision CUDA model to a CPU caller.
    assert model_cache.get_or_load(("clip", "ViT-B-32", "w", "cpu"), cpu) == "cpu-model"
    assert cpu.calls == 1 and cuda.calls == 1


def test_discard_evicts_a_mutated_entry():
    """The CUDA-OOM path mutates a cached model to CPU; the entry must go.

    Both ClipService and PixlStashTaggerService recover from a CUDA OOM with
    `model.float().to("cpu")`, mutating the module in place. The entry is filed
    under the CUDA key, so leaving it there would hand the next engine a CPU
    model when it asked for a CUDA one.
    """
    model_cache.enable()
    cuda_key = ("clip", "ViT-B-32", "w", "cuda")
    loader = _CountingLoader()

    model_cache.get_or_load(cuda_key, loader)
    model_cache.discard(cuda_key)
    model_cache.get_or_load(cuda_key, loader)

    assert loader.calls == 2, "Evicted key must reload rather than serve a stale model"


def test_discard_is_safe_for_unknown_keys_and_when_disabled():
    model_cache.discard("never-stored")  # disabled
    model_cache.enable()
    model_cache.discard("never-stored")  # enabled, absent


def test_clear_releases_entries_so_the_next_call_reloads():
    """Teardown must actually drop the reference, or the weights never free."""
    model_cache.enable()
    loader = _CountingLoader()

    model_cache.get_or_load("k", loader)
    model_cache.clear()
    model_cache.get_or_load("k", loader)

    assert loader.calls == 2


def test_enable_is_idempotent():
    model_cache.enable()
    model_cache.enable()
    assert model_cache.is_enabled()


def test_concurrent_first_use_loads_exactly_once():
    """Two threads racing a cold key must not both pay the load.

    The task runner starts several workers that can reach for the same model
    at once on the first upload of a session, which is precisely the race.
    """
    model_cache.enable()
    started = threading.Barrier(8)
    calls = []
    lock = threading.Lock()

    def loader():
        with lock:
            calls.append(1)
        return "model"

    results = []

    def worker():
        started.wait()
        results.append(model_cache.get_or_load("shared", loader))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["model"] * 8
    assert len(calls) == 1, f"Cold key loaded {len(calls)} times under contention"


def test_clip_service_reuses_weights_across_instances():
    """The wiring, not just the cache: two engines' ClipServices load once.

    `InferenceEngine.create` builds a fresh ClipService per engine and every
    Server builds an engine, so this is the exact shape of the test suite's
    hot path. Patches `_build` rather than importing torch, so this stays a
    cheap unit test — what it pins is that `_load` consults the cache at all
    and that the key does not accidentally vary per instance.
    """
    from pixlstash.tagger_plugins.clip_service import ClipService

    model_cache.enable()
    builds = []

    def fake_build(self):
        builds.append(self)
        return ("model", "preprocess", "tokenizer")

    original = ClipService._build
    ClipService._build = fake_build
    try:
        first, second = ClipService(device="cpu"), ClipService(device="cpu")
        first._load()
        second._load()

        assert len(builds) == 1, f"CLIP loaded {len(builds)} times, expected 1"
        assert first.is_loaded() and second.is_loaded()
        assert first.model is second.model

        # A different device must not reuse the CPU entry: `_build` applies
        # `.to(device)` and `.half()` in place on CUDA.
        ClipService(device="cuda")._load()
        assert len(builds) == 2
    finally:
        ClipService._build = original


def test_tagger_services_sharing_a_model_share_its_localisation_lock():
    """A shared model must not be guarded by two independent locks.

    ``localize_anomaly`` upcasts the model to fp32 for the Grad-CAM pass and
    restores the dtype in a ``finally``. That is only safe because a lock
    serialises it. If the lock stayed per-instance while the model became
    shared, two services could interleave and one would restore fp16 under the
    other's CAM pass — a silent NaN-gradient bug with no failing test attached.
    """
    from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService

    model_cache.enable()
    sentinel = object()
    key = ("pixlstash_tagger", "/models/tagger.safetensors", "convnext_base", 3, "cpu")
    lock = threading.Lock()
    model_cache.get_or_load(key, lambda: (sentinel, lock))

    services = []
    for _ in range(2):
        service = PixlStashTaggerService.__new__(PixlStashTaggerService)
        service._model, service._localize_lock = model_cache.get_or_load(
            key, lambda: (object(), threading.Lock())
        )
        services.append(service)

    assert services[0]._model is services[1]._model, "expected a shared model"
    assert services[0]._localize_lock is services[1]._localize_lock, (
        "services sharing a cached model must share the lock that guards it"
    )


def test_loader_exception_is_not_cached():
    """A failed load must be retried, not memoised as a permanent failure.

    The tagger's `init_or_cpu_fallback` retries on CPU after a CUDA OOM; if the
    failure stuck, that fallback would never get a second chance.
    """
    model_cache.enable()
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("out of memory")
        return "model"

    with pytest.raises(RuntimeError):
        model_cache.get_or_load("k", flaky)

    assert model_cache.get_or_load("k", flaky) == "model"
    assert len(attempts) == 2

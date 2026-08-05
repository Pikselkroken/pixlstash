"""Opt-in, process-wide cache for loaded model weights.

Why this exists
---------------
``InferenceEngine.create`` builds a fresh ``ClipService``,
``PixlStashTaggerService``, ``SBertService`` and friends for every engine, and
every ``Server`` builds an engine. In production that happens once, so nothing
is gained by caching and the memory-management toggles
(``keep_models_in_memory``, ``unload()``) must keep working exactly as written.

In the test suite it happens once *per test function*. Measured over the
committed ``tests/ci_test_durations.json`` on 2026-08-05:

    workers off, no upload   610 tests   1.47 s/test
    workers ON,  no upload   217 tests   2.17 s/test
    workers ON,  uploads     508 tests   7.11 s/test

The ~5 s that separates the last row from the one above it is the import
pipeline loading models — and it is essentially flat in the number of images
(``test_read_token_security`` averaged 12.1 s/test uploading twelve 19 MB
photographs against 10.4 s/test for a sibling helper uploading two generated
PNGs, a 1.7 s difference). It is the *load*, not the inference. Loading
ViT-B-32 and a convnext_base checkpoint 508 times to assert HTTP status codes
is the single largest line item in the suite.

``FaceExtractionTask`` already solved this for InsightFace with a class-level
``_global_insightface_app``; this is the same idea, factored out so CLIP and
the tagger can share it without each growing its own bespoke global.

Contract
--------
* **Off by default.** ``get_or_load`` is a straight pass-through to *loader*
  until something calls :func:`enable`, so production behaviour — including
  ``unload()`` actually freeing memory — is byte-for-byte unchanged.
* **Tests enable it** in ``tests/conftest.py::pytest_configure`` and clear it
  in ``pytest_sessionfinish``, so teardown still releases native memory.
* **The key must capture everything that changes the loaded object.** Device
  belongs in every key: ``_load`` implementations call ``.to(device)`` and
  ``.half()`` on CUDA, which mutate the model in place, so a CPU entry and a
  CUDA entry are different objects and must never collide.

Sharing a model across engines means concurrent forward passes from two
engines' workers can hit the same object. That is not a new assumption — the
workers inside a single engine already share one ``ClipService``, and the
InsightFace global above already spans tasks — and it holds because a loaded
model is read-only during inference.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Hashable

logger = logging.getLogger(__name__)

_enabled = False
_cache: dict[Hashable, Any] = {}
_lock = threading.RLock()


def enable() -> None:
    """Turn the shared cache on for this process.

    Intended for the test suite. Calling it in a long-lived server process
    would pin model weights in memory for the process lifetime and make
    ``unload()`` a no-op, which is exactly what the memory toggles exist to
    avoid.
    """
    global _enabled
    with _lock:
        if not _enabled:
            _enabled = True
            logger.info(
                "Process-wide model cache ENABLED: loaded weights will be "
                "reused across InferenceEngine instances."
            )


def is_enabled() -> bool:
    """Return True when :func:`get_or_load` is memoising."""
    return _enabled


def get_or_load(key: Hashable, loader: Callable[[], Any]) -> Any:
    """Return the cached object for *key*, calling *loader* on a miss.

    A straight pass-through to *loader* while the cache is disabled — no
    lookup, no store, no lock contention on the production path.

    *loader* runs while the cache lock is held, so two threads racing the same
    cold key load once rather than twice. Loads are seconds long and happen a
    handful of times per process, so the coarse lock costs nothing that
    matters; the alternative (per-key locks) would buy concurrency between two
    *different* cold models, which does not happen in practice.
    """
    if not _enabled:
        return loader()
    with _lock:
        if key not in _cache:
            logger.debug("Model cache miss, loading: %r", key)
            _cache[key] = loader()
        else:
            logger.debug("Model cache hit: %r", key)
        return _cache[key]


def discard(key: Hashable) -> None:
    """Evict one entry, if present.

    For the case where a caller has *mutated* a cached object out of agreement
    with its key. Both ClipService and PixlStashTaggerService fall back to CPU
    on a CUDA OOM by moving the model in place (``.float().to("cpu")``); the
    entry is still filed under the CUDA key, so without this the next engine
    asking for the CUDA model would be handed a CPU one. Evicting means that
    engine reloads — correct, and rare enough not to matter.

    Safe to call when the cache is disabled or the key was never stored.
    """
    with _lock:
        if _cache.pop(key, None) is not None:
            logger.debug("Model cache entry evicted after in-place mutation: %r", key)


def clear() -> None:
    """Drop every cached entry.

    Callers that need the native memory back (ONNX/CUDA arenas are freed only
    when the last reference is collected) should follow this with the same
    ``gc.collect()`` the task-level release helpers already do.
    """
    with _lock:
        if _cache:
            logger.debug("Clearing %d cached model(s)", len(_cache))
        _cache.clear()

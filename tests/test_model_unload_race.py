"""Unloading a model must never race a load that is still in flight.

``ModelLifecycleManager.aggressive_unload`` runs from the idle sweep and from
shutdown. Neither knows whether a tagger is halfway through
``from_pretrained``, and ``unload`` frees CUDA state unconditionally. Freeing
memory another thread is still allocating into is memory-unsafe, and it took
the whole process down: reproduced against the real JoyCaption model as
SIGSEGV (139) and as SIGABRT (134) with "terminate called without an active
exception", which is what a user saw on Ctrl-C.

These tests pin the ordering contract without loading a model, so they stay
fast: a stubbed ``_init`` holds the lock the way a real load does.
"""

import threading
import time

import pytest

from pixlstash.tagger_plugins.clip_service import ClipService
from pixlstash.tagger_plugins.florence2 import Florence2Service
from pixlstash.tagger_plugins.joycaption import JoyCaptionService
from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
from pixlstash.tagger_plugins.sbert import SBertService
from pixlstash.tagger_plugins.wd14 import WD14Service


def _service():
    """A service whose model load is stubbed out but still takes real time."""
    service = JoyCaptionService(device="cpu", precision="nf4")
    started = threading.Event()
    finished = threading.Event()

    def _slow_init():
        started.set()
        time.sleep(0.3)
        service._model = object()
        service._processor = object()
        finished.set()

    service._init = _slow_init
    return service, started, finished


def test_unload_waits_for_an_in_flight_load():
    """The unload must not land in the middle of the load."""
    service, started, finished = _service()

    loader = threading.Thread(target=service.ensure_ready, daemon=True)
    loader.start()
    assert started.wait(5), "the stubbed load never began"

    service.unload()  # must block until the load completes

    assert finished.is_set(), (
        "unload returned while the load was still running; that is the race "
        "that frees CUDA memory out from under from_pretrained"
    )
    assert not service.is_loaded(), "the unload must still take effect"
    loader.join(timeout=5)


def test_a_load_already_holding_the_lock_can_still_unload_itself():
    """``ensure_ready`` calls ``unload`` on a precision change, under the lock.

    A plain (non-reentrant) lock would deadlock the loader against itself, so
    this pins the reentrancy the fix depends on.
    """
    service = JoyCaptionService(device="cpu", precision="nf4")
    service._init = lambda: setattr(service, "_model", object())
    service._processor = object()
    service._model = object()

    done = threading.Event()

    def _switch():
        service.ensure_ready(precision="bf16")
        done.set()

    thread = threading.Thread(target=_switch, daemon=True)
    thread.start()

    assert done.wait(5), "a precision change deadlocked on its own lock"
    assert service._precision == "bf16"


def _install_slow_load(service, monkeypatch, slow):
    """Replace *service*'s innermost load step with *slow*.

    Patched at the innermost step on purpose: the public entry point keeps its
    real body, so the lock under test is the one production takes. SBert loads
    through a module-level helper rather than a method, so it is named
    explicitly instead of guessed.
    """
    if isinstance(service, SBertService):
        monkeypatch.setattr(
            "pixlstash.tagger_plugins.sbert.load_sentence_transformer",
            lambda *a, **k: slow() or object(),
        )
        return
    patched = False
    for attribute in ("_init", "_load", "_init_locked", "_init_onnx_session"):
        if hasattr(service, attribute):
            monkeypatch.setattr(service, attribute, slow)
            patched = True
    assert patched, f"no load step found on {type(service).__name__}"
    if hasattr(service, "_load_tags"):
        monkeypatch.setattr(service, "_load_tags", lambda: None)


_SERVICES = [
    (lambda: SBertService(device="cpu"), "ensure_ready"),
    (lambda: ClipService(device="cpu"), "ensure_ready"),
    (lambda: JoyCaptionService(device="cpu", precision="nf4"), "ensure_ready"),
    (lambda: Florence2Service(device="cpu"), "ensure_ready"),
    (
        lambda: WD14Service(
            device="cpu", model_dir="/nonexistent", batch_size_fn=lambda: 1
        ),
        "init",
    ),
    (
        lambda: PixlStashTaggerService(
            device="cpu", model_dir="/nonexistent", batch_size_fn=lambda: 1
        ),
        "init",
    ),
]


@pytest.mark.parametrize(
    "factory,load_name",
    _SERVICES,
    ids=[f().__class__.__name__ for f, _ in _SERVICES],
)
def test_every_tagger_service_serialises_unload_against_load(
    factory, load_name, monkeypatch
):
    """One unguarded service is enough to crash a shutdown.

    ``aggressive_unload`` unloads every tagger, so the guarantee has to hold for
    all of them, not just the one that happened to be loading when a user hit
    Ctrl-C.
    """
    service = factory()
    started = threading.Event()
    finished = threading.Event()

    def _slow(*_args, **_kwargs):
        started.set()
        time.sleep(0.3)
        finished.set()

    _install_slow_load(service, monkeypatch, _slow)

    loader = threading.Thread(target=getattr(service, load_name), daemon=True)
    loader.start()
    assert started.wait(5), f"{type(service).__name__}: the stubbed load never began"

    service.unload()

    assert finished.is_set(), (
        f"{type(service).__name__}.unload() returned while a load was still "
        "running: that frees device memory out from under the loader"
    )
    loader.join(timeout=5)

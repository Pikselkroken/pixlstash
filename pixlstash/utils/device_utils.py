"""Inference device detection across CUDA, Apple Metal (MPS), and CPU."""

import os
import sys
import threading
from typing import Optional

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: Devices that are a GPU of some kind, i.e. everything except plain CPU.
ACCELERATORS = frozenset({"cuda", "mps"})

#: The ``default_device`` values a server may be given, in ``server-config.json``
#: or through ``PIXLSTASH_DEFAULT_DEVICE``. Start-up checks the one and the
#: server the other against this set, so neither can accept a value the other
#: refuses.
VALID_DEVICE_SETTINGS = frozenset({"cpu", "cuda", "gpu", "auto"})

#: What to tell an owner whose inference ran on the CPU. ``auto`` is the one
#: value that reaches both CUDA and Metal: start-up rejects ``mps``, and
#: ``cuda`` refuses to start on a Mac.
USE_GPU_ADVICE = "Set default_device=auto in server-config.json to use the GPU."

#: What to tell an owner whose onnxruntime CUDA session loaded on the CPU.
ONNX_CUDA_ADVICE = (
    "The usual cause is an execution provider this build advertises whose "
    "libraries are not installed (the CUDA provider needs libcublasLt). Fix "
    "with: pip uninstall -y onnxruntime && pip install onnxruntime-gpu"
)

#: What to tell an owner whose onnxruntime session moved to the CPU during a
#: run: onnxruntime's own fallback reports the provider's error only on stdout.
ONNX_RUN_FALLBACK_ADVICE = (
    "onnxruntime printed the provider's error to standard output, not to this "
    "log; look for 'EP Error' there."
)

#: transformers 5.x loads weights on a thread pool unless this is true.
HF_ASYNC_LOAD_ENV = "HF_DEACTIVATE_ASYNC_LOAD"

# The one thread allowed to use Apple Metal: the running task runner's GPU
# worker. See register_metal_thread and ensure_metal_thread.
_metal_thread_lock = threading.Lock()
_metal_thread: Optional[threading.Thread] = None


def detect_device() -> str:
    """Return the best inference device available: cuda or cpu.

    Never raises. torch is a hard dependency, and on a working install the
    availability probe answers ``False`` rather than raising when there is no
    such GPU, so a CPU-only host logs nothing here. A torch that cannot be
    imported, or a probe that raises, is a broken install that costs the owner
    the GPU, so each is logged at WARNING: the callers only see "cpu" and
    cannot say why.
    """
    try:
        import torch
    except Exception as exc:
        logger.warning(
            "torch could not be imported (%s: %s); inference will use the CPU.",
            type(exc).__name__,
            exc,
        )
        return "cpu"

    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception as exc:
        logger.warning(
            "The CUDA availability probe raised (%s: %s); CUDA will not be used.",
            type(exc).__name__,
            exc,
        )

    return "cpu"


def configure_metal_model_loading() -> bool:
    """Make transformers load weights on one thread when Metal is present.

    transformers 5.x copies and casts weights on a pool of worker threads unless
    ``HF_DEACTIVATE_ASYNC_LOAD`` is true, and torch's Metal backend crashes or
    hangs when those threads cast on it at once
    (``docs/apple-metal-thread-safety.md``). The variable is set whenever Metal
    exists, not only when it is the inference device, because accelerate's
    ``device_map="auto"`` places weights on Metal whenever it is available.
    transformers reads it on every load, so it only has to be set before the
    first one. A value already in the environment is the owner's and is kept,
    with a warning when transformers reads it as false.

    torch is imported rather than read from :data:`sys.modules`, as in
    :func:`detect_device`: this runs before any model has loaded, which is
    exactly when torch may not have been imported yet.

    Returns:
        True when this call set the variable.
    """
    try:
        import torch
    except Exception as exc:
        logger.debug(
            "torch unavailable while configuring model loading (%s); leaving %s unset.",
            exc,
            HF_ASYNC_LOAD_ENV,
        )
        return False

    try:
        available = bool(torch.backends.mps.is_available())
    except Exception as exc:
        logger.debug(
            "MPS availability probe failed (%s); leaving %s unset.",
            exc,
            HF_ASYNC_LOAD_ENV,
        )
        return False
    if not available:
        return False

    if HF_ASYNC_LOAD_ENV in os.environ:
        value = os.environ[HF_ASYNC_LOAD_ENV]
        # transformers' own reading (utils.import_utils.is_env_variable_true):
        # any other value, "0" and "" included, leaves its loader threaded.
        if value.lower() in ("true", "1", "y", "yes", "on"):
            logger.debug(
                "Apple Metal present; keeping %s=%s from the environment.",
                HF_ASYNC_LOAD_ENV,
                value,
            )
        else:
            logger.warning(
                "Apple Metal present, but %s=%r from the environment leaves "
                "transformers loading model weights on several threads, which "
                "crashes or hangs on Metal (docs/apple-metal-thread-safety.md). "
                "The value is kept; unset it or set it to 1.",
                HF_ASYNC_LOAD_ENV,
                value,
            )
        return False
    os.environ[HF_ASYNC_LOAD_ENV] = "1"
    logger.info(
        "Apple Metal present: transformers will load model weights on one "
        "thread (%s=1). See docs/apple-metal-thread-safety.md.",
        HF_ASYNC_LOAD_ENV,
    )
    return True


def is_accelerator(device) -> bool:
    """True when *device* is GPU-class (cuda/mps), False for CPU or None.

    Accepts a string or a ``torch.device``; ``torch.device("mps:0")`` and the
    string ``"mps"`` both answer True, since call sites hold both forms.
    """
    if device is None:
        return False
    name = getattr(device, "type", None) or str(device)
    return name.split(":", 1)[0].lower() in ACCELERATORS


def is_metal(device) -> bool:
    """True when *device* is Apple Metal, as a string or a ``torch.device``.

    ``"mps"`` and ``torch.device("mps:0")`` both answer True; ``None`` is False.
    """
    if device is None:
        return False
    name = getattr(device, "type", None) or str(device)
    return name.split(":", 1)[0].lower() == "mps"


def metal_present() -> bool:
    """True when the torch already loaded reports Apple Metal available.

    For deciding whether a warning about the Metal thread applies. torch is
    read from :data:`sys.modules`, as in :func:`empty_device_cache`: without it
    loaded, nothing has used Metal. A probe that raises is logged and counts as
    no Metal.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        return bool(torch.backends.mps.is_available())
    except Exception as exc:
        logger.warning(
            "The Apple Metal (mps) availability probe raised (%s: %s).",
            type(exc).__name__,
            exc,
        )
        return False


def register_metal_thread(thread: threading.Thread) -> None:
    """Record *thread* as the one thread allowed to use Apple Metal.

    ``TaskRunner.start`` registers its GPU worker, whatever the device: the
    registration only has an effect through :func:`ensure_metal_thread`, which
    ignores every device but ``mps``. A process runs one started runner at a
    time (a library switch stops the old vault before starting the new one,
    and where Metal is present ``Vault.start`` first waits for a previous
    worker that outlived its runner's stop), so a second registration replaces
    the first.
    Replacing a worker that is still alive logs a warning where Metal is
    present (:func:`metal_present`); anywhere else the registration guards
    nothing.

    Args:
        thread: The GPU worker thread.
    """
    global _metal_thread
    with _metal_thread_lock:
        previous = _metal_thread
        _metal_thread = thread
    if (
        previous is not None
        and previous is not thread
        and previous.is_alive()
        and metal_present()
    ):
        logger.warning(
            "GPU worker %r replaces %r as the Apple Metal thread while the "
            "previous one is still running; on Metal only one of them may use "
            "the device.",
            thread.name,
            previous.name,
        )


def unregister_metal_thread(thread: threading.Thread) -> None:
    """Forget *thread* as the Metal thread, if it is still the registered one.

    A runner stopping after another has registered leaves that registration
    alone.

    Args:
        thread: The GPU worker thread its runner registered.
    """
    global _metal_thread
    with _metal_thread_lock:
        if _metal_thread is thread:
            _metal_thread = None


def registered_metal_thread() -> Optional[threading.Thread]:
    """The thread allowed to use Apple Metal, or ``None`` when none is registered.

    A registered thread is normally alive. It can be dead when the GPU worker
    was killed by an exception its runner did not catch: the registration then
    stays until that runner stops or another starts, so Metal stays refused
    (see :func:`ensure_metal_thread`).
    """
    with _metal_thread_lock:
        return _metal_thread


def ensure_metal_thread(device) -> None:
    """Raise when this thread would use Apple Metal and is not the GPU worker.

    torch's Metal backend crashes or hangs the process when two threads use it
    at once (``docs/apple-metal-thread-safety.md``), so the task runner's GPU
    worker is the only thread that may. Called at the entry points code outside
    a task reaches - the SBERT and CLIP encoders, the built-in tagger's anomaly
    localisation, the model unload - so a path that was never routed through
    ``Vault.run_inference`` fails with a traceback naming the thread instead of
    taking the process down.

    Silent unless *device* is ``mps`` and a GPU worker is registered: CUDA and
    the CPU have no such rule, and with no running task runner (the CLI tools,
    tests without one) there is no worker for the call to race.

    A registered worker that has died still refuses every other thread, with a
    message saying so. Its runner has not stopped, and treating the dead worker
    as "no runner" would let request threads use Metal together, and beside the
    worker of any runner started later.

    Args:
        device: The device the caller is about to use.

    Raises:
        RuntimeError: *device* is Metal, a GPU worker is registered, and the
            calling thread is not it.
    """
    if not is_metal(device):
        return
    worker = registered_metal_thread()
    current = threading.current_thread()
    if worker is None or current is worker:
        return
    if not worker.is_alive():
        raise RuntimeError(
            f"Apple Metal used from thread {current.name!r}, but the GPU worker "
            f"{worker.name!r} registered for it is no longer running. Metal "
            "stays refused until its task runner stops or a new one starts, "
            "because torch crashes when two threads use it "
            "(docs/apple-metal-thread-safety.md)."
        )
    raise RuntimeError(
        f"Apple Metal used from thread {current.name!r}, but only the GPU worker "
        f"{worker.name!r} may use it: torch crashes when two threads do. Route "
        "the call through Vault.run_inference (docs/apple-metal-thread-safety.md)."
    )


def empty_device_cache(device=None) -> bool:
    """Release cached allocator blocks back to the driver for *device*.

    ``torch`` is read from :data:`sys.modules` rather than imported: a process
    that never imported it cannot have allocated on a device, so there is
    nothing to release, and importing it here purely to discover that would
    cost seconds on paths (the API server's imports, every test teardown) that
    usually never touched a model.

    With no *device*, both backends are flushed - callers on teardown paths
    know a model existed but not where it lived.

    A backend whose probe or flush raises is logged at WARNING, naming the
    backend, and counts as not flushed. The callers treat ``False`` as "nothing
    to account for", so this log line is the only trace of the failure.

    Returns:
        True if a cache was actually flushed.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False

    name = None
    if device is not None:
        name = (getattr(device, "type", None) or str(device)).split(":", 1)[0].lower()

    flushed = False
    if name in (None, "cuda"):
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                flushed = True
        except Exception as exc:
            logger.warning(
                "Could not flush the CUDA allocator cache (%s: %s); its cached "
                "blocks stay reserved.",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
    if name in (None, "mps"):
        try:
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
                flushed = True
        except Exception as exc:
            logger.warning(
                "Could not flush the Apple Metal (mps) allocator cache (%s: %s); "
                "its cached blocks stay reserved.",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
    return flushed

"""Inference device detection across CUDA, Apple Metal (MPS), and CPU."""

import sys

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: Devices that are a GPU of some kind, i.e. everything except plain CPU.
ACCELERATORS = frozenset({"cuda", "mps"})

#: What to tell an owner whose inference ran on the CPU. ``auto`` is the one
#: value that reaches both CUDA and Metal: start-up rejects ``mps``, and
#: ``cuda`` refuses to start on a Mac.
USE_GPU_ADVICE = "Set default_device=auto in server-config.json to use the GPU."


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

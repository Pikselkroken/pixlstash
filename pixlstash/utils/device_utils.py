"""Inference device detection across CUDA, Apple Metal (MPS), and CPU.

Device selection used to be a two-way choice written inline as
``"cuda" if torch.cuda.is_available() else "cpu"``. That held while every
supported accelerator answered to ``torch.cuda``: PyTorch's ROCm build drives
AMD hardware through the CUDA API (HIP masquerades as CUDA), so adding ROCm
needed no new device string. Apple's Metal backend is the first one that does
not — ``torch.backends.mps`` is a separate namespace — so the choice moved
here rather than being widened in place at each call site.
"""

import sys

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: Devices that are a GPU of some kind, i.e. everything except plain CPU.
ACCELERATORS = frozenset({"cuda", "mps"})


def detect_device() -> str:
    """Return the best inference device available: cuda, mps, or cpu.

    CUDA is preferred over MPS because the two are never present together —
    the order only documents intent. ROCm answers ``torch.cuda.is_available()``
    and so is reported as ``"cuda"``, which is what the rest of the codebase
    expects of it.

    A probe that raises is treated as "not available" rather than propagating:
    a broken driver install should degrade to the next device, not abort
    start-up. Returns ``"cpu"`` when torch cannot be imported at all.
    """
    try:
        import torch
    except Exception as exc:
        logger.debug("torch unavailable while detecting device (%s); using CPU.", exc)
        return "cpu"

    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception as exc:
        logger.debug("CUDA availability probe failed (%s); trying MPS.", exc)

    try:
        if torch.backends.mps.is_available():
            return "mps"
    except Exception as exc:
        logger.debug("MPS availability probe failed (%s); using CPU.", exc)

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


def empty_device_cache(device=None) -> bool:
    """Release cached allocator blocks back to the driver for *device*.

    ``torch`` is read from :data:`sys.modules` rather than imported: a process
    that never imported it cannot have allocated on a device, so there is
    nothing to release, and importing it here purely to discover that would
    cost seconds on paths (the API server's imports, every test teardown) that
    usually never touched a model.

    With no *device*, both backends are flushed — callers on teardown paths
    know a model existed but not where it lived.

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
            logger.debug("torch.cuda.empty_cache() failed: %s", exc)
    if name in (None, "mps"):
        try:
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
                flushed = True
        except Exception as exc:
            logger.debug("torch.mps.empty_cache() failed: %s", exc)
    return flushed

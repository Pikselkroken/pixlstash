"""VRAM budget utilities for GPU memory-aware batch sizing.

"VRAM" is a discrete card's word. On Apple Silicon there is no card and no
separate pool: the GPU reads the same RAM the OS and every other application
is using. So on that hardware every figure here means **how much of the single
shared pool PixlStash may claim**, not how much of a dedicated device it owns,
and the numbers are deliberately conservative for that reason - overshooting on
a discrete card wastes the card, overshooting on unified memory swaps the whole
machine.
"""

import subprocess
import sys

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.accelerator import (
    MPS,
    ALL_DEVICE_ERROR_WORDS,
    accelerator_total_memory_mb,
    empty_accelerator_cache,
    is_accelerated,
    is_apple_silicon,
)

logger = get_logger(__name__)


def query_total_vram_mb() -> int:
    """Return the GPU memory this host can offer, in MiB.

    Two very different machines answer this:

    * **NVIDIA**: the sum of installed VRAM across all cards, from
      ``nvidia-smi``. Memory the GPU owns outright.
    * **Apple Silicon**: Metal's recommended working set, which is a *share of
      system RAM* (see
      :func:`~pixlstash.utils.accelerator.accelerator_total_memory_mb`).
      Nothing owns it; the OS, the browser and PixlStash all spend one pool.

    Returning 0 on a Mac - which is what the nvidia-smi-only version did - is
    not harmless. It sends ``system_utils.default_max_vram_gb`` to its 6 GB
    fallback and ``max_vram_budget_gb`` to 12 GB, figures that were invisible
    only while no Apple GPU was ever selected.

    Returns:
        Total GPU-usable memory in MiB, or 0 when there is no GPU to ask.
    """
    if is_apple_silicon():
        return accelerator_total_memory_mb(MPS)
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        totals = []
        for line in output.splitlines():
            value = line.strip()
            if not value:
                continue
            totals.append(int(float(value)))
        return sum(totals)
    except Exception:
        # nvidia-smi absent/failing is normal on CPU-only hosts; 0 (no VRAM) IS
        # the documented answer, so logging it would be routine noise.
        return 0


def vram_limited_batch_cap(
    budget_mb: int | None,
    device: str,
    base_mb: int,
    per_item_mb: int,
) -> int:
    """Return the maximum batch size that fits within a VRAM budget.

    Args:
        budget_mb: Configured VRAM budget in MiB, or ``None`` for unlimited.
        device: Inference device string. Any accelerator (``"cuda"``, ``"mps"``)
            enables the cap; the CPU has no device memory to run out of.
        base_mb: Fixed model footprint in MiB (loaded once).
        per_item_mb: Incremental VRAM per image/item in MiB.

    Returns:
        Maximum item count that fits, or ``10_000`` when the cap is inactive.
    """
    if not is_accelerated(device) or not budget_mb:
        return 10_000
    reserve_mb = max(256, int(budget_mb * 0.20))
    task_budget_mb = max(1, budget_mb - reserve_mb)
    if task_budget_mb <= base_mb:
        return 1
    return max(1, int((task_budget_mb - base_mb) / max(1, per_item_mb)))


#: Words that make an "out of memory" message a *device* one. Without one of
#: these the phrase is ambiguous: ``sqlite3.OperationalError: out of memory``
#: (SQLITE_NOMEM) says it too, and treating that as transient GPU pressure
#: would retry a task that has nothing to do with the GPU.
#:
#: ``mps`` and ``metal`` are here because Apple's two OOM messages carry neither
#: "cuda" nor "gpu", so every MPS OOM was being classified as a permanent
#: failure: torch says ``MPS backend out of memory (MPS allocated: …)`` and the
#: driver says ``kIOGPUCommandBufferCallbackErrorOutOfMemory``.
_DEVICE_WORDS = ALL_DEVICE_ERROR_WORDS

#: Metal's command-buffer failure, which is the *driver* running out rather
#: than torch's allocator. It says "OutOfMemory" as one word and never says
#: "out of memory", so the phrase check below cannot see it.
_METAL_COMMAND_BUFFER_OOM = "kiogpucommandbuffercallbackerroroutofmemory"

#: How far up the ``__cause__``/``__context__`` chain to look. A plugin that
#: wraps the driver's error in its own class is the common case; a chain deeper
#: than this is not.
_CAUSE_DEPTH = 5


def is_vram_oom(error: BaseException) -> bool:
    """True when *error* is an out-of-GPU-memory failure.

    Type identity is the reliable signal (``torch.OutOfMemoryError``), but a
    plugin may run its model through a runtime that raises its own exception
    type for the same condition, so the message is checked as well - and the
    wrapped-cause chain with it, because ``raise RuntimeError(...) from oom``
    is exactly how a plugin reports one. ``torch`` is read from
    :data:`sys.modules` for the same reason as in :func:`empty_device_cache`: a
    process that never imported it cannot have raised its OOM.

    Args:
        error: The exception to classify.

    Returns:
        ``True`` for a GPU OOM, which callers treat as transient and retry.
    """
    torch = sys.modules.get("torch")
    oom_type = getattr(torch, "OutOfMemoryError", None) if torch else None
    seen = set()
    current: BaseException | None = error
    for _ in range(_CAUSE_DEPTH):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        if isinstance(oom_type, type) and isinstance(current, oom_type):
            return True
        message = str(current).lower()
        if "cuda_error_out_of_memory" in message:
            return True
        if _METAL_COMMAND_BUFFER_OOM in message:
            return True
        # ONNX Runtime's BFC arena says neither "out of memory" nor a device
        # word when the card is full: "Failed to allocate memory for requested
        # buffer of size N" from bfc_arena.cc. Another process holding the
        # card (a local LLM, a ComfyUI graph) produces exactly this, and it is
        # as transient as torch's.
        if "failed to allocate memory for requested buffer" in message:
            return True
        if "out of memory" in message and any(w in message for w in _DEVICE_WORDS):
            return True
        current = current.__cause__ or current.__context__
    return False


def empty_device_cache(device=None) -> bool:
    """Flush the accelerator allocator's cache back to the driver.

    Kept as the name every recovery and teardown path in the codebase already
    imports; the accelerator-specific work is
    :func:`~pixlstash.utils.accelerator.empty_accelerator_cache`. It was called
    ``empty_cuda_cache`` and released CUDA's cache only, so on any other
    accelerator every caller's "free memory and retry" did nothing at all.

    Args:
        device: Flush only this accelerator's cache. ``None`` flushes whichever
            one this host has, which is what every teardown path wants. A
            spill-to-CPU path passes the device it is spilling *from*, because
            by the time it flushes it has already reassigned its own device to
            ``"cpu"`` and asking the host would flush the wrong allocator - or,
            on a host with two, the wrong one of the two.

    Returns:
        ``True`` if a cache was flushed, ``False`` when torch is not loaded or
        the host has no accelerator (callers use this to skip their own cache
        bookkeeping).
    """
    return empty_accelerator_cache(device)

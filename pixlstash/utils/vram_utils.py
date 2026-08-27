"""VRAM budget utilities for GPU memory-aware batch sizing."""

import subprocess
import sys

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def query_total_vram_mb() -> int:
    """Return the total installed VRAM across all NVIDIA GPUs in MiB.

    Uses ``nvidia-smi`` to query installed VRAM.  Returns 0 if the query
    fails (e.g. on CPU-only machines or when nvidia-smi is not installed).
    """
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
        device: Inference device string (``"cuda"`` enables the cap).
        base_mb: Fixed model footprint in MiB (loaded once).
        per_item_mb: Incremental VRAM per image/item in MiB.

    Returns:
        Maximum item count that fits, or ``10_000`` when the cap is inactive.
    """
    if device != "cuda" or not budget_mb:
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
_DEVICE_WORDS = ("cuda", "gpu", "hip", "vram")

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
    :data:`sys.modules` for the same reason as in :func:`empty_cuda_cache`: a
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


def empty_cuda_cache() -> bool:
    """Flush PyTorch's CUDA allocator cache back to the driver.

    ``torch`` is looked up in :data:`sys.modules` rather than imported. If torch
    was never imported, this process cannot have allocated any CUDA memory, so
    there is nothing to flush - and importing it here purely to discover that
    would cost seconds. That matters because this module sits on the API
    server's import path and on every best-effort teardown path in the test
    suite, where the caller usually never touched a model at all.

    Returns:
        ``True`` if the cache was flushed, ``False`` when torch is not loaded or
        no CUDA device is available (callers use this to skip their own cache
        bookkeeping).
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    if not torch.cuda.is_available():
        return False
    torch.cuda.empty_cache()
    return True

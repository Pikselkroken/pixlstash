"""VRAM budget utilities for GPU memory-aware batch sizing."""

import re
import subprocess
import sys

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.device_utils import empty_device_cache, is_accelerator

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

#: Metal's OOM reads ``MPS backend out of memory`` and carries none of the words
#: above, so ``mps`` joins them for :func:`is_vram_oom` - as a whole word, which
#: keeps "clamps" and "timestamps" from reading as a device.
_MPS_DEVICE_WORD = re.compile(r"\bmps\b")

#: What a CUDA fault says: ``CUDA error:`` from the runtime (cuBLAS statuses and
#: "no kernel image" arrive inside it), ``CUDA driver`` and ``CUDA unknown
#: error`` from the driver and device enumeration, and cuDNN's ``cuDNN error:``,
#: version mismatch and frontend errors. When no convolution algorithm or engine
#: fits, usually for want of workspace memory, cuDNN says so without the word
#: "error", and the CPU can still run the pass. The engine message is prefixed
#: with the heuristic mode torch asked for - ``GET`` in the default mode,
#: ``FIND`` when ``torch.backends.cudnn.benchmark`` is on - so the mode word is
#: left out and both match. cuDNN's older convolution path says ``no valid
#: convolution algorithms available``, and its frontend reports a plan the card
#: cannot run with a bare ``CUDNN_STATUS_`` code. Faults that arrive through
#: other layers: the caching allocator's ``NVML_SUCCESS == r`` assert, and
#: Triton's ``Triton Error [CUDA]`` from a compiled kernel. ``cudnn`` alone is
#: not enough: torch's argument checks name the cuDNN op a misplaced or
#: mistyped tensor reached ("... while checking arguments for
#: cudnn_batch_norm"), and a bug must raise, not move to the CPU.
_CUDA_FAULT = re.compile(
    r"cuda error|cuda driver|cuda unknown error"
    r"|cudnn error|cudnn version|cudnn frontend error|cudnn_status_"
    r"|unable to find a valid cudnn algorithm"
    r"|no valid convolution algorithms available"
    r"|was unable to find an engine"
    r"|nvml_success == r internal assert failed"
    r"|triton error \[cuda\]"
)

#: What a Metal fault says besides its OOM: ``Invalid buffer size`` for one
#: allocation larger than the device's maximum buffer, and the MPS backend's
#: other size limits (matmul output, channels, graph dims); an operator or an
#: input shape it does not implement; float64, which Metal cannot represent,
#: and the dtypes and dtype combinations it cannot run. The buffer refusal is
#: not an out-of-memory condition and is_vram_oom does not read it: the same
#: allocation is refused the same way on every attempt, so only the CPU, which
#: has no such limit, can run it. The rest are capability gaps rather than
#: failures; the CPU can run what Metal cannot, so they retry there as well.
#: The phrases are torch's own and deliberately narrower than "MPS does not
#: support": "MPS device does not support linear for non-float inputs" is an
#: input the CPU refuses too against float weights, and a bug must raise, not
#: move to the CPU.
_MPS_FAULT = re.compile(
    r"invalid buffer size"
    r"|not supported on mps"
    r"|not supported at the mps device"
    r"|mpsgraph does not support tensor dims larger than"
    r"|not currently implemented for the mps device"
    r"|not implemented on mps device yet"
    r"|mps framework doesn't support float64"
    r"|to the mps backend but it does not have support for that dtype"
    r"|scaled_dot_product_attention for mps does not support"
)

#: The fault phrases for each accelerator in ``device_utils.ACCELERATORS``.
_DEVICE_FAULTS = {"cuda": _CUDA_FAULT, "mps": _MPS_FAULT}

#: How far up the ``__cause__``/``__context__`` chain to look. A plugin that
#: wraps the driver's error in its own class is the common case; a chain deeper
#: than this is not.
_CAUSE_DEPTH = 5


def _iter_causes(error: BaseException):
    """Yield *error* and what it was raised from, nearest first.

    ``raise RuntimeError(...) from oom`` is how a plugin reports a failure its
    own way, so a classifier that reads only the exception it was handed misses
    the one underneath. :data:`_CAUSE_DEPTH` bounds it, which is also what makes
    the loop ``__context__`` can form - two handlers re-raising at each other -
    terminate without needing cycle detection.
    """
    current: BaseException | None = error
    for _ in range(_CAUSE_DEPTH):
        if current is None:
            return
        yield current
        current = current.__cause__ or current.__context__


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
    for current in _iter_causes(error):
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
        if "out of memory" in message and (
            any(w in message for w in _DEVICE_WORDS) or _MPS_DEVICE_WORD.search(message)
        ):
            return True
    return False


def is_device_error(error: BaseException, device) -> bool:
    """True when *error* is a fault of *device* worth retrying on the CPU.

    Broader than :func:`is_vram_oom`, which answers only "the device ran out of
    memory". This also covers a card the installed build cannot drive, a
    driver-level fault and, on Metal, an operation the backend cannot run -
    conditions that are not OOM but have the same remedy, which is to reload
    on the CPU and carry on.

    Each accelerator is matched on what its own faults say, never on its name:
    a tensor left on the wrong device and a ``view`` that needed ``reshape``
    both name the device too, and they are bugs, so they raise. ``None`` and
    ``"cpu"`` are never device errors.

    Args:
        error: The exception raised by the failed inference.
        device: The device the model was running on when it raised; a string
            such as ``"mps:0"`` or a ``torch.device``.

    Returns:
        ``True`` when the caller should move to the CPU and retry.
    """
    if not is_accelerator(device):
        return False

    # Everything that is an out-of-memory condition, in any of its spellings:
    # torch's typed OOM, the CUDA and Metal texts and onnxruntime's arena
    # message. Already chain-aware.
    if is_vram_oom(error):
        return True

    # Normalised as is_accelerator does: a torch.device or a string, no index.
    name = getattr(device, "type", None) or str(device)
    device_type = name.split(":", 1)[0].lower()
    fault_phrases = _DEVICE_FAULTS[device_type]

    # A driver-level fault carries no reliable words at all: CudaError renders
    # whatever cudaGetErrorString returns, so type identity is the only stable
    # signal for it. torch.OutOfMemoryError is deliberately NOT listed here -
    # is_vram_oom already matches it by type, and a second copy would be a
    # branch no test could ever isolate.
    cuda_error = None
    if device_type == "cuda":
        torch = sys.modules.get("torch")
        cuda_error = getattr(getattr(torch, "cuda", None), "CudaError", None)

    # A plugin reporting a device failure as its own exception type is the
    # common case, so the chain is walked here as it is in is_vram_oom.
    for current in _iter_causes(error):
        if isinstance(cuda_error, type) and isinstance(current, cuda_error):
            return True
        if fault_phrases.search(str(current).lower()):
            return True

    return False


def empty_cuda_cache() -> bool:
    """Flush the torch allocator cache back to the driver, on any backend.

    Covers Metal as well as CUDA.

    See :func:`pixlstash.utils.device_utils.empty_device_cache` for why torch is
    read from :data:`sys.modules` rather than imported.

    Returns:
        ``True`` if a cache was flushed, ``False`` when torch is not loaded or
        no accelerator is available (callers use this to skip their own cache
        bookkeeping).
    """
    return empty_device_cache()

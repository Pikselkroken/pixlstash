"""Suspend this process's CUDA context while a training run has the card (#2557).

PyTorch cannot release a live CUDA context, and the context alone costs about
700 MB. NVIDIA's checkpoint driver API (``cuCheckpointProcess*``, Linux, driver
570+) can: :func:`suspend` locks the process's CUDA work and copies everything
it holds on the device to host memory, after which the process no longer
appears on the card; :func:`resume` copies it back.

While suspended, CUDA calls from this process are not safe. Measured on an
RTX 5090 (driver 615, torch 2.13): an allocation, a kernel, ``empty_cache``
and ``torch.cuda.utilization`` block until :func:`resume`, ``mem_get_info``
raises, and ``torch.cuda.synchronize`` **kills the process**. Every NVML call
made in this process blocks as well, so the ledger answers from
``nvidia-smi`` meanwhile (``ledger.SmiNvml``). So nothing PixlStash runs makes
one: the arbiter refuses PixlStash's GPU leases, ``empty_accelerator_cache``
does nothing, and the device calls made outside a lease (NVML through the
ledger, a search's query encode, the tag model preload, the planner's
utilisation sample) are counted in flight with :func:`enter_device_call`,
which a suspend waits to drain and which turns them away once it has begun.

A failed restore cannot be retried: a restore into a full card fails out of
memory, the next attempt fails with an OS error and the context never comes
back, so the arbiter only restores once the card has room for what was saved.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: The rollback line. False keeps PixlStash's CUDA context on the card while a
#: training run holds it, as before #2557.
CUDA_SUSPEND_ENABLED = True

#: How long the lock waits for CUDA work in flight to finish.
LOCK_TIMEOUT_MS = 10_000

_lock = threading.Lock()
# Guards _suspended against the count of device calls in flight.
_calls = threading.Condition()
_in_flight = 0
_suspended = False
# Counts the context leaving and returning to the card: a reading taken in
# another generation describes a different card.
_generation = 0
_failed = False
_driver: ctypes.CDLL | None = None
_driver_read = False


class _LockArgs(ctypes.Structure):
    _fields_ = [
        ("timeoutMs", ctypes.c_uint),
        ("reserved0", ctypes.c_uint),
        ("reserved1", ctypes.c_uint64 * 7),
    ]


def _cuda() -> ctypes.CDLL | None:
    """libcuda with the checkpoint API, or None (not Linux, no driver, < 570)."""
    global _driver, _driver_read
    if not _driver_read:
        _driver_read = True
        if sys.platform.startswith("linux"):
            try:
                lib = ctypes.CDLL("libcuda.so.1")
                if hasattr(lib, "cuCheckpointProcessCheckpoint"):
                    _driver = lib
                else:
                    logger.info(
                        "The NVIDIA driver has no CUDA checkpoint API (570+ "
                        "needed); PixlStash keeps its CUDA context during training"
                    )
            except OSError as exc:
                logger.debug("libcuda could not be loaded: %s", exc)
    return _driver


def suspended() -> bool:
    """Whether this process's CUDA context is suspended, or being suspended.

    True also after a restore failed: the context is gone until a restart.
    """
    return _suspended


def generation() -> int:
    """Changes each time the context leaves or returns to the card."""
    return _generation


def available() -> bool:
    """Whether :func:`suspend` can work now: enabled, the API is there and
    this process has a CUDA context to suspend."""
    torch = sys.modules.get("torch")
    if not CUDA_SUSPEND_ENABLED or _failed or torch is None:
        return False
    try:
        initialized = torch.cuda.is_initialized()
    except Exception as exc:
        logger.debug("Could not ask torch whether CUDA is initialised: %s", exc)
        return False
    return initialized and _cuda() is not None


def begin() -> None:
    """Mark the context as being suspended, before :func:`suspend` runs.

    From here :func:`suspended` is True, so nothing starts new CUDA work. The
    arbiter calls this under its lock, where it hands out GPU leases.
    """
    global _suspended
    with _calls:
        _suspended = True


def abandon() -> None:
    """Undo :func:`begin` when the suspend does not go ahead."""
    global _suspended
    with _calls:
        if not _failed:
            _suspended = False


def enter_device_call() -> bool:
    """Register a CUDA or NVML call about to be made outside a GPU lease.

    Returns:
        False while suspended, or being suspended, when the call must not be
        made here (it would block until the restore); otherwise True, and
        :func:`leave_device_call` must follow the call.
    """
    global _in_flight
    with _calls:
        if _suspended:
            return False
        _in_flight += 1
        return True


def leave_device_call() -> None:
    global _in_flight
    with _calls:
        _in_flight -= 1
        _calls.notify_all()


def drain() -> bool:
    """Wait for the device calls in flight to end. Call after :func:`begin`.

    Returns:
        False when one did not end within :data:`LOCK_TIMEOUT_MS`.
    """
    with _calls:
        return _calls.wait_for(lambda: _in_flight == 0, timeout=LOCK_TIMEOUT_MS / 1000)


def suspend() -> bool | None:
    """Lock and checkpoint this process's CUDA context. Call after :func:`begin`.

    Returns:
        True when the context is off the card. None when it could not be done
        now (a device call or CUDA work in flight did not finish in time) and
        may be tried again; False when it failed. Either leaves it as it was.
    """
    global _failed, _generation
    cuda = _cuda()
    if cuda is None:
        abandon()
        return False
    if not drain():
        logger.info(
            "A device call did not finish in %ss; PixlStash keeps its CUDA "
            "context for now",
            LOCK_TIMEOUT_MS / 1000,
        )
        abandon()
        return None
    pid = os.getpid()
    with _lock:
        code = cuda.cuCheckpointProcessLock(
            pid, ctypes.byref(_LockArgs(LOCK_TIMEOUT_MS))
        )
        if code != 0:
            logger.info(
                "Could not lock PixlStash's CUDA work to suspend it (CUresult %s); "
                "it keeps its context for now",
                code,
            )
            abandon()
            return None
        code = cuda.cuCheckpointProcessCheckpoint(pid, None)
        if code == 0:
            _generation += 1
            return True
        unlocked = cuda.cuCheckpointProcessUnlock(pid, None)
        if unlocked != 0:
            _failed = True
            logger.error(
                "Suspending PixlStash's CUDA context failed (CUresult %s) and it "
                "could not be unlocked (CUresult %s): PixlStash stays off the GPU "
                "until it is restarted",
                code,
                unlocked,
            )
            return False
        logger.warning(
            "Could not checkpoint PixlStash's CUDA context (CUresult %s); it "
            "keeps its context",
            code,
        )
        abandon()
        return False


def resume() -> bool:
    """Restore and unlock the context :func:`suspend` took off the card.

    Only call once the card has room for it: a failed restore is final.

    Returns:
        True when CUDA is usable again.
    """
    global _suspended, _failed, _generation
    cuda = _cuda()
    if cuda is None or _failed:
        return False
    pid = os.getpid()
    with _lock:
        code = cuda.cuCheckpointProcessRestore(pid, None)
        if code == 0:
            code = cuda.cuCheckpointProcessUnlock(pid, None)
        if code != 0:
            state = ctypes.c_int(-1)
            cuda.cuCheckpointProcessGetState(pid, ctypes.byref(state))
            _failed = True
            logger.error(
                "Restoring PixlStash's CUDA context failed (CUresult %s, process "
                "state %s): PixlStash stays off the GPU until it is restarted",
                code,
                state.value,
            )
            return False
        with _calls:
            _suspended = False
            _generation += 1
        return True

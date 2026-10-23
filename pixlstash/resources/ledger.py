"""What is on this machine's GPUs: one NVML handle, read per device.

The ledger is the arbiter's only source of device memory (see
``docs/backend_architecture.md`` §7, "VRAM arbitration"). It answers per
device, keyed by NVML UUID and never by index, because NVML's ordering and
CUDA's differ.

It does not attribute memory per process: NVML reports per-process memory as
NOT_AVAILABLE on Windows WDDM and inside Docker, which is where it is needed
most. Each tenant measures itself instead (ComfyUI through ``/system_stats``,
PixlStash through its own allocator), and what nobody claims is "other apps".
The one per-process read left is ``other_processes``, the OOM notice's best
effort at naming who else is on the card.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass

from pixlstash.pixl_logging import get_logger

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None

try:
    import pynvml
except Exception:  # pragma: no cover - optional dependency
    pynvml = None

logger = get_logger(__name__)

#: The id a device gets when it can be used but not measured: Metal, ROCm
#: without NVML, a CUDA box whose driver has no NVML.
UNMEASURED_DEVICE = "local"

#: How old a device reading may be before it is read again.
READING_TTL_S = 1.0

_MB = 1024 * 1024


@dataclass(frozen=True)
class DeviceReading:
    """One GPU as NVML sees it, in MB."""

    uuid: str
    name: str
    total_mb: int
    used_mb: int
    free_mb: int
    measurable: bool = True


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class Ledger:
    """Device memory, read through one long-lived NVML handle.

    ``nvmlInit`` is called once and never shut down: ``HardwareMonitor`` used
    to initialise and shut NVML down on every poll, and two threads doing that
    at once can shut the library down under each other.

    Args:
        nvml: The NVML binding, injectable for tests. ``None`` means no NVML.
    """

    def __init__(self, nvml=pynvml) -> None:
        self._nvml_module = nvml
        self._nvml = None
        self._init_failed = False
        self._lock = threading.Lock()
        self._readings: list[DeviceReading] = []
        self._read_at = 0.0

    def nvml(self):
        """The initialised NVML binding, or None where there is none."""
        with self._lock:
            if self._nvml is not None or self._init_failed:
                return self._nvml
            if self._nvml_module is None:
                self._init_failed = True
                return None
            try:
                self._nvml_module.nvmlInit()
                self._nvml = self._nvml_module
            except Exception as exc:
                # No NVIDIA driver is the normal case on Apple, AMD and CPU
                # hosts: the devices are then unmeasurable, said once.
                logger.info("NVML is not available, so GPUs are not measured: %s", exc)
                self._init_failed = True
            return self._nvml

    def devices(self, max_age_s: float = READING_TTL_S) -> list[DeviceReading]:
        """Every GPU, read at most *max_age_s* ago.

        With no NVML, one unmeasurable device when this process has an
        accelerator in use, else none.
        """
        now = time.monotonic()
        with self._lock:
            if self._readings and now - self._read_at < max_age_s:
                return list(self._readings)
        nvml = self.nvml()
        readings: list[DeviceReading] = []
        if nvml is not None:
            try:
                for index in range(nvml.nvmlDeviceGetCount()):
                    handle = nvml.nvmlDeviceGetHandleByIndex(index)
                    memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                    total = int(memory.total) // _MB
                    used = int(memory.used) // _MB
                    readings.append(
                        DeviceReading(
                            uuid=_text(nvml.nvmlDeviceGetUUID(handle)),
                            name=_text(nvml.nvmlDeviceGetName(handle)),
                            total_mb=total,
                            used_mb=used,
                            free_mb=max(0, total - used),
                        )
                    )
            except Exception as exc:
                logger.warning(
                    "Reading GPU memory through NVML failed; the last reading "
                    "stands until it can be read again: %s",
                    exc,
                )
                with self._lock:
                    return list(self._readings)
        elif _accelerator_in_use():
            readings.append(
                DeviceReading(
                    uuid=UNMEASURED_DEVICE,
                    name="GPU",
                    total_mb=0,
                    used_mb=0,
                    free_mb=0,
                    measurable=False,
                )
            )
        with self._lock:
            self._readings = readings
            self._read_at = time.monotonic()
        return list(readings)

    def device(self, uuid: str | None) -> DeviceReading | None:
        """The reading of one device, or None."""
        return next((d for d in self.devices() if d.uuid == uuid), None)

    def pixlstash_device(self) -> str | None:
        """The UUID of the GPU this process's ``cuda:0`` is.

        ``gpu_select`` puts the chosen GPU's UUID in ``CUDA_VISIBLE_DEVICES``
        before torch loads, so that names it. An index there, or nothing, is
        taken as NVML's device at that index.

        ponytail: NVML orders by PCI bus and CUDA, by default, fastest first;
        an index on a mixed two-GPU box can name the other card. Set the
        variable to a UUID (or pick the GPU in Settings) when that matters.
        """
        devices = self.devices()
        if not devices:
            return None
        first = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0].strip()
        if first.startswith("GPU-"):
            return next((d.uuid for d in devices if d.uuid == first), first)
        index = int(first) if first.isdigit() else 0
        return devices[index].uuid if index < len(devices) else devices[0].uuid

    def other_processes(self, limit: int = 3) -> list[dict]:
        """The other processes on the cards, largest first: ``{name, used_mb}``.

        For the out-of-memory notice only. Empty where NVML cannot attribute
        memory (Windows WDDM, Docker), which is the honest answer there.
        """
        nvml = self.nvml()
        if nvml is None:
            return []
        own = os.getpid()
        found: dict[int, int] = {}
        try:
            for index in range(nvml.nvmlDeviceGetCount()):
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                for entry in nvml.nvmlDeviceGetComputeRunningProcesses(handle):
                    used = getattr(entry, "usedGpuMemory", None)
                    if entry.pid == own or not isinstance(used, int) or used <= 0:
                        continue
                    if used == getattr(nvml, "NVML_VALUE_NOT_AVAILABLE", -1):
                        continue
                    found[entry.pid] = found.get(entry.pid, 0) + used // _MB
        except Exception as exc:
            logger.debug("Could not list the GPU's other processes: %s", exc)
            return []
        rows = [
            {"name": _process_name(pid), "used_mb": used} for pid, used in found.items()
        ]
        rows.sort(key=lambda row: -row["used_mb"])
        return rows[:limit]


def pixlstash_usage_mb() -> int | None:
    """What this process's torch allocator holds on its GPU, or None.

    None when torch is not loaded or has no CUDA device: there is nothing of
    ours on a card then.
    """
    torch = sys.modules.get("torch")
    try:
        if torch is None or not torch.cuda.is_available():
            return None
        return int(torch.cuda.memory_reserved(0)) // _MB
    except Exception as exc:
        logger.debug("Could not read torch's reserved memory: %s", exc)
        return None


def ram() -> dict:
    """``{total_mb, available_mb}`` of this machine's RAM; zeros without psutil."""
    if psutil is None:
        return {"total_mb": 0, "available_mb": 0}
    memory = psutil.virtual_memory()
    return {
        "total_mb": int(memory.total) // _MB,
        "available_mb": int(memory.available) // _MB,
    }


def _accelerator_in_use() -> bool:
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        if torch.cuda.is_available():
            return True
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        return bool(mps is not None and mps.is_available())
    except Exception as exc:
        logger.debug("Could not ask torch for an accelerator: %s", exc)
        return False


def _process_name(pid: int) -> str:
    if psutil is not None:
        try:
            return psutil.Process(pid).name()
        except Exception as exc:
            logger.debug("Could not name process %s: %s", pid, exc)
    return f"pid {pid}"


_ledger: Ledger | None = None
_ledger_lock = threading.Lock()


def ledger() -> Ledger:
    """The process's one ledger."""
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = Ledger()
        return _ledger

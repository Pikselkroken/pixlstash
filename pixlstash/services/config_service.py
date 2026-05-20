"""Service layer for system configuration and hardware monitoring.

Extracted from pixlstash/routes/config.py to keep route handlers thin.
Provides hardware monitoring helpers and import folder utilities.
"""

import os
import subprocess
import time
from typing import TYPE_CHECKING

from sqlmodel import select

from pixlstash.db_models import ImportFolder
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.vault import Vault

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None

try:
    import pynvml
except Exception:  # pragma: no cover - optional dependency
    pynvml = None

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Import folder helpers
# ---------------------------------------------------------------------------


def get_import_folder_paths(vault: "Vault") -> list[str]:
    """Return the list of configured import folder paths from the database.

    Args:
        vault: Application vault, used for DB task dispatch.

    Returns:
        List of import folder path strings, in ID order.
    """
    try:
        db_folders = vault.db.run_immediate_read_task(
            lambda session: session.exec(
                select(ImportFolder).order_by(ImportFolder.id)
            ).all()
        )
        return [
            folder.folder
            for folder in (db_folders or [])
            if getattr(folder, "folder", None)
        ]
    except Exception as exc:
        logger.debug("Failed to read import folders from DB: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Hardware monitoring helpers
# ---------------------------------------------------------------------------


def _get_total_process_cpu_seconds(process) -> float:
    """Return total CPU seconds consumed by a process and its children.

    Args:
        process: A ``psutil.Process`` instance.

    Returns:
        Total CPU time in seconds (user + system, including children).
    """
    cpu_times = process.cpu_times()
    total_seconds = float(cpu_times.user + cpu_times.system)
    try:
        for child in process.children(recursive=True):
            child_times = child.cpu_times()
            total_seconds += float(child_times.user + child_times.system)
    except Exception as exc:
        logger.debug("Failed to collect child process CPU times: %s", exc)
    return total_seconds


def _set_vram_payload(payload: dict, used_bytes: int, total_bytes: int) -> bool:
    """Populate *payload* with VRAM usage keys from raw byte values.

    Args:
        payload: Dict to update in-place.
        used_bytes: VRAM currently in use, in bytes.
        total_bytes: Total VRAM capacity, in bytes.

    Returns:
        True if successfully populated, False on invalid input.
    """
    try:
        total = int(total_bytes or 0)
        if total <= 0:
            return False
        used = max(0, int(used_bytes or 0))
        payload["vram_total_gb"] = round(total / (1024**3), 2)
        payload["vram_used_gb"] = round(used / (1024**3), 2)
        payload["vram_percent"] = round((used / total) * 100.0, 1)
        return True
    except Exception:
        return False


def collect_vram_from_torch(payload: dict) -> bool:
    """Populate *payload* with CUDA VRAM usage via PyTorch.

    Args:
        payload: Dict to update in-place with VRAM keys.

    Returns:
        True if VRAM data was successfully collected, False otherwise.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        total_bytes = 0
        used_bytes = 0
        device_count = int(torch.cuda.device_count() or 0)
        if device_count <= 0:
            return False
        for index in range(device_count):
            props = torch.cuda.get_device_properties(index)
            total_bytes += int(getattr(props, "total_memory", 0) or 0)
            try:
                used_bytes += int(torch.cuda.memory_reserved(index) or 0)
            except Exception as exc:
                logger.debug("Failed to read CUDA memory for device %d: %s", index, exc)
        return _set_vram_payload(payload, used_bytes, total_bytes)
    except Exception:
        return False


def parse_nvidia_smi_values(command: list[str]) -> list[int]:
    """Run an nvidia-smi command and parse the integer output values.

    Args:
        command: Full command list to pass to subprocess.

    Returns:
        List of parsed integer values from the output.
    """
    output = subprocess.check_output(command, stderr=subprocess.DEVNULL, text=True)
    values = []
    for line in output.splitlines():
        value = line.strip().split(",", 1)[0].strip()
        if not value or value.upper() == "N/A":
            continue
        try:
            values.append(int(float(value)))
        except Exception:
            continue
    return values


def collect_vram_from_nvidia_smi(payload: dict) -> bool:
    """Populate *payload* with VRAM usage via nvidia-smi subprocess.

    Args:
        payload: Dict to update in-place with VRAM keys.

    Returns:
        True if VRAM data was successfully collected, False otherwise.
    """
    try:
        totals_mib = parse_nvidia_smi_values(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
        if not totals_mib:
            return False
        total_bytes = sum(totals_mib) * 1024 * 1024

        pid = os.getpid()
        used_mib = 0
        try:
            process_lines = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_gpu_memory",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in process_lines.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    entry_pid = int(parts[0])
                except Exception:
                    continue
                if entry_pid != pid:
                    continue
                value = parts[1]
                if not value or value.upper() == "N/A":
                    continue
                try:
                    used_mib += int(float(value))
                except Exception:
                    continue
        except Exception:
            used_mib = 0

        used_bytes = used_mib * 1024 * 1024
        return _set_vram_payload(payload, used_bytes, total_bytes)
    except Exception:
        return False


class HardwareMonitor:
    """Stateful hardware monitor that samples CPU and caches VRAM readings.

    Maintains per-instance state for incremental CPU percentage calculation
    and VRAM cache expiry.

    Attributes:
        _VRAM_CACHE_TTL_S: Seconds to cache VRAM readings to avoid repeated
            nvidia-smi subprocess invocations.
    """

    _VRAM_CACHE_TTL_S: float = 5.0

    def __init__(self) -> None:
        """Initialise the hardware monitor and try to attach to the current process."""
        self._process_handle = None
        self._process_pid: int | None = None
        self._last_cpu_sample_at: float | None = None
        self._last_cpu_seconds: float | None = None
        self._vram_cache_ts: float = 0.0
        self._vram_cache_payload: dict = {}

        if psutil:
            try:
                self._process_handle = psutil.Process(os.getpid())
                self._process_pid = self._process_handle.pid
            except Exception as exc:
                logger.warning("Failed to initialise process usage handle: %s", exc)

    def get_usage(self) -> dict:
        """Sample current CPU, RAM, and VRAM usage for the server process.

        Returns:
            Dict with keys: cpu_percent, cpu_percent_all_cores,
            cpu_percent_one_core, ram_used_gb, ram_total_gb, ram_percent,
            vram_used_gb, vram_total_gb, vram_percent.
        """
        payload: dict = {
            "cpu_percent": None,
            "cpu_percent_all_cores": None,
            "cpu_percent_one_core": None,
            "ram_used_gb": None,
            "ram_total_gb": None,
            "ram_percent": None,
            "vram_used_gb": None,
            "vram_total_gb": None,
            "vram_percent": None,
        }

        if psutil:
            try:
                current_pid = os.getpid()
                process = self._process_handle
                if (
                    process is None
                    or self._process_pid != current_pid
                    or not process.is_running()
                ):
                    process = psutil.Process(current_pid)
                    self._process_handle = process
                    self._process_pid = process.pid
                    self._last_cpu_sample_at = None
                    self._last_cpu_seconds = None

                now = time.monotonic()
                cpu_seconds = _get_total_process_cpu_seconds(process)
                if (
                    self._last_cpu_sample_at is not None
                    and self._last_cpu_seconds is not None
                    and now > self._last_cpu_sample_at
                ):
                    elapsed = now - self._last_cpu_sample_at
                    used_cpu = max(0.0, cpu_seconds - self._last_cpu_seconds)
                    cpu_count = psutil.cpu_count() or 1
                    cpu_percent_one_core = max(0.0, (used_cpu / elapsed) * 100.0)
                    cpu_percent_all_cores = max(
                        0.0, min(100.0, cpu_percent_one_core / cpu_count)
                    )
                    payload["cpu_percent_one_core"] = cpu_percent_one_core
                    payload["cpu_percent_all_cores"] = cpu_percent_all_cores
                    payload["cpu_percent"] = cpu_percent_all_cores
                else:
                    payload["cpu_percent"] = 0.0
                    payload["cpu_percent_all_cores"] = 0.0
                    payload["cpu_percent_one_core"] = 0.0

                self._last_cpu_sample_at = now
                self._last_cpu_seconds = cpu_seconds

                memory = process.memory_info()
                payload["ram_used_gb"] = round(memory.rss / (1024**3), 2)
                payload["ram_percent"] = process.memory_percent()
            except Exception as exc:
                logger.warning("Failed to read CPU/RAM usage: %s", exc)

        vram_collected = False
        now_mono = time.monotonic()
        if (
            now_mono - self._vram_cache_ts < self._VRAM_CACHE_TTL_S
            and self._vram_cache_payload
        ):
            payload.update(self._vram_cache_payload)
            vram_collected = True

        if not vram_collected and pynvml:
            logger.debug("Collecting VRAM usage via NVML")
            try:
                pynvml.nvmlInit()
                pid = os.getpid()
                used_bytes = 0
                total_bytes = 0
                device_count = pynvml.nvmlDeviceGetCount()
                for index in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                    try:
                        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                        total_bytes += int(getattr(mem_info, "total", 0) or 0)
                    except Exception as exc:
                        logger.debug(
                            "Failed to read NVML memory info for device %d: %s",
                            index,
                            exc,
                        )
                    processes = []
                    try:
                        processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                    except Exception:
                        processes = []
                    try:
                        processes += pynvml.nvmlDeviceGetGraphicsRunningProcesses(
                            handle
                        )
                    except Exception as exc:
                        logger.debug(
                            "Failed to read NVML graphics processes for device %d: %s",
                            index,
                            exc,
                        )
                    for entry in processes:
                        if entry.pid != pid:
                            continue
                        used_gpu = getattr(entry, "usedGpuMemory", None)
                        if used_gpu is None:
                            continue
                        if used_gpu == getattr(pynvml, "NVML_VALUE_NOT_AVAILABLE", -1):
                            continue
                        used_bytes += used_gpu
                vram_collected = _set_vram_payload(payload, used_bytes, total_bytes)
            except Exception as exc:
                logger.debug("Failed to read VRAM usage: %s", exc)
            finally:
                try:
                    pynvml.nvmlShutdown()
                except Exception as exc:
                    logger.debug("pynvml shutdown failed: %s", exc)

        if not vram_collected:
            vram_collected = collect_vram_from_torch(payload)

        if not vram_collected:
            collect_vram_from_nvidia_smi(payload)

        _vram_keys = ("vram_used_gb", "vram_total_gb", "vram_percent")
        self._vram_cache_payload = {k: payload[k] for k in _vram_keys if k in payload}
        self._vram_cache_ts = time.monotonic()

        return payload

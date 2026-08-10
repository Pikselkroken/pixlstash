"""System-level utilities (hardware detection, etc.)."""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Hard upper bound for the VRAM budget setting. Applies both to the UI slider
# maximum and to backend validation. Keep in sync with the frontend constant.
MAX_VRAM_BUDGET_GB: float = 12.0


@dataclass(frozen=True)
class StorageDevice:
    """The filesystem a path sits on, and how full it is.

    Attributes:
        device_id: ``st_dev`` as a string. Opaque and stable only while the
            device stays mounted, which is all a single response needs: it is a
            grouping key, never something to persist. Two folders sharing it
            share one drive and therefore one capacity meter.
        mount_point: Where that filesystem is mounted (``/``, ``/mnt/models``,
            ``D:\\``). This is the label a drive band shows.
        total_bytes: Size of the filesystem.
        free_bytes: What is left on it. Free, not "used": a shelf that reports
            how much room is left answers the question the owner is asking
            before a 24 GB checkpoint lands.
    """

    device_id: str
    mount_point: str
    total_bytes: int
    free_bytes: int


def mount_point_of(path: str) -> str:
    """The mount point *path* sits under.

    Walks up until ``os.path.ismount`` says yes, which is the stdlib's own
    answer on both platforms: on POSIX it compares ``st_dev`` against the
    parent's, and on Windows it recognises drive roots and mount points. Stops
    at the filesystem root, so a path on a device we cannot stat still returns
    something printable rather than looping.

    Args:
        path: An absolute or relative filesystem path.

    Returns:
        The mount point as an absolute path.
    """
    current = os.path.abspath(path)
    while not os.path.ismount(current):
        parent = os.path.dirname(current)
        if parent == current:
            return current
        current = parent
    return current


def describe_storage_device(path: str) -> Optional[StorageDevice]:
    """Identify and measure the filesystem holding *path*.

    Both calls touch the filesystem, so an offline network mount can make this
    **block** rather than raise. That is why it is not on ``GET
    /model-folders``: the folder list answers from the hub alone and must stay
    that way, and a capacity meter is the one caller that can afford to be slow
    or absent.

    Args:
        path: The folder to measure.

    Returns:
        The device, or ``None`` if the path cannot be stat'd (gone, permission
        denied, a mount that is offline rather than merely slow).
    """
    try:
        device_id = os.stat(path).st_dev
        usage = shutil.disk_usage(path)
    except OSError as exc:
        logger.warning(
            "Cannot measure the filesystem under %r (%s); its capacity meter "
            "will report unavailable.",
            path,
            exc,
        )
        return None
    return StorageDevice(
        device_id=str(device_id),
        mount_point=mount_point_of(path),
        total_bytes=int(usage.total),
        free_bytes=int(usage.free),
    )


def default_max_vram_gb() -> float:
    """Return default VRAM budget in GB: min(6GB, 50% of available VRAM).

    Falls back to 6GB when VRAM cannot be detected.
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
        totals_mb = []
        for line in output.splitlines():
            value = line.strip()
            if not value:
                continue
            totals_mb.append(int(float(value)))
        total_mb = sum(totals_mb)
        if total_mb <= 0:
            return 6.0
        half_gb = (total_mb / 1024.0) / 2.0
        return round(min(6.0, half_gb), 2)
    except Exception:
        # nvidia-smi absent/failing is normal on CPU-only hosts; the documented
        # 6GB default IS the answer, so logging it would be routine noise.
        return 6.0

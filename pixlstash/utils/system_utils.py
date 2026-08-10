"""System-level utilities (hardware detection, etc.)."""

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Where udev keeps its label → device symlinks on Linux, and the kernel's own
# mount table. Constants so a test can point both at fixtures and exercise the
# matching without a real disk, a real label or root.
_BY_LABEL_DIR = "/dev/disk/by-label"
_MOUNTS_FILE = "/proc/mounts"

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
            ``D:\\``). Precise, and on Linux often long enough to crowd a band
            header, so it belongs in a tooltip rather than in the label.
        label: What the owner called the volume (``Models``, ``WinStorage``), or
            ``None`` when it has none. This is what a drive band shows.
        total_bytes: Size of the filesystem.
        free_bytes: What is left on it. Free, not "used": a shelf that reports
            how much room is left answers the question the owner is asking
            before a 24 GB checkpoint lands.
    """

    device_id: str
    mount_point: str
    label: Optional[str]
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


def _unescape_mount_field(field: str) -> str:
    """Decode the octal escapes ``/proc/mounts`` writes in a path.

    Three octal digits after a backslash, not two after a leading zero: the
    kernel escapes space as ``\\040`` and tab as ``\\011``, which a
    zero-prefixed pattern happens to cover, but it escapes a literal backslash
    as ``\\134``, which one does not. A mount point holding one would then
    fail to match its device and the drive would silently lose its label.
    """
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _linux_volume_label(mount_point: str) -> Optional[str]:
    """The volume label of the Linux filesystem mounted at *mount_point*.

    Two stdlib reads and no dependency: ``/proc/mounts`` gives device →
    mount point, and the ``/dev/disk/by-label`` symlinks udev maintains give
    label → device. Matching them is the whole trick. `lsblk` and `blkid` would
    each answer in one call and each is a subprocess that may not be installed.

    Returns:
        The label, or ``None`` if the filesystem has none (a root partition
        usually does not) or the tables cannot be read.
    """
    try:
        with open(_MOUNTS_FILE, encoding="utf-8") as handle:
            mounted = {
                _unescape_mount_field(parts[1]): os.path.realpath(parts[0])
                for line in handle
                if len(parts := line.split()) >= 2
            }
    except OSError as exc:
        logger.debug(
            "Cannot read %s (%s); drive bands lose their labels.", _MOUNTS_FILE, exc
        )
        return None

    device = mounted.get(mount_point)
    if device is None:
        return None
    try:
        entries = os.listdir(_BY_LABEL_DIR)
    except OSError:
        # No by-label directory at all is normal (a container, a system whose
        # filesystems carry no labels), not a fault worth a warning.
        return None
    for entry in entries:
        link = os.path.join(_BY_LABEL_DIR, entry)
        if os.path.realpath(link) == device:
            # udev escapes anything awkward as \xNN, spaces included.
            return re.sub(
                r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), entry
            )
    return None


def _windows_volume_label(mount_point: str) -> Optional[str]:
    """The volume label Windows reports for *mount_point*, or ``None``.

    ``GetVolumeInformationW`` through ctypes: the label is what Explorer shows
    beside the drive letter, and a drive letter alone is exactly the "precise
    and unhelpful" string this whole function exists to replace.
    """
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(261)
        root = mount_point if mount_point.endswith("\\") else mount_point + "\\"
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            buffer,
            ctypes.sizeof(buffer),
            None,
            None,
            None,
            None,
            0,
        )
    except (AttributeError, OSError, ValueError) as exc:
        logger.debug("GetVolumeInformationW failed for %r (%s).", mount_point, exc)
        return None
    # Parenthesised: `or` binds tighter than the conditional, so the bare
    # expression already returned None on failure — but it reads as though it
    # might not, and a reviewer should not have to check the grammar to see
    # that a failed call cannot leak a stale buffer.
    return (buffer.value or None) if ok else None


def volume_label(mount_point: str) -> Optional[str]:
    """What the owner called this volume, or ``None`` if it has no name.

    Platform-specific by necessity and best-effort by design: a band that cannot
    name its drive falls back to the mount point, which is never wrong, only
    long.
    """
    if sys.platform.startswith("linux"):
        return _linux_volume_label(mount_point)
    if sys.platform == "win32":
        return _windows_volume_label(mount_point)
    if sys.platform == "darwin":
        # macOS mounts everything but the boot volume under /Volumes/<name>,
        # so the last segment IS the label the user chose.
        parent, name = os.path.split(mount_point.rstrip("/"))
        return name if parent == "/Volumes" else None
    return None


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
    mount_point = mount_point_of(path)
    return StorageDevice(
        device_id=str(device_id),
        mount_point=mount_point,
        label=volume_label(mount_point),
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

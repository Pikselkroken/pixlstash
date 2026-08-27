"""System-level utilities (hardware detection, etc.)."""

import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Optional

from pixlstash.utils.vram_utils import query_total_vram_mb

logger = logging.getLogger(__name__)

# Where udev keeps its label → device symlinks on Linux, and the kernel's own
# mount table. Constants so a test can point both at fixtures and exercise the
# matching without a real disk, a real label or root.
_BY_LABEL_DIR = "/dev/disk/by-label"
_MOUNTS_FILE = "/proc/mounts"
_SYS_BLOCK_CLASS = "/sys/class/block"

# What kind of storage a drive is, in the only four flavours whose evidence
# cannot lie about itself. See `device_kind` for what is deliberately missing.
DEVICE_KIND_LOCAL = "local"
DEVICE_KIND_NETWORK = "network"
DEVICE_KIND_RAMDISK = "ramdisk"
DEVICE_KIND_REMOVABLE = "removable"
DEVICE_KINDS = (
    DEVICE_KIND_LOCAL,
    DEVICE_KIND_NETWORK,
    DEVICE_KIND_RAMDISK,
    DEVICE_KIND_REMOVABLE,
)

# Every one of these puts the bytes on another machine, which is the single
# fact about a drive that most changes what the owner will do with it. `fuse.`
# names carry the helper that mounted them, so sshfs and rclone are matched by
# their own full names rather than by a prefix that would also catch a local
# fuse filesystem.
_NETWORK_FSTYPES = frozenset(
    {
        "9p",
        "afs",
        "afpfs",
        "cifs",
        "fuse.rclone",
        "fuse.sshfs",
        "ncpfs",
        "nfs",
        "nfs4",
        "smb3",
        "smbfs",
        "sshfs",
    }
)

_RAM_FSTYPES = frozenset({"ramfs", "tmpfs"})

# What this machine calls the place a deleted file goes. Windows says "Recycle
# Bin"; macOS and every Linux desktop say "Trash". Used in the delete route's
# own description, so the API documents what it will actually do on the host
# it is running on.
TRASH_NAME = "Recycle Bin" if sys.platform == "win32" else "Trash"

# Upper bound for the VRAM budget setting when the card cannot be read (no
# nvidia-smi, a CPU-only host). With a card present the bound is the card: see
# `max_vram_budget_gb`. This used to be the bound for every card, which meant
# a 32 GB card was offered a 16 GB default it could never be set back to.
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
        kind: One of :data:`DEVICE_KINDS`, or ``None`` where the platform will
            not say. It is the connection, not the medium: it separates a
            network share and a memory disk and a stick from a disk in the
            machine, which is what changes how fast the bytes move and whether
            they survive a reboot. See :func:`device_kind`.
    """

    device_id: str
    mount_point: str
    label: Optional[str]
    total_bytes: int
    free_bytes: int
    kind: Optional[str] = None


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


def _linux_mounts() -> dict[str, tuple[str, str]]:
    """``/proc/mounts`` as mount point → (device node, filesystem type).

    One parse for the two questions the drive band asks - what this volume is
    called, and what kind of storage it is - because both answers are in the
    same three fields of the same line. Reading the file twice for them would
    be two syscalls to learn what one already said.

    Returns:
        The table, or an empty dict if it cannot be read. The callers all treat
        "not in the table" as "we do not know", which is what a missing file
        means anyway.
    """
    try:
        with open(_MOUNTS_FILE, encoding="utf-8") as handle:
            return {
                _unescape_mount_field(parts[1]): (
                    os.path.realpath(parts[0]),
                    parts[2],
                )
                for line in handle
                if len(parts := line.split()) >= 3
            }
    except OSError as exc:
        logger.debug(
            "Cannot read %s (%s); drive bands lose their labels and their kind.",
            _MOUNTS_FILE,
            exc,
        )
        return {}


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
    mounted = _linux_mounts().get(mount_point)
    if mounted is None:
        return None
    device = mounted[0]
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
    # expression already returned None on failure - but it reads as though it
    # might not, and a reviewer should not have to check the grammar to see
    # that a failed call cannot leak a stale buffer.
    return (buffer.value or None) if ok else None


def _linux_device_kind(mount_point: str) -> Optional[str]:
    """What kind of storage is mounted at *mount_point* on Linux.

    Off the table :func:`_linux_mounts` already built, plus at most one
    ``/sys`` read: the filesystem type answers network and RAM outright, and
    ``removable`` is a one-byte file udev maintains beside the block device.
    """
    entry = _linux_mounts().get(mount_point)
    if entry is None:
        return None
    device, fstype = entry
    # A network filesystem IS the connection, whatever is spinning at the far
    # end, and tmpfs IS memory. Neither is an inference.
    if fstype in _NETWORK_FSTYPES:
        return DEVICE_KIND_NETWORK
    if fstype in _RAM_FSTYPES:
        return DEVICE_KIND_RAMDISK
    if not device.startswith("/dev/"):
        # A fstype we do not recognise, backed by something that is not a block
        # device at all: fuse mounts of every description land here. Saying
        # `local` about them would be a guess.
        return None
    if _linux_is_removable(os.path.basename(device)):
        return DEVICE_KIND_REMOVABLE
    return DEVICE_KIND_LOCAL


def _linux_is_removable(device_name: str) -> bool:
    """Whether the block device backing *device_name* says it is removable.

    ``/sys/class/block/sdb1`` is a symlink into the parent disk's directory, so
    ``..`` from a partition lands on the disk that carries the flag; a whole
    device carries it directly. Both are plain one-byte reads.

    A ``False`` here is weaker than a ``True``: an SSD in a USB enclosure
    reports 0, so the band calls it a local disk. That is the direction to be
    wrong in - it makes no claim about speed rather than a false one.
    """
    candidates = (
        os.path.join(_SYS_BLOCK_CLASS, device_name, "removable"),
        os.path.join(_SYS_BLOCK_CLASS, device_name, "..", "removable"),
    )
    for candidate in candidates:
        try:
            with open(candidate, encoding="ascii") as handle:
                return handle.read().strip() == "1"
        except OSError as exc:
            # Expected on the first candidate for a partition, which is why the
            # miss is not itself worth a line. Both missing is a real gap, and
            # that is what the line after the loop says.
            last_error = exc
    logger.debug(
        "No `removable` flag for %r under %s (%s); its band will call the drive local.",
        device_name,
        _SYS_BLOCK_CLASS,
        last_error,
    )
    return False


def _windows_device_kind(mount_point: str) -> Optional[str]:
    """What kind of storage *mount_point* is on Windows.

    ``GetDriveTypeW`` beside the ``GetVolumeInformationW`` call above, and the
    same ctypes idiom: it answers the connection class only, which is exactly
    the part that does not lie. Windows can be asked whether a disk has a seek
    penalty, but only through two ``DeviceIoControl`` calls that report through
    a USB bridge or a Storage Space as though the enclosure were the disk, so
    this deliberately does not distinguish an SSD from a platter.
    """
    try:
        import ctypes

        root = mount_point if mount_point.endswith("\\") else mount_point + "\\"
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
    except (AttributeError, OSError, ValueError) as exc:
        logger.debug("GetDriveTypeW failed for %r (%s).", mount_point, exc)
        return None
    # 2 REMOVABLE, 3 FIXED, 4 REMOTE, 6 RAMDISK. 0 UNKNOWN, 1 NO_ROOT_DIR and
    # 5 CDROM all fall through to None: two of them are failures wearing a
    # number, and an optical drive is not somewhere models live.
    return {
        2: DEVICE_KIND_REMOVABLE,
        3: DEVICE_KIND_LOCAL,
        4: DEVICE_KIND_NETWORK,
        6: DEVICE_KIND_RAMDISK,
    }.get(drive_type)


def device_kind(mount_point: str) -> Optional[str]:
    """What kind of storage the filesystem at *mount_point* is, or ``None``.

    One of :data:`DEVICE_KINDS`, and **null is a normal answer**: macOS has
    neither ``/proc/mounts`` nor ``/sys`` and the stdlib will not name a
    filesystem type there, so every band on a Mac reports nothing and draws the
    plain disk glyph. The band must never print "Unknown" for this.

    What is deliberately NOT here is the SSD-versus-platter question the speed
    of a disk actually turns on. Linux will answer it -
    ``/sys/block/<dev>/queue/rotational`` is one more one-byte read - but it
    answers wrongly in exactly the setups where being wrong costs most: a VM's
    virtio disk reports rotational on an NVMe host, an LVM or LUKS mapper
    reports its own default rather than the disk underneath, and a SATA SSD in
    a USB enclosure reports "fast" for a 35 MB/s link. A drive band that
    mislabels a slow disk as fast is worse than one that says nothing, so the
    four kinds here are the ones whose evidence cannot lie.
    """
    if sys.platform.startswith("linux"):
        return _linux_device_kind(mount_point)
    if sys.platform == "win32":
        return _windows_device_kind(mount_point)
    return None


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
        kind=device_kind(mount_point),
    )


def default_max_vram_gb() -> float:
    """Return default VRAM budget in GB: max(4GB, 50% of total VRAM).

    Card-aware so a large card is not starved: 16GB on a 32GB card, 6GB on
    12GB, 4GB on 8GB. Falls back to 6GB when VRAM cannot be detected.
    """
    total_gb = query_total_vram_mb() / 1024.0
    if total_gb <= 0:
        return 6.0
    return round(max(4.0, total_gb / 2.0), 2)


def max_vram_budget_gb() -> float:
    """Return the largest budget a user may set: the card itself.

    The TaskRunner already clamps a budget to the installed total, so the
    validator refusing less than that only ever refused a value the runtime
    would have honoured. Falls back to `MAX_VRAM_BUDGET_GB` without a card.
    """
    total_gb = query_total_vram_mb() / 1024.0
    if total_gb <= 0:
        return MAX_VRAM_BUDGET_GB
    return round(total_gb, 2)


# The same 10 % as ``model_mover._SPACE_HEADROOM``, and deliberately the same
# number: both answer "will this large copy fit", and two different margins for
# one question is how they drift apart.
SPACE_HEADROOM = 1.1


def space_shortfall(path: str, needed_bytes: int) -> Optional[tuple[int, int]]:
    """Return ``(required, free)`` when *path* lacks room, or ``None`` when it fits.

    A sanity check, not a guarantee: free space can change under us, and a
    caller working from an estimate is only ever approximately right. It exists
    to catch the case worth catching - a 200 GB library aimed at a disk with
    2 GB left - before an hour of copying ends in ENOSPC half-written.

    An unreadable path is reported as a shortfall of ``(needed, 0)`` rather than
    passed silently: not being able to measure is a reason to ask, given the
    alternative is discovering it at the end.

    Args:
        path: A folder on the filesystem that will receive the bytes.
        needed_bytes: The estimate, before headroom.

    Returns:
        ``None`` when there is room, else the required and free byte counts.
    """
    if needed_bytes <= 0:
        return None
    required = int(needed_bytes * SPACE_HEADROOM)
    try:
        free = shutil.disk_usage(path).free
    except OSError as exc:
        logger.warning(
            "Could not read free space on %s (%s); reporting it as a shortfall "
            "so the caller asks rather than assuming it fits.",
            path,
            exc,
        )
        return (required, 0)
    return None if free >= required else (required, int(free))

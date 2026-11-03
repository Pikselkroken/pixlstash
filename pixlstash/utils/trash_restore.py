"""Take a file PixlStash sent to the system trash back out of it.

``send2trash`` only goes one way. Undoing a delete that went to the trash needs
the other half: find the entry that came from a given path and move it back
there. The two trashes that record where an entry came from are read here:

* **freedesktop** (Linux and the BSDs): ``<trash>/info/<name>.trashinfo`` holds
  the original ``Path``, ``<trash>/files/<name>`` the entry itself. The home
  trash and the trashes at the top of the original's volume are searched, which
  is everywhere ``send2trash`` (and GIO, when it is the one installed) puts it.
* **Windows**: ``<drive>\\$Recycle.Bin\\<SID>\\$I<id>`` holds the original path,
  ``$R<id>`` beside it the entry.

The macOS Trash keeps where an item came from in Finder's private ``.DS_Store``,
so there it answers :class:`NotInTrash` and the owner uses Finder's Put Back.

When the same path was trashed more than once, the newest entry is the one put
back: that is the one the delete being undone made.
"""

from __future__ import annotations

import errno
import ntpath
import os
import shutil
import struct
import sys
from urllib.parse import unquote_to_bytes

from pixlstash.pixl_logging import get_logger

if sys.platform not in ("win32", "darwin"):
    # Where send2trash's own freedesktop code puts things; it reads os.getuid at
    # import, so it cannot be imported on Windows.
    from send2trash import plat_other

logger = get_logger(__name__)


class NotInTrash(LookupError):
    """The trash holds nothing that came from this path, or cannot be read."""


def restore_from_trash(original: str) -> None:
    """Move the newest trash entry that came from *original* back there.

    Args:
        original: The absolute path the entry was trashed from.

    Raises:
        NotInTrash: Nothing in the trash came from *original* (it was emptied,
            or put back already), or this platform's trash cannot be read.
        FileExistsError: Something is at *original* again; it is never
            replaced.
        OSError: The move itself failed.
    """
    if os.path.lexists(original):
        raise FileExistsError(errno.EEXIST, "Something is already there", original)
    if sys.platform == "win32":
        data, info = _find_windows_entry(original, _windows_recycle_dirs(original))
    elif sys.platform == "darwin":
        raise NotInTrash(
            f"The macOS Trash does not say where {original} came from; "
            "use Put Back in Finder."
        )
    else:
        data, info = _find_freedesktop_entry(original, _freedesktop_trashes(original))
    os.makedirs(os.path.dirname(original), exist_ok=True)
    try:
        os.rename(data, original)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # send2trash falls back to the home trash across devices.
        shutil.move(data, original)
    try:
        os.remove(info)
    except OSError as exc:
        logger.warning(
            "Put %s back from the trash but could not remove its record %s: %s. "
            "The trash may list an entry that is no longer there.",
            original,
            info,
            exc,
        )
    logger.info("Put %s back from the trash.", original)


def _same_place(path: bytes) -> bytes:
    """*path* with its directory resolved, so a symlinked parent compares equal."""
    head, tail = os.path.split(os.path.normpath(path))
    return os.path.join(os.path.realpath(head), tail)


def _freedesktop_trashes(original: str) -> list[tuple[bytes, bytes]]:
    """``(trash_dir, topdir)`` for every trash *original* could have gone to."""
    trashes = [(plat_other.HOMETRASH_B, plat_other.XDG_DATA_HOME)]
    try:
        mount = plat_other.find_mount_point(os.fsencode(os.path.dirname(original)))
    except OSError as exc:
        logger.warning("Could not find the volume %s is on: %s", original, exc)
        return trashes
    uid = str(os.getuid()).encode("ascii")
    trashes.append((os.path.join(mount, plat_other.TOPDIR_TRASH, uid), mount))
    trashes.append((os.path.join(mount, plat_other.TOPDIR_FALLBACK), mount))
    return trashes


def _find_freedesktop_entry(
    original: str, trashes: list[tuple[bytes, bytes]]
) -> tuple[bytes, bytes]:
    """``(entry, trashinfo)`` of the newest entry trashed from *original*."""
    want = _same_place(os.fsencode(os.path.abspath(original)))
    best = None
    for trash_dir, topdir in trashes:
        info_dir = os.path.join(trash_dir, b"info")
        try:
            names = os.listdir(info_dir)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not read the trash at %s: %s", info_dir, exc)
            continue
        for name in names:
            if not name.endswith(b".trashinfo"):
                continue
            info = os.path.join(info_dir, name)
            fields = _read_trashinfo(info)
            if fields is None or "Path" not in fields:
                continue
            path = unquote_to_bytes(fields["Path"])
            if not os.path.isabs(path):
                path = os.path.join(topdir, path)
            if _same_place(path) != want:
                continue
            data = os.path.join(trash_dir, b"files", name[: -len(b".trashinfo")])
            if not os.path.lexists(data):
                continue
            key = (fields.get("DeletionDate", ""), os.stat(info).st_mtime_ns)
            if best is None or key > best[0]:
                best = (key, data, info)
    if best is None:
        raise NotInTrash(f"Nothing in the trash came from {original}.")
    return best[1], best[2]


def _read_trashinfo(path: bytes) -> dict[str, str] | None:
    """The ``key=value`` lines of one ``.trashinfo``, or None if it will not read."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Skipping unreadable trash record %r: %s", path, exc)
        return None
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _windows_recycle_dirs(original: str) -> list[str]:
    """This user's Recycle Bin folders on *original*'s drive (others' deny us)."""
    root = os.path.join(ntpath.splitdrive(original)[0] + "\\", "$Recycle.Bin")
    try:
        return [entry.path for entry in os.scandir(root) if entry.is_dir()]
    except OSError as exc:
        logger.warning("Could not read the Recycle Bin at %s: %s", root, exc)
        return []


def _find_windows_entry(original: str, recycle_dirs: list[str]) -> tuple[str, str]:
    """``($R entry, $I record)`` of the newest entry deleted from *original*."""
    want = ntpath.normcase(ntpath.normpath(original))
    best = None
    for folder in recycle_dirs:
        try:
            names = os.listdir(folder)
        except OSError:
            # Another account's bin: listed by scandir, closed to us.
            continue
        for name in names:
            if not name.startswith("$I"):
                continue
            info = os.path.join(folder, name)
            record = _read_windows_record(info)
            if record is None or ntpath.normcase(ntpath.normpath(record[1])) != want:
                continue
            data = os.path.join(folder, "$R" + name[2:])
            if not os.path.lexists(data):
                continue
            if best is None or record[0] > best[0]:
                best = (record[0], data, info)
    if best is None:
        raise NotInTrash(f"Nothing in the Recycle Bin came from {original}.")
    return best[1], best[2]


def _read_windows_record(path: str) -> tuple[int, str] | None:
    """``(deleted_at FILETIME, original path)`` from one ``$I`` file.

    Version 1 (Vista to 8.1) stores the path in a fixed 520-byte field; version
    2 (Windows 10 on) prefixes it with its length in characters.
    """
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
        version, _size, deleted_at = struct.unpack_from("<qqq", blob, 0)
        if version == 1:
            raw = blob[24 : 24 + 520]
        elif version == 2:
            (chars,) = struct.unpack_from("<i", blob, 24)
            raw = blob[28 : 28 + 2 * chars]
        else:
            logger.warning(
                "Skipping Recycle Bin record %s of version %d.", path, version
            )
            return None
        return deleted_at, raw.decode("utf-16-le").split("\x00", 1)[0]
    except (OSError, struct.error, UnicodeDecodeError) as exc:
        logger.warning("Skipping unreadable Recycle Bin record %s: %s", path, exc)
        return None

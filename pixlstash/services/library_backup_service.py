"""Write a library, and the hub, to a single archive.

**No locking, even while the library is open.** The databases are copied with
``VACUUM INTO``, the same mechanism :class:`~pixlstash.services.snapshot_service.SnapshotService`
already uses against the live vault: it takes a read transaction and, under WAL,
produces a consistent point-in-time copy without blocking writers. So a backup of
the active library needs no quiesce and no coordination with the server.

**Databases first, then image files.** The archived catalogue then references
only images that already existed on disk, all of which the file pass picks up.
An image imported during the copy is simply an extra file the catalogue does not
mention. The one residual gap is a picture purged between the two passes, which
leaves a reference to a file that is not in the archive; that window is small and
is stated rather than engineered away.

**The archive contains the hub**, and therefore the password hash and every token
hash. It is written owner-readable only and the CLI says so. That is a different
thing from putting credentials *inside a library folder*, which stays forbidden:
this is a deliberate artifact the owner creates and stores, not something that
travels with a library that gets copied or shared.

**Open-core boundary (CEO ruling 2026-08-01).** This is one-shot, to a local
path, a full archive, run by hand. It must never grow a scheduler, a remote
destination, incremental sync, encryption at rest, or restore verification:
those five are the commercial vault-protection tier
(``pixlstash-dam-roadmap.md`` §4.4). Pointing the output at a mounted NAS is the
user's filesystem's business; a ``--s3`` or ``--daily`` flag is not.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import zstandard

from pixlstash.hub.registry import VAULT_FILENAME, Library
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Only the owner may read a backup: it carries the hub.
BACKUP_FILE_MODE = 0o600

# Files that belong to the live database rather than to the library's contents.
# The archived copies are made with VACUUM INTO, so the originals (and their WAL
# sidecars, which are meaningless without the exact file they belong to) are
# skipped.
_DATABASE_FILES = frozenset(
    {
        VAULT_FILENAME,
        f"{VAULT_FILENAME}-wal",
        f"{VAULT_FILENAME}-shm",
        f"{VAULT_FILENAME}-journal",
    }
)


@dataclass
class BackupResult:
    """What a completed backup wrote, for the CLI to report."""

    path: str
    byte_size: int
    picture_count: int
    file_count: int
    metadata_only: bool
    reference_folders: list[str] = field(default_factory=list)

    @property
    def has_external_folders(self) -> bool:
        """Whether the library points at folders the archive does not contain."""
        return bool(self.reference_folders)


class BackupError(RuntimeError):
    """The backup could not be written, with a message for the terminal."""


def _vacuum_into(source_path: str, destination_path: str) -> None:
    """Write a consistent copy of a live SQLite database.

    Read-only on the source and safe against concurrent writers, which is what
    lets this run against a library the server has open.
    """
    try:
        conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise BackupError(f"Could not read {source_path}: {exc}") from exc
    try:
        conn.execute("VACUUM INTO ?", (destination_path,))
    except sqlite3.Error as exc:
        raise BackupError(f"Could not copy {source_path}: {exc}") from exc
    finally:
        conn.close()


def _read_scalar(db_path: str, sql: str, default=None):
    """Run a one-value read against a database, returning *default* on failure."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return default
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row else default
    except sqlite3.Error:
        return default
    finally:
        conn.close()


def _reference_folders(vault_path: str) -> list[str]:
    """Return the external folders this library points at.

    These live outside the library by definition, so they are **not** in the
    archive. Users assume otherwise unless told, so the caller names them.
    """
    try:
        conn = sqlite3.connect(f"file:{vault_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute("SELECT folder FROM referencefolder").fetchall()
        return [row[0] for row in rows if row[0]]
    except sqlite3.Error:
        # The table's name or absence is not worth failing a backup over.
        return []
    finally:
        conn.close()


def _library_files(library_root: str) -> list[tuple[str, str]]:
    """Return ``(absolute, archive-relative)`` for every non-database file."""
    collected = []
    for directory, _subdirs, filenames in os.walk(library_root):
        for filename in filenames:
            if os.path.relpath(directory, library_root) == "." and (
                filename in _DATABASE_FILES
            ):
                continue
            absolute = os.path.join(directory, filename)
            relative = os.path.relpath(absolute, library_root)
            collected.append((absolute, os.path.join("images", relative)))
    return collected


def create_backup(
    library: Library,
    destination: str,
    hub_path: str,
    *,
    metadata_only: bool = False,
    compress: bool = True,
    tool_version: str = "unknown",
) -> BackupResult:
    """Write *library* and the hub to a ``.tar.zst`` (or ``.tar``) at *destination*.

    Args:
        library: The library to archive. May be the active one.
        destination: Output file, or a directory to write a dated name into.
        hub_path: The hub database, included so credentials and the registry are
            recoverable alongside the pictures.
        metadata_only: Skip the image files. Fast, and honest about what it is:
            a catalogue is worth nothing if the pictures are gone, so this is the
            case the user has to ask for.
        compress: zstd the archive. Worth it for the databases, close to wasted
            on JPEG and PNG, so a large image set may prefer it off.
        tool_version: Recorded in the manifest.

    Returns:
        A :class:`BackupResult` describing what was written.

    Raises:
        BackupError: The library is unreadable, or the archive could not be
            written.
    """
    vault_path = os.path.join(library.path, VAULT_FILENAME)
    if not os.path.isfile(vault_path):
        raise BackupError(
            f'"{library.name}" is not readable at {library.path}. Reconnect the '
            "drive, or point the library at its new location, then try again."
        )

    destination = _resolve_destination(library, destination, compress)
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)

    picture_count = _read_scalar(vault_path, "SELECT COUNT(*) FROM picture", 0) or 0
    revision = _read_scalar(vault_path, "SELECT version_num FROM alembic_version")
    references = _reference_folders(vault_path)

    manifest = {
        "library_uuid": library.uuid,
        "library_name": library.name,
        "source_path": library.path,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pixlstash_version": tool_version,
        "vault_revision": revision,
        "picture_count": picture_count,
        "metadata_only": metadata_only,
        "reference_folders": references,
        "contains_hub": True,
    }

    scratch = tempfile.mkdtemp(prefix="pixlstash_backup_")
    file_count = 0
    try:
        # Databases first: the archived catalogue then names only images that
        # already exist, and the file pass below collects them.
        vault_copy = os.path.join(scratch, VAULT_FILENAME)
        _vacuum_into(vault_path, vault_copy)

        hub_copy = os.path.join(scratch, "hub.db")
        if os.path.isfile(hub_path):
            _vacuum_into(hub_path, hub_copy)
        else:
            hub_copy = None
            manifest["contains_hub"] = False

        manifest_copy = os.path.join(scratch, "manifest.json")
        with open(manifest_copy, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)

        payload = [(manifest_copy, "manifest.json"), (vault_copy, VAULT_FILENAME)]
        if hub_copy:
            payload.append((hub_copy, "hub.db"))
        if not metadata_only:
            payload.extend(_library_files(library.path))
        file_count = len(payload)

        _write_archive(payload, destination, compress)
    finally:
        _remove_tree(scratch)

    os.chmod(destination, BACKUP_FILE_MODE)
    byte_size = os.path.getsize(destination)
    logger.info(
        "Backed up library %s (%s) to %s (%d bytes, %d file(s))",
        library.name,
        library.uuid,
        destination,
        byte_size,
        file_count,
    )
    return BackupResult(
        path=destination,
        byte_size=byte_size,
        picture_count=picture_count,
        file_count=file_count,
        metadata_only=metadata_only,
        reference_folders=references,
    )


def _resolve_destination(library: Library, destination: str, compress: bool) -> str:
    """Expand a directory destination into a dated filename."""
    suffix = ".tar.zst" if compress else ".tar"
    expanded = os.path.abspath(os.path.expanduser(destination))
    if os.path.isdir(expanded):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(
            char if char.isalnum() or char in "-_" else "-" for char in library.name
        )
        return os.path.join(expanded, f"{safe_name}-{stamp}{suffix}")
    return expanded


def _open_private(destination: str):
    """Open *destination* for writing, owner-readable from the first byte.

    Not ``open()`` followed by ``chmod``: that leaves a window in which the file
    exists under the process umask (commonly 0644) while the hub's password and
    token hashes are being written into it, and any local user could open it in
    that window and keep the handle after the mode is tightened. Creating with
    the right mode closes the window rather than narrowing it (CWE-732).
    """
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, BACKUP_FILE_MODE)
    return os.fdopen(fd, "wb")


def _write_archive(
    payload: list[tuple[str, str]], destination: str, compress: bool
) -> None:
    """Stream *payload* into a tar, optionally through zstd."""
    try:
        if not compress:
            with _open_private(destination) as raw:
                with tarfile.open(fileobj=raw, mode="w") as tar:
                    for absolute, arcname in payload:
                        tar.add(absolute, arcname=arcname)
            return

        compressor = zstandard.ZstdCompressor(level=3)
        with _open_private(destination) as raw:
            with compressor.stream_writer(raw) as stream:
                with tarfile.open(mode="w|", fileobj=stream) as tar:
                    for absolute, arcname in payload:
                        tar.add(absolute, arcname=arcname)
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"Could not write {destination}: {exc}") from exc


def _remove_tree(path: Optional[str]) -> None:
    """Delete a scratch directory, logging rather than raising on failure."""
    if not path:
        return
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError as exc:
        logger.warning(
            "Could not clean up the backup scratch directory %s: %s", path, exc
        )

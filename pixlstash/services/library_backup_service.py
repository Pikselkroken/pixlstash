"""Write a library, and the hub, to a single archive.

**No locking, even while the library is open.** The databases are copied with
``VACUUM INTO``, the same mechanism :class:`~pixlstash.services.snapshot_service.SnapshotService`
already uses against the live vault: it takes a read transaction and, under WAL,
produces a consistent point-in-time copy without blocking writers. So a backup of
the active library needs no quiesce and no coordination with the server.

**Databases first, then image files.** Every internal picture referenced by the
copied catalogue must still be a regular file when the payload is assembled and
again when it is archived. A concurrent purge therefore fails the backup instead
of publishing an incomplete one. New, unreferenced files may harmlessly appear in
the archive. Symlinks and other non-regular payloads are refused.

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
import sys
import stat
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import zstandard
from tqdm import tqdm

from pixlstash.hub.registry import VAULT_FILENAME, Library
from pixlstash.utils.media_files import is_supported_media_file
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.system_utils import space_shortfall
from pixlstash.hub.bootstrap import known_vault_revisions, newer_library_message
from pixlstash.services.portable_identity import (
    PortableIdentityScrubError,
    sanitize_historical_snapshots,
)
from pixlstash.trusted_sqlite import (
    TrustedSQLiteLocation,
    TrustedSQLiteLocationError,
)

logger = get_logger(__name__)


def progress_disabled() -> bool:
    """Whether to suppress progress bars, decided here rather than by tqdm.

    ``disable=None`` looks like it means "auto-detect a terminal", but tqdm
    wraps ``file`` in ``DisableOnWriteError`` *before* testing ``isatty``, so
    what it actually does depends on that wrapper forwarding the call. Deciding
    it from ``sys.stderr`` directly is one line, says what it means, and is
    testable - which matters because the failure mode is silent either way: a
    bar that never draws, or a cron log full of redrawn ones.
    """
    return not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty())


# Only the owner may read a backup: it carries the hub.
BACKUP_FILE_MODE = 0o600

# What a backup is called. zstd rather than gzip: it decompresses several times
# faster at a better ratio, which is what a restore of a large library feels.
# `restore` identifies an archive by its magic bytes rather than its name, so a
# renamed file still works - the suffix is for the person reading the folder.
ARCHIVE_SUFFIX = ".tar.zst"
UNCOMPRESSED_SUFFIX = ".tar"

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
    #: Picture files under the library root that no catalogue row names.
    orphan_count: int = 0
    #: Whether those files went into the archive (see ``create_backup``).
    orphans_included: bool = True

    @property
    def has_external_folders(self) -> bool:
        """Whether the library points at folders the archive does not contain."""
        return bool(self.reference_folders)


# Asked only when the destination looks too small. The service never reads
# stdin itself: the CLI owns prompting, so a scripted caller passes None and
# gets a refusal instead of a hang.
ConfirmCallback = Callable[[str], bool]


class BackupError(RuntimeError):
    """The backup could not be written, with a message for the terminal."""


def _open_guarded_source(
    path: str, *, label: str, private: bool
) -> tuple[sqlite3.Connection, TrustedSQLiteLocation]:
    try:
        # `private=True` means the hub. Its trusted_root is the hub path's own
        # parent - tautological containment that exists only so a private open
        # cannot forget to declare it (see trusted_sqlite's "Windows, and what
        # this cannot check").
        guard = TrustedSQLiteLocation.open(
            path,
            private=private,
            trusted_root=os.path.dirname(path) if private else None,
        )
    except TrustedSQLiteLocationError as exc:
        raise BackupError(f"Could not securely open {label} {path}: {exc}") from exc
    try:
        conn = sqlite3.connect(f"file:{guard.path}?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        guard.verify_after_open()
    except Exception as exc:
        guard.close()
        raise BackupError(f"Could not read {label} {path}: {exc}") from exc
    return conn, guard


def _validate_connection(
    conn: sqlite3.Connection,
    *,
    label: str,
    required_tables: set[str],
) -> None:
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"{label} is unreadable: {exc}") from exc
    if not required_tables <= tables or not integrity or integrity[0] != "ok":
        raise BackupError(f"{label} is not a valid PixlStash database.")


def _validate_vault_connection(
    conn: sqlite3.Connection, library: Library, *, label: str
) -> None:
    _validate_connection(
        conn,
        label=label,
        required_tables={"picture", "alembic_version"},
    )
    rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    known = known_vault_revisions()
    unknown = [row[0] for row in rows if row[0] and row[0] not in known]
    if unknown:
        raise BackupError(newer_library_message(unknown, library=label))
    if library.vault_uuid is not None:
        try:
            row = conn.execute(
                "SELECT library_uuid FROM library_settings LIMIT 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise BackupError(f"Could not validate {label} fingerprint: {exc}") from exc
        observed = row[0] if row else None
        if observed != library.vault_uuid:
            raise BackupError(
                f"{label} fingerprint {observed!r} does not match the registered "
                f"library {library.vault_uuid!r}."
            )


def _open_validated_hub_source(
    hub_path: str,
) -> tuple[sqlite3.Connection, TrustedSQLiteLocation]:
    """Open and validate the exact credential-hub inode to be archived."""
    conn, guard = _open_guarded_source(hub_path, label="hub database", private=True)
    try:
        _validate_connection(
            conn,
            label=f"Hub database {hub_path}",
            required_tables={"user", "usertoken", "library"},
        )
    except Exception:
        conn.close()
        guard.close()
        raise
    return conn, guard


def _vacuum_connection_into(
    source: sqlite3.Connection, destination_path: str, source_label: str
) -> None:
    """Copy the already validated SQLite source without reopening its path."""
    try:
        source.execute("VACUUM INTO ?", (destination_path,))
    except sqlite3.Error as exc:
        raise BackupError(f"Could not copy {source_label}: {exc}") from exc


def _read_scalar(connection: sqlite3.Connection, sql: str, default=None):
    """Run a one-value read against a database, returning *default* on failure."""
    try:
        row = connection.execute(sql).fetchone()
        return row[0] if row else default
    except sqlite3.Error:
        return default


def _reference_folders(connection: sqlite3.Connection) -> list[str]:
    """Return the external folders this library points at.

    These live outside the library by definition, so they are **not** in the
    archive. Users assume otherwise unless told, so the caller names them.
    """
    try:
        rows = connection.execute("SELECT folder FROM referencefolder").fetchall()
        return [row[0] for row in rows if row[0]]
    except sqlite3.Error:
        # The table's name or absence is not worth failing a backup over.
        return []


def _library_files(library_root: str) -> list[tuple[str, str]]:
    """Return every regular non-database file, refusing ambiguous payloads."""
    collected: list[tuple[str, str]] = []
    for directory, subdirs, filenames in os.walk(library_root, followlinks=False):
        relative_directory = os.path.relpath(directory, library_root)
        parts = relative_directory.split(os.sep)
        if (
            len(parts) >= 2
            and parts[0] == "snapshots"
            and (
                parts[1] == ".tmp"
                or any(part.startswith(".pixlstash_identity_scrub_") for part in parts)
            )
        ):
            subdirs[:] = []
            continue
        for dirname in subdirs:
            _validate_regular_directory(
                os.path.join(directory, dirname), label="library payload directory"
            )
        for filename in filenames:
            if os.path.relpath(directory, library_root) == "." and (
                filename in _DATABASE_FILES
            ):
                continue
            absolute = os.path.join(directory, filename)
            _validate_regular_file(absolute, label="library payload")
            relative = os.path.relpath(absolute, library_root)
            collected.append((absolute, os.path.join("images", relative)))
    return collected


def _validate_regular_directory(path: str, *, label: str) -> None:
    """Refuse symlinked or non-directory path components in an archive walk."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BackupError(f"Could not inspect {label} {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise BackupError(f"Refusing symlinked {label} {path}.")
    if not stat.S_ISDIR(info.st_mode):
        raise BackupError(f"Refusing non-directory {label} {path}.")


def _validate_regular_file(path: str, *, label: str) -> os.stat_result:
    """Return an lstat for a regular file or fail without following symlinks."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BackupError(f"Could not read {label} {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise BackupError(f"Refusing symlinked {label} {path}.")
    if not stat.S_ISREG(info.st_mode):
        raise BackupError(f"Refusing non-regular {label} {path}.")
    return info


def _required_internal_picture_files(
    connection: sqlite3.Connection, library_root: str
) -> set[str]:
    """Resolve internal picture references from the exact copied catalogue."""
    root = os.path.normcase(os.path.abspath(library_root))
    required: set[str] = set()
    try:
        rows = connection.execute(
            "SELECT file_path FROM picture WHERE file_path IS NOT NULL"
        ).fetchall()
    except sqlite3.Error as exc:
        raise BackupError(f"Could not read copied picture paths: {exc}") from exc
    for row in rows:
        stored = row[0]
        if not stored:
            continue
        resolved = stored if os.path.isabs(stored) else os.path.join(root, stored)
        resolved = os.path.normcase(os.path.abspath(resolved))
        try:
            internal = os.path.commonpath((root, resolved)) == root
        except ValueError:
            internal = False
        if internal:
            required.add(resolved)
    return required


def _orphaned_media(
    library_root: str, payload: list[tuple[str, str]], required: set[str]
) -> set[str]:
    """Picture files in *payload* that no catalogue row references.

    The reverse of :func:`_verify_required_files`: that proves every catalogued
    picture is in the archive, this counts the pictures in the archive that the
    catalogue never heard of - copies left by a library split, files whose row
    was deleted while the file stayed. Thumbnails, PixlStash's own folders and
    non-media files are not pictures and are not counted.
    """
    orphans: set[str] = set()
    for absolute, _arcname in payload:
        relative = os.path.relpath(absolute, library_root)
        top = relative.split(os.sep, 1)[0]
        if top.startswith(".") or top in ("snapshots", "tmp"):
            continue
        if not is_supported_media_file(absolute):
            continue
        if os.path.normcase(os.path.abspath(absolute)) in required:
            continue
        orphans.add(absolute)
    return orphans


def _verify_required_files(required: set[str], payload: list[tuple[str, str]]) -> None:
    """Prove that every copied internal reference is present in the payload."""
    included = {os.path.normcase(os.path.abspath(path)) for path, _ in payload}
    missing = sorted(required - included)
    if missing:
        example = missing[0]
        raise BackupError(
            "The copied catalogue references an internal picture that is missing "
            f"from the backup payload: {example}. Repair or purge the missing "
            "record, then retry. No archive was published."
        )


def _scrub_outstanding_snapshot_identity(library: Library, vault_path: str) -> None:
    """Finish any legacy snapshot scrub before the archives can be packaged.

    ``_library_files`` collects ``snapshots/**`` verbatim, and ``_DATABASE_FILES``
    excludes only the root-level vault. So a backup taken before the background
    scrub has drained would package pre-hub ``user`` / ``usertoken`` rows -- a
    password hash and live token hashes -- into a portable artifact, which is
    precisely the leak the scrub exists to prevent. The restore-path scrub does
    not help here: nothing materializes these archives, they are copied as bytes.

    Doing the work rather than refusing follows the convention this codebase
    already uses for permissions (``hub.db.check_file_mode``): the CLI repairs,
    the server reports. A backup is an operator-initiated, non-interactive
    command where waiting is acceptable; being unable to back up until an
    unrelated background pass finishes is not.

    Args:
        library: The library being archived.
        vault_path: Its vault database.

    Raises:
        BackupError: An archive could not be scrubbed, so the backup must not
            proceed: the alternative is writing the credentials into the tarball.
    """
    connection = sqlite3.connect(vault_path)
    try:
        outstanding = connection.execute(
            "SELECT COUNT(*) FROM snapshot WHERE identity_scrubbed_at IS NULL"
        ).fetchone()[0]
    except sqlite3.Error:
        # No snapshot table, or no identity_scrubbed_at column: a vault from
        # before 0102, which the migration has not reached yet. Nothing to
        # reason about here, and the caller's own validation covers the rest.
        connection.close()
        return
    if not outstanding:
        connection.close()
        return

    logger.info(
        "Backup: %d legacy snapshot archive(s) in %s still carry portable "
        "identity. Scrubbing them before packaging; this is one-time work that "
        "the background pass would otherwise have done.",
        outstanding,
        library.name,
    )
    try:
        sanitize_historical_snapshots(connection, library.path)
    except PortableIdentityScrubError as exc:
        raise BackupError(
            f'Could not remove stale owner credentials from "{library.name}" '
            f"snapshot archives, so the backup was not written: {exc}"
        ) from exc
    finally:
        connection.close()


def create_backup(
    library: Library,
    destination: str,
    hub_path: str,
    *,
    metadata_only: bool = False,
    compress: bool = True,
    tool_version: str = "unknown",
    confirm: Optional[ConfirmCallback] = None,
    include_orphans: Optional[bool] = None,
    ask_orphans: Optional[ConfirmCallback] = None,
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
        confirm: Asked, with a message, when the destination looks too small to
            hold the archive. ``None`` declines - a scripted caller gets a
            refusal rather than a prompt it cannot answer.
        include_orphans: Whether picture files no catalogue row names go into
            the archive. ``None`` asks *ask_orphans* when there are any.
        ask_orphans: Asked, with a message naming the count, when
            *include_orphans* is ``None`` and the library holds such files.
            ``None`` includes them: a scripted backup must not quietly leave
            files out, and nothing is lost by carrying them.

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

    vault_source, vault_guard = _open_guarded_source(
        vault_path, label="vault database", private=False
    )
    try:
        _validate_vault_connection(
            vault_source, library, label=f"Vault database {vault_path}"
        )
        # Strictly after the guard. Scrubbing opens its own writable connection
        # to the vault, and opening it any earlier would let SQLite consume a
        # pre-positioned -wal sidecar that _open_guarded_source exists to
        # refuse. Skipped for metadata_only, which never packages snapshots/**.
        if not metadata_only:
            _scrub_outstanding_snapshot_identity(library, vault_path)
    except Exception:
        vault_source.close()
        vault_guard.close()
        raise
    hub_source, hub_guard = _open_validated_hub_source(hub_path)
    scratch = tempfile.mkdtemp(prefix="pixlstash_backup_")
    file_count = 0
    byte_size = 0
    picture_count = 0
    references: list[str] = []
    required_files: set[str] = set()
    orphan_count = 0
    orphans_included = True
    try:
        # Databases first: the archived catalogue then names only images that
        # already exist, and the file pass below collects them.
        vault_copy = os.path.join(scratch, VAULT_FILENAME)
        _vacuum_connection_into(vault_source, vault_copy, vault_path)

        hub_copy = os.path.join(scratch, "hub.db")
        _vacuum_connection_into(hub_source, hub_copy, hub_path)

        # The manifest is derived from the private copies that will actually be
        # archived, and both copies are independently validated before packing.
        vault_copy_conn = sqlite3.connect(vault_copy)
        vault_copy_conn.row_factory = sqlite3.Row
        try:
            _validate_vault_connection(
                vault_copy_conn, library, label="Copied vault database"
            )
            picture_count = (
                _read_scalar(vault_copy_conn, "SELECT COUNT(*) FROM picture", 0) or 0
            )
            revision = _read_scalar(
                vault_copy_conn, "SELECT version_num FROM alembic_version"
            )
            references = _reference_folders(vault_copy_conn)
            if not metadata_only:
                required_files = _required_internal_picture_files(
                    vault_copy_conn, library.path
                )
        finally:
            vault_copy_conn.close()

        hub_copy_conn = sqlite3.connect(hub_copy)
        try:
            _validate_connection(
                hub_copy_conn,
                label="Copied hub database",
                required_tables={"user", "usertoken", "library"},
            )
        finally:
            hub_copy_conn.close()

        # The file pass comes before the manifest so the manifest can say what
        # was decided about orphans.
        library_payload: list[tuple[str, str]] = []
        if not metadata_only:
            library_payload = _library_files(library.path)
            _verify_required_files(required_files, library_payload)
            orphans = _orphaned_media(library.path, library_payload, required_files)
            orphan_count = len(orphans)
            if orphans:
                if include_orphans is None:
                    include_orphans = (
                        ask_orphans(
                            f"{orphan_count} picture file(s) in the library folder "
                            "are not in the catalogue (no PixlStash record). "
                            "Include them in the backup?"
                        )
                        if ask_orphans is not None
                        else True
                    )
                orphans_included = bool(include_orphans)
                if not orphans_included:
                    library_payload = [
                        entry for entry in library_payload if entry[0] not in orphans
                    ]
                    logger.info(
                        "Backup of %s leaves out %d orphaned picture file(s) at the "
                        "owner's request.",
                        library.name,
                        orphan_count,
                    )

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
            "orphan_count": orphan_count,
            "orphans_included": orphans_included,
        }

        manifest_copy = os.path.join(scratch, "manifest.json")
        with open(manifest_copy, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)

        payload = [(manifest_copy, "manifest.json"), (vault_copy, VAULT_FILENAME)]
        payload.append((hub_copy, "hub.db"))
        payload.extend(library_payload)
        file_count = len(payload)

        payload_bytes = _payload_bytes(payload)
        _confirm_free_space(destination, payload_bytes, confirm)
        _announce_start(destination, file_count, payload_bytes, compress)

        byte_size = _write_archive(payload, destination, compress, payload_bytes)
    finally:
        hub_source.close()
        hub_guard.close()
        vault_source.close()
        vault_guard.close()
        _remove_tree(scratch)

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
        orphan_count=orphan_count,
        orphans_included=orphans_included,
    )


def _resolve_destination(library: Library, destination: str, compress: bool) -> str:
    """Expand a directory destination into a dated filename, and fix the suffix.

    A directory has always produced a correctly suffixed, dated name. An
    explicit filename did not: ``backup lib ~/backups/monday`` wrote a file
    called ``monday`` with nothing to say what it was, and a year later neither
    the user nor ``file`` can tell it from any other blob. The suffix is not
    decoration here - ``restore`` sniffs the magic bytes, but the *human*
    picking a file out of a folder has only the name.

    So the correct suffix is applied rather than assumed: an already-correct one
    is left alone, the other archive suffix is swapped (``--no-compress`` on a
    name ending ``.tar.zst`` would otherwise lie about the contents), and
    anything else gains one.
    """
    suffix = ARCHIVE_SUFFIX if compress else UNCOMPRESSED_SUFFIX
    expanded = os.path.abspath(os.path.expanduser(destination))
    if os.path.isdir(expanded):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(
            char if char.isalnum() or char in "-_" else "-" for char in library.name
        )
        return os.path.join(expanded, f"{safe_name}-{stamp}{suffix}")
    if expanded.endswith(suffix):
        return expanded
    other = UNCOMPRESSED_SUFFIX if compress else ARCHIVE_SUFFIX
    if expanded.endswith(other):
        return expanded[: -len(other)] + suffix
    return expanded + suffix


def _destination_exists_error(destination: str) -> BackupError:
    if os.path.islink(destination):
        return BackupError(
            f"Refusing to write backup through symlink {destination}. "
            "Choose a new regular-file path."
        )
    return BackupError(
        f"Backup destination already exists: {destination}. Choose a new path; "
        "PixlStash never overwrites a backup."
    )


def _publish_private_temp(
    temp_path: str,
    destination: str,
    expected: os.stat_result,
) -> None:
    """Atomically publish a completed adjacent temp without overwriting."""
    linked = False
    try:
        current = os.lstat(temp_path)
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise BackupError(
                "The private backup temporary file changed before publication."
            )
        os.link(temp_path, destination, follow_symlinks=False)
        linked = True
        published = os.lstat(destination)
        if (published.st_dev, published.st_ino) != (expected.st_dev, expected.st_ino):
            try:
                os.unlink(destination)
            except OSError:
                pass
            raise BackupError("The published backup did not match its private temp.")
        _fsync_directory(os.path.dirname(destination) or ".")
    except FileExistsError as exc:
        raise _destination_exists_error(destination) from exc
    except BackupError:
        if linked:
            _remove_uncommitted_publication(destination, expected)
        raise
    except OSError as exc:
        if linked:
            _remove_uncommitted_publication(destination, expected)
        raise BackupError(f"Could not publish {destination}: {exc}") from exc


def _remove_uncommitted_publication(destination: str, expected: os.stat_result) -> None:
    """Remove only the final hardlink this writer created before commit."""
    try:
        observed = os.lstat(destination)
        if (observed.st_dev, observed.st_ino) == (expected.st_dev, expected.st_ino):
            os.unlink(destination)
            try:
                _fsync_directory(os.path.dirname(destination) or ".")
            except BackupError as exc:
                logger.warning(
                    "Could not make failed backup cleanup durable for %s: %s",
                    destination,
                    exc,
                )
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(
            "Could not remove failed backup publication %s: %s", destination, exc
        )


def _fsync_directory(path: str) -> None:
    """Make adjacent create/unlink directory entries durable on POSIX."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise BackupError(
            f"Could not make backup directory {path} durable: {exc}"
        ) from exc


def _payload_bytes(payload: list[tuple[str, str]]) -> int:
    """Total size of everything to be archived, for the space check and the bar.

    A missing file is counted as zero rather than raised on: this is a
    denominator, and ``_validate_regular_file`` is what turns a vanished payload
    into an error with a path in it, a moment later.
    """
    total = 0
    for absolute, _ in payload:
        try:
            total += os.lstat(absolute).st_size
        except OSError:
            continue
    return total


def human_bytes(count: int) -> str:
    """Format a byte count the way the CLI reports sizes."""
    size = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _confirm_free_space(
    destination: str, needed_bytes: int, confirm: Optional[ConfirmCallback]
) -> None:
    """Ask before starting a backup that looks too big for the destination.

    A warning and a question rather than a refusal: the estimate is the
    uncompressed payload, so a library of already-compressed JPEGs will be close
    and a library of PNGs may come in well under. Refusing on an estimate would
    block backups that would have fitted.

    Raises:
        BackupError: The caller declined.
    """
    shortfall = space_shortfall(os.path.dirname(destination) or ".", needed_bytes)
    if shortfall is None:
        return
    required, free = shortfall
    message = (
        f"{os.path.dirname(destination) or '.'} may not have room: this backup "
        f"needs about {human_bytes(required)} including 10% headroom, and "
        f"{human_bytes(free)} is free. Compression usually makes it smaller, so "
        "this is an estimate rather than a refusal. Continue anyway?"
    )
    if confirm is None or not confirm(message):
        raise BackupError(
            "Cancelled: not enough free space at the destination. Nothing was written."
        )


def _announce_start(
    destination: str, file_count: int, payload_bytes: int, compress: bool
) -> None:
    """Say what is about to happen, before the bar takes over the line.

    A progress bar that appears with no preamble leaves the reader guessing what
    is being counted and where it is going. Printed to stderr beside the bar so
    stdout stays the report.
    """
    print(
        f"Archiving {file_count} file(s), {human_bytes(payload_bytes)} to "
        f"{destination}" + ("" if compress else " (uncompressed)"),
        file=sys.stderr,
    )


def _add_payload(
    tar: tarfile.TarFile, payload: list[tuple[str, str]], total: int
) -> None:
    """Add every payload file to *tar*, reporting progress while it happens.

    Measured in bytes rather than files. A library is a 6 GB checkpoint sitting
    next to ten thousand thumbnails, so "file 300 of 10000" can read 97% done
    with most of the work left; bytes track the wait the user is actually
    having.

    Suppressed off a terminal (see :func:`progress_disabled`), which is what
    keeps a cron backup's mail from being a wall of redrawn bars. It draws on
    stderr, so the report on stdout stays clean and pipeable.
    """
    with tqdm(
        total=total,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc="Archiving",
        disable=progress_disabled(),
        leave=False,
    ) as bar:
        for absolute, arcname in payload:
            info = _validate_regular_file(absolute, label="backup payload")
            tar.add(absolute, arcname=arcname)
            bar.update(info.st_size)


def _write_archive(
    payload: list[tuple[str, str]],
    destination: str,
    compress: bool,
    payload_bytes: int,
) -> int:
    """Stream to a private adjacent temp, then publish atomically."""
    destination_dir = os.path.dirname(destination) or "."
    if os.path.lexists(destination):
        raise _destination_exists_error(destination)
    temp_fd = -1
    temp_path: Optional[str] = None
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            prefix=".pixlstash-backup-", suffix=".tmp", dir=destination_dir
        )
        os.fchmod(temp_fd, BACKUP_FILE_MODE)
        expected = os.fstat(temp_fd)
        if not compress:
            with os.fdopen(temp_fd, "wb") as raw:
                temp_fd = -1
                with tarfile.open(fileobj=raw, mode="w") as tar:
                    _add_payload(tar, payload, payload_bytes)
                raw.flush()
                os.fsync(raw.fileno())
        else:
            compressor = zstandard.ZstdCompressor(level=3)
            with os.fdopen(temp_fd, "wb") as raw:
                temp_fd = -1
                with compressor.stream_writer(raw, closefd=False) as stream:
                    with tarfile.open(mode="w|", fileobj=stream) as tar:
                        _add_payload(tar, payload, payload_bytes)
                raw.flush()
                os.fsync(raw.fileno())
        completed = os.lstat(temp_path)
        if (completed.st_dev, completed.st_ino) != (expected.st_dev, expected.st_ino):
            raise BackupError("The private backup temp changed while it was written.")
        _publish_private_temp(temp_path, destination, completed)
        os.unlink(temp_path)
        temp_path = None
        try:
            _fsync_directory(destination_dir)
        except BackupError as exc:
            # The final hardlink was already fsynced and is committed. Failure
            # to durably record removal of its private sibling must not turn a
            # valid backup into a reported failure that cannot be retried.
            logger.warning("Could not fsync private backup temp cleanup: %s", exc)
        return completed.st_size
    except BackupError:
        raise
    except (OSError, tarfile.TarError, zstandard.ZstdError) as exc:
        raise BackupError(f"Could not write {destination}: {exc}") from exc
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Could not remove backup temp %s: %s", temp_path, exc)


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

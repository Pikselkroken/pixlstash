"""Mandatory removal of portable identity from vault databases and snapshots."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import tempfile

from pixlstash.utils.snapshot_compression import (
    compress_snapshot,
    is_compressed,
    materialize_snapshot,
)


class PortableIdentityScrubError(RuntimeError):
    """A vault or snapshot could not be proven free of portable identity."""


_SIDECARS = ("-wal", "-shm", "-journal")
_SCRUB_PREFIX = ".pixlstash_identity_scrub_"
_PORTABLE_TABLES_CHILD_FIRST = (
    "guest_score",
    "guest_session",
    "usertoken",
    "user",
)


def _fsync_directory(path: str) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file_and_parent(path: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PortableIdentityScrubError(f"Refusing to fsync non-file {path}.")
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(os.path.dirname(path) or ".")


def _privatize_regular_file(
    path: str, expected: os.stat_result | None = None
) -> os.stat_result:
    """Open without following links, validate ownership, and force mode 0600."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PortableIdentityScrubError(
            f"Could not securely open {path}: {exc}"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PortableIdentityScrubError(f"Refusing non-regular file {path}.")
        if expected is not None and (info.st_dev, info.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise PortableIdentityScrubError(
                f"File changed while it was being securely opened: {path}"
            )
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PortableIdentityScrubError(
                f"Refusing file not owned by the current account: {path}"
            )
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        os.fsync(fd)
        return os.fstat(fd)
    finally:
        os.close(fd)


def _verify_identity_free_file(path: str) -> None:
    """Read-only proof that a plain SQLite file contains no portable rows."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise PortableIdentityScrubError(f"Could not inspect {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PortableIdentityScrubError(f"Identity-free proof requires a file: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise PortableIdentityScrubError(
                    f"SQLite integrity_check failed for {path}: {integrity!r}"
                )
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for table in _PORTABLE_TABLES_CHILD_FIRST:
                if table not in tables:
                    continue
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if count:
                    raise PortableIdentityScrubError(
                        f"Portable scrub left {count} row(s) in {table} at {path}."
                    )
        finally:
            conn.close()
    except PortableIdentityScrubError:
        raise
    except sqlite3.Error as exc:
        raise PortableIdentityScrubError(f"Could not verify {path}: {exc}") from exc
    remaining = [
        path + suffix for suffix in _SIDECARS if os.path.lexists(path + suffix)
    ]
    if remaining:
        raise PortableIdentityScrubError(
            f"SQLite sidecars remain beside verified file: {', '.join(remaining)}"
        )


def _secure_remove_unsanitized_plaintext(path: str) -> None:
    """Overwrite a failed plaintext materialization before unlinking it."""
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return
        with open(path, "r+b", buffering=0) as handle:
            remaining = info.st_size
            zeros = b"\0" * (1024 * 1024)
            while remaining:
                chunk = zeros if remaining >= len(zeros) else zeros[:remaining]
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.unlink(path)
    except OSError as exc:
        raise PortableIdentityScrubError(
            f"Could not securely remove failed plaintext scrub file {path}: {exc}"
        ) from exc


def _raw_connection(connection) -> sqlite3.Connection:
    if isinstance(connection, sqlite3.Connection):
        return connection
    fairy = getattr(connection, "connection", None)
    raw = getattr(fairy, "driver_connection", None)
    if not isinstance(raw, sqlite3.Connection):
        raise PortableIdentityScrubError(
            "Portable identity scrub requires a SQLite connection."
        )
    return raw


def _remove_sidecars(path: str) -> None:
    for suffix in _SIDECARS:
        sidecar = path + suffix
        if not os.path.lexists(sidecar):
            continue
        info = os.lstat(sidecar)
        if not stat.S_ISREG(info.st_mode):
            raise PortableIdentityScrubError(
                f"Refusing portable identity scrub with unsafe sidecar {sidecar}."
            )
        try:
            os.unlink(sidecar)
        except OSError as exc:
            raise PortableIdentityScrubError(
                f"Could not remove SQLite sidecar {sidecar}: {exc}"
            ) from exc
    remaining = [
        path + suffix for suffix in _SIDECARS if os.path.lexists(path + suffix)
    ]
    if remaining:
        raise PortableIdentityScrubError(
            f"SQLite sidecars remain after identity scrub: {', '.join(remaining)}"
        )


def sanitize_vault_connection(
    connection, path: str, *, restore_wal: bool = False
) -> None:
    """Delete identity and erase its free-page/WAL remnants on one SQLite connection."""
    raw = _raw_connection(connection)
    try:
        if callable(getattr(connection, "in_transaction", None)):
            if connection.in_transaction():
                connection.commit()
        else:
            raw.commit()

        secure_delete = raw.execute("PRAGMA secure_delete=ON").fetchone()
        if not secure_delete or int(secure_delete[0]) != 1:
            raise PortableIdentityScrubError("SQLite did not enable secure_delete.")
        tables = {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        raw.execute("BEGIN IMMEDIATE")
        for table in _PORTABLE_TABLES_CHILD_FIRST:
            if table in tables:
                raw.execute(f'DELETE FROM "{table}"')
        raw.commit()

        # Fold every committed WAL frame into the main file before removing
        # the portable journal namespace, then rewrite every live page.
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        mode = raw.execute("PRAGMA journal_mode=DELETE").fetchone()
        if not mode or str(mode[0]).lower() != "delete":
            raise PortableIdentityScrubError(
                f"SQLite stayed in journal mode {mode[0] if mode else 'unknown'}."
            )
        raw.execute("VACUUM")
        raw.commit()

        integrity = raw.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise PortableIdentityScrubError(
                f"SQLite integrity_check failed after identity scrub: {integrity!r}"
            )
        for table in _PORTABLE_TABLES_CHILD_FIRST:
            if table in tables:
                count = raw.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if count:
                    raise PortableIdentityScrubError(
                        f"Portable identity scrub left {count} row(s) in {table}."
                    )
    except PortableIdentityScrubError:
        raise
    except sqlite3.Error as exc:
        try:
            raw.rollback()
        except sqlite3.Error:
            pass
        raise PortableIdentityScrubError(
            f"Could not sanitize portable identity in {path}: {exc}"
        ) from exc

    _remove_sidecars(path)
    _verify_identity_free_file(path)
    _fsync_file_and_parent(path)
    if restore_wal:
        try:
            mode = raw.execute("PRAGMA journal_mode=WAL").fetchone()
            if not mode or str(mode[0]).lower() != "wal":
                raise PortableIdentityScrubError(
                    f"SQLite did not restore WAL mode for live vault {path}."
                )
            raw.commit()
        except PortableIdentityScrubError:
            raise
        except sqlite3.Error as exc:
            raise PortableIdentityScrubError(
                f"Could not restore WAL mode for live vault {path}: {exc}"
            ) from exc


def sanitize_vault_file(path: str) -> None:
    """Sanitize a closed plain SQLite vault and prove it has no sidecars."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise PortableIdentityScrubError(
            f"Could not inspect vault {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise PortableIdentityScrubError(f"Vault {path} is not a regular file.")
    try:
        connection = sqlite3.connect(path, timeout=30)
        try:
            sanitize_vault_connection(connection, path)
        finally:
            connection.close()
    except PortableIdentityScrubError:
        raise
    except sqlite3.Error as exc:
        raise PortableIdentityScrubError(f"Could not open vault {path}: {exc}") from exc
    _remove_sidecars(path)


def sanitize_snapshot_archive(path: str) -> int:
    """Atomically replace a compressed or legacy snapshot with a sanitized copy."""
    try:
        original = os.lstat(path)
    except OSError as exc:
        raise PortableIdentityScrubError(
            f"Could not inspect snapshot {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(original.st_mode):
        raise PortableIdentityScrubError(
            f"Registered snapshot is not a regular file: {path}"
        )
    # Older releases inherited the process umask and commonly produced 0664
    # archives. Accept only a regular file owned by this account, then make it
    # private through an O_NOFOLLOW descriptor before reading any bytes.
    original = _privatize_regular_file(path, original)
    sidecars = [path + suffix for suffix in _SIDECARS if os.path.lexists(path + suffix)]
    if sidecars:
        raise PortableIdentityScrubError(
            f"Registered snapshot has unsafe SQLite sidecars: {', '.join(sidecars)}"
        )
    parent = os.path.dirname(path)
    workdir = tempfile.mkdtemp(prefix=_SCRUB_PREFIX, dir=parent)
    os.chmod(workdir, 0o700)
    plain = os.path.join(workdir, "snapshot.sqlite")
    compressed = is_compressed(path)
    replacement = os.path.join(
        workdir, "replacement.sqlite.zst" if compressed else "replacement.sqlite"
    )
    verify_plain = os.path.join(workdir, "verify.sqlite")
    plain_sanitized = False
    try:
        materialize_snapshot(path, plain)
        sanitize_vault_file(plain)
        plain_sanitized = True
        if compressed:
            compress_snapshot(plain, replacement)
        else:
            shutil.copy2(plain, replacement)
        os.chmod(replacement, 0o600)

        # Verify the bytes that will be renamed, not merely the source used to
        # produce them. This catches corrupt/truncated recompression.
        materialize_snapshot(replacement, verify_plain)
        _verify_identity_free_file(verify_plain)
        replacement_info = os.lstat(replacement)
        if not stat.S_ISREG(replacement_info.st_mode):
            raise PortableIdentityScrubError(
                f"Snapshot replacement became non-regular: {replacement}"
            )
        _fsync_file_and_parent(replacement)

        # Refuse a concurrent replacement of the registered archive between
        # validation/materialization and the destructive rename.
        current = os.lstat(path)
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) != (
            original.st_dev,
            original.st_ino,
            original.st_size,
            original.st_mtime_ns,
        ):
            raise PortableIdentityScrubError(
                f"Registered snapshot changed during identity scrub: {path}"
            )
        os.replace(replacement, path)
        _fsync_directory(parent or ".")
        return replacement_info.st_size
    except Exception as exc:
        if isinstance(exc, PortableIdentityScrubError):
            raise
        raise PortableIdentityScrubError(
            f"Could not atomically sanitize snapshot {path}: {exc}"
        ) from exc
    finally:
        try:
            if os.path.lexists(plain) and not plain_sanitized:
                _secure_remove_unsanitized_plaintext(plain)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def _snapshots_root(vault_root: str) -> str | None:
    """Return a non-symlink snapshots directory strictly below the vault."""
    root = os.path.realpath(os.path.abspath(vault_root))
    snapshots = os.path.join(root, "snapshots")
    if not os.path.lexists(snapshots):
        return None
    info = os.lstat(snapshots)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PortableIdentityScrubError(
            f"Vault snapshots path is not a trusted directory: {snapshots}"
        )
    if os.path.commonpath((root, os.path.realpath(snapshots))) != root:
        raise PortableIdentityScrubError(
            f"Vault snapshots path escapes the library: {snapshots}"
        )
    return snapshots


def _registered_snapshot_path(
    vault_root: str, snapshots_root: str, relative_path: str
) -> str:
    """Resolve an untrusted Snapshot.relative_path without following links."""
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or os.path.isabs(relative_path)
    ):
        raise PortableIdentityScrubError(
            f"Registered snapshot has an unsafe path: {relative_path!r}"
        )
    root = os.path.realpath(os.path.abspath(vault_root))
    candidate = os.path.abspath(os.path.join(root, relative_path))
    try:
        contained = os.path.commonpath((snapshots_root, candidate)) == snapshots_root
    except ValueError:
        contained = False
    if not contained:
        raise PortableIdentityScrubError(
            f"Registered snapshot escapes the snapshots directory: {relative_path!r}"
        )

    relative_inside = os.path.relpath(candidate, snapshots_root)
    current = snapshots_root
    parts = relative_inside.split(os.sep)
    for index, component in enumerate(parts):
        if component in ("", ".", ".."):
            raise PortableIdentityScrubError(
                f"Registered snapshot has an unsafe component: {relative_path!r}"
            )
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise PortableIdentityScrubError(
                f"Registered snapshot is missing at {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise PortableIdentityScrubError(
                f"Registered snapshot path contains a symlink: {current}"
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise PortableIdentityScrubError(
                f"Registered snapshot parent is not a directory: {current}"
            )
        if index == len(parts) - 1 and not stat.S_ISREG(info.st_mode):
            raise PortableIdentityScrubError(
                f"Registered snapshot is not a regular file: {current}"
            )
    return candidate


def _cleanup_stale_snapshot_scrubs(vault_root: str) -> None:
    """Sanitize and remove plaintext left by an interrupted archive rewrite."""
    snapshots_root = _snapshots_root(vault_root)
    if snapshots_root is None:
        return
    stale_dirs: list[str] = []
    for directory, subdirs, _files in os.walk(snapshots_root):
        for name in list(subdirs):
            candidate = os.path.join(directory, name)
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                subdirs.remove(name)
                if name.startswith(_SCRUB_PREFIX):
                    raise PortableIdentityScrubError(
                        f"Refusing symlinked stale scrub directory {candidate}."
                    )
                continue
            if name.startswith(_SCRUB_PREFIX):
                stale_dirs.append(candidate)
                subdirs.remove(name)
    for stale in stale_dirs:
        plain = os.path.join(stale, "snapshot.sqlite")
        if os.path.lexists(plain):
            info = os.lstat(plain)
            if not stat.S_ISREG(info.st_mode):
                raise PortableIdentityScrubError(
                    f"Refusing unsafe stale scrub plaintext {plain}."
                )
            sanitize_vault_file(plain)
        shutil.rmtree(stale, ignore_errors=False)
    if stale_dirs:
        _fsync_directory(snapshots_root)


def sanitize_historical_snapshots(connection, vault_root: str) -> None:
    """Sanitize every registered archive and update its byte size."""
    _cleanup_stale_snapshot_scrubs(vault_root)
    snapshots_root = _snapshots_root(vault_root)
    raw = _raw_connection(connection)
    tables = {
        row[0]
        for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "snapshot" not in tables:
        return
    rows = raw.execute("SELECT id, relative_path FROM snapshot ORDER BY id").fetchall()
    for snapshot_id, relative_path in rows:
        if snapshots_root is None:
            raise PortableIdentityScrubError(
                f"Registered snapshot {snapshot_id} exists but snapshots/ is missing."
            )
        archive = _registered_snapshot_path(vault_root, snapshots_root, relative_path)
        byte_size = sanitize_snapshot_archive(archive)
        raw.execute(
            "UPDATE snapshot SET byte_size=? WHERE id=?", (byte_size, snapshot_id)
        )
        raw.commit()

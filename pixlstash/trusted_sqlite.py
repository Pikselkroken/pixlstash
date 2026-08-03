"""Trusted-location guards for security-sensitive SQLite opens.

SQLite's main file cannot be safely redirected through ``/proc/self/fd`` when
WAL is enabled: its ``-wal`` and ``-shm`` siblings are derived from the path.
Instead, require a namespace in which another OS principal cannot replace the
main file or pre-position a sidecar, hold a no-follow guard, open SQLite by the
canonical path, and compare identities before doing decisive work.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from platformdirs import user_config_dir


class TrustedSQLiteLocationError(RuntimeError):
    """A SQLite main file or its namespace cannot be trusted."""


_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _require_owned_directory(path: str, *, immediate: bool) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise TrustedSQLiteLocationError(
            f"Could not inspect SQLite directory {path}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise TrustedSQLiteLocationError(
            f"SQLite directory component {path} is not a real directory."
        )
    if os.name == "nt":
        return info
    if not hasattr(os, "geteuid"):
        raise TrustedSQLiteLocationError(
            f"Cannot verify ownership of SQLite directory {path} on this platform."
        )
    uid = os.geteuid()
    if info.st_uid not in (uid, 0):
        raise TrustedSQLiteLocationError(
            f"SQLite directory {path} is owned by uid {info.st_uid}, not this "
            "user or root."
        )
    writable = stat.S_IMODE(info.st_mode) & 0o022
    if writable and (immediate or not (info.st_mode & stat.S_ISVTX)):
        raise TrustedSQLiteLocationError(
            f"SQLite directory {path} is group/world-writable; another account "
            "could replace the database or its WAL/SHM files."
        )
    return info


def _validate_namespace(canonical_path: str) -> None:
    parent = os.path.dirname(canonical_path)
    current = parent
    immediate = True
    while True:
        _require_owned_directory(current, immediate=immediate)
        next_parent = os.path.dirname(current)
        if next_parent == current:
            break
        current = next_parent
        immediate = False


def _validate_file(path: str, *, private: bool) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise TrustedSQLiteLocationError(
            f"Could not inspect SQLite file {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        kind = "symlink" if stat.S_ISLNK(info.st_mode) else "non-regular file"
        raise TrustedSQLiteLocationError(
            f"SQLite file {path} is a {kind}; refusing to open it."
        )
    if os.name != "nt" and hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise TrustedSQLiteLocationError(
            f"SQLite file {path} is owned by uid {info.st_uid}, not this user."
        )
    if private and stat.S_IMODE(info.st_mode) & 0o077:
        raise TrustedSQLiteLocationError(
            f"SQLite credential file {path} must be mode 600."
        )
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise TrustedSQLiteLocationError(
            f"SQLite file {path} is group/world-writable; refusing to open it."
        )
    return info


@dataclass
class TrustedSQLiteLocation:
    """A guarded canonical SQLite path whose surrounding namespace is trusted."""

    path: str
    fd: int
    identity: tuple[int, int]
    parent_identity: tuple[int, int, int, int]
    private: bool = False
    strict_parent_changes: bool = True

    @classmethod
    def open(
        cls,
        path: str,
        *,
        private: bool = False,
        create: bool = False,
        allow_windows_app_config: bool = False,
        strict_parent_changes: bool = True,
    ) -> "TrustedSQLiteLocation":
        absolute = os.path.abspath(os.path.expanduser(path))
        canonical = os.path.realpath(absolute)
        # A canonical path is used for SQLite, but accepting a symlink in the
        # caller-provided path would make the visible target mutable.
        if absolute != canonical:
            raise TrustedSQLiteLocationError(
                f"SQLite path {absolute} contains a symlink; refusing to open it."
            )
        if os.name == "nt":
            # Python exposes neither owner SID nor directory DACL portably.
            # Until a native ACL verifier is available, fail closed for custom
            # locations. The one supported exception is PixlStash's own
            # per-user configuration directory, created by the application.
            app_config = os.path.realpath(user_config_dir("pixlstash"))
            try:
                in_app_config = (
                    os.path.commonpath((canonical, app_config)) == app_config
                )
            except ValueError:
                in_app_config = False
            if not (allow_windows_app_config and in_app_config):
                raise TrustedSQLiteLocationError(
                    "Cannot verify the Windows DACL for this custom SQLite "
                    "location; refusing a security-sensitive open."
                )
        _validate_namespace(canonical)
        if create and not os.path.lexists(canonical):
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                created_fd = os.open(canonical, flags, 0o600)
            except FileExistsError:
                pass
            except OSError as exc:
                raise TrustedSQLiteLocationError(
                    f"Could not securely create SQLite file {canonical}: {exc}"
                ) from exc
            else:
                os.close(created_fd)
        parent_info = os.lstat(os.path.dirname(canonical))
        parent_identity = (
            parent_info.st_dev,
            parent_info.st_ino,
            parent_info.st_mtime_ns,
            parent_info.st_ctime_ns,
        )
        expected = _validate_file(canonical, private=private)
        for suffix in _SIDECAR_SUFFIXES:
            sidecar = canonical + suffix
            if os.path.lexists(sidecar):
                _validate_file(sidecar, private=private)

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(canonical, flags)
        except OSError as exc:
            raise TrustedSQLiteLocationError(
                f"Could not securely guard SQLite file {canonical}: {exc}"
            ) from exc
        guarded = os.fstat(fd)
        if _identity(guarded) != _identity(expected):
            os.close(fd)
            raise TrustedSQLiteLocationError(
                f"SQLite file {canonical} changed while it was guarded."
            )
        return cls(
            canonical,
            fd,
            _identity(guarded),
            parent_identity,
            private,
            strict_parent_changes,
        )

    def verify_after_open(self) -> None:
        current = _validate_file(self.path, private=False)
        if _identity(current) != self.identity:
            raise TrustedSQLiteLocationError(
                f"SQLite file {self.path} changed while it was being opened."
            )
        parent = os.lstat(os.path.dirname(self.path))
        current_parent = (
            parent.st_dev,
            parent.st_ino,
            parent.st_mtime_ns,
            parent.st_ctime_ns,
        )
        expected_parent = (
            self.parent_identity
            if self.strict_parent_changes
            else self.parent_identity[:2]
        )
        observed_parent = (
            current_parent if self.strict_parent_changes else current_parent[:2]
        )
        if observed_parent != expected_parent:
            raise TrustedSQLiteLocationError(
                f"SQLite namespace for {self.path} changed while it was being opened."
            )
        # SQLite may have created WAL/SHM between the guard and this check;
        # validate those new namespace entries now rather than treating the
        # expected directory timestamp change as a replacement attack.
        for suffix in _SIDECAR_SUFFIXES:
            sidecar = self.path + suffix
            if os.path.lexists(sidecar):
                _validate_file(sidecar, private=self.private)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "TrustedSQLiteLocation":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

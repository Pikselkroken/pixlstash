"""Detect and safely repair loose POSIX permissions at startup.

PixlStash's SQLite guards warn about namespaces another local account can
modify.  Older releases created the app config and default library under the
process umask, which is commonly ``0002`` on Linux and therefore produced
``0775`` directories.  This module finds those and offers a bounded, explicit
chmod; startup goes ahead either way.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from platformdirs import user_config_dir

from pixlstash.trusted_sqlite import TrustedSQLiteLocation, TrustedSQLiteLocationError


PERMISSION_REPAIR_ENV = "PIXLSTASH_REPAIR_PERMISSIONS"

_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _app_owned_config_directories() -> set[str]:
    """Canonical config roots whose contents are private PixlStash state."""

    return {
        os.path.realpath(user_config_dir("pixlstash")),
        os.path.realpath(user_config_dir("pixlstash-desktop")),
    }


def mkdir_private(path: Path) -> None:
    """Create every missing component of *path* with mode ``0700``.

    ``Path.mkdir(parents=True, mode=0o700)`` applies the requested mode only to
    the leaf.  Under umask ``0002`` its missing parents still become ``0775``.
    Existing directories are never changed here; repairing them requires an
    explicit user decision through :func:`repair_permission_issues`.
    """

    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)


@dataclass(frozen=True)
class PermissionIssue:
    """One current-user-owned path whose mode can be safely tightened."""

    area: str
    path: str
    current_mode: int
    repaired_mode: int
    is_directory: bool
    device: int
    inode: int


def _repairable_issue(
    path: str,
    *,
    area: str,
    private: bool,
    is_directory: bool,
) -> PermissionIssue | None:
    """Return a mode-only issue, never offering to repair suspicious objects."""

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return None

    expected_type = stat.S_ISDIR if is_directory else stat.S_ISREG
    if not expected_type(info.st_mode):
        return None
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return None

    mode = stat.S_IMODE(info.st_mode)
    unsafe = mode & (0o077 if private else 0o022)
    if not unsafe:
        return None
    repaired = (
        0o700 if is_directory and private else 0o600 if private else mode & ~0o022
    )
    return PermissionIssue(
        area=area,
        path=os.path.abspath(path),
        current_mode=mode,
        repaired_mode=repaired,
        is_directory=is_directory,
        device=info.st_dev,
        inode=info.st_ino,
    )


def _database_issues(path: str, *, area: str, private: bool) -> list[PermissionIssue]:
    issues: list[PermissionIssue] = []
    for candidate in (path, *(path + suffix for suffix in _SQLITE_SIDECARS)):
        issue = _repairable_issue(
            candidate,
            area=area,
            private=private,
            is_directory=False,
        )
        if issue is not None:
            issues.append(issue)
    return issues


def _ancestor_issues(path: str, *, area: str) -> list[PermissionIssue]:
    """Mirror the guard's ancestor walk over the directories enclosing *path*.

    ``TrustedSQLiteLocation`` refuses a database whose *enclosing* directories
    are group/world-writable, not only the directory holding it, so an offer
    that stops at the leaf reports "no issues" for a startup the guard will
    still refuse.  The sticky-bit allowance is copied from the guard as well:
    a shared root such as ``/tmp`` is acceptable above the immediate parent and
    must never be offered for a chmod.
    """

    issues: list[PermissionIssue] = []
    current = os.path.dirname(os.path.realpath(path))
    immediate = True
    while True:
        try:
            info = os.lstat(current)
        except OSError:
            break
        if immediate or not info.st_mode & stat.S_ISVTX:
            issue = _repairable_issue(
                current,
                area=area,
                private=False,
                is_directory=True,
            )
            if issue is not None:
                issues.append(issue)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        immediate = False
    return issues


def _deduplicated(issues: Iterable[PermissionIssue]) -> list[PermissionIssue]:
    """Keep the first offer recorded for each path; earlier ones are stricter."""

    seen: set[str] = set()
    unique: list[PermissionIssue] = []
    for issue in issues:
        key = os.path.realpath(issue.path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def _active_library_root(hub_path: str) -> str | None:
    """Read the active registered library after the hub has passed its guard."""

    if not os.path.isfile(hub_path):
        return None
    guard: TrustedSQLiteLocation | None = None
    connection: sqlite3.Connection | None = None
    try:
        guard = TrustedSQLiteLocation.open(
            hub_path,
            private=True,
            trusted_root=os.path.dirname(hub_path),
        )
        connection = sqlite3.connect(f"file:{guard.path}?mode=ro", uri=True, timeout=5)
        row = connection.execute(
            "SELECT path FROM library WHERE is_active = 1 AND attached = 1 LIMIT 1"
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    except (sqlite3.Error, TrustedSQLiteLocationError, OSError):
        # A missing/legacy schema simply has no registered active library yet.
        # Non-mode trust failures are left to the authoritative startup guard;
        # they are intentionally not converted into a chmod offer.
        return None
    finally:
        if connection is not None:
            connection.close()
        if guard is not None:
            guard.close()


def find_startup_permission_issues(
    server_config_path: str,
    configured_image_root: str | None,
) -> list[PermissionIssue]:
    """Find repairable hub and active-library mode problems on POSIX."""

    if os.name == "nt":
        return []

    config_dir = os.path.abspath(os.path.dirname(server_config_path))
    issues: list[PermissionIssue] = []
    canonical_config_dir = os.path.realpath(config_dir)
    # Build the hub path from the *resolved* directory, matching what
    # `canonical_hub_path` hands the guard. A symlinked config directory
    # otherwise leaves this scan inspecting one spelling while the guard opens
    # another, which is how the offer came to report "no issues" for a startup
    # that then failed. The leaf is joined rather than resolved, so a symlink
    # standing at hub.db is still the guard's to refuse.
    hub_path = os.path.join(canonical_config_dir, "hub.db")
    private_config_dir = canonical_config_dir in _app_owned_config_directories()

    config_issue = _repairable_issue(
        config_dir,
        area="PixlStash settings",
        private=private_config_dir,
        is_directory=True,
    )
    if config_issue is not None:
        issues.append(config_issue)
    issues.extend(_database_issues(hub_path, area="PixlStash settings", private=True))
    issues.extend(_ancestor_issues(hub_path, area="Folder holding PixlStash settings"))

    # Do not open the hub until every repairable credential-store issue is gone.
    # A second discovery pass after repair then picks up a switched active library.
    roots = [configured_image_root] if configured_image_root else []
    if not issues:
        active = _active_library_root(hub_path)
        if active:
            roots.append(active)

    # The vault guard opens every library with private=False and refuses only
    # group/world-writable modes, wherever the library lives. Holding the
    # default library under the config root to 0700 here refused a 0755
    # directory the guard would have accepted (the Docker image did exactly
    # that on first run), so this scan asks for no more than the guard does.
    seen: set[str] = set()
    for root in roots:
        resolved = os.path.realpath(os.path.abspath(os.path.expanduser(str(root))))
        if resolved in seen:
            continue
        seen.add(resolved)
        directory_issue = _repairable_issue(
            resolved,
            area="Library",
            private=False,
            is_directory=True,
        )
        if directory_issue is not None:
            issues.append(directory_issue)
        vault_path = os.path.join(resolved, "vault.db")
        issues.extend(_database_issues(vault_path, area="Library", private=False))
        issues.extend(_ancestor_issues(vault_path, area="Folder holding your library"))
    return _deduplicated(issues)


def repair_permission_issues(issues: Iterable[PermissionIssue]) -> None:
    """Tighten recorded paths after rechecking type, owner, and identity."""

    if os.name == "nt":
        return
    for issue in issues:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if issue.is_directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        fd = os.open(issue.path, flags)
        try:
            current = os.fstat(fd)
            correct_type = (
                stat.S_ISDIR(current.st_mode)
                if issue.is_directory
                else stat.S_ISREG(current.st_mode)
            )
            if not correct_type or (current.st_dev, current.st_ino) != (
                issue.device,
                issue.inode,
            ):
                raise OSError(f"{issue.path} changed before permissions could be fixed")
            if hasattr(os, "geteuid") and current.st_uid != os.geteuid():
                raise PermissionError(f"{issue.path} is not owned by the current user")
            os.fchmod(fd, issue.repaired_mode)
        finally:
            os.close(fd)


def format_permission_problem(issues: Iterable[PermissionIssue]) -> str:
    """Return the human-facing explanation shared by CLI and Electron."""

    issue_list = list(issues)
    lines = [
        "PixlStash found unsafe file permissions.",
        "",
        "Other users on this computer can read private credentials or modify a database:",
    ]
    for issue in issue_list:
        lines.append(
            f"- {issue.area}: {issue.path} "
            f"(mode {issue.current_mode:03o}; needs {issue.repaired_mode:03o})"
        )
    lines.extend(
        [
            "",
            "PixlStash will start anyway.",
        ]
    )
    return "\n".join(lines)

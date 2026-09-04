"""The hub database connection: multi-process contract and file permissions.

Two callers open this file: the long-running server and the short-lived
``pixlstash.libraries`` CLI, potentially at the same moment. That is the
opposite of :class:`pixlstash.database.VaultDatabase`, whose single writer
thread and in-process queue assume exactly one process owns the file. Sharing
that design here would be a correctness bug, so the hub gets its own small
connection module with an explicitly multi-process contract:

* **WAL** so a reader never blocks the writer and vice versa;
* **a busy timeout** so the loser of a write race waits instead of raising
  "database is locked";
* **short transactions only** - every write in :mod:`pixlstash.hub.registry` is
  a single statement or a single ``with`` block. Nothing holds the write lock
  across user interaction or filesystem work.

The file also holds the password hash and every token hash, so it is created
0600 and its permissions are re-checked on every open.
"""

from __future__ import annotations

import os
import shlex
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from platformdirs import user_config_dir

from pixlstash.hub.schema import apply_migrations
from pixlstash.pixl_logging import get_logger
from pixlstash.startup_permissions import mkdir_private
from pixlstash.trusted_sqlite import (
    TrustedSQLiteLocation,
    TrustedSQLiteLocationError,
)

logger = get_logger(__name__)

APP_NAME = "pixlstash"

# How long a connection waits for the write lock before raising "database is
# locked". The hub's writes are tiny (single rows), so contention should be
# sub-millisecond; this is headroom for a busy filesystem, not for long
# transactions. Deliberately shorter than the vault's 30 s
# (:data:`pixlstash.database.SQLITE_BUSY_TIMEOUT_S`), which is sized for
# background batches holding a write transaction - a hub write that waits 5 s
# is a bug worth surfacing, not something to wait out.
HUB_BUSY_TIMEOUT_S = 5

# The mode the hub file must have: owner read/write only.
HUB_FILE_MODE = 0o600


class HubPermissionError(RuntimeError):
    """The hub file is readable or writable by users other than its owner."""


def _validate_hub_file(path: str, *, repair: bool) -> os.stat_result:
    """Require an owner-controlled regular file, without following symlinks."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise HubPermissionError(f"Could not inspect hub file {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        kind = "symlink" if stat.S_ISLNK(info.st_mode) else "non-regular file"
        raise HubPermissionError(f"Hub path {path} is a {kind}; refusing to open it.")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise HubPermissionError(
            f"Hub file {path} is owned by uid {info.st_uid}, not the current user."
        )
    check_file_mode(path, repair=repair)
    return info


def _prepare_hub_file(path: str, *, repair: bool) -> tuple[bool, tuple[int, int]]:
    """Atomically create *path* as 0600, or validate the existing file.

    Returns True only for the process that won creation. ``O_EXCL`` handles a
    simultaneous server/CLI first open without either observing a permissive
    sqlite-created file; ``O_NOFOLLOW`` rejects a pre-positioned symlink where
    the platform provides it.
    """
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, HUB_FILE_MODE)
    except FileExistsError:
        info = _validate_hub_file(path, repair=repair)
        return False, (info.st_dev, info.st_ino)
    except OSError as exc:
        raise HubPermissionError(
            f"Could not securely create hub file {path}: {exc}"
        ) from exc

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise HubPermissionError(f"New hub path {path} is not a regular file.")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise HubPermissionError(
                f"New hub file {path} is not owned by the current user."
            )
        # POSIX-only, like the getuid check above and the O_NOFOLLOW flag:
        # Windows has no fchmod, and its ACLs do not carry these bits anyway.
        # The mode passed to os.open() already applied where it means anything.
        if hasattr(os, "fchmod"):
            os.fchmod(fd, HUB_FILE_MODE)
    finally:
        os.close(fd)
    return True, (info.st_dev, info.st_ino)


def default_hub_path() -> str:
    """Return the platform config path for ``hub.db``.

    Sits beside ``server-config.json`` (``pixlstash.app.SERVER_CONFIG_PATH``),
    so the whole of PixlStash's app-level state is one directory.
    """
    return os.path.join(user_config_dir(APP_NAME), "hub.db")


def canonical_hub_path(path: str) -> str:
    """Return *path* with every symlinked ancestor resolved.

    ``TrustedSQLiteLocation`` opens by the canonical path but refuses a
    *caller-supplied* path that reaches its target through a symlink, and the
    hub's path is derived from the config directory rather than registered the
    way a library's is.  A user whose ``~/.config`` is managed by stow or
    chezmoi, whose ``$HOME`` is symlinked onto another disk, or whose path
    crosses macOS's ``/var`` -> ``/private/var`` therefore had a server that
    would not start at all -- and no route back, because ``startup_permissions``
    mirrors the guard's walk over ``realpath`` and so cannot see a redirect that
    only exists before resolution, and the Electron shell exposes no way to
    override the config path.

    Resolving here is what the library registry has always done for vault paths
    (``registry.resolve_path``), so the two lanes now agree.  It is deliberately
    weaker than the refusal it replaces: a redirect is followed once per boot
    rather than refused, which admits an attacker who can write a directory on
    the pre-resolution path redirecting us to a *different* database this user
    already owns.  Every other guard still holds -- the namespace walk refuses a
    target whose parent another principal can write, and the file checks refuse
    one this user does not own at mode 600 -- so the residue is confusion
    between our own hubs, not substitution.  Reaching even that needs write
    access inside the owner's own config directory, which also holds
    ``autostart/`` and ``systemd/user/``: code execution as the owner by a
    shorter route.  Accepted risk, recorded beside W17 in
    ``docs/backend_architecture.md`` section 13.

    Only the *ancestors* are resolved.  The final component is left exactly as
    given so that a symlink standing at ``hub.db`` itself still reaches
    ``_reject_symlinked_path`` and is refused: nobody legitimately makes the
    database file a link, and that is the classic pre-positioned-symlink
    attack.  ``os.path.realpath`` over the whole path would follow it silently.

    On Windows the path is returned absolute but otherwise *unchanged*, ancestors
    included.  ``os.path.realpath`` there resolves junctions and symlinks, so
    pre-resolving the ancestors would hand ``TrustedSQLiteLocation.open`` a
    redirect-free path and defeat ``_reject_symlinked_path`` on an ancestor
    junction -- the redirect an unprivileged account can create with only
    directory write access (``mklink /J``).  The two POSIX controls that make
    following a redirect tolerable both return early on ``nt``:
    ``_require_owned_directory``'s namespace walk (which would refuse an exposed
    parent) and ``check_file_mode``'s mode-600 check.  Windows therefore keeps
    refusing ancestor redirects rather than following them; see the W19 record in
    ``docs/backend_architecture.md`` section 13.
    """
    absolute = os.path.abspath(os.path.expanduser(path))
    if os.name == "nt":
        return absolute
    directory, name = os.path.split(absolute)
    return os.path.join(os.path.realpath(directory), name)


def check_file_mode(path: str, *, repair: bool) -> None:
    """Verify that *path* is not group- or world-accessible.

    Args:
        path: The hub file. A missing file is not an error (it is about to be
            created 0600).
        repair: When True, tighten the mode in place and log it, which is what
            the CLI does. When False, warn and carry on, which is what the
            server does: a hub whose permissions were loosened by something
            else is worth telling the operator about, but not worth refusing
            to start over.
    """
    if os.name == "nt":
        # Windows has no mode bits to check: `st_mode` is synthesised from the
        # read-only attribute and reads 0o666 for an ordinary file, so this
        # would refuse every hub, including one this process just created 0600.
        # Access there is governed by the file's ACL, which Python cannot read
        # portably; `TrustedSQLiteLocation` trusts the hub at the root its own
        # configuration placed it (see that module's docstring, W17 record).
        return
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.error("Could not stat hub file %s: %s", path, exc)
        raise

    extra = mode & ~HUB_FILE_MODE
    if not extra:
        return

    if repair:
        logger.warning(
            "Hub file %s had mode %o (group/world bits %o set); tightening to "
            "%o. It holds the password hash and every token hash.",
            path,
            mode,
            extra,
            HUB_FILE_MODE,
        )
        os.chmod(path, HUB_FILE_MODE)
        return

    logger.warning(
        "Hub file %s has mode %o; it holds the password hash and every token "
        "hash and should be %o. Fix it with chmod %o %s",
        path,
        mode,
        HUB_FILE_MODE,
        HUB_FILE_MODE,
        shlex.quote(path),
    )


class HubDatabase:
    """A connection to the hub, upgraded to the current schema on open.

    Not thread-safe by itself: ``check_same_thread`` stays at sqlite3's default,
    so a connection belongs to the thread that opened it. The server holds one
    per request path via :meth:`connect`; the CLI opens one for its lifetime.
    """

    def __init__(self, path: str | None = None, *, repair_permissions: bool = False):
        """Open (creating if needed) the hub at *path*.

        Args:
            path: Hub file path. Defaults to :func:`default_hub_path`.
            repair_permissions: Passed to :func:`check_file_mode`. The CLI sets
                it; the server does not.
        """
        self._path = canonical_hub_path(path or default_hub_path())
        self._closed = False

        parent = Path(self._path).parent
        mkdir_private(parent)

        created, _expected_identity = _prepare_hub_file(
            self._path, repair=repair_permissions
        )

        # ``check_same_thread=False`` plus an explicit lock, because this
        # connection is reached from request-handler threads as well as from
        # startup: the registry answers "which library is active" on the token
        # path. sqlite3's own guard is per-connection and would reject those
        # calls outright. Serialising here is safe precisely because the hub's
        # contract is short transactions only (see the module docstring), so no
        # caller holds the lock across I/O or user interaction.
        self._lock = threading.RLock()
        try:
            guard = TrustedSQLiteLocation.open(
                self._path,
                private=True,
                trusted_root=os.path.dirname(self._path),
            )
        except TrustedSQLiteLocationError as exc:
            raise HubPermissionError(str(exc)) from exc
        # The guard fd must outlive the connection. POSIX advisory locks are
        # per-process, per-inode: closing ANY fd this process holds on the hub
        # file releases every fcntl lock the process has on it - including the
        # ones SQLite took for self._conn (sqlite.org/howtocorrupt.html §2.2).
        # The hub is explicitly multi-process (server + CLI), so a lock-stripped
        # connection lets another process treat the hub as unused and delete its
        # live -wal/-shm on close. Retained as self._guard, closed in close().
        try:
            self._conn = sqlite3.connect(
                guard.path,
                timeout=HUB_BUSY_TIMEOUT_S,
                isolation_level="",
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            guard.verify_after_open()

            if created:
                logger.info("Created hub database at %s", self._path)

            self._configure()
            apply_migrations(self._conn)
        except TrustedSQLiteLocationError as exc:
            connection = getattr(self, "_conn", None)
            if connection is not None:
                connection.close()
            guard.close()
            raise HubPermissionError(str(exc)) from exc
        except Exception:
            connection = getattr(self, "_conn", None)
            if connection is not None:
                connection.close()
            guard.close()
            raise
        self._guard = guard

    @property
    def path(self) -> str:
        """Filesystem path of the hub database."""
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        """The live connection, for callers issuing their own statements."""
        return self._conn

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Run a read returning at most one row, serialised against writers."""
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Run a read returning every row, serialised against writers."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a short write transaction, committing on success.

        Deliberately minimal: the hub's multi-process contract depends on write
        transactions staying short, so this is for one logical write, never for
        a block that also touches the filesystem or waits on a user.

        **The ``BEGIN IMMEDIATE`` is load-bearing, not a tidiness flourish.**
        ``with self._conn`` commits on exit but never begins, and the connection
        is opened ``isolation_level=""`` (see :meth:`_connect`), which defers
        ``BEGIN`` to the first *DML* statement. A block that reads before it
        writes therefore ran its reads in **autocommit**, and in WAL an
        autocommit read takes no lock at all, so another process could commit
        between the read and the write, and the write would still land on the
        strength of what the read saw. Every caller that gates a write on a
        preceding ``SELECT`` was exposed, and several document a critical
        section they did not have. ``BEGIN IMMEDIATE`` takes the write lock up
        front, so the whole block is one transaction and ``HUB_BUSY_TIMEOUT_S``
        applies to acquiring it.

        The other process is not hypothetical: this module exists because the
        server and the ``pixlstash.libraries`` CLI open this file at the same
        moment (see the module docstring). ``self._lock`` is a ``threading``
        lock and does nothing about that.
        """
        with self._lock:
            try:
                with self._conn:
                    # Guarded because SQLite has no nested transactions: a second
                    # `BEGIN` raises. One can already be open here without any
                    # nesting in this module's own callers, because
                    # `HubDatabase.connection` hands the raw connection out and
                    # a caller that ran DML on it left a transaction behind.
                    # Deliberately only the BEGIN is skipped: `with self._conn`
                    # still owns commit and rollback, exactly as before, so this
                    # adds no second way for a hub write to be published.
                    if not self._conn.in_transaction:
                        self._conn.execute("BEGIN IMMEDIATE")
                    yield self._conn
            except sqlite3.Error as exc:
                logger.error("Hub transaction on %s rolled back: %s", self._path, exc)
                raise

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            logger.warning("Error closing hub database %s: %s", self._path, exc)
        # Guard fd released only after the connection: closing it earlier drops
        # the process's POSIX locks on the hub file (see __init__).
        guard = getattr(self, "_guard", None)
        if guard is not None:
            try:
                guard.close()
            except OSError as exc:
                logger.warning(
                    "Error closing hub location guard %s: %s", self._path, exc
                )
            self._guard = None

    def __enter__(self) -> "HubDatabase":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _configure(self) -> None:
        """Apply the connection pragmas the multi-process contract depends on."""
        # Install the wait policy before journal-mode negotiation: two processes
        # can win secure open at the same time on first startup, and the empty
        # database's WAL transition itself takes the write lock.
        self._conn.execute(f"PRAGMA busy_timeout={HUB_BUSY_TIMEOUT_S * 1000}")
        # WAL persists in the file header, so this is a no-op after the first
        # open; it is re-issued because a hub file could be restored from a
        # rollback-journal copy. SQLite's journal-mode PRAGMA can return LOCKED
        # immediately (without honoring busy_timeout) when two processes open a
        # brand-new file together, so bound that one negotiation explicitly.
        deadline = time.monotonic() + HUB_BUSY_TIMEOUT_S
        while True:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

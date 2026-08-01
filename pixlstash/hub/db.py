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
* **short transactions only** — every write in :mod:`pixlstash.hub.registry` is
  a single statement or a single ``with`` block. Nothing holds the write lock
  across user interaction or filesystem work.

The file also holds the password hash and every token hash, so it is created
0600 and its permissions are re-checked on every open.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from platformdirs import user_config_dir

from pixlstash.hub.schema import apply_migrations
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

APP_NAME = "pixlstash"

# How long a connection waits for the write lock before raising "database is
# locked". The hub's writes are tiny (single rows), so contention should be
# sub-millisecond; this is headroom for a busy filesystem, not for long
# transactions. Deliberately shorter than the vault's 30 s
# (:data:`pixlstash.database.SQLITE_BUSY_TIMEOUT_S`), which is sized for
# background batches holding a write transaction — a hub write that waits 5 s
# is a bug worth surfacing, not something to wait out.
HUB_BUSY_TIMEOUT_S = 5

# The mode the hub file must have: owner read/write only.
HUB_FILE_MODE = 0o600


class HubPermissionError(RuntimeError):
    """The hub file is readable or writable by users other than its owner."""


def default_hub_path() -> str:
    """Return the platform config path for ``hub.db``.

    Sits beside ``server-config.json`` (``pixlstash.app.SERVER_CONFIG_PATH``),
    so the whole of PixlStash's app-level state is one directory.
    """
    return os.path.join(user_config_dir(APP_NAME), "hub.db")


def check_file_mode(path: str, *, repair: bool) -> None:
    """Verify that *path* is not group- or world-accessible.

    Args:
        path: The hub file. A missing file is not an error (it is about to be
            created 0600).
        repair: When True, tighten the mode in place and log it, which is what
            the CLI does. When False, raise instead, which is what the server
            does: a hub whose permissions were loosened by something else is a
            credential-exposure event the operator must see, and silently
            fixing it hides that it happened.

    Raises:
        HubPermissionError: ``repair`` is False and the mode is too permissive.
    """
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
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

    raise HubPermissionError(
        f"Hub file {path} has mode {mode:o}; it holds the password hash and "
        f"every token hash and must be {HUB_FILE_MODE:o}. Fix it with "
        f"`chmod {HUB_FILE_MODE:o} {path}` and restart."
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
        self._path = path or default_hub_path()
        self._closed = False

        parent = Path(self._path).parent
        parent.mkdir(parents=True, exist_ok=True)

        existed = os.path.exists(self._path)
        check_file_mode(self._path, repair=repair_permissions)

        # ``check_same_thread=False`` plus an explicit lock, because this
        # connection is reached from request-handler threads as well as from
        # startup: the registry answers "which library is active" on the token
        # path. sqlite3's own guard is per-connection and would reject those
        # calls outright. Serialising here is safe precisely because the hub's
        # contract is short transactions only (see the module docstring), so no
        # caller holds the lock across I/O or user interaction.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path,
            timeout=HUB_BUSY_TIMEOUT_S,
            isolation_level="",
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        if not existed:
            # Create it locked down before the first write puts a hash in it.
            # sqlite3 honours the process umask on create, which is commonly
            # 022 (0644), so this is a narrowing, not a formality.
            os.chmod(self._path, HUB_FILE_MODE)
            logger.info("Created hub database at %s", self._path)

        self._configure()
        apply_migrations(self._conn)

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
        """
        with self._lock:
            try:
                with self._conn:
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

    def __enter__(self) -> "HubDatabase":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _configure(self) -> None:
        """Apply the connection pragmas the multi-process contract depends on."""
        # WAL persists in the file header, so this is a no-op after the first
        # open; it is re-issued because a hub file could be restored from a
        # rollback-journal copy.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(f"PRAGMA busy_timeout={HUB_BUSY_TIMEOUT_S * 1000}")

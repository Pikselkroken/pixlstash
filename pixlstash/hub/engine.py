"""A ``VaultDatabase``-shaped façade over the hub file.

:class:`~pixlstash.auth.AuthService` reaches its database through three methods
(``run_task``, ``run_immediate_read_task``, ``submit_task``), each taking a
callable whose first argument is a SQLModel ``Session``. Identity moves from the
vault to the hub by handing it one of these instead of the vault's
:class:`~pixlstash.database.VaultDatabase` - 23 call sites keep working
verbatim, and the hub's ``user`` / ``usertoken`` tables are shaped to match the
same models (see :mod:`pixlstash.hub.schema`).

**No writer thread, deliberately.** ``VaultDatabase`` serialises writes through
one thread because a vault is owned by a single process and its background
batches are long. The hub is the opposite on both counts: it is opened by the
server *and* the CLI concurrently, so in-process serialisation would buy nothing
a second process could not defeat, and its writes are single rows. Concurrency
is arbitrated where it actually has to be - SQLite WAL plus a busy timeout - and
tasks run inline on the calling thread. ``submit_task`` therefore returns an
already-completed :class:`~concurrent.futures.Future`, which keeps the
``add_done_callback`` contract callers rely on.
"""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Callable

from sqlalchemy import event
from sqlmodel import Session, create_engine

from pixlstash.hub.db import HUB_BUSY_TIMEOUT_S
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def _configure_connection(dbapi_conn, _conn_record) -> None:
    """Apply the hub's multi-process pragmas to a new pooled connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={HUB_BUSY_TIMEOUT_S * 1000}")
    cursor.close()


class HubEngine:
    """SQLModel sessions over the hub, with the vault database's task API.

    The schema is owned by :mod:`pixlstash.hub.schema` and applied by
    :class:`~pixlstash.hub.db.HubDatabase`; this class never creates tables, so
    a caller must have opened the hub once before pointing an engine at it.
    """

    def __init__(self, hub_path: str):
        """Open a connection pool against the hub at *hub_path*."""
        self._path = hub_path
        self._closed = False
        self._engine = create_engine(
            f"sqlite:///{hub_path}",
            echo=False,
            connect_args={"timeout": HUB_BUSY_TIMEOUT_S},
        )
        event.listen(self._engine, "connect", _configure_connection)

    @property
    def path(self) -> str:
        """Filesystem path of the hub this engine is bound to."""
        return self._path

    @property
    def engine(self):
        """The underlying SQLAlchemy engine."""
        return self._engine

    def submit_task(
        self, func: Callable[..., Any], *args, priority=None, **kwargs
    ) -> Future:
        """Run *func* inline and return a completed Future.

        Args:
            func: Callable taking a ``Session`` as its first argument.
            priority: Accepted and ignored. The vault's queue orders work
                against long background batches; the hub has no queue and no
                batches, so there is nothing to order. Kept in the signature so
                the vault's call sites need no edit.

        Returns:
            A Future that is already done, holding the result or the exception.
        """
        future: Future = Future()
        try:
            future.set_result(self._run(func, *args, **kwargs))
        except Exception as exc:
            logger.error(
                "Hub task %s on %s failed: %s",
                getattr(func, "__name__", repr(func)),
                self._path,
                exc,
            )
            future.set_exception(exc)
        return future

    def run_task(self, func: Callable[..., Any], *args, priority=None, **kwargs) -> Any:
        """Run *func* and return its result, raising whatever it raised."""
        return self._run(func, *args, **kwargs)

    def run_immediate_read_task(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Run a read and return its result.

        Identical to :meth:`run_task` here. The vault distinguishes the two so
        reads can bypass its writer queue (``docs/backend_architecture.md``
        §16.4); with no queue to bypass, the distinction is only a statement of
        intent by the caller.
        """
        return self._run(func, *args, **kwargs)

    @staticmethod
    def result_or_throw(future: Future, timeout: float | None = None) -> Any:
        """Return a future's result, propagating its exception."""
        return future.result(timeout)

    def close(self) -> None:
        """Dispose of the connection pool. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        self._engine.dispose()

    def _run(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Open a session, run *func*, and roll back if it raises.

        One session per call, never shared across calls or threads. Commits are
        the callable's own business, matching the vault's contract.
        """
        with Session(self._engine) as session:
            try:
                return func(session, *args, **kwargs)
            except Exception:
                session.rollback()
                raise

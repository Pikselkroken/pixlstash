"""SQLite connection settings on the vault engine (issue #651, tier 1).

These assert the settings on a *real* connection handed out by
``VaultDatabase._engine``, not that some function was called. Every test here
fails against the pre-fix code, which built the engine with no ``connect_args``
(sqlite3's 5 s busy timeout) and set no ``cache_size`` (SQLite's 2 MiB default).

See docs/backend_architecture.md §13.
"""

import os
import sqlite3
import threading
import time

import pytest

from pixlstash.database import (
    SQLITE_BUSY_TIMEOUT_S,
    SQLITE_CACHE_SIZE_KIB,
    VaultDatabase,
)


@pytest.fixture
def vault_db(tmp_path):
    """A real VaultDatabase (engine + migrations) on a throwaway file."""
    db = VaultDatabase(str(tmp_path / "vault.db"))
    try:
        yield db
    finally:
        db.close()


def _pragma(engine, name):
    with engine.connect() as conn:
        return conn.exec_driver_sql(f"PRAGMA {name}").scalar()


def test_busy_timeout_is_configured_on_a_real_connection(vault_db):
    """The pool hands out connections with the 30 s busy timeout, not 5 s."""
    assert _pragma(vault_db._engine, "busy_timeout") == SQLITE_BUSY_TIMEOUT_S * 1000
    # Guard against the constant being lowered back to sqlite3's default.
    assert SQLITE_BUSY_TIMEOUT_S > 5


def test_cache_size_is_configured_on_a_real_connection(vault_db):
    """``init_database`` raises the page cache above SQLite's 2 MiB default."""
    cache_size = _pragma(vault_db._engine, "cache_size")
    assert cache_size == SQLITE_CACHE_SIZE_KIB
    # Negative means KiB rather than pages; -2000 (2 MiB) is the default we
    # are moving off, so anything at or above it is a no-op.
    assert cache_size < -2000


def test_settings_apply_to_every_pooled_connection(vault_db):
    """Not just the first connection: the pool opens up to 15 of them."""
    conns = [vault_db._engine.connect() for _ in range(5)]
    try:
        for conn in conns:
            assert (
                conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
                == SQLITE_BUSY_TIMEOUT_S * 1000
            )
            assert (
                conn.exec_driver_sql("PRAGMA cache_size").scalar()
                == SQLITE_CACHE_SIZE_KIB
            )
    finally:
        for conn in conns:
            conn.close()


def test_existing_pragmas_are_untouched(vault_db):
    """Regression guard: WAL, synchronous and FK enforcement still applied."""
    assert _pragma(vault_db._engine, "journal_mode") == "wal"
    assert _pragma(vault_db._engine, "synchronous") == 1  # NORMAL
    assert _pragma(vault_db._engine, "foreign_keys") == 1


def test_writer_waits_past_the_sqlite_default_instead_of_failing(vault_db):
    """Behavioural proof, not a PRAGMA readback.

    A competing connection holds the write lock for longer than sqlite3's 5 s
    default busy timeout. With the default the engine's write raises
    "database is locked"; with the configured 30 s it waits and succeeds.
    """
    hold_seconds = 6.0
    assert hold_seconds > 5, "must outlast sqlite3's default timeout to be a test"
    assert SQLITE_BUSY_TIMEOUT_S > hold_seconds, "must not outlast ours"

    db_path = vault_db._db_path
    assert os.path.exists(db_path)

    lock_taken = threading.Event()
    lock_failed = []

    def hold_write_lock():
        blocker = sqlite3.connect(db_path, timeout=1)
        try:
            blocker.execute("BEGIN EXCLUSIVE")
            lock_taken.set()
            time.sleep(hold_seconds)
            blocker.rollback()
        except Exception as exc:  # pragma: no cover - surfaced via lock_failed
            lock_failed.append(exc)
            lock_taken.set()
        finally:
            blocker.close()

    holder = threading.Thread(target=hold_write_lock, daemon=True)
    holder.start()
    try:
        assert lock_taken.wait(timeout=10), "blocker never acquired the write lock"
        assert not lock_failed, f"blocker failed to take the lock: {lock_failed}"

        started = time.perf_counter()
        with vault_db._engine.begin() as conn:
            # No-op write that still needs the write lock, on a table every
            # migrated vault has.
            conn.exec_driver_sql("UPDATE alembic_version SET version_num=version_num")
        elapsed = time.perf_counter() - started
    finally:
        holder.join(timeout=hold_seconds + 10)

    assert elapsed >= 5.0, (
        f"write returned after {elapsed:.2f}s; it cannot have waited out the "
        f"{hold_seconds}s lock, so the busy timeout is not in effect"
    )

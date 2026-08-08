"""Tests for the hub engine and for AuthService running against the hub.

The point of :class:`pixlstash.hub.engine.HubEngine` is that identity can move
out of the vault by re-pointing one constructor argument, so these tests assert
the real :class:`~pixlstash.auth.AuthService` works when handed one — that is
the claim the hub schema's shape exists to support.
"""

import os
import sqlite3
import stat
import threading

import pytest
from sqlmodel import select

from pixlstash.auth import AuthService
from pixlstash.db_models import User
from pixlstash.db_models.user_token import UserToken
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.db import HubPermissionError
from pixlstash.hub.engine import HubEngine
from pixlstash.trusted_sqlite import (
    TrustedSQLiteLocation,
    TrustedSQLiteLocationError,
)


@pytest.fixture
def hub_path(tmp_path):
    """A hub file that has been created and migrated to the current schema."""
    path = str(tmp_path / "hub.db")
    HubDatabase(path).close()
    return path


@pytest.fixture
def library_uuid(hub_path):
    """A registered library, so tokens have something to be stamped with."""
    import sqlite3

    value = "33333333-3333-4333-8333-333333333333"
    conn = sqlite3.connect(hub_path)
    try:
        conn.execute(
            "INSERT INTO library (uuid, name, path, created_at, attached_at, "
            "is_active) VALUES (?, 'Test', '/tmp/test-library', ?, ?, 1)",
            (value, "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return value


@pytest.fixture
def engine(hub_path):
    """A HubEngine over the temporary hub."""
    hub_engine = HubEngine(hub_path)
    yield hub_engine
    hub_engine.close()


@pytest.fixture
def auth(engine, tmp_path):
    """A real AuthService backed by the hub rather than by a vault."""
    return AuthService(
        engine,
        {"cookie_samesite": "Lax", "cookie_secure": False},
        str(tmp_path / "server-config.json"),
        __import__("logging").getLogger("test-auth"),
    )


class TestHubEngine:
    def test_run_task_returns_the_callables_result(self, engine):
        assert engine.run_task(lambda session: 42) == 42

    def test_run_task_propagates_the_callables_exception(self, engine):
        with pytest.raises(ValueError):

            def boom(_session):
                raise ValueError("no")

            engine.run_task(boom)

    def test_submit_task_returns_a_completed_future(self, engine):
        future = engine.submit_task(lambda session: "done")
        assert future.done()
        assert future.result() == "done"

    def test_submit_task_captures_the_exception_in_the_future(self, engine):
        def boom(_session):
            raise RuntimeError("nope")

        future = engine.submit_task(boom)
        assert future.done()
        with pytest.raises(RuntimeError):
            future.result()

    def test_done_callbacks_still_fire(self, engine):
        # auth.py refreshes ``last_used_at`` through submit_task().
        # add_done_callback(), so an inline future must still honour it.
        seen = []
        engine.submit_task(lambda session: None).add_done_callback(seen.append)
        assert len(seen) == 1

    def test_priority_argument_is_accepted_and_ignored(self, engine):
        from pixlstash.database import DBPriority

        assert engine.run_task(lambda s: 1, priority=DBPriority.IMMEDIATE) == 1

    def test_writes_are_visible_to_a_later_session(self, engine):
        engine.run_task(lambda session: _add_user(session, "alice"))
        found = engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).first()
        )
        assert found.username == "alice"

    def test_a_failed_write_is_rolled_back(self, engine):
        def add_then_fail(session):
            _add_user(session, "bob", commit=False)
            raise RuntimeError("changed my mind")

        with pytest.raises(RuntimeError):
            engine.run_task(add_then_fail)

        remaining = engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).all()
        )
        assert remaining == []

    def test_wal_is_enabled_on_pooled_connections(self, engine):
        from sqlalchemy import text

        with engine.engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert mode.lower() == "wal"


class TestHubFileSecurity:
    def test_first_sqlite_open_sees_an_already_private_file(
        self, tmp_path, monkeypatch
    ):
        path = str(tmp_path / "hub.db")
        real_connect = sqlite3.connect
        seen_modes = []

        def observing_connect(database, *args, **kwargs):
            if str(database) == path:
                seen_modes.append(stat.S_IMODE(os.lstat(path).st_mode))
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", observing_connect)
        HubDatabase(path).close()

        assert seen_modes
        assert seen_modes[0] == 0o600

    def test_the_hub_opens_on_a_platform_without_fchmod(self, tmp_path, monkeypatch):
        """Windows has no ``os.fchmod``, and every backend test opens a hub.

        The unguarded call made `AttributeError: module 'os' has no attribute
        'fchmod'` the failure of every test in both Windows CI shards. Linux is
        the only place this can be caught before Windows CI runs, so the
        platform difference is simulated rather than waited for.
        """
        monkeypatch.delattr(os, "fchmod", raising=False)
        path = str(tmp_path / "hub.db")

        HubDatabase(path).close()

        # Where the bits mean something they must still be exactly 0600: the
        # mode handed to os.open() carries them when fchmod cannot.
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600

    def test_a_symlink_hub_is_refused_without_touching_its_target(self, tmp_path):
        target = tmp_path / "target.db"
        target.write_bytes(b"do not open")
        link = tmp_path / "hub.db"
        link.symlink_to(target)

        with pytest.raises(HubPermissionError, match="symlink"):
            HubDatabase(str(link))

        assert target.read_bytes() == b"do not open"

    def test_a_non_regular_hub_path_is_refused(self, tmp_path):
        path = tmp_path / "hub.db"
        path.mkdir()

        with pytest.raises(HubPermissionError, match="non-regular"):
            HubDatabase(str(path))

    def test_simultaneous_first_open_never_observes_a_creation_race(self, tmp_path):
        """Every concurrent opener must succeed, on every attempt.

        Four openers AND repeated trials. Both matter, and the second was the
        one that had been missing: the original two-thread single-trial version
        passed 20/20 while the defect was live, and so did a four-thread single
        trial. The window is only hit ~22% of the time, so a test that runs one
        trial is a coin flip, not a regression test. Twenty trials makes a live
        defect essentially certain to surface.

        The defect: verify_after_open compared the parent directory's
        mtime/ctime to a snapshot, and SQLite creating our own -wal/-shm moves
        both, so a second opener was refused as though the namespace had been
        tampered with.
        """
        openers = 4
        trials = 20
        failures = []

        for trial in range(trials):
            path = str(tmp_path / f"hub-{trial}.db")
            barrier = threading.Barrier(openers)

            def open_hub():
                try:
                    barrier.wait(timeout=10)
                    HubDatabase(path).close()
                except Exception as exc:  # pragma: no cover - asserted below
                    failures.append(f"trial {trial}: {type(exc).__name__}: {exc}")

            threads = [threading.Thread(target=open_hub) for _ in range(openers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

            assert all(not thread.is_alive() for thread in threads)
            assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600

        assert failures == [], (
            f"{len(failures)}/{trials} trials refused a concurrent opener: "
            f"{failures[:3]}"
        )

    def test_path_replacement_during_sqlite_open_is_refused_before_schema_writes(
        self, tmp_path, monkeypatch
    ):
        path = str(tmp_path / "hub.db")
        real_connect = sqlite3.connect
        replaced = False

        def replacing_connect(database, *args, **kwargs):
            nonlocal replaced
            if not replaced and (str(database) == path or "/fd/" in str(database)):
                replaced = True
                os.unlink(path)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(fd, b"replacement")
                os.close(fd)
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", replacing_connect)
        with pytest.raises(HubPermissionError, match="changed while"):
            HubDatabase(path)

        assert open(path, "rb").read() == b"replacement"

    def test_a_same_uid_swap_during_open_is_a_documented_non_goal(
        self, tmp_path, monkeypatch
    ):
        """Swap-away-and-back by the SAME uid is out of scope, on purpose.

        This replaces a test that asserted the swap WAS caught. It was caught,
        by comparing the parent directory's mtime/ctime, and that comparison
        also refused ~22% of concurrent opens because SQLite creating our own
        -wal/-shm is indistinguishable from tampering when you watch a
        directory's timestamps. The trade was withdrawn (2026-08-07, human
        decision after principal review) because the attacker it stopped is one
        the threat model excludes: these files are mode 600 owned by this uid,
        so a same-uid process can already read and rewrite them directly, with
        no race to win. See the module docstring of pixlstash/trusted_sqlite.py.

        The test is kept, inverted, so the boundary is asserted rather than
        merely written down: if someone reintroduces the timestamp comparison,
        this fails and points at the reasoning.
        """
        path = str(tmp_path / "hub.db")
        decoy = str(tmp_path / "decoy.db")
        held_original = str(tmp_path / "held-original.db")
        held_decoy = str(tmp_path / "held-decoy.db")
        for database_path, value in ((path, "original"), (decoy, "decoy")):
            hub = HubDatabase(database_path)
            with hub.transaction() as conn:
                conn.execute("CREATE TABLE connection_probe (value TEXT)")
                conn.execute("INSERT INTO connection_probe VALUES (?)", (value,))
            hub.close()

        real_connect = sqlite3.connect
        swapped = False

        def swapping_connect(database, *args, **kwargs):
            nonlocal swapped
            if not swapped and (str(database) == path or "/fd/" in str(database)):
                swapped = True
                os.rename(path, held_original)
                os.rename(decoy, path)
                connection = real_connect(database, *args, **kwargs)
                os.rename(path, held_decoy)
                os.rename(held_original, path)
                return connection
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", swapping_connect)
        # Not raising is the documented outcome, not an oversight.
        HubDatabase(path).close()

        # Assert the swap actually ran. Without this the test passes vacuously
        # the moment the interception stops matching (a changed path, or
        # HubDatabase no longer routing through sqlite3.connect), and would then
        # assert nothing at all while still looking green.
        assert swapped, "the swap never fired; this test proved nothing"

    def test_a_directory_that_turns_writable_after_open_is_refused(self, tmp_path):
        """The property that replaced the timestamp snapshot, asserted directly.

        verify_after_open now re-runs the ownership/permission check instead of
        comparing timestamps. That is strictly more than the old comparison
        managed: a chmod between open and verify used to be caught only
        incidentally, through the ctime it happened to bump. Here it is the
        thing being tested.
        """
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        path = str(directory / "hub.db")
        guard = TrustedSQLiteLocation.open(path, private=True, create=True)
        try:
            guard.verify_after_open()  # clean while the directory is private

            os.chmod(directory, 0o777)
            with pytest.raises(TrustedSQLiteLocationError, match="world-writable"):
                guard.verify_after_open()
        finally:
            os.chmod(directory, 0o700)
            guard.close()


class TestAuthServiceOnTheHub:
    def test_ensure_user_creates_the_owner_in_the_hub(self, auth, engine):
        user = auth.ensure_user()

        assert user is not None
        stored = engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).all()
        )
        assert len(stored) == 1
        assert stored[0].id == user.id

    def test_ensure_user_is_idempotent(self, auth):
        first = auth.ensure_user()
        second = auth.ensure_user()
        assert first.id == second.id

    def test_the_owner_row_lands_in_the_hub_file_not_a_vault(self, auth, hub_path):
        auth.ensure_user()

        import sqlite3

        conn = sqlite3.connect(hub_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        finally:
            conn.close()
        assert count == 1

    def test_credentials_persist_in_the_hub(self, auth, hub_path):
        from passlib.hash import bcrypt

        auth.ensure_user()
        auth.set_username("owner")
        auth.set_password_hash(bcrypt.hash("correct horse battery staple"))

        import sqlite3

        conn = sqlite3.connect(hub_path)
        try:
            username, password_hash = conn.execute(
                "SELECT username, password_hash FROM user"
            ).fetchone()
        finally:
            conn.close()

        assert username == "owner"
        assert bcrypt.verify("correct horse battery staple", password_hash)

    def test_a_token_stored_in_the_hub_resolves_back_through_auth(
        self, auth, engine, library_uuid
    ):
        """The token lookup path (prefix fetch + bcrypt verify) on hub storage."""
        from passlib.hash import bcrypt

        user = auth.ensure_user()
        token_value = "abcdef0123456789abcdef0123456789"

        def add_token(session):
            session.add(
                UserToken(
                    user_id=user.id,
                    library_uuid=library_uuid,
                    token_hash=bcrypt.hash(token_value),
                    token_prefix=token_value[:8],
                    created_at=__import__("datetime").datetime.utcnow(),
                    description="test token",
                    scope="ALL",
                )
            )
            session.commit()

        engine.run_task(add_token)

        resolved = auth.token_from_value(token_value)
        assert resolved is not None
        assert resolved.description == "test token"
        assert auth.token_from_value("not-the-right-token") is None

    def test_a_token_without_a_library_is_rejected_by_the_database(self, auth, engine):
        """There is no such thing as an unpinned token.

        The hub column is NOT NULL, so a code path that forgets to stamp a
        token fails loudly at write time instead of quietly minting one that
        would change what it grants whenever the owner switched library.
        """
        import sqlalchemy.exc
        from passlib.hash import bcrypt

        user = auth.ensure_user()

        def add_unstamped_token(session):
            session.add(
                UserToken(
                    user_id=user.id,
                    token_hash=bcrypt.hash("x" * 32),
                    token_prefix="xxxxxxxx",
                    created_at=__import__("datetime").datetime.utcnow(),
                    scope="ALL",
                )
            )
            session.commit()

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            engine.run_task(add_unstamped_token)

    def test_a_token_cannot_name_a_library_that_does_not_exist(self, auth, engine):
        """The foreign key, so a stamp always resolves to a real library."""
        import sqlalchemy.exc
        from passlib.hash import bcrypt

        user = auth.ensure_user()

        def add_token(session):
            session.add(
                UserToken(
                    user_id=user.id,
                    library_uuid="00000000-0000-4000-8000-000000000000",
                    token_hash=bcrypt.hash("x" * 32),
                    token_prefix="xxxxxxxx",
                    created_at=__import__("datetime").datetime.utcnow(),
                    scope="ALL",
                )
            )
            session.commit()

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            engine.run_task(add_token)


def _add_user(session, username, *, commit=True):
    """Insert a user row through the shared SQLModel model."""
    user = User(username=username)
    session.add(user)
    if commit:
        session.commit()
        session.refresh(user)
    return user


def test_hub_schema_carries_every_column_the_user_model_declares(hub_path):
    """The shared-model contract, asserted rather than assumed.

    If a column is added to ``User`` without being added to the hub schema,
    every ``SELECT user.*`` against the hub breaks at runtime. This fails at
    the moment the model changes instead.
    """
    import sqlite3

    conn = sqlite3.connect(hub_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info('user')")}
    finally:
        conn.close()

    declared = set(User.model_fields) - {"tokens"}
    assert declared <= columns, f"hub `user` is missing: {sorted(declared - columns)}"


def test_hub_schema_carries_every_column_the_token_model_declares(hub_path):
    """Same contract for ``UserToken``, plus the hub-only ``library_id``."""
    import sqlite3

    conn = sqlite3.connect(hub_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info('usertoken')")}
    finally:
        conn.close()

    declared = set(UserToken.model_fields) - {"user"}
    assert declared <= columns, (
        f"hub `usertoken` is missing: {sorted(declared - columns)}"
    )
    assert "library_uuid" in columns


def test_hub_file_is_the_only_database_touched(hub_path, tmp_path):
    """Opening the hub must not create a vault next to it."""
    HubEngine(hub_path).close()
    assert not os.path.exists(os.path.join(str(tmp_path), "vault.db"))

"""Tests for the hub engine and for AuthService running against the hub.

The point of :class:`pixlstash.hub.engine.HubEngine` is that identity can move
out of the vault by re-pointing one constructor argument, so these tests assert
the real :class:`~pixlstash.auth.AuthService` works when handed one — that is
the claim the hub schema's shape exists to support.
"""

import os

import pytest
from sqlmodel import select

from pixlstash.auth import AuthService
from pixlstash.db_models import User
from pixlstash.db_models.user_token import UserToken
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.engine import HubEngine


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

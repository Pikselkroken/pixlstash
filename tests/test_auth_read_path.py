"""Auth reads bypass the serialised DB writer queue (issue #651).

Every authenticated request used to resolve its principal through
``VaultDatabase.run_task``, i.e. the single-writer queue.  ``DBPriority.IMMEDIATE``
only wins queue *ordering*: the worker loop still runs the in-flight task's
session to completion first, so an API request inherited the full duration of
whatever background batch was committing.  The auth reads (owner lookup, token
candidate fetch, guest-session cookie lookup) now run on
``run_immediate_read_task`` instead, and the ``last_used_at`` refresh is
fire-and-forget.

The security-critical property that must survive that move is **revoke → immediate
401**.  Serialising through the writer queue used to be what guaranteed it; the
replacement guarantee is two-part and is what these tests pin:

1. Every revocation path commits the delete (synchronously, on the writer queue)
   *before* it flushes the token cache, so the next lookup's read - which now runs
   on the read path - necessarily observes the committed delete.
2. ``_flush_token_cache`` bumps ``_token_cache_epoch``, so a lookup that read the
   token row just *before* the delete committed cannot install its stale result
   into the cache just *after* the flush and keep a revoked token alive for the
   full 5-minute cache TTL.  (That window existed before this change too - the
   cache write has always been outside the queue - so closing it is a strict
   improvement, not a repair of something the refactor broke.)

Both directions are asserted: revoked/deleted credentials are refused, and valid
tokens, cookie sessions and guest-session cookies keep working.  Over-blocking
would be its own regression.
"""

import hashlib
import tempfile
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

import pixlstash.auth as auth_module
from pixlstash.database import DBPriority
from pixlstash.db_models import User, UserToken
from pixlstash.db_models.guest_session import GuestSession
from pixlstash.server import Server

# How long a simulated background batch holds the single writer thread. Long
# enough that a request which queued behind it is unmistakable.
SLOW_WRITE_S = 3.0
# An auth read that bypasses the queue must return in a small fraction of that.
# Deliberately generous (bcrypt.verify alone is ~200 ms, and CI machines are
# loaded), while still far below SLOW_WRITE_S.
FAST_REQUEST_S = 2.0


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server():
    """One Server for the module - building it runs migrations and vault start-up."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as srv:
            yield srv


@pytest.fixture
def owner_client(server):
    """A TestClient logged in as the owner, starting from clean auth state."""

    def _wipe_vault(session: Session):
        # Guest sessions stay per-vault by design (plan §9), so this one is not
        # identity and does not move to the hub.
        session.exec(delete(GuestSession))
        session.commit()

    def _wipe_identity(session: Session):
        session.exec(delete(UserToken))
        session.exec(delete(User))
        session.commit()

    server.vault.db.run_task(_wipe_vault)
    server.hub_engine.run_task(_wipe_identity)
    server.auth.password_hash = None
    server.auth.username = None
    server.auth.user = None
    server.auth.active_session_ids = {}
    server.auth._flush_token_cache()
    server.auth.ensure_user()

    client = TestClient(server.api)
    response = client.post(
        "/login", json={"username": "owner651", "password": "example-owner-password"}
    )
    assert response.status_code == 200, response.text
    yield client


def _mint_read_token(owner_client) -> tuple[str, int]:
    """Create a global READ share token and return ``(value, id)``."""
    response = owner_client.post(
        "/users/me/token", json={"description": "issue-651", "scope": "READ"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["token"], body["token_id"]


def _token_public_id(server, token_id: int) -> str:
    """Return a token's public id, which is how the vault's guest tables name it.

    Guest sessions live in the vault and the token lives in the hub, so they
    reference it by public id rather than by a foreign key (see GuestSession).
    """

    def _read(session: Session):
        return session.get(UserToken, token_id).public_id

    return server.hub_engine.run_immediate_read_task(_read)


def _token_client(server) -> TestClient:
    """A TestClient with no owner cookie, so only the Bearer token authenticates."""
    return TestClient(server.api)


@contextmanager
def _writer_busy_for(db, seconds: float = SLOW_WRITE_S):
    """Occupy the single DB writer thread with a slow committing transaction.

    Mirrors a background batch commit: the worker loop runs this task's session to
    completion before it picks up anything else, whatever the queued priority.
    """
    started = threading.Event()

    def _slow_write(session: Session):
        # Touch the DB so this is a real transaction, not just a sleep.
        session.exec(select(User)).first()
        started.set()
        time.sleep(seconds)
        session.commit()
        return True

    future = db.submit_task(_slow_write, priority=DBPriority.IMMEDIATE)
    assert started.wait(timeout=30.0), "the slow writer task never started"
    try:
        yield
    finally:
        # Surface a failure in the background write rather than swallowing it.
        assert future.result(timeout=seconds + 60.0) is True


def _get_protected(client, token_value: str):
    """Issue an authenticated GET and return ``(response, elapsed_seconds)``."""
    started = time.monotonic()
    # Guest enrichment is intentionally skipped on HUB_ONLY routes such as
    # /protected. Exercise an active-library read route for this vault lookup.
    response = client.get(
        "/users/me/penalised-tags",
        headers={"Authorization": f"Bearer {token_value}"},
    )
    return response, time.monotonic() - started


# ---------------------------------------------------------------------------
# Revocation: the security-critical direction
# ---------------------------------------------------------------------------


def test_revoked_token_is_401_immediately_while_a_slow_write_is_in_flight(
    server, owner_client
):
    """Revoke → immediate 401, with the writer thread busy on both sides of it.

    This is the regression test for the TOCTOU the #651 refactor could have
    introduced.  A revocation performed on an idle server would not exercise the
    bug at all, so the writer is deliberately held busy while the revocation
    lands AND while the post-revocation request is served.
    """
    token_value, token_id = _mint_read_token(owner_client)

    # Positive control: the token authenticates, and the verification is cached.
    response, _ = _get_protected(_token_client(server), token_value)
    assert response.status_code == 200, response.text
    digest = hashlib.sha256(token_value.encode()).hexdigest()
    with server.auth._token_cache_lock:
        assert digest in server.auth._token_cache, (
            "the token cache was not warmed, so this test would not exercise "
            "the cache-invalidation path at all"
        )

    # The revocation itself lands while a background batch holds the writer.
    with _writer_busy_for(server.vault.db):
        response = owner_client.delete(f"/users/me/token/{token_id}")
        assert response.status_code == 200, response.text

    # ...and the very next request is served while the writer is busy again.
    with _writer_busy_for(server.vault.db):
        response, elapsed = _get_protected(_token_client(server), token_value)

    assert response.status_code == 401, (
        f"a revoked token still authenticated: {response.status_code} {response.text}"
    )
    assert elapsed < FAST_REQUEST_S, (
        f"the auth path waited {elapsed:.2f}s on the writer queue; it must not "
        f"queue behind background writes (issue #651)"
    )


def test_revoking_by_resource_is_401_immediately_while_a_slow_write_is_in_flight(
    server, owner_client
):
    """The sibling bulk-revoke path (``DELETE /users/me/tokens/by-resource``)."""
    response = owner_client.post(
        "/users/me/token",
        json={
            "description": "issue-651 scoped",
            "scope": "READ",
            "resource_type": "picture",
            "resource_id": 4242,
        },
    )
    assert response.status_code == 200, response.text
    token_value = response.json()["token"]

    # Warm the cache with a successful request.
    response, _ = _get_protected(_token_client(server), token_value)
    assert response.status_code == 200, response.text

    response = owner_client.delete(
        "/users/me/tokens/by-resource",
        params={"resource_type": "picture", "resource_id": 4242},
    )
    assert response.status_code == 200, response.text
    assert response.json()["deleted_count"] == 1

    with _writer_busy_for(server.vault.db):
        response, elapsed = _get_protected(_token_client(server), token_value)

    assert response.status_code == 401, response.text
    assert elapsed < FAST_REQUEST_S, f"auth queued behind the writer ({elapsed:.2f}s)"


def test_flushing_the_token_cache_bumps_the_revocation_epoch(server, owner_client):
    """``_flush_token_cache`` must clear entries *and* invalidate in-flight lookups."""
    auth = server.auth
    token_value, _ = _mint_read_token(owner_client)
    assert auth._token_from_value(token_value) is not None
    digest = hashlib.sha256(token_value.encode()).hexdigest()

    with auth._token_cache_lock:
        assert digest in auth._token_cache
        epoch_before = auth._token_cache_epoch

    auth._flush_token_cache()

    with auth._token_cache_lock:
        assert auth._token_cache == {}
        assert auth._token_cache_epoch == epoch_before + 1


def test_a_lookup_racing_a_revocation_does_not_repopulate_the_cache(
    server, owner_client
):
    """The epoch guard: a stale in-flight lookup must not survive the flush.

    Without it, this interleaving keeps a revoked token authenticating for the
    full ``_TOKEN_CACHE_TTL`` (5 minutes), because the cache write happens
    outside the writer queue and so is not ordered against the revocation:

        lookup: read token row  →  ...  →  write cache entry
        revoke:                    commit delete + flush cache
    """
    auth = server.auth
    token_value, _ = _mint_read_token(owner_client)
    auth._flush_token_cache()  # force a real DB lookup, not a cache hit
    digest = hashlib.sha256(token_value.encode()).hexdigest()

    reached_verify = threading.Event()
    may_continue = threading.Event()
    real_bcrypt = auth_module.bcrypt

    def _blocking_verify(secret, hashed):
        matched = real_bcrypt.verify(secret, hashed)
        if matched:
            # We are past the DB read and about to write the cache entry.
            reached_verify.set()
            assert may_continue.wait(timeout=30.0)
        return matched

    result = {}

    def _lookup():
        result["token"] = auth._token_from_value(token_value)

    stub = SimpleNamespace(verify=_blocking_verify, hash=real_bcrypt.hash)
    with patch.object(auth_module, "bcrypt", stub):
        worker = threading.Thread(target=_lookup, name="racing-token-lookup")
        worker.start()
        try:
            assert reached_verify.wait(timeout=30.0)
            # A revocation lands in the middle of the lookup.
            auth._flush_token_cache()
        finally:
            may_continue.set()
            worker.join(timeout=30.0)
        assert not worker.is_alive()

    # This request began before the revocation, so refusing it would be
    # over-blocking; it is allowed to succeed.
    assert result["token"] is not None
    # But its result must NOT have been cached, so the *next* request re-reads
    # the database and sees the revocation.
    with auth._token_cache_lock:
        assert digest not in auth._token_cache, (
            "a lookup that raced a revocation repopulated the token cache; a "
            "revoked token would keep working for the full cache TTL"
        )


# ---------------------------------------------------------------------------
# Positive direction: valid credentials must keep working, and not queue
# ---------------------------------------------------------------------------


def test_valid_token_authenticates_while_a_slow_write_is_in_flight(
    server, owner_client
):
    """A live token still authenticates, and does not wait for the writer."""
    token_value, _ = _mint_read_token(owner_client)
    server.auth._flush_token_cache()  # exercise the DB lookup, not the cache

    with _writer_busy_for(server.vault.db):
        response, elapsed = _get_protected(_token_client(server), token_value)

    assert response.status_code == 200, response.text
    assert elapsed < FAST_REQUEST_S, f"auth queued behind the writer ({elapsed:.2f}s)"


def test_cookie_session_authenticates_while_a_slow_write_is_in_flight(
    server, owner_client
):
    """The owner's cookie session is unaffected and equally unqueued."""
    with _writer_busy_for(server.vault.db):
        started = time.monotonic()
        response = owner_client.get("/users/me/auth")
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert response.json()["username"] == "owner651"
    assert elapsed < FAST_REQUEST_S, f"auth queued behind the writer ({elapsed:.2f}s)"


def test_auth_reads_submit_nothing_to_the_writer_queue(server, owner_client):
    """Direct evidence: the auth read helpers no longer call ``run_task`` at all.

    The spy filters on the calling thread so concurrent background work on the
    WorkPlanner threads cannot make this flaky.
    """
    auth = server.auth
    token_value, _ = _mint_read_token(owner_client)
    auth._flush_token_cache()

    db = server.vault.db
    real_run_task = db.run_task
    test_thread = threading.get_ident()
    queued: list[str] = []

    def _spy_run_task(func, *args, **kwargs):
        if threading.get_ident() == test_thread:
            queued.append(getattr(func, "__name__", repr(func)))
        return real_run_task(func, *args, **kwargs)

    user = auth.get_user()
    assert user is not None
    stub_request = SimpleNamespace(
        state=SimpleNamespace(auth_user_id=user.id), cookies={}, headers={}
    )

    with patch.object(db, "run_task", _spy_run_task):
        assert auth.get_user() is not None
        assert auth._token_from_value(token_value) is not None
        assert auth.get_user_for_request(stub_request).id == user.id

    assert queued == [], f"auth still uses the serialised writer queue: {queued}"


def test_last_used_at_is_still_recorded_off_the_critical_path(server, owner_client):
    """The debounced ``last_used_at`` write still lands - just not synchronously."""
    token_value, token_id = _mint_read_token(owner_client)
    server.auth._flush_token_cache()

    response, _ = _get_protected(_token_client(server), token_value)
    assert response.status_code == 200, response.text

    # It is fire-and-forget, so poll briefly rather than assuming it committed
    # before the response was written.
    def _read_last_used(session: Session):
        row = session.get(UserToken, token_id)
        return row.last_used_at if row is not None else None

    deadline = time.monotonic() + 30.0
    last_used = None
    while time.monotonic() < deadline:
        # Tokens live in the hub now, so the refresh lands there.
        last_used = server.hub_engine.run_immediate_read_task(_read_last_used)
        if last_used is not None:
            break
        time.sleep(0.05)

    assert last_used is not None, (
        "last_used_at was never written; the background refresh was lost"
    )


# ---------------------------------------------------------------------------
# Guest sessions - different semantics from token auth, so pinned separately
# ---------------------------------------------------------------------------


def _insert_guest_session(
    server, session_id: str, token_public_id: str, cookie_token: str
):
    def _insert(session: Session):
        session.add(
            GuestSession(
                session_id=session_id,
                token_public_id=token_public_id,
                cookie_token=cookie_token,
            )
        )
        session.commit()

    server.vault.db.run_task(_insert)


def _delete_guest_session(server, session_id: str):
    def _delete(session: Session):
        row = session.get(GuestSession, session_id)
        if row is not None:
            session.delete(row)
            session.commit()

    server.vault.db.run_task(_delete)


@contextmanager
def _recorded_guest_activity(server):
    """Capture the session ids the middleware resolved from the guest cookie.

    ``record_guest_activity`` is called only when the cookie resolved to a live
    ``GuestSession`` row, so it is a precise observable for the lookup.
    """
    seen: list[str] = []
    real_record = server.auth.record_guest_activity

    def _spy(session_id: str) -> None:
        seen.append(session_id)
        real_record(session_id)

    with patch.object(server.auth, "record_guest_activity", _spy):
        yield seen


def test_guest_session_cookie_resolves_while_a_slow_write_is_in_flight(
    server, owner_client
):
    """A live guest session still resolves, without queueing behind the writer."""
    token_value, token_id = _mint_read_token(owner_client)
    _insert_guest_session(
        server, "guest-651", _token_public_id(server, token_id), "guest-cookie-651"
    )

    client = _token_client(server)
    client.cookies.set("guest_session", "guest-cookie-651")

    with _recorded_guest_activity(server) as seen:
        with _writer_busy_for(server.vault.db):
            response, elapsed = _get_protected(client, token_value)

    assert response.status_code == 200, response.text
    assert seen == ["guest-651"], f"guest cookie did not resolve (recorded: {seen})"
    assert elapsed < FAST_REQUEST_S, (
        f"the guest-session lookup queued behind the writer ({elapsed:.2f}s)"
    )


def test_deleted_guest_session_stops_resolving_immediately(server, owner_client):
    """Guest sessions carry no cache, so removal takes effect on the next request.

    This is the guest-side analogue of revoke → immediate 401: the middleware
    re-reads ``GuestSession`` by ``cookie_token`` on every request, so there is
    no invalidation step that the move to the read path could get wrong.
    """
    token_value, token_id = _mint_read_token(owner_client)
    _insert_guest_session(
        server,
        "guest-651-gone",
        _token_public_id(server, token_id),
        "guest-cookie-651-gone",
    )

    client = _token_client(server)
    client.cookies.set("guest_session", "guest-cookie-651-gone")

    with _recorded_guest_activity(server) as seen:
        response, _ = _get_protected(client, token_value)
    assert response.status_code == 200, response.text
    assert seen == ["guest-651-gone"]

    _delete_guest_session(server, "guest-651-gone")

    with _recorded_guest_activity(server) as seen:
        with _writer_busy_for(server.vault.db):
            response, elapsed = _get_protected(client, token_value)

    # The token itself is still valid, so the request still authenticates...
    assert response.status_code == 200, response.text
    # ...but the deleted guest session no longer resolves.
    assert seen == [], f"a deleted guest session still resolved: {seen}"
    assert elapsed < FAST_REQUEST_S, (
        f"guest lookup queued behind the writer ({elapsed:.2f}s)"
    )

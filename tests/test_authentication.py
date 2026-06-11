import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from pixlstash.db_models import User, UserToken
from pixlstash.server import Server

API_PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def server():
    """Shared Server instance for all auth tests in this module.

    Building a Server (DB migrations, vault start-up, FastAPI route
    registration, etc.) takes a couple of seconds, so we pay that cost once
    per module and reset auth state between tests with the ``reset_auth``
    fixture instead of re-instantiating the Server.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"
        with Server(server_config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def reset_auth(server):
    """Clear stored user credentials and tokens before each test.

    Each test in this module starts from a clean auth state: no user row,
    no tokens and no cached/active sessions. This mirrors the behaviour of
    the original per-test ``with Server(...)`` blocks where every test got
    a fresh database.
    """

    def _wipe(session: Session):
        session.exec(delete(UserToken))
        session.exec(delete(User))
        session.commit()

    server.vault.db.run_task(_wipe)

    # Reset in-memory auth caches that mirror the on-disk state.
    server.auth.password_hash = None
    server.auth.username = None
    server.auth.user = None
    server.auth.active_session_ids = {}
    with server.auth._token_cache_lock:
        server.auth._token_cache.clear()

    # Re-create the User row so the rest of the server behaves as on first
    # startup (no password set yet).
    server.auth.ensure_user()

    yield


def test_authentication_without_login(server):
    """Test accessing a protected endpoint without logging in."""
    client = TestClient(server.api)

    # Access without a session cookie
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_authentication_with_password_setup(server):
    """Test setting up the password on first login."""
    client = TestClient(server.api)

    # First login to set the password
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Username and password set successfully."


def test_authentication_with_valid_password(server):
    """Test logging in with the correct password after setup."""
    with TestClient(server.api) as client1:
        # First login to set the password
        response = client1.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Username and password set successfully."

    with TestClient(server.api) as client2:
        # Login with the correct password
        response = client2.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."

        # Access a protected endpoint
        response = client2.get(f"{API_PREFIX}/protected")
        assert response.status_code == 200
        assert response.json()["message"] == "You are authenticated!"


def test_authentication_with_invalid_password(server):
    """Test logging in with an incorrect password."""
    with TestClient(server.api) as client1:
        # First login to set the password
        response = client1.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Username and password set successfully."

    with TestClient(server.api) as client2:
        # Attempt login with an incorrect password
        response = client2.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid password"

        # Access a protected endpoint
        response = client2.get(f"{API_PREFIX}/protected")
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"


def test_authentication_with_token_login(server):
    """Test creating a token and logging in with it."""
    with TestClient(server.api) as client1:
        # First login to set the password
        response = client1.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Username and password set successfully."

        # Create a token
        response = client1.post(
            f"{API_PREFIX}/users/me/token", json={"description": "Test token"}
        )
        assert response.status_code == 200
        token = response.json().get("token")
        assert token

    with TestClient(server.api) as client2:
        # Login with token
        response = client2.post(f"{API_PREFIX}/login", json={"token": token})
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."

        # Access a protected endpoint
        response = client2.get(f"{API_PREFIX}/protected")
        assert response.status_code == 200
        assert response.json()["message"] == "You are authenticated!"

    with TestClient(server.api) as client3:
        # Login with wrong token
        response = client3.post(f"{API_PREFIX}/login", json={"token": "bad-token"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"


# --- Seamless desktop (Electron) loopback session ---------------------------

DESKTOP_TOKEN = "desktop-session-token-0123456789abcdef"


def test_seed_desktop_session_authenticates_loopback_owner(server, monkeypatch):
    """A seeded desktop token logs the local window straight in — no /login."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)

    seeded = server.auth.seed_desktop_session()
    assert seeded == DESKTOP_TOKEN
    # The token maps to the owner user with no password/registration step.
    assert server.auth.active_session_ids.get(DESKTOP_TOKEN) is not None

    client = TestClient(server.api)
    # Presenting the token as the session cookie grants owner access, exactly
    # as the Electron shell does by injecting it into its BrowserWindow.
    client.cookies.set("session_id", DESKTOP_TOKEN)
    response = client.get(f"{API_PREFIX}/protected")
    assert response.status_code == 200
    assert response.json()["message"] == "You are authenticated!"


def test_seed_desktop_session_noop_without_env(server, monkeypatch):
    """With no env var set (every non-desktop install), nothing is seeded."""
    monkeypatch.delenv(server.auth.DESKTOP_SESSION_ENV, raising=False)
    assert server.auth.seed_desktop_session() is None
    assert server.auth.active_session_ids == {}


def test_seed_desktop_session_rejects_short_token(server, monkeypatch):
    """A weak (<16 char) token is refused so it can't grant owner access."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, "short")
    assert server.auth.seed_desktop_session() is None
    assert "short" not in server.auth.active_session_ids


def test_desktop_session_does_not_authenticate_other_clients(server, monkeypatch):
    """Remote clients without the token still hit the normal auth wall."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    client = TestClient(server.api)
    # No cookie / a different cookie value must not be authenticated.
    assert client.get(f"{API_PREFIX}/protected").status_code == 401
    client.cookies.set("session_id", "some-other-unseeded-value")
    assert client.get(f"{API_PREFIX}/protected").status_code == 401


def test_desktop_session_rejected_from_non_local_ip(server, monkeypatch):
    """The loopback owner session must not grant access on the external listener.

    The desktop app can expose an optional external listener; the seeded owner
    session is high-privilege and pinned to local connections, so a request
    presenting it from a non-local IP is fail-closed (falls through to the normal
    auth wall) even though the cookie is normally scoped to the loopback origin.
    """
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    # Simulate the request arriving from a public (non-local) client IP.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")

    client = TestClient(server.api)
    client.cookies.set("session_id", DESKTOP_TOKEN)
    assert client.get(f"{API_PREFIX}/protected").status_code == 401

    # Sanity: a local client with the same cookie is still authenticated.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    assert client.get(f"{API_PREFIX}/protected").status_code == 200


def test_secure_endpoint_works_on_loopback_with_require_ssl(server, monkeypatch):
    """require_ssl drives the external listener — it must NOT 403 the HTTP loopback.

    The desktop window always reaches the backend over plain-HTTP loopback while
    require_ssl may be enabled for the external listener. Secure-required
    endpoints (settings, taggers, share lookups, ...) must keep working for the
    local window; only genuinely remote plaintext requests are rejected.
    """
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()
    # Enable require_ssl as the external-listener setting would.
    monkeypatch.setitem(server.auth._server_config, "require_ssl", True)

    client = TestClient(server.api)
    client.cookies.set("session_id", DESKTOP_TOKEN)
    # TestClient requests look like local HTTP, so a secure-required endpoint
    # must still succeed (this is the settings-save / shared-ids regression).
    response = client.get(
        f"{API_PREFIX}/users/me/shared-resource-ids?resource_type=character"
    )
    assert response.status_code == 200


def test_secure_required_rejects_remote_plaintext(server, monkeypatch):
    """With require_ssl on, a remote (non-local) plaintext request is still 403."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    monkeypatch.setitem(server.auth._server_config, "require_ssl", True)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")

    remote_http = SimpleNamespace(url=SimpleNamespace(scheme="http"))
    with pytest.raises(HTTPException) as exc:
        server.auth.ensure_secure_when_required(remote_http)
    assert exc.value.status_code == 403

    # https from the same remote client is allowed (it's over TLS).
    remote_https = SimpleNamespace(url=SimpleNamespace(scheme="https"))
    server.auth.ensure_secure_when_required(remote_https)  # no raise

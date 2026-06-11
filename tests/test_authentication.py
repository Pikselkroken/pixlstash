import tempfile
from types import SimpleNamespace

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
    """A weak (<32 char) token is refused so it can't grant owner access."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, "short")
    assert server.auth.seed_desktop_session() is None
    assert "short" not in server.auth.active_session_ids


def test_seed_desktop_session_rejects_token_below_contract_floor(server, monkeypatch):
    """A 16-31 char token is now rejected: the floor matches the 32+ char contract.

    The shell ships a 32-byte token rendered as 64 hex chars, so the documented
    contract is 32+ chars. A token between the old floor (16) and the contract
    (32) must be refused so a regression in the shell's generator can't hand out
    a weaker owner credential.
    """
    below_floor = "a" * (server.auth.DESKTOP_SESSION_MIN_LEN - 1)
    assert len(below_floor) >= 16  # would have passed the old <16 floor
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, below_floor)
    assert server.auth.seed_desktop_session() is None
    assert below_floor not in server.auth.active_session_ids


def test_seed_desktop_session_accepts_token_at_contract_floor(server, monkeypatch):
    """A token exactly at the 32-char contract floor is accepted (no over-block)."""
    at_floor = "b" * server.auth.DESKTOP_SESSION_MIN_LEN
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, at_floor)
    assert server.auth.seed_desktop_session() == at_floor
    assert server.auth.active_session_ids.get(at_floor) is not None


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

    # A private RFC 1918 LAN IP must ALSO be rejected: the external listener is
    # reached over the LAN, so the backstop is pinned to loopback, not is_local_ip.
    monkeypatch.setattr(
        server.auth, "_get_real_client_ip", lambda request: "192.168.1.50"
    )
    assert client.get(f"{API_PREFIX}/protected").status_code == 401

    # Sanity: a loopback client with the same cookie is still authenticated.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    assert client.get(f"{API_PREFIX}/protected").status_code == 200


# --- First-owner registration must be loopback-only -------------------------


def test_registration_blocked_from_lan_ip(server, monkeypatch):
    """A LAN/non-loopback client must NOT be able to claim the empty owner account.

    The desktop owner is auto-logged-in and never sets a password; if the
    external listener is exposed, a co-network device could POST /login to set
    the owner credentials and take over the library (BLOCKER 1). Registration is
    pinned to loopback.
    """
    # Public IP — registration must be refused.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")
    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "attacker", "password": "attackerpass"},
    )
    assert response.status_code == 403
    # The account must remain unclaimed.
    assert server.auth.get_user().password_hash is None

    # Private RFC 1918 LAN IP — also refused.
    monkeypatch.setattr(
        server.auth, "_get_real_client_ip", lambda request: "192.168.1.50"
    )
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "attacker", "password": "attackerpass"},
    )
    assert response.status_code == 403
    assert server.auth.get_user().password_hash is None


def test_registration_allowed_from_loopback(server, monkeypatch):
    """The legitimate owner can still claim the account from loopback (no over-block)."""
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    client = TestClient(server.api)
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "owner", "password": "ownerpassword"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Username and password set successfully."
    assert server.auth.get_user().password_hash is not None


def test_login_with_existing_credentials_allowed_from_lan(server, monkeypatch):
    """Once credentials exist, a normal password login from the LAN is NOT blocked here.

    The loopback gate only protects first-owner *registration* (claiming the empty
    account). After credentials are set, remote-access policy is governed by the
    separate require_local_for_write check, not the registration gate — so a valid
    password login over the LAN must still authenticate (it would otherwise be an
    over-block regression).
    """
    # Claim the account from loopback first.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    with TestClient(server.api) as setup_client:
        assert (
            setup_client.post(
                f"{API_PREFIX}/login",
                json={"username": "owner", "password": "ownerpassword"},
            ).status_code
            == 200
        )

    # Remote write protection off so the registration gate is the only thing
    # under test here.
    monkeypatch.setitem(server.auth._server_config, "require_local_for_write", False)
    monkeypatch.setattr(
        server.auth, "_get_real_client_ip", lambda request: "192.168.1.50"
    )
    with TestClient(server.api) as client:
        response = client.post(
            f"{API_PREFIX}/login",
            json={"username": "owner", "password": "ownerpassword"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Login successful."


# --- change_password on an UNCLAIMED account must be loopback-only -----------


def _fake_request(client_host):
    """Minimal Starlette-Request stand-in for the IP-dependent auth guards."""
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        headers={},
        url=SimpleNamespace(scheme="http"),
    )


def test_change_password_on_unclaimed_account_blocked_from_lan(server, monkeypatch):
    """Setting the first password on the empty owner account must require loopback.

    The desktop owner is auto-logged-in with no password (``password_hash``
    None). ``change_password`` skips the current-password check for such an
    account, so without an explicit guard anyone holding a session for it could
    set its password. Claiming it that way must be pinned to loopback exactly
    like first-owner registration.
    """
    unclaimed = server.auth.get_user()
    assert unclaimed.password_hash is None
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: unclaimed)

    payload = SimpleNamespace(current_password=None, new_password="newownerpass")

    # Public IP — refused.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "8.8.8.8")
    with pytest.raises(Exception) as public_exc:
        server.auth.change_password(_fake_request("8.8.8.8"), payload)
    assert getattr(public_exc.value, "status_code", None) == 403

    # Private RFC 1918 LAN IP — also refused.
    monkeypatch.setattr(
        server.auth, "_get_real_client_ip", lambda request: "192.168.1.50"
    )
    with pytest.raises(Exception) as lan_exc:
        server.auth.change_password(_fake_request("192.168.1.50"), payload)
    assert getattr(lan_exc.value, "status_code", None) == 403

    # The account must remain unclaimed.
    assert server.auth.get_user().password_hash is None


def test_change_password_on_unclaimed_account_allowed_from_loopback(
    server, monkeypatch
):
    """The local desktop window (loopback) can still set the first password."""
    unclaimed = server.auth.get_user()
    assert unclaimed.password_hash is None
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: unclaimed)
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")

    payload = SimpleNamespace(current_password=None, new_password="newownerpass")
    result = server.auth.change_password(_fake_request("127.0.0.1"), payload)
    assert result["status"] == "success"
    assert server.auth.get_user().password_hash is not None


def test_change_password_on_claimed_account_allowed_from_lan(server, monkeypatch):
    """With a password already set, a valid current-password change is NOT loopback-gated.

    The loopback gate only protects the *claim* (first password). Once claimed,
    a normal authenticated password change from the LAN must still work (the
    current-password check governs it), or it would be an over-block regression.
    """
    # Claim from loopback first.
    monkeypatch.setattr(server.auth, "_get_real_client_ip", lambda request: "127.0.0.1")
    claimed = server.auth.get_user()
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: claimed)
    server.auth.change_password(
        _fake_request("127.0.0.1"),
        SimpleNamespace(current_password=None, new_password="firstpass"),
    )
    claimed = server.auth.get_user()
    assert claimed.password_hash is not None
    monkeypatch.setattr(server.auth, "get_user_for_request", lambda request: claimed)

    # Now change it again from a LAN IP with the correct current password.
    monkeypatch.setattr(
        server.auth, "_get_real_client_ip", lambda request: "192.168.1.50"
    )
    result = server.auth.change_password(
        _fake_request("192.168.1.50"),
        SimpleNamespace(current_password="firstpass", new_password="secondpass"),
    )
    assert result["status"] == "success"


# --- WebSocket desktop-session backstop (must match the HTTP path) -----------


def _fake_websocket(session_id, client_host):
    """Build a minimal stand-in for a Starlette WebSocket handshake."""
    return SimpleNamespace(
        cookies={"session_id": session_id} if session_id else {},
        headers={},
        query_params={},
        client=SimpleNamespace(host=client_host),
    )


def test_ws_desktop_session_rejected_from_non_loopback(server, monkeypatch):
    """A non-loopback WS handshake presenting the desktop session must NOT authenticate.

    The HTTP path drops the seeded desktop session for non-loopback clients; the
    WS path must agree (BLOCKER 2), or a LAN client could ride the desktop token
    to full owner access on /ws/updates.
    """
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    # Public IP — rejected.
    public_ws = _fake_websocket(DESKTOP_TOKEN, "8.8.8.8")
    assert server.auth.authenticate_websocket(public_ws) is None

    # Private RFC 1918 LAN IP — also rejected.
    lan_ws = _fake_websocket(DESKTOP_TOKEN, "192.168.1.50")
    assert server.auth.authenticate_websocket(lan_ws) is None


def test_ws_desktop_session_allowed_from_loopback(server, monkeypatch):
    """The local desktop window (loopback) still authenticates over WS (no over-block)."""
    monkeypatch.setenv(server.auth.DESKTOP_SESSION_ENV, DESKTOP_TOKEN)
    server.auth.seed_desktop_session()

    loopback_ws = _fake_websocket(DESKTOP_TOKEN, "127.0.0.1")
    auth = server.auth.authenticate_websocket(loopback_ws)
    assert auth is not None
    assert auth.is_owner is True


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

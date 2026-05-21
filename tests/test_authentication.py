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

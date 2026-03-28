import tempfile
from fastapi.testclient import TestClient
from pixlstash.server import Server

API_PREFIX = "/api/v1"


def test_authentication_without_login():
    """Test accessing a protected endpoint without logging in."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(server_config_path) as server:
            client = TestClient(server.api)

            # Access without a session cookie
            response = client.get(f"{API_PREFIX}/protected")
            assert response.status_code == 401
            assert response.json()["detail"] == "Not authenticated"


def test_authentication_with_password_setup():
    """Test setting up the password on first login."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(server_config_path) as server:
            client = TestClient(server.api)

            # First login to set the password
            response = client.post(
                f"{API_PREFIX}/login",
                json={"username": "testuser", "password": "testpassword"},
            )
            assert response.status_code == 200
            assert (
                response.json()["message"] == "Username and password set successfully."
            )


def test_authentication_with_valid_password():
    """Test logging in with the correct password after setup."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(server_config_path) as server:
            with TestClient(server.api) as client1:
                # First login to set the password
                response = client1.post(
                    f"{API_PREFIX}/login",
                    json={"username": "testuser", "password": "testpassword"},
                )
                assert response.status_code == 200
                assert (
                    response.json()["message"]
                    == "Username and password set successfully."
                )

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


def test_authentication_with_invalid_password():
    """Test logging in with an incorrect password."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(server_config_path) as server:
            with TestClient(server.api) as client1:
                # First login to set the password
                response = client1.post(
                    f"{API_PREFIX}/login",
                    json={"username": "testuser", "password": "testpassword"},
                )
                assert response.status_code == 200
                assert (
                    response.json()["message"]
                    == "Username and password set successfully."
                )

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


def test_authentication_with_token_login():
    """Test creating a token and logging in with it."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(server_config_path) as server:
            with TestClient(server.api) as client1:
                # First login to set the password
                response = client1.post(
                    f"{API_PREFIX}/login",
                    json={"username": "testuser", "password": "testpassword"},
                )
                assert response.status_code == 200
                assert (
                    response.json()["message"]
                    == "Username and password set successfully."
                )

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

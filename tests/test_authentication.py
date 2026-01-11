import tempfile
from fastapi.testclient import TestClient
from pixlvault.server import Server


def test_authentication_without_login():
    """Test accessing a protected endpoint without logging in."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            client = TestClient(server.api)

            # Access without a session cookie
            response = client.get("/protected")
            assert response.status_code == 401
            assert response.json()["detail"] == "Not authenticated"


def test_authentication_with_password_setup():
    """Test setting up the password on first login."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            client = TestClient(server.api)

            # First login to set the password
            response = client.post("/login", json={"password": "testpassword"})
            assert response.status_code == 200
            assert response.json()["message"] == "Password set successfully."


def test_authentication_with_valid_password():
    """Test logging in with the correct password after setup."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            with TestClient(server.api) as client1:
                # First login to set the password
                response = client1.post("/login", json={"password": "testpassword"})
                assert response.status_code == 200
                assert response.json()["message"] == "Password set successfully."

            with TestClient(server.api) as client2:
                # Login with the correct password
                response = client2.post("/login", json={"password": "testpassword"})
                assert response.status_code == 200
                assert response.json()["message"] == "Login successful."

                # Access a protected endpoint
                response = client2.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"


def test_authentication_with_invalid_password():
    """Test logging in with an incorrect password."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            with TestClient(server.api) as client1:
                # First login to set the password
                response = client1.post("/login", json={"password": "testpassword"})
                assert response.status_code == 200
                assert response.json()["message"] == "Password set successfully."

            with TestClient(server.api) as client2:
                # Attempt login with an incorrect password
                response = client2.post("/login", json={"password": "wrongpassword"})
                assert response.status_code == 401
                assert response.json()["detail"] == "Invalid password"

                # Access a protected endpoint
                response = client2.get("/protected")
                assert response.status_code == 401
                assert response.json()["detail"] == "Not authenticated"

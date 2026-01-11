import pytest
import tempfile
from fastapi.testclient import TestClient
from pixlvault.server import Server
from fastapi.exceptions import HTTPException


def test_authentication_without_token():
    """Test accessing a protected endpoint without a token."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            client = TestClient(server.api)

            # First access without a token
            with pytest.raises(HTTPException) as exc_info:
                client.get("/protected")
            assert exc_info.value.status_code == 401
            assert exc_info.value.detail == "Not authenticated"

            # Second access without a token
            with pytest.raises(HTTPException) as exc_info:
                client.get("/protected")
            assert exc_info.value.status_code == 401
            assert exc_info.value.detail == "Not authenticated"


def test_authentication_with_token():
    """Test accessing a protected endpoint with a valid token."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            client = TestClient(server.api)

            # Login to get the cookie
            response = client.post("/login")
            print(
                "RESPONSE: ", response.json()
            )  # Ensure the response is logged for debugging
            assert response.status_code == 200

            # Log the response headers to verify the cookie is set
            print("Response headers:", response.headers)
            assert "set-cookie" in response.headers, (
                "Cookie not set in response headers"
            )

            # Verify the cookie is set
            cookies = client.cookies
            assert "access_token" in cookies, (
                "Cookie 'access_token' not set after login"
            )

            # First access with the cookie
            response = client.get("/protected")
            assert response.status_code == 200
            assert response.json()["message"] == "You are authenticated!"

            # Second access with the same cookie
            response = client.get("/protected")
            assert response.status_code == 200
            assert response.json()["message"] == "You are authenticated!"


def test_authentication_with_token_multiple_clients():
    """Test accessing a protected endpoint with a valid token but multiple clients. Only the first client should succeed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            with TestClient(server.api) as client1:
                # Get a valid cookie
                response = client1.post("/login")
                print("RESPONSE: ", response.json())
                assert response.status_code == 200

                # First access with the cookie
                response = client1.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

                # Second access with the same cookie
                response = client1.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

            with TestClient(server.api) as client2:
                # Attempt to login with a second client
                response = client2.post("/login")
                assert response.status_code == 403, (
                    "Subsequent login attempts should be forbidden"
                )
                assert (
                    response.json()["detail"]
                    == "Login not allowed. Server has already handed out the SECRET_KEY to another client. Run the server again with --regenerate-secret-key to reset."
                )


def test_authentication_with_token_multiple_clients_with_removal():
    """Test accessing a protected endpoint with a valid token but multiple clients but removing the secret key in between. Both clients should succeed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            with TestClient(server.api) as client1:
                # Get a valid cookie
                response = client1.post("/login")
                print("RESPONSE: ", response.json())
                assert response.status_code == 200

                # First access with the cookie
                response = client1.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

                # Second access with the same cookie
                response = client1.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

            # Remove the secret key to allow a new client to login
            server.remove_secret_key()

            with TestClient(server.api) as client2:
                # Get a valid cookie
                response = client2.post("/login")
                print("RESPONSE: ", response.json())
                assert response.status_code == 200

                # First access with the cookie
                response = client2.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

                # Second access with the same cookie
                response = client2.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"


def test_authentication_with_token_multiple_clients_with_shared_token():
    """Test accessing a protected endpoint with a valid token but multiple clients sharing the same token. Both clients should succeed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/config.json"
        server_config_path = f"{temp_dir}/server-config.json"

        with Server(config_path, server_config_path) as server:
            token = None
            with TestClient(server.api) as client1:
                # Get a valid cookie
                response = client1.post("/login")
                print("RESPONSE: ", response.json())
                assert response.status_code == 200
                token = client1.cookies.get("access_token")
                assert token is not None, "Token should be set in client1 cookies"

                # First access with the cookie
                response = client1.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

                # Second access with the same cookie
                response = client1.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

            with TestClient(server.api) as client2:
                # Mimic the browser saving the cookie by setting it in client2
                client2.cookies.set("access_token", token, path="/")

                # First access with the cookie
                response = client2.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

                # Second access with the same cookie
                response = client2.get("/protected")
                assert response.status_code == 200
                assert response.json()["message"] == "You are authenticated!"

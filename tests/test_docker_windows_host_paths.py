import tempfile

from fastapi.testclient import TestClient

from pixlstash.server import Server


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        json={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 200


def test_docker_accepts_windows_absolute_host_paths(monkeypatch):
    """Windows absolute host paths should be accepted in Linux Docker mode."""

    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    windows_host_path = "C:\\Users\\lindk\\Pictures\\Test"

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"
        with Server(server_config_path) as server:
            with TestClient(server.api) as client:
                _login(client)

                ref_response = client.post(
                    "/reference-folders",
                    json={
                        "folder": "/data/ref/pictures-001",
                        "host_path": windows_host_path,
                    },
                )
                assert ref_response.status_code == 200, ref_response.text
                assert ref_response.json()["host_path"] == windows_host_path

                import_response = client.post(
                    "/import-folders",
                    json={
                        "folder": "/data/import/pictures-001",
                        "host_path": windows_host_path,
                        "delete_after_import": False,
                    },
                )
                assert import_response.status_code == 200, import_response.text
                assert import_response.json()["host_path"] == windows_host_path


def test_docker_rejects_relative_host_paths(monkeypatch):
    """Relative host paths must still be rejected in Docker mode."""

    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    relative_host_path = "Users\\lindk\\Pictures\\Test"

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = f"{temp_dir}/server-config.json"
        with Server(server_config_path) as server:
            with TestClient(server.api) as client:
                _login(client)

                ref_response = client.post(
                    "/reference-folders",
                    json={
                        "folder": "/data/ref/pictures-002",
                        "host_path": relative_host_path,
                    },
                )
                assert ref_response.status_code == 400
                assert (
                    ref_response.json().get("detail")
                    == "Host path must be an absolute path."
                )

                import_response = client.post(
                    "/import-folders",
                    json={
                        "folder": "/data/import/pictures-002",
                        "host_path": relative_host_path,
                        "delete_after_import": False,
                    },
                )
                assert import_response.status_code == 400
                assert (
                    import_response.json().get("detail")
                    == "Host path must be an absolute path."
                )

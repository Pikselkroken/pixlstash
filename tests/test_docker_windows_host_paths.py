import os
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


def test_running_in_docker_env_flag(monkeypatch):
    """The PIXLSTASH_IN_DOCKER=1 env flag is the primary positive signal."""
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    assert Server.running_in_docker() is True


def test_running_in_docker_dockerenv_marker(monkeypatch):
    """``/.dockerenv`` is a secondary signal used when the env flag is unset."""
    monkeypatch.delenv("PIXLSTASH_IN_DOCKER", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/.dockerenv")
    assert Server.running_in_docker() is True


def test_running_in_docker_false_outside_container(monkeypatch):
    """No env flag and no marker file means not running in Docker."""
    monkeypatch.delenv("PIXLSTASH_IN_DOCKER", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda path: False)
    assert Server.running_in_docker() is False


def test_detect_install_type_docker(monkeypatch):
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    assert Server.detect_install_type() == "docker"


def test_detect_install_type_pip_default(monkeypatch):
    monkeypatch.delenv("PIXLSTASH_IN_DOCKER", raising=False)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda path: False)
    assert Server.detect_install_type() == "pip"


def test_detect_install_type_override_other(monkeypatch):
    """A valid override wins even when docker signals are present."""
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "other")
    assert Server.detect_install_type() == "other"


def test_detect_install_type_invalid_override_ignored(monkeypatch):
    """An invalid/empty override is ignored and detection takes over."""
    monkeypatch.delenv("PIXLSTASH_IN_DOCKER", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda path: False)
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "not-a-real-type")
    assert Server.detect_install_type() == "pip"
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "   ")
    assert Server.detect_install_type() == "pip"


def test_detect_install_type_always_in_allowed_set(monkeypatch):
    """Whatever the inputs, the result is always one of the three values."""
    for env_value in ("1", "0", "", "true"):
        monkeypatch.setenv("PIXLSTASH_IN_DOCKER", env_value)
        monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
        assert Server.detect_install_type() in Server.INSTALL_TYPES

"""Tests for the workers progress endpoint and the version endpoint."""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from pixlstash.server import Server


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def test_workers_progress_has_expected_keys():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/workers/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "workers" in data
        assert "process" in data
        process = data["process"]
        assert "ram_used_gb" in process
        assert "ram_total_gb" in process
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_version_endpoint_returns_200():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/version")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

"""Tests for the workers progress endpoint and the version endpoint."""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from pixlstash.server import Server
from pixlstash.tasks.dedup_scan_task import DedupScanTask
from pixlstash.tasks.task_type import TaskType


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


def test_an_active_dedup_scan_never_reports_terminal_task_progress(monkeypatch):
    temp_dir, _client, server = _setup()
    try:
        task = DedupScanTask(server.vault.db, scan_id=1)
        task._set_task_progress(6, 6)
        original = server.vault._task_runner.get_active_tasks_of_type

        def active_tasks(task_type):
            if task_type == "DedupScanTask":
                return [task]
            return original(task_type)

        monkeypatch.setattr(
            server.vault._task_runner, "get_active_tasks_of_type", active_tasks
        )
        snapshot = server.vault.get_worker_progress()[TaskType.DEDUP_SCAN.value]
        assert snapshot["label"] == "duplicate_scan"
        assert snapshot["active"] is True
        assert snapshot["current"] == 5
        assert snapshot["total"] == 6
        assert snapshot["remaining"] == 1
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

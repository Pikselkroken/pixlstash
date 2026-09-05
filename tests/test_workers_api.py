"""Tests for the workers progress endpoint and the version endpoint."""

import gc
import json
import os
import tempfile
import types

from fastapi.testclient import TestClient

from pixlstash.server import Server
from pixlstash.services import config_service
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
        # Asserted on a warm server rather than in a file of its own: the GPU
        # out-of-memory retry announces itself through this notifier, and
        # without the wiring the toast never leaves the machine
        # (tests/test_vram_oom_retry.py covers everything downstream of it).
        assert server.vault._task_runner._notifier is not None
    finally:
        server.close()
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
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_checkpoint_hash_does_not_report_the_picture_library_as_its_progress(
    monkeypatch,
):
    """The Checkpoint Hash row must not borrow the library's numbers.

    It used to fall through to the generic `planner_managed` branch, which set
    `missing = 0` and left `total` at the picture count. Reading tens of
    gigabytes off disk then rendered as "N / N, 0 remaining, 0/s" on a row that
    still said running, so a healthy long task read as a stuck one.

    The picture count is forced to a sentinel rather than importing pictures:
    an empty library makes the two branches indistinguishable, which is exactly
    how this would pass while still broken.
    """
    temp_dir, _client, server = _setup()
    try:
        monkeypatch.setattr(
            server.vault, "_count_total_pictures", lambda _session: 4242
        )
        workers = server.vault.get_worker_progress()
        snapshot = workers[TaskType.CHECKPOINT_HASH.value]
        assert snapshot["label"] == "checkpoints_hashed"
        # The sentinel proves the branch ran: a picture-scoped worker shows it.
        assert workers[TaskType.QUALITY.value]["total"] == 4242
        # No hub registration in this fixture, so there is no shelf to count and
        # the honest answer is zero - never the picture library's total.
        assert snapshot["total"] == 0
        assert snapshot["current"] == 0
    finally:
        server.close()
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
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_vram_falls_back_to_torch_when_nvml_reports_no_process_figure(monkeypatch):
    """Windows (WDDM) NVML lists the process but its ``usedGpuMemory`` is not
    available, which rendered "0 / 31.8 GB" during CUDA inference (#1162). With
    no per-process figure the monitor must leave the reading to torch."""
    entry = types.SimpleNamespace(pid=os.getpid(), usedGpuMemory=None)
    handle = object()
    fake_nvml = types.SimpleNamespace(
        nvmlInit=lambda: None,
        nvmlShutdown=lambda: None,
        nvmlDeviceGetCount=lambda: 1,
        nvmlDeviceGetHandleByIndex=lambda index: handle,
        nvmlDeviceGetMemoryInfo=lambda h: types.SimpleNamespace(total=32 * 1024**3),
        nvmlDeviceGetComputeRunningProcesses=lambda h: [entry],
        nvmlDeviceGetGraphicsRunningProcesses=lambda h: [],
        NVML_VALUE_NOT_AVAILABLE=-1,
    )
    monkeypatch.setattr(config_service, "pynvml", fake_nvml)

    def torch_reading(payload):
        payload["vram_used_gb"] = 4.5
        payload["vram_total_gb"] = 32.0
        payload["vram_percent"] = 14.1
        return True

    monkeypatch.setattr(config_service, "collect_vram_from_torch", torch_reading)
    monitor = config_service.HardwareMonitor()
    usage = monitor.get_usage()
    assert usage["vram_used_gb"] == 4.5

    # With a real per-process figure NVML still wins.
    entry.usedGpuMemory = 2 * 1024**3
    assert config_service.HardwareMonitor().get_usage()["vram_used_gb"] == 2.0

    # A process NVML does not list holds nothing: that zero stands, no torch.
    fake_nvml.nvmlDeviceGetComputeRunningProcesses = lambda h: []
    assert config_service.HardwareMonitor().get_usage()["vram_used_gb"] == 0.0

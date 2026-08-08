"""Tests for the picture export (async ZIP) and project export (streaming ZIP) APIs."""

import gc
import json
import os
import tempfile
import time
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from pixlstash.server import Server
from pixlstash.utils.service.export_utils import _safe_archive_stem
from tests.utils import upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")


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


def _upload_picture(client, filename="Bad1.png"):
    img_path = os.path.join(PICTURES_DIR, filename)
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(
            client, [("file", (filename, f, "image/png"))]
        )
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


def _wait_for_export(client, task_id, timeout_s=30, poll_interval=0.2):
    """Poll export status until completed or failed."""
    from tests.utils import API_PREFIX

    start = time.time()
    while time.time() - start < timeout_s:
        resp = client.get(
            f"{API_PREFIX}/pictures/export/status", params={"task_id": task_id}
        )
        assert resp.status_code == 200, resp.text
        status = resp.json().get("status")
        if status == "completed":
            return resp.json()
        if status == "failed":
            raise AssertionError(f"Export task failed: {resp.json()}")
        time.sleep(poll_interval)
    raise AssertionError(f"Export task did not complete within {timeout_s}s")


def test_pictures_export_produces_valid_zip():
    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)

        resp = client.get("/pictures/export")
        assert resp.status_code == 200
        task_id = resp.json().get("task_id")
        assert task_id

        status = _wait_for_export(client, task_id)
        assert status["status"] == "completed"

        from tests.utils import API_PREFIX

        resp = client.get(f"{API_PREFIX}/pictures/export/download/{task_id}")
        assert resp.status_code == 200

        buf = BytesIO(resp.content)
        assert zipfile.is_zipfile(buf)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_project_export_produces_valid_zip():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "ExportTestProject"})
        assert resp.status_code == 200
        project_id = resp.json()["id"]

        resp = client.get(f"/projects/{project_id}/export")
        assert resp.status_code == 200
        assert "zip" in resp.headers.get("content-type", "").lower()

        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert any("project.json" in n for n in names)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# Zip member names must not escape the extraction directory (zip slip, CWE-22)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../evil",
        "..\\..\\evil",
        "/etc/passwd",
        "C:\\Windows\\system32\\evil",
        "....//....//evil",
        "..",
        ".",
        "",
        "  ..  ",
        ".hidden",
        "na\x00me",
    ],
)
def test_safe_archive_stem_cannot_escape(hostile):
    """``original_file_name`` is attacker-influenced at upload time and is used
    as a zip member name when exporting with original names. A member name is a
    relative path to the extracting tool, so traversal in it writes outside the
    recipient's extraction directory."""
    out = _safe_archive_stem(hostile, "fallback")
    assert "/" not in out and "\\" not in out
    assert not out.startswith(".")
    assert out not in ("", ".", "..")
    assert "\x00" not in out
    # And it must stay a single component when joined for extraction.
    assert os.path.basename(out) == out


def test_safe_archive_stem_preserves_legitimate_names():
    """Over-sanitising is its own regression: non-ASCII names must survive."""
    assert _safe_archive_stem("holiday_photo", "fb") == "holiday_photo"
    assert _safe_archive_stem("café shot", "fb") == "café shot"
    assert _safe_archive_stem("日本語", "fb") == "日本語"
    assert _safe_archive_stem("a-b_c.1", "fb") == "a-b_c.1"


def test_export_status_unknown_task_returns_404():
    temp_dir, client, server = _setup()
    try:
        from tests.utils import API_PREFIX

        resp = client.get(
            f"{API_PREFIX}/pictures/export/status",
            params={"task_id": "nonexistent-task-id"},
        )
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()

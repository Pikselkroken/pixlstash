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


# ---------------------------------------------------------------------------
# Export to folder (#291): writes straight onto the host disk instead of
# packaging a ZIP to download, then opens the destination in the host file
# manager.
# ---------------------------------------------------------------------------


def test_pictures_export_folder_writes_files_and_opens_it():
    from unittest import mock

    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        destination = os.path.join(temp_dir.name, "destination")
        os.makedirs(destination, exist_ok=True)

        with mock.patch(
            "pixlstash.utils.service.export_utils.open_in_file_manager",
            return_value=True,
        ) as opener:
            resp = client.post(
                "/pictures/export/folder", params={"destination": destination}
            )
            assert resp.status_code == 200, resp.text
            task_id = resp.json().get("task_id")
            assert task_id

            status = _wait_for_export(client, task_id)
            assert status["status"] == "completed"
            assert status.get("destination") == destination
            assert status.get("download_url") is None
            assert status.get("opened") is True

            opener.assert_called_once_with(destination)

        written = [
            f
            for f in os.listdir(destination)
            if os.path.isfile(os.path.join(destination, f))
        ]
        assert written, "expected at least one exported file in the destination"

        # A folder export's task is collected on the first "completed" status
        # read (there is no download step to trigger it) - a second status
        # poll must 404, not report the same task forever.
        from tests.utils import API_PREFIX

        resp = client.get(
            f"{API_PREFIX}/pictures/export/status", params={"task_id": task_id}
        )
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pictures_export_folder_reports_when_it_could_not_open():
    """A headless host's file manager spawn failing must not be silently
    swallowed: the export itself succeeded (files are on disk), but the task
    must say it could not be opened rather than reporting a plain success."""
    from unittest import mock

    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        destination = os.path.join(temp_dir.name, "destination")
        os.makedirs(destination, exist_ok=True)

        with mock.patch(
            "pixlstash.utils.service.export_utils.open_in_file_manager",
            return_value=False,
        ):
            resp = client.post(
                "/pictures/export/folder", params={"destination": destination}
            )
            task_id = resp.json().get("task_id")

            status = _wait_for_export(client, task_id)
            assert status["status"] == "completed"
            assert status.get("opened") is False
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pictures_export_folder_rejects_missing_destination():
    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        missing = os.path.join(temp_dir.name, "does-not-exist")

        resp = client.post("/pictures/export/folder", params={"destination": missing})
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pictures_export_folder_rejects_non_empty_destination():
    """A folder export writes plain files and silently overwrites a same-named
    one already there (unlike a ZIP, which tolerates duplicate members) - a
    non-empty destination must be refused rather than risk overwriting
    something that isn't part of this export."""
    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        destination = os.path.join(temp_dir.name, "destination")
        os.makedirs(destination, exist_ok=True)
        with open(os.path.join(destination, "already-here.txt"), "w") as f:
            f.write("not part of this export")

        resp = client.post(
            "/pictures/export/folder", params={"destination": destination}
        )
        assert resp.status_code == 409
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pictures_export_folder_resolves_symlink_before_blocklist_check():
    """The blocklist must run on the RESOLVED path, not the string the caller
    sent: a symlink to a restricted directory must not pass just because its
    own name isn't on the list."""
    temp_dir, client, server = _setup()
    try:
        link_path = os.path.join(temp_dir.name, "sneaky-link")
        os.symlink("/etc", link_path)

        resp = client.post("/pictures/export/folder", params={"destination": link_path})
        assert resp.status_code == 400
        assert "restricted" in resp.json().get("detail", "").lower()
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()

"""Tests for the picture export (async ZIP) and project export (streaming ZIP) APIs."""

import gc
import json
import os
import tempfile
import time
import unicodedata
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from pixlstash.server import Server
from pixlstash.utils.service.export_utils import (
    _safe_archive_stem,
    _unique_export_stem,
)
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


def test_unique_export_stem_never_hands_out_a_name_twice():
    """One picture must never be written over another (#1177 item 1).

    The third case is the bug: a per-stem counter alone turns the second
    ``photo`` into ``photo_2``, which is the name of a picture that already
    exists in the library. In a folder export that is one file silently
    replacing another; in a ZIP it is a duplicate member. The case-folded pair
    covers the case-insensitive filesystems the desktop build ships to.
    """
    claimed: dict = {}
    stems = ["photo", "photo", "photo_2", "Photo", "holiday", "photo_2"]
    handed_out = [_unique_export_stem(s, claimed) for s in stems]

    folded = [name.casefold() for name in handed_out]
    assert len(set(folded)) == len(folded), handed_out
    # The first claim of a name is that name; only later ones are suffixed.
    assert handed_out[0] == "photo"
    assert handed_out[4] == "holiday"


def test_unique_export_stem_separates_nfc_and_nfd_spellings():
    """Case-folding alone is not what "same file" means on macOS.

    APFS and HFS+ compare normalisation-insensitively as well as
    case-insensitively, so ``café.jpg`` composed (NFC, typed anywhere) and
    decomposed (NFD, the spelling a file arriving from a Mac carries) are one
    path - and a library holding both is ordinary rather than exotic. A
    case-folded-only key hands them the same name and the second export copy
    silently replaces the first, which is the very bug this function exists
    for. The names handed out must therefore differ after normalisation, not
    merely as Python strings.
    """
    composed = "\u00e9clair"  # NFC
    decomposed = "e\u0301clair"  # NFD - the same file name on APFS/HFS+
    assert composed != decomposed
    claimed: dict = {}
    handed_out = [_unique_export_stem(stem, claimed) for stem in (composed, decomposed)]
    normalised = [unicodedata.normalize("NFC", name) for name in handed_out]
    assert len(set(normalised)) == 2, handed_out
    assert handed_out[0] == composed


def test_unique_export_stem_stays_linear_on_a_large_duplicate_run():
    """Claiming names must not degrade into a rescan per picture: a library of
    identically-named originals is the normal case this guard exists for, not
    an exotic one."""
    claimed: dict = {}
    handed_out = [_unique_export_stem("photo", claimed) for _ in range(2000)]
    assert len(set(handed_out)) == 2000
    # One key per name handed out, plus nothing else: no accumulating scan
    # state, which is what would make this quadratic.
    assert len(claimed) == 2000


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
            assert status.get("download_url") is None
            assert status.get("opened") is True
            # GET /pictures/export/status is any_token - a share token polls
            # its own ZIP export through it - so the folder export's absolute
            # host destination must not come back in that payload (#1177 item
            # 12). The caller supplied it; it has nothing to learn from it.
            assert "destination" not in status, status
            assert destination not in json.dumps(status)

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


def test_pictures_export_folder_rejects_a_destination_inside_the_library():
    """An empty new subfolder of the library passes every other check (#1177
    item 2): it exists, it is writable, it is not on the system blocklist and it
    is empty. It is also read back by the library that just wrote it, so the
    export returns as a fresh set of pictures. The refusal is the only thing
    standing between "Export to folder" and duplicating the library into
    itself."""
    from unittest import mock

    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        inside = os.path.join(server.vault.image_root, "exported")
        os.makedirs(inside, exist_ok=True)

        resp = client.post("/pictures/export/folder", params={"destination": inside})
        assert resp.status_code == 400, resp.text
        assert "part of your library" in resp.json().get("detail", "")

        # The positive control, in the same environment: a folder that is not
        # inside the library is still accepted. Over-blocking every empty
        # folder would pass the assertion above and break the feature.
        outside = os.path.join(temp_dir.name, "outside-destination")
        os.makedirs(outside, exist_ok=True)
        with mock.patch(
            "pixlstash.utils.service.export_utils.open_in_file_manager",
            return_value=True,
        ):
            accepted = client.post(
                "/pictures/export/folder", params={"destination": outside}
            )
            assert accepted.status_code == 200, accepted.text
            _wait_for_export(client, accepted.json()["task_id"])
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pictures_export_folder_refuses_when_the_reference_folders_are_unreadable():
    """#1177 item 59: an unknown blocklist must refuse, not permit.

    The roots the check above compares against were previously reported as an
    empty tuple when the folder table could not be read, which is exactly what
    "no reference folders are configured" looks like - so a read failure turned
    the destination check off silently, and an export into a reference folder
    was accepted.
    """
    from unittest import mock

    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        destination = os.path.join(temp_dir.name, "unreadable-refs-destination")
        os.makedirs(destination, exist_ok=True)

        # Broken at the DB read, not at the vault method, so this exercises the
        # whole chain: replacing the raise with `return ()` again turns this
        # test green-to-red rather than leaving it passing on a stubbed vault.
        def _explode(_task, *args, **kwargs):
            raise RuntimeError("test-induced reference folder read failure")

        with mock.patch.object(server.vault.db, "run_immediate_read_task", _explode):
            resp = client.post(
                "/pictures/export/folder", params={"destination": destination}
            )
        assert resp.status_code == 503, resp.text
        assert "reference folders" in resp.json().get("detail", "")
        assert not os.listdir(destination), "nothing may be written on a refusal"

        # The positive control: the same destination is accepted once the
        # folder table reads normally again.
        with mock.patch(
            "pixlstash.utils.service.export_utils.open_in_file_manager",
            return_value=True,
        ):
            accepted = client.post(
                "/pictures/export/folder", params={"destination": destination}
            )
            assert accepted.status_code == 200, accepted.text
            _wait_for_export(client, accepted.json()["task_id"])
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pictures_export_folder_rejects_a_destination_in_a_watched_folder():
    """An import folder is the same refusal as a reference folder, and worse.

    The reference-folder case duplicates the library. A watch folder *also*
    re-imports - a re-encoded export carries a new ``pixel_sha`` so the
    duplicate check does not catch it - and a folder carrying
    ``delete_after_import`` then removes the file it just imported
    (``pixlstash/tasks/watch_folder_import_task.py``), so the export destroys
    its own output. Found by the adversarial review of #1177 item 2, which had
    covered ``image_root`` and the reference folders but not this sibling.
    """
    temp_dir, client, server = _setup()
    try:
        watched = os.path.join(temp_dir.name, "watched")
        os.makedirs(watched, exist_ok=True)
        added = client.post(
            "/import-folders",
            json={
                "folder": watched,
                "label": "watched",
                "delete_after_import": True,
            },
        )
        assert added.status_code == 200, added.text

        destination = os.path.join(watched, "exported")
        os.makedirs(destination, exist_ok=True)
        resp = client.post(
            "/pictures/export/folder", params={"destination": destination}
        )
        assert resp.status_code == 400, resp.text
        assert "part of your library" in resp.json().get("detail", "")
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

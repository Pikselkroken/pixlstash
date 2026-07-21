"""Object-scope (BOLA) tests for the detection endpoints.

``POST /pictures/detect`` enqueues per-picture work and ``GET
/pictures/{id}/detections`` returns per-picture data, so both must enforce the
scoping system before touching resource data (CLAUDE.md §"Endpoint scope
enforcement"). These assert both directions: out-of-scope is denied, in-scope
still works, and the owner/unscoped path is unaffected.

The POST scope filter is exercised by patching ``fetch_scope_allowed_picture_ids``
(so no real GPU detection runs); the GET object check is exercised end-to-end
through a real resource-scoped READ token and the auth middleware.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

import pixlstash.routes.pictures._crud as crud_module
from pixlstash.auth import READ_SAFE_POST_PATHS
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


def _good_picture_files():
    pictures_dir = os.path.join(os.path.dirname(__file__), "..", "pictures", "good")
    results = []
    for name in sorted(os.listdir(pictures_dir)):
        path = os.path.join(pictures_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            ct = "image/png" if ext == ".png" else "image/jpeg"
            with open(path, "rb") as fh:
                results.append((name, fh.read(), ct))
    return results


def _setup_server_with_pictures():
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    client = TestClient(server.api, raise_server_exceptions=True)

    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert r.status_code == 200, r.text

    picture_files = _good_picture_files()
    assert picture_files, "No test pictures found in pictures/good/"
    files = [("file", (name, data, ct)) for name, data, ct in picture_files]
    import_status = upload_pictures_and_wait(client, files, timeout_s=30)
    assert import_status["status"] == "completed", import_status

    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    picture_ids = [p["id"] for p in r.json()]
    assert len(picture_ids) >= 2, "Need at least two pictures for the scope test"

    return temp_dir, server, client, picture_ids


def test_detect_endpoint_not_in_read_safe_post_paths():
    # POST /pictures/detect enqueues work; a READ-scoped token must not reach it.
    assert f"{API}/pictures/detect" not in READ_SAFE_POST_PATHS


def test_detect_scoped_token_filters_to_allowed_ids(monkeypatch):
    """A scoped token only enqueues detection for its in-scope pictures."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        in_scope, out_of_scope = picture_ids[0], picture_ids[1]

        monkeypatch.setattr(
            crud_module,
            "fetch_scope_allowed_picture_ids",
            lambda server, request: {in_scope},
        )

        # Capture the task instead of running real Florence detection.
        captured = {}

        def _capture(task):
            captured["task"] = task
            return "test-task-id"

        monkeypatch.setattr(server.vault, "submit_task", _capture)

        r = client.post(
            f"{API}/pictures/detect",
            json={"picture_ids": [in_scope, out_of_scope], "prompt": "dog"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Only the in-scope id is processed; the out-of-scope id is dropped.
        assert body["picture_ids"] == [in_scope]
        assert body["prompt"] == "dog"
        assert body["task_id"] == "test-task-id"
        task = captured.get("task")
        assert task is not None
        assert task.params["picture_ids"] == [in_scope]
        assert task.params["prompt"] == "dog"
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_detect_all_out_of_scope_is_forbidden(monkeypatch):
    """When every requested id is out of scope, the request is denied (403)."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        monkeypatch.setattr(
            crud_module,
            "fetch_scope_allowed_picture_ids",
            lambda server, request: set(),
        )
        # Should never reach submission; fail loudly if it does.
        monkeypatch.setattr(
            server.vault,
            "submit_task",
            lambda task: (_ for _ in ()).throw(
                AssertionError(
                    "submit_task must not run for an all-out-of-scope request"
                )
            ),
        )
        r = client.post(
            f"{API}/pictures/detect",
            json={"picture_ids": picture_ids[:2]},
        )
        assert r.status_code == 403, r.text
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_detect_unscoped_owner_processes_all_ids(monkeypatch):
    """An unscoped/owner token (scope helper returns None) keeps full access."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        monkeypatch.setattr(
            crud_module,
            "fetch_scope_allowed_picture_ids",
            lambda server, request: None,
        )
        captured = {}

        def _capture(task):
            captured["t"] = task
            return "test-task-id"

        monkeypatch.setattr(server.vault, "submit_task", _capture)

        ids = picture_ids[:2]
        r = client.post(f"{API}/pictures/detect", json={"picture_ids": ids})
        assert r.status_code == 200, r.text
        assert set(r.json()["picture_ids"]) == set(ids)
        assert sorted(captured["t"].params["picture_ids"]) == sorted(ids)
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_detect_requires_picture_ids():
    temp_dir, server, client, _picture_ids = _setup_server_with_pictures()
    try:
        r = client.post(f"{API}/pictures/detect", json={})
        assert r.status_code == 400, r.text
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_detect_rejects_too_many_ids():
    """An over-cap picture_ids list is rejected (422), bounding the GPU work."""
    from pixlstash.routes.pictures._crud import DETECT_MAX_IDS

    temp_dir, server, client, _picture_ids = _setup_server_with_pictures()
    try:
        too_many = list(range(1, DETECT_MAX_IDS + 2))
        r = client.post(f"{API}/pictures/detect", json={"picture_ids": too_many})
        assert r.status_code == 422, r.text
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_get_detections_scope_both_directions():
    """A resource-scoped READ token reads in-scope detections but is 403 out of scope."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        in_scope, out_of_scope = picture_ids[0], picture_ids[1]

        # Scope a READ token to a picture set containing only the first picture.
        r = client.post(f"{API}/picture_sets", json={"name": "ScopedSet"})
        assert r.status_code == 200, r.text
        set_id = r.json()["picture_set"]["id"]
        r = client.post(f"{API}/picture_sets/{set_id}/members/{in_scope}")
        assert r.status_code == 200, r.text

        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "set read token",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": set_id,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        token_client = TestClient(server.api)

        # In-scope: 200 and a bare JSON list (dedicated route, not the
        # /{id}/{field} catch-all which would return a dict).
        r = token_client.get(f"{API}/pictures/{in_scope}/detections", headers=auth)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

        # Out-of-scope: 403 from enforce_picture_scope before any data is read.
        r = token_client.get(f"{API}/pictures/{out_of_scope}/detections", headers=auth)
        assert r.status_code == 403, (
            f"out-of-scope detections read must be 403, got {r.status_code}: {r.text}"
        )

        # Owner keeps full access.
        r = client.get(f"{API}/pictures/{out_of_scope}/detections")
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()

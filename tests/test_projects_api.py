"""Tests for the Projects API: CRUD, picture assignment, export, and attachments."""

import gc
import json
import os
import tempfile
import zipfile
from io import BytesIO

from fastapi.testclient import TestClient

from pixlstash.server import Server
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
    resp = client.post("/login", json={"username": "testuser", "password": "testpassword"})
    assert resp.status_code == 200
    return temp_dir, client, server


def _upload_picture(client, filename="Bad1.png"):
    img_path = os.path.join(PICTURES_DIR, filename)
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(client, [("file", (filename, f, "image/png"))])
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


def test_create_and_get_project():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "MyProject", "description": "Test"})
        assert resp.status_code == 200
        project = resp.json()
        project_id = project["id"]
        assert project["name"] == "MyProject"

        resp = client.get(f"/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == project_id
        assert resp.json()["name"] == "MyProject"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_list_projects():
    temp_dir, client, server = _setup()
    try:
        client.post("/projects", json={"name": "ListProject"})

        resp = client.get("/projects")
        assert resp.status_code == 200
        projects = resp.json()
        assert isinstance(projects, list)
        assert any(p["name"] == "ListProject" for p in projects)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_update_project():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "UpdateMe"})
        project_id = resp.json()["id"]

        resp = client.put(f"/projects/{project_id}", json={"name": "UpdatedName"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "UpdatedName"

        resp = client.get(f"/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "UpdatedName"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_delete_project_pictures_survive():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.post("/projects", json={"name": "DeleteMeProject"})
        project_id = resp.json()["id"]

        client.patch(
            "/pictures/project",
            json={"picture_ids": [pic_id], "project_id": project_id, "mode": "add"},
        )

        resp = client.delete(f"/projects/{project_id}")
        assert resp.status_code == 200

        resp = client.get(f"/projects/{project_id}")
        assert resp.status_code == 404

        resp = client.get(f"/pictures/{pic_id}/metadata")
        assert resp.status_code == 200
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_assign_pictures_to_project():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.post("/projects", json={"name": "AssignProject"})
        project_id = resp.json()["id"]

        resp = client.patch(
            "/pictures/project",
            json={"picture_ids": [pic_id], "project_id": project_id, "mode": "add"},
        )
        assert resp.status_code == 200

        resp = client.get(f"/projects/{project_id}/summary")
        assert resp.status_code == 200
        assert resp.json()["image_count"] >= 1
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_project_export_is_valid_zip():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "ExportProject"})
        project_id = resp.json()["id"]

        resp = client.get(f"/projects/{project_id}/export")
        assert resp.status_code == 200
        assert "zip" in resp.headers.get("content-type", "").lower()

        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert any("project.json" in n for n in names)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_project_attachment_upload_list_delete():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "AttachProject"})
        project_id = resp.json()["id"]

        attachment_content = b"hello attachment"
        resp = client.post(
            f"/projects/{project_id}/attachments",
            files={"file": ("note.txt", attachment_content, "text/plain")},
        )
        assert resp.status_code == 200
        att_id = resp.json()["id"]

        resp = client.get(f"/projects/{project_id}/attachments")
        assert resp.status_code == 200
        attachments = resp.json()
        assert any(a["id"] == att_id for a in attachments)

        resp = client.delete(f"/projects/{project_id}/attachments/{att_id}")
        assert resp.status_code == 200

        resp = client.get(f"/projects/{project_id}/attachments")
        assert resp.status_code == 200
        assert not any(a["id"] == att_id for a in resp.json())
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_project_get_by_name():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "NamedProject"})
        project_id = resp.json()["id"]

        resp = client.get("/projects/NamedProject")
        assert resp.status_code == 200
        assert resp.json()["id"] == project_id
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_project_duplicate_name_rejected():
    temp_dir, client, server = _setup()
    try:
        client.post("/projects", json={"name": "DupProject"})
        resp = client.post("/projects", json={"name": "DupProject"})
        assert resp.status_code == 409
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

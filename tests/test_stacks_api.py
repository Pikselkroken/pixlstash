"""Tests for the Stacks API: create, read, reorder, and auto-delete on last removal."""

import gc
import json
import os
import tempfile

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


def test_create_stack_and_list_members():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post(
            "/stacks",
            json={"picture_ids": [pic_id1, pic_id2], "name": "TestStack"},
        )
        assert resp.status_code == 200
        stack_id = resp.json()["id"]
        assert stack_id

        resp = client.get(f"/stacks/{stack_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["picture_ids"]) == {pic_id1, pic_id2}
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_stack_pictures_in_order():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post("/stacks", json={"picture_ids": [pic_id1, pic_id2]})
        assert resp.status_code == 200
        stack_id = resp.json()["id"]

        resp = client.get(f"/stacks/{stack_id}/pictures")
        assert resp.status_code == 200
        pics = resp.json()
        assert isinstance(pics, list)
        assert len(pics) == 2
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stack_member_reorder():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post("/stacks", json={"picture_ids": [pic_id1, pic_id2]})
        stack_id = resp.json()["id"]

        # Move pic_id1 to position 1 (last)
        resp = client.patch(
            f"/stacks/{stack_id}/members/{pic_id1}",
            json={"position": 1},
        )
        assert resp.status_code == 200
        ordered_ids = resp.json()["picture_ids"]
        assert ordered_ids.index(pic_id1) == 1
        assert ordered_ids.index(pic_id2) == 0
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_remove_last_member_auto_deletes_stack():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post("/stacks", json={"picture_ids": [pic_id1, pic_id2]})
        stack_id = resp.json()["id"]

        # Remove both members — stack should be auto-deleted when only 1 remains
        resp = client.request(
            "DELETE",
            f"/api/v1/stacks/{stack_id}/members",
            json={"picture_ids": [pic_id1, pic_id2]},
        )
        assert resp.status_code == 200
        assert resp.json().get("stack_id") is None

        resp = client.get(f"/stacks/{stack_id}")
        assert resp.status_code == 404
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stack_not_found_returns_404():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/stacks/99999")
        assert resp.status_code == 404
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

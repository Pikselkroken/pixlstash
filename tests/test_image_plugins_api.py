"""Tests for the image plugins API: list plugins and run the rotate plugin."""

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


def test_list_plugins_returns_rotate():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/pictures/plugins")
        assert resp.status_code == 200
        data = resp.json()
        plugin_names = [p["name"] for p in data.get("plugins", [])]
        assert "rotate" in plugin_names
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_rotate_plugin_swaps_dimensions():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.get(f"/pictures/{pic_id}/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        orig_width = meta.get("width")
        orig_height = meta.get("height")

        resp = client.post(
            "/pictures/plugins/rotate",
            json={
                "picture_ids": [pic_id],
                "parameters": {"direction": "90_right"},
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result.get("status") == "success"

        created_ids = result.get("created_picture_ids") or []
        assert created_ids, "Rotate plugin did not produce a new picture"
        new_pic_id = created_ids[0]

        resp = client.get(f"/pictures/{new_pic_id}/metadata")
        assert resp.status_code == 200
        new_meta = resp.json()

        # After 90° rotation, width and height should be swapped
        if orig_width and orig_height:
            assert new_meta.get("width") == orig_height
            assert new_meta.get("height") == orig_width
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_run_plugin_with_unknown_name_returns_404():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        resp = client.post(
            "/pictures/plugins/no_such_plugin",
            json={"picture_ids": [pic_id]},
        )
        assert resp.status_code == 404
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_run_plugin_without_picture_ids_returns_400():
    temp_dir, client, server = _setup()
    try:
        resp = client.post(
            "/pictures/plugins/rotate",
            json={"picture_ids": []},
        )
        assert resp.status_code == 400
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

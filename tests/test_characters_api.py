"""Tests for the Characters API: create, update, delete, and reference pictures."""

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


def test_create_character():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "Alice"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        char = data["character"]
        assert char["name"] == "Alice"
        assert char["id"] is not None
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_character_by_id():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "Bob"})
        char_id = resp.json()["character"]["id"]

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == char_id
        assert resp.json()["name"] == "Bob"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_patch_character_name_and_description():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "Charlie"})
        char_id = resp.json()["character"]["id"]

        resp = client.patch(
            f"/characters/{char_id}",
            json={"name": "Charles", "description": "Updated description"},
        )
        assert resp.status_code == 200

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Charles"
        assert resp.json()["description"] == "Updated description"

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Charles"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_delete_character_returns_success():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "DeleteMe"})
        char_id = resp.json()["character"]["id"]

        resp = client.delete(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted_id"] == char_id

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.json() is None
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_character_reference_pictures_empty_without_faces():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "NoFaces"})
        char_id = resp.json()["character"]["id"]

        resp = client.get(f"/characters/{char_id}/reference_pictures")
        assert resp.status_code == 200
        assert resp.json()["reference_picture_ids"] == []
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_delete_nonexistent_character_returns_404():
    temp_dir, client, server = _setup()
    try:
        resp = client.delete("/characters/99999")
        assert resp.status_code == 404
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

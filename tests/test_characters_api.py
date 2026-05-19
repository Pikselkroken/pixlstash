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


def test_get_characters_filtered_by_numeric_project_id():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "CharFilter Project"})
        assert resp.status_code == 200
        project_id = resp.json()["id"]

        resp = client.post(
            "/characters", json={"name": "InProject", "project_id": project_id}
        )
        assert resp.status_code == 200

        resp = client.post("/characters", json={"name": "NotInProject"})
        assert resp.status_code == 200

        resp = client.get(f"/characters?project_id={project_id}")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "InProject" in names
        assert "NotInProject" not in names
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_characters_filtered_by_unassigned():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "UnassignedFilter Project"})
        assert resp.status_code == 200
        project_id = resp.json()["id"]

        resp = client.post(
            "/characters", json={"name": "Assigned", "project_id": project_id}
        )
        assert resp.status_code == 200

        resp = client.post("/characters", json={"name": "Unassigned"})
        assert resp.status_code == 200

        resp = client.get("/characters?project_id=UNASSIGNED")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "Unassigned" in names
        assert "Assigned" not in names
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_characters_invalid_project_id_returns_400():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/characters?project_id=not-a-number")
        assert resp.status_code == 400
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_character_scoped_token_respects_project_id_filter():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "ScopedTokenProject"})
        assert resp.status_code == 200
        project_id = resp.json()["id"]

        resp = client.post(
            "/characters", json={"name": "CharInProject", "project_id": project_id}
        )
        assert resp.status_code == 200
        char_id = resp.json()["character"]["id"]

        resp = client.post(
            "/users/me/token",
            json={
                "description": "char token",
                "scope": "READ",
                "resource_type": "character",
                "resource_id": char_id,
            },
        )
        assert resp.status_code == 200
        char_token = resp.json()["token"]

        token_client = TestClient(server.api)

        # Filter by the correct project: character should be returned
        resp = token_client.get(
            f"/characters?project_id={project_id}",
            headers={"Authorization": f"Bearer {char_token}"},
        )
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()]
        assert char_id in ids

        # Filter by UNASSIGNED: character belongs to a project, so it must not appear
        resp = token_client.get(
            "/characters?project_id=UNASSIGNED",
            headers={"Authorization": f"Bearer {char_token}"},
        )
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()]
        assert char_id not in ids
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

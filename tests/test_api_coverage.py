"""Tests for authentication, config, picture listing, tags, and score API."""

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
    results = result.get("results") or []
    assert results, "No pictures imported"
    return results[0]["picture_id"]


def test_logout_clears_session():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/check-session")
        assert resp.status_code == 200

        resp = client.post("/logout")
        assert resp.status_code == 200

        resp = client.get("/check-session")
        assert resp.status_code == 401
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_check_session_unauthenticated():
    temp_dir, client, server = _setup()
    try:
        client.post("/logout")
        resp = client.get("/check-session")
        assert resp.status_code == 401
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_api_token_lifecycle():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/users/me/token", json={"description": "test token"})
        assert resp.status_code == 200
        token_id = resp.json().get("token_id")
        assert token_id

        resp = client.get("/users/me/token")
        assert resp.status_code == 200
        assert any(t["id"] == token_id for t in resp.json())

        resp = client.delete(f"/users/me/token/{token_id}")
        assert resp.status_code == 200

        resp = client.get("/users/me/token")
        assert resp.status_code == 200
        assert not any(t["id"] == token_id for t in resp.json())
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_user_config():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/users/me/config")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_patch_user_config():
    temp_dir, client, server = _setup()
    try:
        resp = client.patch("/users/me/config", json={"theme_mode": "dark"})
        assert resp.status_code == 200

        resp = client.get("/users/me/config")
        assert resp.status_code == 200
        assert resp.json().get("theme_mode") == "dark"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_watch_folders():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/server-config/watch-folders")
        assert resp.status_code == 200
        data = resp.json()
        assert "watch_folders" in data
        assert isinstance(data["watch_folders"], list)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_sort_mechanisms():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/sort_mechanisms")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_sort_stability():
    temp_dir, client, server = _setup()
    try:
        _upload_picture(client, "Bad1.png")
        _upload_picture(client, "Bad2.png")

        resp1 = client.get("/pictures?sort=SCORE&descending=true")
        assert resp1.status_code == 200
        resp2 = client.get("/pictures?sort=SCORE&descending=true")
        assert resp2.status_code == 200

        ids1 = [p["id"] for p in resp1.json()]
        ids2 = [p["id"] for p in resp2.json()]
        assert ids1 == ids2
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_filter_pictures_by_tag():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client, "Bad1.png")
        _upload_picture(client, "Bad2.png")

        unique_tag = "unique_filter_test_tag"
        resp = client.post(f"/pictures/{pic_id}/tags", json={"tag": unique_tag})
        assert resp.status_code == 200

        resp = client.get(f"/pictures?tag={unique_tag}")
        assert resp.status_code == 200
        pics = resp.json()
        assert len(pics) == 1
        assert pics[0]["id"] == pic_id
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_picture_metadata_fields():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.get(f"/pictures/{pic_id}/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("id") == pic_id
        assert "format" in data
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_thumbnail_returns_image():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.get(f"/pictures/thumbnails/{pic_id}.webp")
        assert resp.status_code == 200
        assert "image" in resp.headers.get("content-type", "")
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_add_remove_tag_to_picture():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.post(f"/pictures/{pic_id}/tags", json={"tag": "mytesttag"})
        assert resp.status_code == 200

        resp = client.get(f"/pictures/{pic_id}/tags")
        assert resp.status_code == 200
        tags = resp.json().get("tags", [])
        mytag = next((t for t in tags if t["tag"] == "mytesttag"), None)
        assert mytag, "Tag not found after adding"
        tag_id = mytag["id"]

        resp = client.delete(f"/pictures/{pic_id}/tags/{tag_id}")
        assert resp.status_code == 200

        resp = client.get(f"/pictures/{pic_id}/tags")
        assert resp.status_code == 200
        tags_after = resp.json().get("tags", [])
        assert not any(t["tag"] == "mytesttag" for t in tags_after)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_all_tags_with_counts():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "countabletag"})

        resp = client.get("/tags")
        assert resp.status_code == 200
        tags = resp.json()
        assert isinstance(tags, list)
        matching = [t for t in tags if t.get("tag") == "countabletag"]
        assert matching, "Expected tag not found in /tags response"
        assert matching[0]["count"] >= 1
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_tag_fetch():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")
        client.post(f"/pictures/{pic_id1}/tags", json={"tag": "bulktag1"})
        client.post(f"/pictures/{pic_id2}/tags", json={"tag": "bulktag2"})

        resp = client.post(
            "/pictures/tags/bulk_fetch",
            json={"picture_ids": [pic_id1, pic_id2]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        returned_ids = {item["id"] for item in data}
        assert pic_id1 in returned_ids
        assert pic_id2 in returned_ids
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_patch_picture_score():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.patch(f"/pictures/{pic_id}", json={"score": 7})
        assert resp.status_code == 200

        resp = client.get(f"/pictures/{pic_id}/metadata")
        assert resp.status_code == 200
        assert resp.json()["score"] == 7
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

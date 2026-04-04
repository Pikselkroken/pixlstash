"""Tests for the Tag Predictions API: list, confirm, reject, and delete."""

import gc
import json
import os
import tempfile
from datetime import datetime

from fastapi.testclient import TestClient

from pixlstash.db_models.tag_prediction import TagPrediction
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


def _upload_picture(client):
    img_path = os.path.join(PICTURES_DIR, "Bad1.png")
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(client, [("file", ("Bad1.png", f, "image/png"))])
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


def _seed_prediction(server, pic_id, tag, confidence=0.9, status="PENDING"):
    def insert(session):
        prediction = TagPrediction(
            picture_id=pic_id,
            tag=tag,
            confidence=confidence,
            model_version="test-v1",
            status=status,
            predicted_at=datetime.utcnow(),
        )
        session.add(prediction)
        session.commit()

    server.vault.db.run_task(insert)


def test_get_tag_predictions_empty():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.get(f"/pictures/{pic_id}/tag_predictions")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_confirm_prediction_adds_tag():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "sunny")

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/sunny/confirm")
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"

        resp = client.get(f"/pictures/{pic_id}/tags")
        assert resp.status_code == 200
        tags = resp.json().get("tags", [])
        assert any(t["tag"] == "sunny" for t in tags)

        resp = client.get(f"/pictures/{pic_id}/tag_predictions?status=CONFIRMED")
        assert resp.status_code == 200
        confirmed = resp.json()
        assert any(p["tag"] == "sunny" and p["status"] == "CONFIRMED" for p in confirmed)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_reject_prediction_does_not_add_tag():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "rainy")

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/rainy/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

        resp = client.get(f"/pictures/{pic_id}/tags")
        assert resp.status_code == 200
        tags = resp.json().get("tags", [])
        assert not any(t["tag"] == "rainy" for t in tags)

        resp = client.get(f"/pictures/{pic_id}/tag_predictions?status=REJECTED")
        assert resp.status_code == 200
        rejected = resp.json()
        assert any(p["tag"] == "rainy" and p["status"] == "REJECTED" for p in rejected)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_delete_tag_predictions():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "cloudy")

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/delete")
        assert resp.status_code == 200

        resp = client.get(f"/pictures/{pic_id}/tag_predictions")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_confirm_nonexistent_prediction_returns_404():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/nonexistenttag/confirm")
        assert resp.status_code == 404
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

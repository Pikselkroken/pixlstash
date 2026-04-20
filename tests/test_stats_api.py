"""Tests for the GET /pictures/stats endpoint."""

import gc
import json
import os
import tempfile
import time

from fastapi.testclient import TestClient

import pixlstash.routes.pictures as pictures_module
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


def test_stats_basic_counts():
    """Total, tagged, untagged counts are accurate and response shape is correct."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        _upload_picture(client, "Bad2.png")

        # Tag one picture only.
        resp = client.post(f"/pictures/{pic_id1}/tags", json={"tag": "solo_tag"})
        assert resp.status_code == 200

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 2
        assert data["tagged"] == 1
        assert data["untagged"] == 1
        assert data["total_tags"] == 1
        assert isinstance(data["avg_tags_per_image"], float)
        assert isinstance(data["top_tags"], list)
        assert any(t["tag"] == "solo_tag" for t in data["top_tags"])
        # By default these expensive sections are empty.
        assert data["top_cooccurrences"] == []
        assert data["confidence_histogram"] == []
        assert data["score_distribution"] == []
        assert data["smart_score_distribution"] == []
        assert data["resolution_distribution"] == []
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_tag_filter_reduces_total():
    """Passing tag= restricts the picture population used for counting."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        _upload_picture(client, "Bad2.png")

        client.post(f"/pictures/{pic_id1}/tags", json={"tag": "rare_filter_tag"})

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?tag=rare_filter_tag")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?tag=nonexistent_tag_xyz")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_score_filter():
    """min_score/max_score params restrict the picture population."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        client.patch(f"/pictures/{pic_id1}", json={"score": 5})
        client.patch(f"/pictures/{pic_id2}", json={"score": 2})

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?min_score=4")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?max_score=3")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?min_score=1&max_score=5")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_include_picture():
    """include=picture returns score_distribution, smart_score_distribution, resolution_distribution."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        pic_id = _upload_picture(client, "Bad1.png")
        client.patch(f"/pictures/{pic_id}", json={"score": 3})

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?include=picture")
        assert resp.status_code == 200
        data = resp.json()

        score_dist = data["score_distribution"]
        assert isinstance(score_dist, list)
        labels = [entry["label"] for entry in score_dist]
        assert "3" in labels
        entry_3 = next(e for e in score_dist if e["label"] == "3")
        assert entry_3["count"] == 1

        assert isinstance(data["smart_score_distribution"], list)
        assert len(data["smart_score_distribution"]) > 0
        assert isinstance(data["resolution_distribution"], list)
        assert len(data["resolution_distribution"]) > 0
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_include_cooc():
    """include=cooc returns top_cooccurrences when two tags share a picture."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        pic_id = _upload_picture(client, "Bad1.png")
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "cooc_a"})
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "cooc_b"})

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?include=cooc")
        assert resp.status_code == 200
        data = resp.json()

        cooc = data["top_cooccurrences"]
        assert isinstance(cooc, list)
        assert len(cooc) > 0
        pair = cooc[0]
        assert set(pair["tags"]) == {"cooc_a", "cooc_b"}
        assert pair["count"] == 1
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_include_conf():
    """include=conf returns non-empty confidence_histogram and regular_tags."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        pic_id = _upload_picture(client, "Bad1.png")
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "conf_tag"})

        pictures_module._stats_cache.clear()
        resp = client.get("/pictures/stats?include=conf")
        assert resp.status_code == 200
        data = resp.json()

        assert isinstance(data["confidence_histogram"], list)
        assert isinstance(data["regular_tags"], list)
        assert "conf_tag" in data["regular_tags"]
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_ttl_caching():
    """Identical query params return cached data within the TTL window."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        _upload_picture(client, "Bad1.png")

        pictures_module._stats_cache.clear()
        resp1 = client.get("/pictures/stats")
        assert resp1.status_code == 200
        data1 = resp1.json()

        # Upload a second picture — without clearing the cache, the count must
        # remain the same because the response is served from cache.
        _upload_picture(client, "Bad2.png")

        resp2 = client.get("/pictures/stats")
        assert resp2.status_code == 200
        data2 = resp2.json()

        assert data2["total"] == data1["total"], (
            "Stats should be cached and not reflect newly-imported picture within TTL"
        )
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_cache_expires_after_ttl(monkeypatch):
    """After the TTL elapses the result is recomputed from the database."""
    temp_dir, client, server = _setup()
    pictures_module._stats_cache.clear()
    try:
        _upload_picture(client, "Bad1.png")

        pictures_module._stats_cache.clear()
        resp1 = client.get("/pictures/stats")
        assert resp1.status_code == 200
        assert resp1.json()["total"] == 1

        # Expire the cache entry by back-dating its timestamp.
        expired_ts = time.monotonic() - (pictures_module._STATS_TTL + 1)
        for key in list(pictures_module._stats_cache.keys()):
            _, data = pictures_module._stats_cache[key]
            pictures_module._stats_cache[key] = (expired_ts, data)

        _upload_picture(client, "Bad2.png")

        resp2 = client.get("/pictures/stats")
        assert resp2.status_code == 200
        assert resp2.json()["total"] == 2, (
            "Stats should be recomputed after TTL expires"
        )
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

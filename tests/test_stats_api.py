"""Tests for the GET /pictures/stats endpoint."""

import gc
import json
import os
import tempfile
import time

from fastapi.testclient import TestClient

import pixlstash.routes.pictures as pictures_module  # noqa: F401  (kept for backward compat with other tests)
from pixlstash.utils.service import picture_stats as picture_stats_module
from pixlstash.utils.service.picture_stats import STATS_TTL, clear_stats_cache
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
    clear_stats_cache()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        _upload_picture(client, "Bad2.png")

        # Tag one picture only.
        resp = client.post(f"/pictures/{pic_id1}/tags", json={"tag": "solo_tag"})
        assert resp.status_code == 200

        clear_stats_cache()
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
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_tag_filter_reduces_total():
    """Passing tag= restricts the picture population used for counting."""
    temp_dir, client, server = _setup()
    clear_stats_cache()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        _upload_picture(client, "Bad2.png")

        client.post(f"/pictures/{pic_id1}/tags", json={"tag": "rare_filter_tag"})

        clear_stats_cache()
        resp = client.get("/pictures/stats?tag=rare_filter_tag")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

        clear_stats_cache()
        resp = client.get("/pictures/stats?tag=nonexistent_tag_xyz")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_score_filter():
    """min_score/max_score params restrict the picture population."""
    temp_dir, client, server = _setup()
    clear_stats_cache()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        client.patch(f"/pictures/{pic_id1}", json={"score": 5})
        client.patch(f"/pictures/{pic_id2}", json={"score": 2})

        clear_stats_cache()
        resp = client.get("/pictures/stats?min_score=4")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        clear_stats_cache()
        resp = client.get("/pictures/stats?max_score=3")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        clear_stats_cache()
        resp = client.get("/pictures/stats?min_score=1&max_score=5")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_include_picture():
    """include=picture returns score_distribution, smart_score_distribution, resolution_distribution."""
    temp_dir, client, server = _setup()
    clear_stats_cache()
    try:
        pic_id = _upload_picture(client, "Bad1.png")
        client.patch(f"/pictures/{pic_id}", json={"score": 3})

        clear_stats_cache()
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
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_unscored_histogram_bucket_matches_what_the_unscored_filter_returns():
    """The Unscored bar and ``unscored=1`` must agree, score 0 included.

    Clicking the current star again writes a literal 0 via ``POST
    /pictures/apply-scores``, and nothing normalises it back to NULL. The
    histogram used to read only the NULL group, so a score-0 picture fell into no
    bucket at all: the bars did not sum to the library and the sidebar count
    disagreed with the grid on the very first click.
    """
    temp_dir, client, server = _setup()
    clear_stats_cache()
    try:
        cleared = _upload_picture(client, "Bad1.png")
        never = _upload_picture(client, "Bad2.png")
        rated = _upload_picture(client, "Changed1.png")

        resp = client.post(
            "/pictures/apply-scores",
            json={"scores": {str(cleared): 0, str(rated): 4}, "only_unscored": False},
        )
        assert resp.status_code == 200

        clear_stats_cache()
        data = client.get("/pictures/stats?include=picture").json()
        score_dist = {e["label"]: e["count"] for e in data["score_distribution"]}
        assert score_dist["Unscored"] == 2, "score 0 belongs in Unscored with NULL"
        assert score_dist["4"] == 1
        assert sum(score_dist.values()) == data["total"], (
            "every picture lands in exactly one bar"
        )

        resp = client.get("/pictures?unscored=1&limit=100")
        assert resp.status_code == 200
        returned = {p["id"] for p in resp.json()}
        assert returned == {cleared, never}, "both NULL and 0, and nothing rated"
        assert len(returned) == score_dist["Unscored"]

        # The over-blocking direction: the rated picture is still reachable, and
        # an unfiltered listing is unaffected.
        resp = client.get("/pictures?min_score=1&limit=100")
        assert {p["id"] for p in resp.json()} == {rated}
        resp = client.get("/pictures?limit=100")
        assert {p["id"] for p in resp.json()} == {cleared, never, rated}

        # The param reaches the stats population too, so the sidebar totals track
        # the grid rather than the whole library.
        clear_stats_cache()
        assert client.get("/pictures/stats?unscored=1").json()["total"] == 2
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_include_cooc():
    """include=cooc returns top_cooccurrences when two tags share a picture."""
    temp_dir, client, server = _setup()
    clear_stats_cache()
    try:
        pic_id = _upload_picture(client, "Bad1.png")
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "cooc_a"})
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "cooc_b"})

        clear_stats_cache()
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
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_include_conf():
    """include=conf returns non-empty confidence_histogram and regular_tags."""
    temp_dir, client, server = _setup()
    clear_stats_cache()
    try:
        pic_id = _upload_picture(client, "Bad1.png")
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "conf_tag"})

        clear_stats_cache()
        resp = client.get("/pictures/stats?include=conf")
        assert resp.status_code == 200
        data = resp.json()

        assert isinstance(data["confidence_histogram"], list)
        assert isinstance(data["regular_tags"], list)
        assert "conf_tag" in data["regular_tags"]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stats_cache_expires_after_ttl(monkeypatch):
    """After the TTL elapses the result is recomputed from the database."""
    temp_dir, client, server = _setup()
    clear_stats_cache()
    try:
        _upload_picture(client, "Bad1.png")

        clear_stats_cache()
        resp1 = client.get("/pictures/stats")
        assert resp1.status_code == 200
        assert resp1.json()["total"] == 1

        # Expire the cache entry by back-dating its timestamp.
        expired_ts = time.monotonic() - (STATS_TTL + 1)
        for key in list(picture_stats_module._stats_cache.keys()):
            _, data = picture_stats_module._stats_cache[key]
            picture_stats_module._stats_cache[key] = (expired_ts, data)

        _upload_picture(client, "Bad2.png")

        resp2 = client.get("/pictures/stats")
        assert resp2.status_code == 200
        assert resp2.json()["total"] == 2, (
            "Stats should be recomputed after TTL expires"
        )
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()

"""Tests for the Tag Predictions API: list, confirm, reject, and delete."""

import gc
import json
import os
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from pixlstash.db_models import Picture
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.server import Server
from pixlstash.tasks import TaskType
from tests.utils import bearer_client, mint_read_token, upload_pictures_and_wait

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


def _upload_picture(client):
    img_path = os.path.join(PICTURES_DIR, "Bad1.png")
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(
            client, [("file", ("Bad1.png", f, "image/png"))]
        )
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
            predicted_at=datetime.now(timezone.utc),
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
        server.close()
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
        assert any(
            p["tag"] == "sunny" and p["status"] == "CONFIRMED" for p in confirmed
        )
    finally:
        server.close()
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
        server.close()
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
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_confirm_nonexistent_prediction_returns_404():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/nonexistenttag/confirm")
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_label_thresholds_uses_saved_offset_by_default(monkeypatch):
    temp_dir, client, server = _setup()
    try:
        # The loader now lives in utils.service.anomaly_thresholds (a leaf module the
        # scorer can import without a cycle); patch the name the route actually bound.
        from pixlstash.routes import tag_predictions as tag_predictions_routes

        monkeypatch.setattr(
            tag_predictions_routes,
            "load_raw_label_thresholds",
            lambda meta_path: {"cat": 0.4, "dog": 0.2},
        )
        server.vault.set_pixlstash_tagger_threshold_offset(0.1)

        resp = client.get("/tagger/label-thresholds")
        assert resp.status_code == 200
        rows = {r["label"]: r for r in resp.json()}
        assert rows["cat"]["base_threshold"] == 0.4
        assert rows["cat"]["effective_threshold"] == 0.5
        assert rows["dog"]["effective_threshold"] == 0.3
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_label_thresholds_offset_query_overrides_saved(monkeypatch):
    temp_dir, client, server = _setup()
    try:
        # The loader now lives in utils.service.anomaly_thresholds (a leaf module the
        # scorer can import without a cycle); patch the name the route actually bound.
        from pixlstash.routes import tag_predictions as tag_predictions_routes

        monkeypatch.setattr(
            tag_predictions_routes,
            "load_raw_label_thresholds",
            lambda meta_path: {"cat": 0.4, "dog": 0.2},
        )
        # Saved offset is positive, but the preview passes its own (unsaved) value.
        server.vault.set_pixlstash_tagger_threshold_offset(0.1)

        resp = client.get("/tagger/label-thresholds", params={"offset": -0.5})
        assert resp.status_code == 200
        rows = {r["label"]: r for r in resp.json()}
        # 0.4 - 0.5 and 0.2 - 0.5 both fall below the 0.01 floor.
        assert rows["cat"]["effective_threshold"] == 0.01
        assert rows["dog"]["effective_threshold"] == 0.01

        # Out-of-range offsets are rejected by the query bounds.
        resp = client.get("/tagger/label-thresholds", params={"offset": 5})
        assert resp.status_code == 422
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# GET /tag_predictions/labels: the label ledger in bulk (#2675)
# ---------------------------------------------------------------------------

LABELS = "/api/v1/tag_predictions/labels"

# Finders that write TagPrediction rows into the pictures seeded below.
_CONFLICTING_FINDERS = (TaskType.TAGGER, TaskType.TAG_PREDICTION_BACKFILL)


def _upload_named(client, name):
    with open(os.path.join(PICTURES_DIR, name), "rb") as f:
        result = upload_pictures_and_wait(client, [("file", (name, f, "image/png"))])
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


@pytest.fixture(scope="module")
def ledger():
    """One server holding a small hand-written ledger, shared by the tests below.

    ``live`` carries a human POS, a human NEG, a propagated POS and an unreviewed
    row; ``scrapped`` carries a human NEG and is on the Scrapheap. Nothing below
    writes, except the confirm in the end-to-end test, which uses its own tag.
    """
    temp_dir, client, server = _setup()
    try:
        for task_type in _CONFLICTING_FINDERS:
            server.vault._planner_work_finders.pop(task_type)
        server.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)
        live = _upload_named(client, "Bad1.png")
        scrapped = _upload_named(client, "Bad2.png")

        def seed(session):
            # Predictions the import's own tagging pass may have written.
            for row in session.query(TagPrediction).all():
                session.delete(row)

            def add(pic, tag, state, source, month, version=None, confidence=None):
                session.add(
                    TagPrediction(
                        picture_id=pic,
                        tag=tag,
                        confidence=0.5,
                        model_version="test-v2",
                        label_state=state,
                        label_source=source,
                        labeled_at=(
                            datetime(2026, month, 1, tzinfo=timezone.utc)
                            if source
                            else None
                        ),
                        label_model_version=version,
                        label_confidence=confidence,
                    )
                )

            add(live, "lbl-pos", "POS", "human", 1, "test-v1", 0.8)
            add(live, "lbl-neg", "NEG", "human", 3)
            add(live, "lbl-prop", "POS", "propagated", 2)
            add(live, "lbl-unreviewed", "UNKNOWN", None, 0)
            add(scrapped, "lbl-pos", "NEG", "human", 2)
            session.get(Picture, scrapped).deleted = True
            session.commit()

        server.vault.db.run_task(seed)
        yield client, server, live, scrapped
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _labels(client, **params):
    resp = client.get(LABELS, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _keys(body):
    return [(r["picture_id"], r["tag"], r["label_state"]) for r in body["labels"]]


def test_labels_default_is_human_verdicts_on_live_pictures(ledger):
    client, _server, live, _scrapped = ledger
    body = _labels(client, tag="lbl-pos")
    # The scrapped picture's NEG on the same tag is left out.
    assert body == {
        "total": 1,
        "labels": [
            {
                "picture_id": live,
                "tag": "lbl-pos",
                "label_state": "POS",
                "label_source": "human",
                "labeled_at": "2026-01-01T00:00:00+00:00",
                "label_model_version": "test-v1",
                "label_confidence": 0.8,
            }
        ],
    }

    everything = _keys(_labels(client))
    assert (live, "lbl-pos", "POS") in everything
    assert (live, "lbl-neg", "NEG") in everything
    # Not human, not a verdict.
    assert not [k for k in everything if k[1] in ("lbl-prop", "lbl-unreviewed")]


def test_labels_filters(ledger):
    client, _server, live, _scrapped = ledger
    assert (live, "lbl-pos", "POS") not in _keys(_labels(client, label_state="NEG"))
    assert (live, "lbl-neg", "NEG") in _keys(_labels(client, label_state="NEG"))
    assert _keys(_labels(client, label_source="propagated", tag="lbl-prop")) == [
        (live, "lbl-prop", "POS")
    ]
    assert _labels(client, label_source="model", tag="lbl-prop")["total"] == 0

    # lbl-neg was recorded at 2026-03-01T00:00:00 UTC; the bound is inclusive
    # and an offset is converted, not dropped.
    at_bound = _keys(_labels(client, labeled_since="2026-03-01T01:00:00+01:00"))
    assert (live, "lbl-neg", "NEG") in at_bound
    assert (live, "lbl-pos", "POS") not in at_bound
    after = _keys(_labels(client, labeled_since="2026-03-01T00:00:01"))
    assert (live, "lbl-neg", "NEG") not in after


def test_labels_pages_in_id_order(ledger):
    client, _server, live, _scrapped = ledger
    whole = _labels(client)
    assert whole["total"] >= 2
    # Seeded in this order, so prediction id order puts the POS first.
    keys = _keys(whole)
    assert keys.index((live, "lbl-pos", "POS")) < keys.index((live, "lbl-neg", "NEG"))
    first = _labels(client, limit=1)
    second = _labels(client, limit=1, offset=1)
    assert first["total"] == second["total"] == whole["total"]
    assert first["labels"] == whole["labels"][:1]
    assert second["labels"] == whole["labels"][1:2]


def test_labels_refuses_values_outside_the_contract(ledger):
    client, _server, _live, _scrapped = ledger
    for params in (
        {"label_state": "UNKNOWN"},
        {"label_source": "bogus"},
        {"limit": 0},
        {"limit": 5001},
        {"offset": -1},
        {"labeled_since": "yesterday"},
        # Parses, but has no UTC equivalent.
        {"labeled_since": "0001-01-01T00:00:00+01:00"},
    ):
        assert client.get(LABELS, params=params).status_code == 422, params


def test_labels_sees_a_verdict_given_through_the_api(ledger):
    client, server, live, _scrapped = ledger
    _seed_prediction(server, live, "lbl-confirmed", confidence=0.7)
    assert _labels(client, tag="lbl-confirmed")["total"] == 0

    resp = client.post(f"/pictures/{live}/tag_predictions/lbl-confirmed/confirm")
    assert resp.status_code == 200

    (row,) = _labels(client, tag="lbl-confirmed")["labels"]
    assert (row["picture_id"], row["label_state"], row["label_source"]) == (
        live,
        "POS",
        "human",
    )
    assert (row["label_model_version"], row["label_confidence"]) == ("test-v1", 0.7)
    assert row["labeled_at"].endswith("+00:00")


def test_labels_refuses_a_read_token_and_not_the_owner(ledger):
    client, server, _live, _scrapped = ledger
    reader = bearer_client(server, mint_read_token(client, "ledger-reader"))
    # Owner control first: the path resolves, so the 403 below is the refusal.
    assert client.get(LABELS).status_code == 200
    assert reader.get(LABELS).status_code == 403
    # A token scoped to a picture that has verdicts is refused all the same.
    scoped = bearer_client(
        server,
        mint_read_token(
            client, "ledger-scoped", resource_type="picture", resource_id=_live
        ),
    )
    assert scoped.get(f"/api/v1/pictures/{_live}/tag_predictions").status_code == 200
    assert scoped.get(LABELS).status_code == 403

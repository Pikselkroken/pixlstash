"""Tests for the human-label ledger on TagPrediction.

Verifies that every human accept/reject path records a durable, symmetric POS/NEG
supervision signal (``label_state``/``label_source='human'``) — including the paths that
previously dropped the negative entirely (removing a tag, dismissing an "add" suggestion)
— and that the tagger never clobbers a human label.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.server import Server
from pixlstash.services import tag_prediction_service, tag_suggestion_service
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


def _upload_picture(client, name="Bad1.png"):
    img_path = os.path.join(PICTURES_DIR, name)
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(client, [("file", (name, f, "image/png"))])
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


def _ledger(server, pic_id, tag):
    """Return (label_state, label_source, label_model_version, label_confidence) or None."""

    def _fetch(session):
        row = session.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == pic_id, TagPrediction.tag == tag
            )
        ).first()
        if row is None:
            return None
        return (
            row.label_state,
            row.label_source,
            row.label_model_version,
            row.label_confidence,
        )

    return server.vault.db.run_immediate_read_task(_fetch)


def _seed_prediction(server, pic_id, tag, confidence, model_version, status="PENDING"):
    def insert(session):
        session.add(
            TagPrediction(
                picture_id=pic_id,
                tag=tag,
                confidence=confidence,
                model_version=model_version,
                status=status,
            )
        )
        session.commit()

    server.vault.db.run_task(insert)


def _seed_suggestion(server, pic_id, tag, direction, score=1.0):
    def insert(session):
        s = TagSuggestion(
            picture_id=pic_id,
            tag=tag,
            direction=direction,
            source="near_neighbor",
            score=score,
            reason="near-twin disagrees",
        )
        session.add(s)
        session.commit()
        session.refresh(s)
        return s.id

    return server.vault.db.run_task(insert)


def test_confirm_records_pos_and_snapshots_prediction():
    """Accepting the tagger's call records POS and freezes the version/confidence agreed."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "malformed hand", 0.88, "epoch-50")

        tag_prediction_service.confirm_tag_prediction(
            server.vault, pic_id, "malformed hand"
        )

        state, source, lmv, lconf = _ledger(server, pic_id, "malformed hand")
        assert state == "POS"
        assert source == "human"
        # The version/confidence the reviewer agreed with are snapshotted.
        assert lmv == "epoch-50"
        assert abs(lconf - 0.88) < 1e-6
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_reject_records_neg_and_snapshots_prediction():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "malformed hand", 0.92, "epoch-50")

        tag_prediction_service.reject_tag_prediction(
            server.vault, pic_id, "malformed hand"
        )

        state, source, lmv, lconf = _ledger(server, pic_id, "malformed hand")
        assert state == "NEG"
        assert source == "human"
        assert lmv == "epoch-50"
        assert abs(lconf - 0.92) < 1e-6
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_manual_remove_records_neg_that_survives_lost_tag():
    """The hardest case: removing a tag must leave a reviewed-negative behind."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        # Apply the anomaly tag, then look up its tag id to remove it via the API.
        resp = client.post(f"/pictures/{pic_id}/tags", json={"tag": "malformed hand"})
        assert resp.status_code == 200
        # The manual add is itself a human POS.
        assert _ledger(server, pic_id, "malformed hand")[:2] == ("POS", "human")

        tags = client.get(f"/pictures/{pic_id}/tags").json()["tags"]
        tag_id = next(t["id"] for t in tags if t["tag"] == "malformed hand")
        resp = client.delete(f"/pictures/{pic_id}/tags/{tag_id}")
        assert resp.status_code == 200

        # Tag row is gone, but the NEG supervision survives.
        assert not any(
            t["tag"] == "malformed hand"
            for t in client.get(f"/pictures/{pic_id}/tags").json()["tags"]
        )
        assert _ledger(server, pic_id, "malformed hand")[:2] == ("NEG", "human")
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_manual_add_of_content_tag_is_not_recorded():
    """Non-anomaly content tags are outside the tagger's space — no ledger pollution."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        resp = client.post(f"/pictures/{pic_id}/tags", json={"tag": "beach sunset"})
        assert resp.status_code == 200
        assert _ledger(server, pic_id, "beach sunset") is None
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_accept_remove_suggestion_records_neg():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "malformed hand"})
        sid = _seed_suggestion(server, pic_id, "malformed hand", "remove")

        tag_suggestion_service.accept_suggestion(server.vault, sid)

        assert _ledger(server, pic_id, "malformed hand")[:2] == ("NEG", "human")
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_dismiss_add_suggestion_records_neg():
    """Dismissing an 'add' suggestion asserts the tag is correctly absent → human NEG."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        sid = _seed_suggestion(server, pic_id, "bad anatomy", "add")

        tag_suggestion_service.dismiss_suggestion(server.vault, sid)

        assert _ledger(server, pic_id, "bad anatomy")[:2] == ("NEG", "human")
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_dismiss_remove_suggestion_records_pos():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        client.post(f"/pictures/{pic_id}/tags", json={"tag": "malformed hand"})
        sid = _seed_suggestion(server, pic_id, "malformed hand", "remove")

        tag_suggestion_service.dismiss_suggestion(server.vault, sid)

        assert _ledger(server, pic_id, "malformed hand")[:2] == ("POS", "human")
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_reopen_clears_dismiss_ledger():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        sid = _seed_suggestion(server, pic_id, "bad anatomy", "add")
        tag_suggestion_service.dismiss_suggestion(server.vault, sid)
        assert _ledger(server, pic_id, "bad anatomy")[:2] == ("NEG", "human")

        tag_suggestion_service.reopen_suggestion(server.vault, sid)
        state, source, _, _ = _ledger(server, pic_id, "bad anatomy")
        assert state == "UNKNOWN"
        assert source is None
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_tagger_does_not_clobber_human_label():
    """A model-upgrade write must preserve a human-labeled row (not_human_labeled)."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "malformed hand", 0.92, "epoch-50")
        tag_prediction_service.reject_tag_prediction(
            server.vault, pic_id, "malformed hand"
        )
        assert _ledger(server, pic_id, "malformed hand")[:2] == ("NEG", "human")

        # delete_tag_predictions (the re-tag bulk path) must not remove the human row.
        tag_prediction_service.delete_tag_predictions(server.vault, pic_id)
        assert _ledger(server, pic_id, "malformed hand")[:2] == ("NEG", "human")
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

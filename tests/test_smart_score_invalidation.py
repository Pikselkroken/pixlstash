"""Tests that a cached ``Picture.smart_score`` is invalidated when — and only when —
a picture's anomaly/penalised-tag state changes.

``smart_score`` is a cached derived column and ``SmartScoreTask`` only picks up pictures
whose score is ``NULL``, so an edit that moves the scorer's anomaly inputs must clear it
or the stored score silently goes stale. The scorer's anomaly inputs come from
``TagPrediction`` rows in the anomaly vocabulary (see
``pixlstash.picture_scoring.fetch_anomaly_confidences``), so these tests assert both
directions: a penalised-tag edit invalidates, a content-tag edit does not.
"""

import json
import os
import tempfile
from datetime import datetime

import numpy as np
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.db_models import Picture, Tag
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.server import Server
from pixlstash.tasks.smart_score_task import SmartScoreTask
from pixlstash.tasks.tag_task import TagTask
from tests.utils import upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")

# In ANOMALY_PENALTY_TAGS — feeds the smart score's anomaly penalty.
PENALISED_TAG = "watermark"
# Not in the anomaly vocabulary — a pure content tag the score must ignore.
CONTENT_TAG = "sunset"


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 0}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _upload_picture(client, name="Bad1.png"):
    """Upload one picture and return its id.

    Pass a distinct *name* per call when a test needs several pictures — the importer
    deduplicates by content, so re-uploading the same file yields the same picture.
    """
    img_path = os.path.join(PICTURES_DIR, name)
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(client, [("file", (name, f, "image/png"))])
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


def _seed_prediction(server, pic_id, tag, confidence=0.9, status="PENDING"):
    def insert(session):
        session.add(
            TagPrediction(
                picture_id=pic_id,
                tag=tag,
                confidence=confidence,
                model_version="test-v1",
                status=status,
                predicted_at=datetime.utcnow(),
            )
        )
        session.commit()

    server.vault.db.run_task(insert)


def _set_smart_score(server, pic_id, value=0.5, with_embedding=True):
    """Give the picture a stored smart score (and an embedding so the finder sees it)."""

    def _apply(session):
        pic = session.get(Picture, pic_id)
        pic.smart_score = value
        if with_embedding and pic.image_embedding is None:
            pic.image_embedding = np.random.rand(128).astype(np.float32).tobytes()
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_apply)


def _get_smart_score(server, pic_id):
    return server.vault.db.run_task(lambda s: s.get(Picture, pic_id).smart_score)


def _find_missing_ids(server):
    return server.vault.db.run_task(
        lambda s: [
            p.id for p in SmartScoreTask.find_pictures_missing_smart_score(s, 50)
        ]
    )


def _tag_id_for(server, pic_id, tag):
    return server.vault.db.run_task(
        lambda s: s.exec(
            select(Tag.id).where(Tag.picture_id == pic_id, Tag.tag == tag)
        ).first()
    )


def test_adding_penalised_tag_invalidates_and_requeues():
    """Adding an anomaly tag clears the cached score and re-queues the picture."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _set_smart_score(server, pic_id, 0.5)
        assert _get_smart_score(server, pic_id) == 0.5

        resp = client.post(f"/pictures/{pic_id}/tags", json={"tag": PENALISED_TAG})
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) is None
        assert pic_id in _find_missing_ids(server)
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_adding_content_tag_does_not_invalidate():
    """A non-penalised tag must not invalidate — over-invalidating re-scores the library."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(f"/pictures/{pic_id}/tags", json={"tag": CONTENT_TAG})
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) == 0.5
        assert pic_id not in _find_missing_ids(server)
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_removing_penalised_tag_invalidates():
    """Removing an anomaly tag records a human NEG, moving the scorer's inputs."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        assert (
            client.post(
                f"/pictures/{pic_id}/tags", json={"tag": PENALISED_TAG}
            ).status_code
            == 200
        )
        _set_smart_score(server, pic_id, 0.5)

        tag_id = _tag_id_for(server, pic_id, PENALISED_TAG)
        assert tag_id is not None
        resp = client.delete(f"/pictures/{pic_id}/tags/{tag_id}")
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) is None
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_removing_content_tag_does_not_invalidate():
    """Removing a content tag leaves the anomaly inputs — and the cached score — alone."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        assert (
            client.post(
                f"/pictures/{pic_id}/tags", json={"tag": CONTENT_TAG}
            ).status_code
            == 200
        )
        _set_smart_score(server, pic_id, 0.5)

        tag_id = _tag_id_for(server, pic_id, CONTENT_TAG)
        assert tag_id is not None
        assert client.delete(f"/pictures/{pic_id}/tags/{tag_id}").status_code == 200

        assert _get_smart_score(server, pic_id) == 0.5
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_confirm_penalised_prediction_invalidates():
    """Confirming folds the anomaly probability to 1.0 — the cached score is stale."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.8)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(
            f"/pictures/{pic_id}/tag_predictions/{PENALISED_TAG}/confirm"
        )
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) is None
        assert pic_id in _find_missing_ids(server)
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_reject_penalised_prediction_invalidates():
    """Rejecting folds the anomaly probability to 0.0 — the cached score is stale."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.8)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/{PENALISED_TAG}/reject")
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) is None
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_confirm_content_prediction_does_not_invalidate():
    """A confirmed content-tag prediction is outside the anomaly vocabulary."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, CONTENT_TAG, confidence=0.8)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/{CONTENT_TAG}/confirm")
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) == 0.5
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_bulk_tagger_rewrite_invalidates_batch_in_one_statement():
    """The tagger's batch prediction write invalidates every affected picture at once.

    Asserts both the outcome (all changed pictures cleared, unchanged ones kept) and the
    batching: a per-picture UPDATE here would saturate the single DB writer queue.
    """
    temp_dir, client, server = _setup()
    try:
        pic_ids = [
            _upload_picture(client, name)
            for name in ("Bad1.png", "Bad2.png", "Reference1.png")
        ]
        assert len(set(pic_ids)) == 3
        for pid in pic_ids:
            _set_smart_score(server, pid, 0.5)

        # Two pictures get a fresh anomaly confidence; the third only a content tag,
        # so its anomaly signature — and its cached score — must be untouched.
        label_scores = {
            pic_ids[0]: {PENALISED_TAG: 0.7},
            pic_ids[1]: {PENALISED_TAG: 0.4},
            pic_ids[2]: {CONTENT_TAG: 0.9},
        }
        tags_by_pic = {pid: set() for pid in pic_ids}

        executed: list[str] = []

        def _run(session):
            original_exec = session.exec

            def _tracking_exec(statement, *args, **kwargs):
                executed.append(str(statement))
                return original_exec(statement, *args, **kwargs)

            session.exec = _tracking_exec
            try:
                return TagTask._write_predictions_from_tags(
                    session, label_scores, tags_by_pic, "test-v9"
                )
            finally:
                session.exec = original_exec

        server.vault.db.run_task(_run)

        assert _get_smart_score(server, pic_ids[0]) is None
        assert _get_smart_score(server, pic_ids[1]) is None
        assert _get_smart_score(server, pic_ids[2]) == 0.5

        missing = _find_missing_ids(server)
        assert pic_ids[0] in missing and pic_ids[1] in missing
        assert pic_ids[2] not in missing

        # Exactly one bulk UPDATE cleared the scores for the whole batch.
        smart_score_updates = [
            stmt
            for stmt in executed
            if stmt.startswith("UPDATE picture") and "smart_score" in stmt
        ]
        assert len(smart_score_updates) == 1, smart_score_updates
        assert "IN (" in smart_score_updates[0].replace("\n", " ")
    finally:
        server.vault.close()
        temp_dir.cleanup()

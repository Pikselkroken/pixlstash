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
import time
from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.db_models import Picture, Tag
from pixlstash.db_models.tag import DEFAULT_SMART_SCORE_PENALIZED_TAGS
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.picture_scoring import fetch_anomaly_confidences
from pixlstash.server import Server
from pixlstash.tasks import TaskType
from pixlstash.tasks.smart_score_task import SmartScoreTask
from pixlstash.tasks.tag_task import TagTask
from pixlstash.utils.quality.anomaly_penalty import anomaly_penalty
from pixlstash.utils.service.label_ledger import HUMAN, NEG, POS
from pixlstash.utils.service.smart_score_invalidation import (
    changed_penalised_tags,
    invalidate_for_penalised_tag_change,
)
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


def _seed_human_prediction(server, pic_id, tag, label_state, confidence=0.9):
    """Insert a prediction carrying a human decision in the label ledger."""

    def insert(session):
        session.add(
            TagPrediction(
                picture_id=pic_id,
                tag=tag,
                confidence=confidence,
                model_version="test-v1",
                status="PENDING",
                predicted_at=datetime.utcnow(),
                label_state=label_state,
                label_source=HUMAN,
            )
        )
        session.commit()

    server.vault.db.run_task(insert)


def _wait_for(predicate, timeout=10.0):
    """Poll *predicate* until true; the config-change invalidation runs on the LOW queue."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


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


# --------------------------------------------------------- confidence-gated penalty
#
# A model prediction below the tagger's apply threshold never became a visible ``Tag``,
# so it must not push the score down. Human decisions are exempt in both directions —
# which is why the gate lives in ``fetch_anomaly_confidences`` rather than being replaced
# by a read of the ``Tag`` table, which would silently drop human POS/NEG rows.

_THRESHOLDS = {"watermark": 0.6, "bad anatomy": 0.62}


def _probs(server, pic_id, thresholds=_THRESHOLDS):
    return server.vault.db.run_task(
        lambda s: fetch_anomaly_confidences(s, [pic_id], apply_thresholds=thresholds)
    )


def test_sub_threshold_model_prediction_is_not_scored():
    """A model prediction under its apply threshold contributes nothing."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "watermark", confidence=0.4)  # < 0.6

        probs, human = _probs(server, pic_id)
        assert "watermark" not in probs.get(pic_id, {})
        assert (
            anomaly_penalty(
                probs.get(pic_id, {}),
                tag_thresholds=_THRESHOLDS,
                human_tags=human.get(pic_id),
            )
            == 0.0
        )

        # Ungated (apply_thresholds=None) it *is* present — proving the gate is what
        # removed it, not a missing row.
        raw, _ = server.vault.db.run_task(
            lambda s: fetch_anomaly_confidences(s, [pic_id])
        )
        assert raw[pic_id]["watermark"] == pytest.approx(0.4)
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_above_threshold_model_prediction_is_scored():
    """The positive direction: over-gating would be its own regression."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "watermark", confidence=0.8)  # > 0.6

        probs, human = _probs(server, pic_id)
        assert probs[pic_id]["watermark"] == pytest.approx(0.8)
        assert (
            anomaly_penalty(
                probs[pic_id],
                tag_thresholds=_THRESHOLDS,
                human_tags=human.get(pic_id),
            )
            > 0.0
        )
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_human_positive_below_threshold_still_counts():
    """A human said the defect is there; its model confidence is irrelevant."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_human_prediction(server, pic_id, "watermark", POS, confidence=0.1)

        probs, human = _probs(server, pic_id)
        assert probs[pic_id]["watermark"] == 1.0
        assert "watermark" in human[pic_id]
        assert (
            anomaly_penalty(
                probs[pic_id], tag_thresholds=_THRESHOLDS, human_tags=human[pic_id]
            )
            > 0.0
        )
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_human_negative_suppresses_even_above_threshold():
    """A human said the defect is absent; a confident model must not override that."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_human_prediction(server, pic_id, "watermark", NEG, confidence=0.99)

        probs, human = _probs(server, pic_id)
        assert probs[pic_id]["watermark"] == 0.0
        assert "watermark" not in human.get(pic_id, set())
        assert (
            anomaly_penalty(
                probs[pic_id],
                tag_thresholds=_THRESHOLDS,
                human_tags=human.get(pic_id),
            )
            == 0.0
        )
    finally:
        server.vault.close()
        temp_dir.cleanup()


# ------------------------------------------------ scoped penalised-tag config change
#
# Re-weighting a penalised tag must invalidate only the pictures carrying it. The
# previous behaviour NULLed every row in the table, forcing a full-library re-score on
# any settings edit.


def test_changed_penalised_tags_diffs_the_resolved_tables():
    # Tags outside any anomaly family diff plainly.
    assert changed_penalised_tags({"a": 3}, {"a": 3}) == set()
    assert changed_penalised_tags({"a": 3}, {"a": 5}) == {"a"}  # reweighted
    assert changed_penalised_tags({"a": 3}, {}) == {"a"}  # removed
    assert changed_penalised_tags({}, {"a": 3}) == {"a"}  # added
    assert changed_penalised_tags({"A ": 3}, {"a": 3}) == set()  # normalised


def test_changed_penalised_tags_includes_family_aliases():
    """Aliases inherit the family ceiling, so a ceiling move must invalidate them too."""
    # "blocky" is the compression family's only weighted member; its unweighted siblings
    # "jpeg artifacts" / "compression artifacts" inherit its weight.
    changed = changed_penalised_tags({"blocky": 3}, {"blocky": 5})
    assert {"blocky", "jpeg artifacts", "compression artifacts"} <= changed
    # Removing it drops the whole family to zero — same requirement.
    changed = changed_penalised_tags({"blocky": 3}, {})
    assert {"blocky", "jpeg artifacts", "compression artifacts"} <= changed
    # Merge children are stored under their own name but scored as the parent.
    changed = changed_penalised_tags({"malformed hand": 3}, {"malformed hand": 5})
    assert {"malformed hand", "extra digit", "missing digit"} <= changed
    # A family whose ceiling did not move contributes nothing.
    unchanged = changed_penalised_tags(
        {"blocky": 3, "watermark": 4}, {"blocky": 3, "watermark": 2}
    )
    assert "watermark" in unchanged
    assert "blocky" not in unchanged and "jpeg artifacts" not in unchanged


def test_penalised_tag_config_change_invalidates_only_matching_pictures():
    """Assert both sets: the carriers are cleared and the bystanders keep their score."""
    temp_dir, client, server = _setup()
    try:
        carrier_tag = _upload_picture(client, "Bad1.png")
        carrier_pred = _upload_picture(client, "Bad2.png")
        bystander = _upload_picture(client, "Changed1.png")
        for pic_id in (carrier_tag, carrier_pred, bystander):
            _set_smart_score(server, pic_id, 0.5)

        # One carries an applied Tag, one only an anomaly TagPrediction — the penalty
        # reads both, so invalidation must cover both.
        server.vault.db.run_task(
            lambda s: (
                s.add(Tag(picture_id=carrier_tag, tag=PENALISED_TAG)),
                s.commit(),
            )
        )
        _seed_prediction(server, carrier_pred, PENALISED_TAG, confidence=0.9)
        # The bystander carries an *unrelated* penalised tag, so it is not just "untagged".
        _seed_prediction(server, bystander, "bad anatomy", confidence=0.9)

        cleared = server.vault.db.run_task(
            lambda s: (
                invalidate_for_penalised_tag_change(s, {PENALISED_TAG}),
                s.commit(),
            )[0]
        )
        assert cleared == 2
        assert _get_smart_score(server, carrier_tag) is None
        assert _get_smart_score(server, carrier_pred) is None
        assert _get_smart_score(server, bystander) == 0.5

        missing = _find_missing_ids(server)
        assert carrier_tag in missing and carrier_pred in missing
        assert bystander not in missing
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_no_weight_change_invalidates_nothing():
    """An unrelated config edit must not touch any cached score."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _set_smart_score(server, pic_id, 0.5)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.9)

        cleared = server.vault.db.run_task(
            lambda s: (invalidate_for_penalised_tag_change(s, set()), s.commit())[0]
        )
        assert cleared == 0
        assert _get_smart_score(server, pic_id) == 0.5
    finally:
        server.vault.close()
        temp_dir.cleanup()


def test_patch_config_invalidates_only_pictures_with_the_changed_tag():
    """End-to-end through PATCH /users/me/config."""
    temp_dir, client, server = _setup()
    try:
        carrier = _upload_picture(client, "Bad1.png")
        bystander = _upload_picture(client, "Changed1.png")
        _set_smart_score(server, carrier, 0.5)
        _set_smart_score(server, bystander, 0.5)
        _seed_prediction(server, carrier, PENALISED_TAG, confidence=0.9)
        _seed_prediction(server, bystander, "bad anatomy", confidence=0.9)

        # Re-weight only PENALISED_TAG; leave every other tag where it was.
        new_table = dict(DEFAULT_SMART_SCORE_PENALIZED_TAGS)
        new_table[PENALISED_TAG] = 1 if new_table.get(PENALISED_TAG) != 1 else 5
        resp = client.patch(
            "/users/me/config", json={"smart_score_penalised_tags": new_table}
        )
        assert resp.status_code == 200

        _wait_for(lambda: _get_smart_score(server, carrier) is None)
        assert _get_smart_score(server, carrier) is None
        assert _get_smart_score(server, bystander) == 0.5
    finally:
        server.vault.close()
        temp_dir.cleanup()


# ------------------------------------------------ background scorer honours user config
#
# ``SmartScoreTask`` runs in the background with no request, so it cannot use the
# request-scoped ``get_smart_score_penalised_tags_from_request``. It must resolve the
# owner's table from the DB inside its own read session — this is the wiring that made
# ``User.smart_score_penalised_tags`` reach the scorer at all.


def _prepare_for_scoring(server, pic_id):
    """Give the picture an embedding and clear its score so the finder picks it up."""

    def _apply(session):
        pic = session.get(Picture, pic_id)
        pic.image_embedding = np.random.rand(512).astype(np.float32).tobytes()
        pic.smart_score = None
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_apply)


def test_background_task_scores_and_honours_the_users_penalised_tags():
    """The SMART_SCORE finder builds a runnable task that respects the user's table."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.95)

        # Drive the finder by hand: the live WorkPlanner would otherwise claim and score
        # the same picture concurrently, making the assertions below racy.
        server.vault._work_planner.stop()

        finder = server.vault._planner_work_finders[TaskType.SMART_SCORE]
        assert hasattr(finder, "_vault"), "finder must carry the vault for thresholds"

        # 1) With the tag in the user's table it is charged.
        with_tag = dict(DEFAULT_SMART_SCORE_PENALIZED_TAGS)
        with_tag[PENALISED_TAG] = 5
        assert (
            client.patch(
                "/users/me/config", json={"smart_score_penalised_tags": with_tag}
            ).status_code
            == 200
        )
        _prepare_for_scoring(server, pic_id)
        task = finder.find_task()
        assert task is not None and pic_id in task.params["picture_ids"]
        assert task._run_task()["changed_count"] == 1
        # The TaskRunner normally does this; without it the finder keeps the picture
        # claimed and the second find_task() below would return None.
        finder.on_task_complete(task, None)
        charged = _get_smart_score(server, pic_id)
        assert charged is not None and 1.0 <= charged <= 5.0

        # 2) Remove the tag from the user's table — the same picture must score higher,
        #    which is only possible if the background path reads the user's config.
        without_tag = {k: v for k, v in with_tag.items() if k != PENALISED_TAG}
        assert (
            client.patch(
                "/users/me/config", json={"smart_score_penalised_tags": without_tag}
            ).status_code
            == 200
        )
        _prepare_for_scoring(server, pic_id)
        task = finder.find_task()
        assert task is not None
        assert task._run_task()["changed_count"] == 1
        finder.on_task_complete(task, None)
        uncharged = _get_smart_score(server, pic_id)
        assert uncharged is not None
        assert uncharged > charged, (
            f"removing {PENALISED_TAG!r} from the user's table did not raise the score "
            f"({charged:.4f} -> {uncharged:.4f}); the background scorer is still using "
            "the hardcoded defaults"
        )
    finally:
        server.vault.close()
        temp_dir.cleanup()

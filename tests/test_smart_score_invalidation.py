"""Tests that a cached ``Picture.smart_score`` is invalidated when - and only when -
a picture's anomaly/penalised-tag state changes.

``smart_score`` is a cached derived column and ``SmartScoreTask`` only picks up pictures
whose score is ``NULL``, so an edit that moves the scorer's anomaly inputs must clear it
or the stored score silently goes stale. The scorer's anomaly inputs come from
``TagPrediction`` rows in the anomaly vocabulary (see
``pixlstash.scoring.smart_score.fetch_anomaly_confidences``), so these tests assert both
directions: a penalised-tag edit invalidates, a content-tag edit does not.
"""

import json
import os
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, Tag
from pixlstash.db_models.tag import DEFAULT_SMART_SCORE_PENALIZED_TAGS
from pixlstash.db_models.tag_prediction import (
    feeds_anomaly_score,
    qualify_plugin_model_version,
    TagPrediction,
)
from pixlstash.event_types import EventType
from pixlstash.scoring import (
    fetch_anomaly_confidences,
    fetch_smart_score_data,
    resolve_penalised_tag_weights,
)
from pixlstash.server import Server
from pixlstash.tasks import TaskType
from pixlstash.tasks.base_task import TaskStatus
from pixlstash.tasks.smart_score_task import SmartScoreTask
from pixlstash.tasks.tag_task import TagTask
from pixlstash.utils.quality.anomaly_penalty import anomaly_penalty
from pixlstash.utils.service.label_ledger import HUMAN, NEG, POS
from pixlstash.utils.service.smart_score_invalidation import (
    InteractiveRescoreRegistry,
    anomaly_state_signature,
    changed_penalised_tags,
    invalidate_all_anomaly_scores,
    invalidate_for_penalised_tag_change,
)
from tests.utils import upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")

# In ANOMALY_PENALTY_TAGS - feeds the smart score's anomaly penalty.
PENALISED_TAG = "watermark"
# Not in the anomaly vocabulary - a pure content tag the score must ignore.
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

    Pass a distinct *name* per call when a test needs several pictures - the importer
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


def _seed_tag(server, pic_id, tag):
    """Apply *tag* to the picture directly, bypassing the route's ledger side-effects.

    The scorer charges a model prediction only when the defect is visible in the tag
    list, so a test asserting the *model* path needs a real ``Tag`` row without the human
    NEG/POS the tag routes would also record.
    """

    def insert(session):
        session.add(Tag(picture_id=pic_id, tag=tag))
        session.commit()

    server.vault.db.run_task(insert)


def _set_model_version(server, pic_id, tag, model_version):
    """Restamp a seeded prediction so the test can vary only its source."""

    def update(session):
        row = session.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == pic_id, TagPrediction.tag == tag
            )
        ).one()
        row.model_version = model_version
        session.commit()

    server.vault.db.run_task(update)


def _set_label_state(server, pic_id, tag, label_state):
    """Flip an existing ledger row's human verdict in place."""

    def update(session):
        row = session.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == pic_id, TagPrediction.tag == tag
            )
        ).one()
        row.label_state = label_state
        session.commit()

    server.vault.db.run_task(update)


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

    # Uploads require live workers, but every caller below is about to inspect
    # deliberately controlled intermediate score state. Stop the finder before
    # making that state visible so it cannot claim the row between assertions.
    server.vault._work_planner.stop()

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
        server.close()
        temp_dir.cleanup()


def test_adding_content_tag_does_not_invalidate():
    """A non-penalised tag must not invalidate - over-invalidating re-scores the library."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(f"/pictures/{pic_id}/tags", json={"tag": CONTENT_TAG})
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) == 0.5
        assert pic_id not in _find_missing_ids(server)
    finally:
        server.close()
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
        server.close()
        temp_dir.cleanup()


def test_removing_content_tag_does_not_invalidate():
    """Removing a content tag leaves the anomaly inputs - and the cached score - alone."""
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
        delete_resp = client.delete(f"/pictures/{pic_id}/tags/{tag_id}")
        assert delete_resp.status_code == 200

        assert _get_smart_score(server, pic_id) == 0.5
    finally:
        server.close()
        temp_dir.cleanup()


def test_confirm_penalised_prediction_invalidates():
    """Confirming folds the anomaly probability to 1.0 - the cached score is stale."""
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
        server.close()
        temp_dir.cleanup()


def test_confirm_registers_origin_stamped_rescore_refresh():
    """The user's primary path: confirming a model anomaly prediction with an editing tab
    must origin-stamp the eventual smart_score refresh so that tab updates the visible score
    in place, rather than routing to the deferred "view changed externally" bulk path.

    Proves the registry wiring end-to-end: the confirm records the id under the editing tab,
    and the background recompute completion emits an immediate origin-stamped
    ``{fields:["smart_score"], origin_client_id}`` event for just that card - independent of
    the backfill drain gate (a second unscored picture keeps ``count_remaining() > 0``).
    """
    temp_dir, client, server = _setup()
    try:
        edited = _upload_picture(client, "Bad1.png")
        pending = _upload_picture(client, "Bad2.png")  # keeps the backfill in flight
        server.vault._work_planner.stop()
        _prepare_for_scoring(server, pending)  # embedding + NULL score

        _seed_prediction(server, edited, PENALISED_TAG, confidence=0.8)
        _set_smart_score(server, edited, 0.5)

        # Confirm as the editing tab "tab-a".
        resp = client.post(
            f"/pictures/{edited}/tag_predictions/{PENALISED_TAG}/confirm",
            headers={"X-Client-Id": "tab-a"},
        )
        assert resp.status_code == 200
        # The cached score is NULLed and the id is registered under the editing tab.
        assert _get_smart_score(server, edited) is None
        assert (
            server.vault.interactive_rescore_registry.snapshot().get(edited) == "tab-a"
        )

        events = _capture_smart_score_events(server)

        # The background rescore persists a fresh score; completion emits the refresh.
        _set_smart_score(server, edited, 0.7)
        server.vault._on_task_completed(
            _fake_smart_score_completion([edited], [edited]), None
        )

        # Drain gate NOT reached, yet the card refreshed immediately, origin-stamped.
        assert server.vault.db.run_task(SmartScoreTask.count_remaining) > 0
        assert events == [
            {
                "picture_ids": [edited],
                "fields": ["smart_score"],
                "origin_client_id": "tab-a",
            }
        ]
        assert edited not in server.vault.interactive_rescore_registry.snapshot()
    finally:
        server.close()
        temp_dir.cleanup()


def test_reject_penalised_prediction_invalidates():
    """Rejecting folds the anomaly probability to 0.0 - the cached score is stale."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.8)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/{PENALISED_TAG}/reject")
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) is None
    finally:
        server.close()
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
        server.close()
        temp_dir.cleanup()


def test_delete_anomaly_prediction_invalidates():
    """Deleting an anomaly prediction drops its probability - the cached score is stale.

    ``POST /pictures/{id}/tag_predictions/delete`` bulk-removes the model's predictions so
    the background tagger treats the picture as never seen. Dropping an anomaly-vocabulary
    prediction removes its penalty input, so the stored ``smart_score`` must be NULLed for
    recompute - the confirmed invalidation gap this fix closes.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        # The tag is applied so the prediction is genuinely a scorer input: the scorer
        # only charges a model prediction whose defect is visible in the tag list.
        _seed_tag(server, pic_id, PENALISED_TAG)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.8)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/delete")
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) is None
        assert pic_id in _find_missing_ids(server)
    finally:
        server.close()
        temp_dir.cleanup()


def test_delete_content_prediction_does_not_invalidate():
    """Deleting a pure content prediction is outside the anomaly vocabulary - score stands.

    Guards the intended narrow scope: over-invalidating here re-scores the whole library on
    a routine prediction reset.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, CONTENT_TAG, confidence=0.8)
        _set_smart_score(server, pic_id, 0.5)

        resp = client.post(f"/pictures/{pic_id}/tag_predictions/delete")
        assert resp.status_code == 200

        assert _get_smart_score(server, pic_id) == 0.5
        assert pic_id not in _find_missing_ids(server)
    finally:
        server.close()
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
        # so its anomaly signature - and its cached score - must be untouched.
        # The anomaly tag is applied to the first two because the scorer only reads a
        # model prediction whose defect is visible in the tag list; without the Tag row
        # the prediction write would (correctly) move nothing.
        for pid in pic_ids[:2]:
            _seed_tag(server, pid, PENALISED_TAG)
        label_scores = {
            pic_ids[0]: {PENALISED_TAG: 0.7},
            pic_ids[1]: {PENALISED_TAG: 0.4},
            pic_ids[2]: {CONTENT_TAG: 0.9},
        }
        tags_by_pic = {
            pic_ids[0]: {PENALISED_TAG},
            pic_ids[1]: {PENALISED_TAG},
            pic_ids[2]: set(),
        }

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
        server.close()
        temp_dir.cleanup()


# ------------------------------------------------ applied-tag + confidence-gated penalty
#
# A model prediction is charged only when the defect is genuinely visible in the picture's
# tag list: it must clear the tagger's apply threshold **and** have a matching ``Tag`` row.
# Confidence alone used to stand in for both, but ``TagPredictionBackfillTask`` writes
# predictions without ever writing a ``Tag`` row, so a high-confidence backfilled row
# penalised pictures for defects the user could not see. Human decisions stay exempt in
# both directions, which is why the gate lives in ``fetch_anomaly_confidences`` rather
# than being replaced wholesale by a read of the ``Tag`` table.

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
        # Tag applied, so the *only* thing keeping it out of the score is the threshold.
        _seed_tag(server, pic_id, "watermark")
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

        # Ungated (apply_thresholds=None) it *is* present - proving the gate is what
        # removed it, not a missing row.
        raw, _ = server.vault.db.run_task(
            lambda s: fetch_anomaly_confidences(s, [pic_id])
        )
        assert raw[pic_id]["watermark"] == pytest.approx(0.4)
    finally:
        server.close()
        temp_dir.cleanup()


def test_above_threshold_model_prediction_is_scored():
    """The positive direction: over-gating would be its own regression."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "watermark")
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
        server.close()
        temp_dir.cleanup()


def test_human_positive_below_threshold_still_counts():
    """A human said the defect is there; confidence *and* tag membership are irrelevant.

    No ``Tag`` row is seeded on purpose: the applied-tag requirement covers model
    predictions only, so a human POS must still be charged without one.
    """
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
        server.close()
        temp_dir.cleanup()


def test_human_negative_suppresses_even_above_threshold():
    """A human said the defect is absent; a confident model must not override that.

    The tag is left applied so this asserts the harder direction: a human NEG suppresses
    even while the ``Tag`` row that would otherwise admit the prediction is still there.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "watermark")
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
        server.close()
        temp_dir.cleanup()


def test_unapplied_model_prediction_is_not_scored():
    """The backfill regression: a confident prediction with no ``Tag`` row costs nothing.

    ``TagPredictionBackfillTask`` writes predictions against a picture's existing tag set
    and never writes a ``Tag`` row, so this state is reachable for any picture tagged by
    a different engine; it penalised ~12k pictures for defects invisible in the UI.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        # Well above the 0.6 threshold, but the defect is nowhere in the tag list.
        _seed_prediction(
            server, pic_id, "watermark", confidence=0.99, status="REJECTED"
        )

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

        # Applying the tag admits the very same prediction, proving the missing Tag row
        # is what removed it, not the threshold or a missing prediction row.
        _seed_tag(server, pic_id, "watermark")
        probs, human = _probs(server, pic_id)
        assert probs[pic_id]["watermark"] == pytest.approx(0.99)
        assert (
            anomaly_penalty(
                probs[pic_id],
                tag_thresholds=_THRESHOLDS,
                human_tags=human.get(pic_id),
            )
            > 0.0
        )
    finally:
        server.close()
        temp_dir.cleanup()


def test_unapplied_prediction_is_dropped_from_the_ungated_signature_too():
    """The applied-tag check must not hide behind ``apply_thresholds``.

    ``anomaly_state_signature`` reads with ``apply_thresholds=None``. If the tag check
    were inside the threshold branch, tag membership would be invisible to the signature
    and adding or removing an anomaly tag would leave the cached score stale.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, "watermark", confidence=0.99)

        raw, _ = server.vault.db.run_task(
            lambda s: fetch_anomaly_confidences(s, [pic_id])
        )
        assert "watermark" not in raw.get(pic_id, {})

        before = server.vault.db.run_task(
            lambda s: anomaly_state_signature(s, [pic_id])
        )
        _seed_tag(server, pic_id, "watermark")
        after = server.vault.db.run_task(lambda s: anomaly_state_signature(s, [pic_id]))
        assert before[pic_id] != after[pic_id]
    finally:
        server.close()
        temp_dir.cleanup()


def test_tagger_rewrite_dropping_anomaly_tag_invalidates_cached_score():
    """A re-tag that drops an anomaly tag must clear the cached score.

    ``_add_tags_bulk`` commits its ``Tag`` rewrite in its own DB task, *before*
    ``_write_predictions_from_tags`` takes its anomaly snapshot. Now that an applied tag
    is a scorer input, the rewrite has to guard its own mutation or the score change goes
    unobserved. Without the ``invalidate_on_anomaly_change`` wrapper in ``_add_tags_bulk``
    this test fails.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, PENALISED_TAG)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.9)
        _set_smart_score(server, pic_id, 0.5)
        assert _get_smart_score(server, pic_id) == 0.5

        # The fresh pass no longer finds the defect, so the rewrite drops the tag.
        server.vault.db.run_task(
            lambda s: TagTask._add_tags_bulk(
                s, [{"pic_id": pic_id, "tags": [CONTENT_TAG]}]
            )
        )

        remaining = server.vault.db.run_task(
            lambda s: sorted(
                s.exec(select(Tag.tag).where(Tag.picture_id == pic_id)).all()
            )
        )
        assert remaining == [CONTENT_TAG]
        assert _get_smart_score(server, pic_id) is None
        assert pic_id in _find_missing_ids(server)
    finally:
        server.close()
        temp_dir.cleanup()


def test_tagger_rewrite_without_anomaly_change_keeps_cached_score():
    """The other direction: a content-only rewrite must not re-score the picture."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, CONTENT_TAG)
        _set_smart_score(server, pic_id, 0.5)

        server.vault.db.run_task(
            lambda s: TagTask._add_tags_bulk(
                s, [{"pic_id": pic_id, "tags": [CONTENT_TAG, "sunrise"]}]
            )
        )

        assert _get_smart_score(server, pic_id) == 0.5
        assert pic_id not in _find_missing_ids(server)
    finally:
        server.close()
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
    # Removing it drops the whole family to zero - same requirement.
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

        # One carries an applied Tag, one only an anomaly TagPrediction - the penalty
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
        server.close()
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
        server.close()
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
        server.close()
        temp_dir.cleanup()


# --------------------------------------------- tagger threshold-offset config change
#
# The tagger's ``threshold_offset`` moves both the anomaly apply gate and the penalty's
# ``u = (p - t)/(1 - t)`` normalisation, so every cached score with an anomaly component
# goes stale when it changes. Unlike a penalised-tag re-weight it is not scoped by tag -
# but it is still bounded to pictures that actually carry an anomaly ``TagPrediction``,
# because a picture with no anomaly term cannot have moved.


def _patch_threshold_offset(client, offset):
    return client.patch(
        "/users/me/config",
        json={
            "tagger_settings": {
                "plugins": {
                    "pixlstash_tagger": {"params": {"threshold_offset": offset}}
                }
            }
        },
    )


def test_invalidate_all_anomaly_scores_clears_only_anomaly_bearing_pictures():
    """The helper clears anomaly-bearing scores and leaves content-only / clean ones."""
    temp_dir, client, server = _setup()
    try:
        anomaly = _upload_picture(client, "Bad1.png")
        content_only = _upload_picture(client, "Bad2.png")
        clean = _upload_picture(client, "Changed1.png")
        for pic_id in (anomaly, content_only, clean):
            _set_smart_score(server, pic_id, 0.5)

        # Only the first carries an anomaly-vocabulary prediction; the second carries a
        # pure content prediction; the third carries none.
        _seed_prediction(server, anomaly, PENALISED_TAG, confidence=0.9)
        _seed_prediction(server, content_only, CONTENT_TAG, confidence=0.9)

        cleared = server.vault.db.run_task(
            lambda s: (
                invalidate_all_anomaly_scores(s, context="test"),
                s.commit(),
            )[0]
        )
        assert cleared == 1
        assert _get_smart_score(server, anomaly) is None
        assert _get_smart_score(server, content_only) == 0.5
        assert _get_smart_score(server, clean) == 0.5

        missing = _find_missing_ids(server)
        assert anomaly in missing
        assert content_only not in missing and clean not in missing
    finally:
        server.close()
        temp_dir.cleanup()


def test_threshold_offset_change_invalidates_anomaly_scores_via_patch():
    """End-to-end: changing the offset clears anomaly-bearing scores, spares the rest."""
    temp_dir, client, server = _setup()
    try:
        anomaly = _upload_picture(client, "Bad1.png")
        content_only = _upload_picture(client, "Bad2.png")
        # Stop the planner after upload (import needs its workers): once the offset patch
        # NULLs the anomaly score, the live MissingSmartScoreFinder would otherwise
        # re-score it and make the `is None` assertion racy. The offset-change
        # invalidation runs on the DB queue, so it is unaffected by stopping the planner.
        server.vault._work_planner.stop()
        for pic_id in (anomaly, content_only):
            _set_smart_score(server, pic_id, 0.5)
        _seed_prediction(server, anomaly, PENALISED_TAG, confidence=0.9)
        _seed_prediction(server, content_only, CONTENT_TAG, confidence=0.9)

        # Default offset is 0.0; move it so the anomaly normalisation shifts.
        assert _patch_threshold_offset(client, 0.15).status_code == 200

        assert _wait_for(lambda: _get_smart_score(server, anomaly) is None)
        assert _get_smart_score(server, anomaly) is None
        assert _get_smart_score(server, content_only) == 0.5
        assert anomaly in _find_missing_ids(server)
    finally:
        server.close()
        temp_dir.cleanup()


def test_identical_threshold_offset_save_is_a_noop():
    """Re-saving the same offset must not re-score - the guard is on a real move."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.9)

        # Establish a non-default offset first, then score the picture.
        assert _patch_threshold_offset(client, 0.2).status_code == 200
        _set_smart_score(server, pic_id, 0.5)

        # Saving the identical offset again is a no-op: the score must survive.
        assert _patch_threshold_offset(client, 0.2).status_code == 200
        # Give the LOW queue a chance to (wrongly) run before asserting it did not.
        assert not _wait_for(
            lambda: _get_smart_score(server, pic_id) is None, timeout=1.0
        )
        assert _get_smart_score(server, pic_id) == 0.5
        assert pic_id not in _find_missing_ids(server)
    finally:
        server.close()
        temp_dir.cleanup()


# ------------------------------------------------ background scorer honours user config
#
# ``SmartScoreTask`` runs in the background with no request, so it cannot use the
# request-scoped ``get_smart_score_penalised_tags_from_request``. It must resolve the
# owner's table from the DB inside its own read session - this is the wiring that made
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
        # The tag must be applied, not just predicted: the scorer only charges a model
        # prediction whose defect is visible in the picture's tag list. Without the Tag
        # row the penalty is zero under both tables below and the comparison is float
        # noise rather than a real difference.
        _seed_tag(server, pic_id, PENALISED_TAG)
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

        # 2) Remove the tag from the user's table - the same picture must score higher,
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
        server.close()
        temp_dir.cleanup()


def test_on_demand_fetch_resolves_the_users_penalised_tags_from_the_hub():
    """The sort path's scorer config carries the owner's table, not the shipped seed.

    Identity lives in the hub, so resolving it from the scoring session - which is a
    *vault* session - found no user row on every call and quietly scored with
    ``DEFAULT_SMART_SCORE_PENALIZED_TAGS`` while the owner's edited table sat in the hub
    (one ``No user row found`` warning per batch was the only symptom).
    """
    temp_dir, client, server = _setup()
    try:
        edited = {k: v for k, v in DEFAULT_SMART_SCORE_PENALIZED_TAGS.items()}
        edited.pop(PENALISED_TAG, None)
        edited["bad anatomy"] = 2
        assert (
            client.patch(
                "/users/me/config", json={"smart_score_penalised_tags": edited}
            ).status_code
            == 200
        )

        # No explicit table: the fetch must go and find the owner's, as the background
        # rescore that a config PATCH queues does.
        *_, scorer_config = fetch_smart_score_data(server, None)
        weights = scorer_config["penalised_tag_weights"]
        assert PENALISED_TAG not in weights, (
            f"{PENALISED_TAG!r} was removed from the owner's table but still reached "
            "the scorer; the weights are coming from the shipped defaults"
        )
        assert weights["bad anatomy"] == 2

        # And the same resolution stands alone, so the background task gets it too.
        assert resolve_penalised_tag_weights(server.auth) == weights

        # An explicit empty table means "penalise nothing" one layer down (see
        # test_anomaly_penalty.py), so it must survive the plumbing rather than be
        # read as "not supplied" and replaced by the shipped seed.
        *_, empty_config = fetch_smart_score_data(server, None, penalised_tags={})
        assert empty_config["penalised_tag_weights"] == {}
    finally:
        server.close()
        temp_dir.cleanup()


# --------------------------------------------- persist is a compare-and-swap (B1 race)
#
# Smart scores are computed outside the write transaction. If a tag edit invalidates a
# picture (NULLs its score) between compute and persist, ``_persist_scores`` must NOT
# write the stale value - that would resurrect an invalidated row the finder can never
# re-pick (a NULL score means both "unscored" and "invalidated since claimed", so the
# finder's ``WHERE smart_score IS NULL`` cannot distinguish them). The guard is a CAS on
# the anomaly signature captured at fetch time.


def test_persist_leaves_row_null_when_anomaly_state_changed_mid_scoring():
    """The resurrect-during-edit race: drift between compute and persist → stays NULL."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        # Claimed-but-unscored: has an embedding, score is NULL.
        _prepare_for_scoring(server, pic_id)

        # Signature the scorer would have fed from, captured with the inputs.
        before = server.vault.db.run_task(
            lambda s: anomaly_state_signature(s, [pic_id])
        )

        # A concurrent tag edit lands after compute but before persist, moving the
        # anomaly signature. Both the tag and the prediction are needed, because the
        # scorer only reads a model prediction whose defect is in the tag list.
        _seed_tag(server, pic_id, PENALISED_TAG)
        _seed_prediction(server, pic_id, PENALISED_TAG, confidence=0.9)

        # The task now tries to persist a score computed from the now-stale inputs.
        persisted = server.vault.db.run_task(
            SmartScoreTask._persist_scores, {pic_id: 0.5}, before
        )

        assert persisted == []
        # The row must stay NULL - not resurrected with the stale score - so the finder
        # re-picks it and it rescores from fresh inputs. This is the whole point.
        assert _get_smart_score(server, pic_id) is None
        assert pic_id in _find_missing_ids(server)
    finally:
        server.close()
        temp_dir.cleanup()


def test_persist_writes_score_when_anomaly_state_unchanged():
    """Happy path: no drift between compute and persist → the score is written."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _prepare_for_scoring(server, pic_id)

        before = server.vault.db.run_task(
            lambda s: anomaly_state_signature(s, [pic_id])
        )
        # No mutation between snapshot and persist.
        persisted = server.vault.db.run_task(
            SmartScoreTask._persist_scores, {pic_id: 0.5}, before
        )

        assert persisted == [pic_id]
        assert _get_smart_score(server, pic_id) == 0.5
        assert pic_id not in _find_missing_ids(server)
    finally:
        server.close()
        temp_dir.cleanup()


# ------------------------------------------ interactive rescore refresh (Fix C)
#
# An anomaly-tag edit NULLs a picture's cached smart_score; a background SmartScoreTask
# rescores it. Previously the grid refresh for that card was deferred until the WHOLE
# vault backfill drained (``count_remaining() == 0``). A migration NULL-reset keeps a
# backfill in flight, so an interactive edit's card never refreshed. The
# ``InteractiveRescoreRegistry`` now lets the completion handler emit an immediate,
# origin-stamped ``CHANGED_PICTURES`` for the edited card - independent of the drain gate
# - while bulk backfill work still coalesces into the single drain-time emit.


def _fake_smart_score_completion(picture_ids, persisted_ids):
    """A COMPLETED ``SmartScoreTask`` exactly as ``_on_task_completed`` reads it.

    ``persisted_ids`` is the CAS-written subset (see ``_persist_scores``); an id claimed
    but skipped for drift is present in ``picture_ids`` but absent from ``persisted_ids``.
    """
    return SimpleNamespace(
        type="SmartScoreTask",
        status=TaskStatus.COMPLETED,
        result={
            "changed_count": len(persisted_ids),
            "persisted_ids": list(persisted_ids),
        },
        params={"picture_ids": list(picture_ids)},
    )


def _capture_smart_score_events(server):
    """Record every ``CHANGED_PICTURES`` event carrying the ``smart_score`` field."""
    events: list[dict] = []

    def _listen(event_type, data):
        if event_type is EventType.CHANGED_PICTURES and isinstance(data, dict):
            if "smart_score" in (data.get("fields") or []):
                events.append(data)

    server.vault.add_event_listener(_listen)
    return events


def test_interactive_edit_refreshes_card_without_waiting_for_backfill_drain():
    """The headline fix: an edit mid-backfill refreshes THAT card immediately, stamped."""
    temp_dir, client, server = _setup()
    try:
        edited = _upload_picture(client, "Bad1.png")
        # A second unscored picture keeps the backfill in flight: count_remaining() > 0.
        pending = _upload_picture(client, "Bad2.png")
        # Stop the planner so no background task races us to score/consume.
        server.vault._work_planner.stop()
        _prepare_for_scoring(server, pending)  # embedding + NULL score

        # The edited card is scored, then a user edit (tab "tab-a") NULLs and registers it.
        _set_smart_score(server, edited, 0.5)
        assert (
            client.post(
                f"/pictures/{edited}/tags",
                json={"tag": PENALISED_TAG},
                headers={"X-Client-Id": "tab-a"},
            ).status_code
            == 200
        )
        assert _get_smart_score(server, edited) is None
        assert (
            server.vault.interactive_rescore_registry.snapshot().get(edited) == "tab-a"
        )

        events = _capture_smart_score_events(server)

        # The background rescore persists a fresh score for the edited card.
        _set_smart_score(server, edited, 0.7)
        server.vault._on_task_completed(
            _fake_smart_score_completion([edited], [edited]), None
        )

        # The drain gate has NOT been reached, yet the card refreshed immediately,
        # origin-stamped so the initiating tab reconciles in place.
        assert server.vault.db.run_task(SmartScoreTask.count_remaining) > 0
        assert events == [
            {
                "picture_ids": [edited],
                "fields": ["smart_score"],
                "origin_client_id": "tab-a",
            }
        ]
        # The registry self-evicts on consume.
        assert edited not in server.vault.interactive_rescore_registry.snapshot()
    finally:
        server.close()
        temp_dir.cleanup()


def test_full_backfill_emits_once_at_drain_not_per_batch():
    """No interactive edits → no per-batch flood; exactly one origin-less emit at drain."""
    temp_dir, client, server = _setup()
    try:
        pics = [
            _upload_picture(client, n)
            for n in ("Bad1.png", "Bad2.png", "Reference1.png")
        ]
        server.vault._work_planner.stop()
        for p in pics:
            _prepare_for_scoring(server, p)  # embedding + NULL score

        events = _capture_smart_score_events(server)

        batch1, batch2 = pics[:2], pics[2:]
        # First batch completes with the vault still un-drained: nothing is emitted.
        for p in batch1:
            _set_smart_score(server, p, 0.6)
        server.vault._on_task_completed(
            _fake_smart_score_completion(batch1, batch1), None
        )
        assert events == []

        # Final batch drains the vault: exactly one origin-less bulk emit fires.
        for p in batch2:
            _set_smart_score(server, p, 0.6)
        server.vault._on_task_completed(
            _fake_smart_score_completion(batch2, batch2), None
        )
        assert events == [{"picture_ids": batch2, "fields": ["smart_score"]}]
    finally:
        server.close()
        temp_dir.cleanup()


# --------------------------------------------- own-origin edit must not self-pill at drain
#
# Regression (#self-pill): when a user's own anomaly-tag edit is the LAST unscored picture
# in the vault, its rescore completion reaches the drain gate (``count_remaining() == 0``).
# The completion first emits an origin-stamped ``smart_score`` refresh for that id (slick
# in-place update on the editing tab), then emitted an ADDITIONAL origin-less drain event
# for the WHOLE batch - re-announcing the very same id with no origin. The editing tab
# therefore also raised the "view changed externally" pill for its own edit. The drain must
# exclude ids already announced origin-stamped via the interactive registry in this batch,
# while STILL firing for genuinely unregistered (background) ids - over-suppression is its
# own regression.


def test_own_origin_edit_does_not_also_pill_on_drain():
    """The editing tab's own edit, even when it drains the vault, gets exactly one
    origin-stamped refresh and NO origin-less drain event for that id."""
    temp_dir, client, server = _setup()
    try:
        edited = _upload_picture(client, "Bad1.png")
        # Stop the planner so no background task races us; this edit is the only work.
        server.vault._work_planner.stop()

        # Score, then a real add-penalised-tag from tab "tab-a" NULLs and registers it.
        _set_smart_score(server, edited, 0.5)
        assert (
            client.post(
                f"/pictures/{edited}/tags",
                json={"tag": PENALISED_TAG},
                headers={"X-Client-Id": "tab-a"},
            ).status_code
            == 200
        )
        assert _get_smart_score(server, edited) is None
        assert (
            server.vault.interactive_rescore_registry.snapshot().get(edited) == "tab-a"
        )

        events = _capture_smart_score_events(server)

        # The background rescore persists a fresh score; this batch drains the vault.
        _set_smart_score(server, edited, 0.7)
        server.vault._on_task_completed(
            _fake_smart_score_completion([edited], [edited]), None
        )

        # Drain gate IS reached, yet only the single origin-stamped refresh fired - the
        # editing tab reconciles in place and never sees its own change as external.
        assert server.vault.db.run_task(SmartScoreTask.count_remaining) == 0
        assert events == [
            {
                "picture_ids": [edited],
                "fields": ["smart_score"],
                "origin_client_id": "tab-a",
            }
        ]
        # No origin-less event mentioning the edited id was emitted.
        assert not [
            e
            for e in events
            if "origin_client_id" not in e and edited in e["picture_ids"]
        ]
    finally:
        server.close()
        temp_dir.cleanup()


def test_drain_still_fires_for_unregistered_ids_alongside_own_origin():
    """Over-suppression guard: in a draining batch mixing a registered (own-origin) id and
    an unregistered (background) id, the origin-stamped refresh covers only the registered
    id and the origin-less drain covers only the background id."""
    temp_dir, client, server = _setup()
    try:
        edited = _upload_picture(client, "Bad1.png")  # registered under tab-a
        background = _upload_picture(client, "Bad2.png")  # never registered
        server.vault._work_planner.stop()

        _set_smart_score(server, edited, 0.5)
        assert (
            client.post(
                f"/pictures/{edited}/tags",
                json={"tag": PENALISED_TAG},
                headers={"X-Client-Id": "tab-a"},
            ).status_code
            == 200
        )
        assert (
            server.vault.interactive_rescore_registry.snapshot().get(edited) == "tab-a"
        )
        # background is rescored in the same batch but was never interactively registered.
        assert not server.vault.interactive_rescore_registry.snapshot().get(background)

        events = _capture_smart_score_events(server)

        # Both persist in the one batch that drains the vault (remaining == 0).
        _set_smart_score(server, edited, 0.7)
        _set_smart_score(server, background, 0.6)
        server.vault._on_task_completed(
            _fake_smart_score_completion([edited, background], [edited, background]),
            None,
        )

        assert server.vault.db.run_task(SmartScoreTask.count_remaining) == 0
        # The registered id got its immediate origin-stamped refresh.
        assert {
            "picture_ids": [edited],
            "fields": ["smart_score"],
            "origin_client_id": "tab-a",
        } in events
        # The origin-less drain fired for the background id ONLY - the edited id is not
        # re-announced origin-less (no self-pill), and the background id is not dropped.
        bulk = [e for e in events if "origin_client_id" not in e]
        assert bulk == [{"picture_ids": [background], "fields": ["smart_score"]}]
    finally:
        server.close()
        temp_dir.cleanup()


def test_cas_skipped_id_is_not_announced_and_stays_registered():
    """A picture skipped by _persist_scores' CAS is still NULL - never announced rescored."""
    temp_dir, client, server = _setup()
    try:
        persisted_pic = _upload_picture(client, "Bad1.png")
        skipped_pic = _upload_picture(client, "Bad2.png")
        server.vault._work_planner.stop()
        # Both registered interactive from the same tab.
        server.vault.interactive_rescore_registry.record(
            [persisted_pic, skipped_pic], "tab-x"
        )
        # Keep an unscored picture so the drain gate is not reached.
        _prepare_for_scoring(server, skipped_pic)

        events = _capture_smart_score_events(server)

        # The task claimed both, but the CAS persisted only persisted_pic; skipped_pic
        # drifted mid-scoring and is excluded from persisted_ids (left NULL).
        server.vault._on_task_completed(
            _fake_smart_score_completion([persisted_pic, skipped_pic], [persisted_pic]),
            None,
        )

        # Only the persisted id is announced - never the still-NULL skipped id.
        assert events == [
            {
                "picture_ids": [persisted_pic],
                "fields": ["smart_score"],
                "origin_client_id": "tab-x",
            }
        ]
        # The skipped id is NOT consumed: it stays registered for its next rescore.
        assert (
            server.vault.interactive_rescore_registry.snapshot().get(skipped_pic)
            == "tab-x"
        )
    finally:
        server.close()
        temp_dir.cleanup()


def test_interactive_registry_caps_and_demotes_overflow_without_dropping():
    """Over the cap the overflow id is returned (demoted), not silently stored/dropped."""
    reg = InteractiveRescoreRegistry(max_entries=2)
    assert reg.record([1, 2], "tab") == []  # fits under the cap
    assert reg.record([3], "tab") == [3]  # overflow → demoted, not stored
    # Re-recording an existing id refreshes its origin without counting against the cap.
    assert reg.record([1], "tab2") == []
    assert len(reg) == 2

    consumed = reg.consume([1, 2, 3])
    # Only the two stored ids come back (grouped by their latest origin); the demoted id
    # was never stored, so the completion side falls back to the bulk path for it.
    assert consumed == {"tab2": [1], "tab": [2]}
    assert len(reg) == 0


def test_over_cap_demoted_id_falls_back_to_bulk_drain_emit():
    """Cap→bulk-fallback is fail-safe: a demoted id is delivered by the drain emit, not lost."""
    temp_dir, client, server = _setup()
    try:
        recorded = _upload_picture(client, "Bad1.png")
        demoted = _upload_picture(client, "Bad2.png")
        server.vault._work_planner.stop()

        # Shrink the cap so the second interactive id overflows and is demoted.
        server.vault.interactive_rescore_registry = InteractiveRescoreRegistry(
            max_entries=1
        )
        reg = server.vault.interactive_rescore_registry
        assert reg.record([recorded], "tab-a") == []
        assert reg.record([demoted], "tab-b") == [demoted]

        events = _capture_smart_score_events(server)

        # Both rescores persist in the same batch that drains the vault (remaining == 0).
        for p in (recorded, demoted):
            _set_smart_score(server, p, 0.6)
        server.vault._on_task_completed(
            _fake_smart_score_completion([recorded, demoted], [recorded, demoted]),
            None,
        )

        # The recorded id got its immediate origin-stamped refresh.
        assert {
            "picture_ids": [recorded],
            "fields": ["smart_score"],
            "origin_client_id": "tab-a",
        } in events
        # The demoted id was NOT dropped: it rides the single origin-less bulk drain emit.
        # The recorded id, already announced origin-stamped, is excluded from that drain
        # so its editing tab is not self-pilled.
        bulk = [e for e in events if "origin_client_id" not in e]
        assert bulk == [{"picture_ids": [demoted], "fields": ["smart_score"]}]
    finally:
        server.close()
        temp_dir.cleanup()


# ------------------------------------------------------------------ bulk persist (S4)
#
# ``_persist_scores`` writes the CAS-surviving subset with a SINGLE bulk Core UPDATE over
# ``Picture.__table__`` instead of a per-row ``session.get`` + attribute-set loop (64
# SELECT + 64 UPDATE per batch on the single writer queue, run full-library-wide by
# migration 0076). The write must still: honour the B1 compare-and-swap, return exactly
# the ids written (in input order, drift/deleted excluded), and leave ``metadata_hash``
# untouched - ``smart_score`` is in ``database._HASH_SKIP_COLS``.


def test_persist_writes_batch_as_single_bulk_statement():
    """The whole surviving batch is one executemany UPDATE, not N per-row updates."""
    temp_dir, client, server = _setup()
    try:
        pic_ids = [
            _upload_picture(client, name)
            for name in ("Bad1.png", "Bad2.png", "Reference1.png")
        ]
        assert len(set(pic_ids)) == 3
        # Stop the planner after upload (import needs its workers) but before scoring, so
        # no background SmartScoreTask races our manual persist and overwrites the
        # hand-set scores with a real recompute.
        server.vault._work_planner.stop()
        for pid in pic_ids:
            _prepare_for_scoring(server, pid)

        before = server.vault.db.run_task(lambda s: anomaly_state_signature(s, pic_ids))
        id_to_score = {pid: 0.5 + i * 0.1 for i, pid in enumerate(pic_ids)}

        executed: list[str] = []

        def _run(session):
            original = session.execute

            def _tracking(statement, *args, **kwargs):
                executed.append(str(statement))
                return original(statement, *args, **kwargs)

            session.execute = _tracking
            try:
                return SmartScoreTask._persist_scores(session, id_to_score, before)
            finally:
                session.execute = original

        persisted = server.vault.db.run_task(_run)

        # Returned exactly the surviving subset, in input order.
        assert persisted == pic_ids
        for pid in pic_ids:
            assert _get_smart_score(server, pid) == id_to_score[pid]

        # Exactly one UPDATE ... smart_score statement carried the whole batch - the
        # executemany fires a single ``session.execute`` regardless of row count.
        score_updates = [
            s
            for s in executed
            if s.strip().upper().startswith("UPDATE PICTURE") and "smart_score" in s
        ]
        assert len(score_updates) == 1, score_updates
    finally:
        server.close()
        temp_dir.cleanup()


def test_persist_preserves_metadata_hash():
    """A smart_score write must not move metadata_hash (smart_score is a skip column)."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        # Stop the planner after upload (import needs its workers) but before scoring, so
        # no background SmartScoreTask races our manual persist.
        server.vault._work_planner.stop()
        _prepare_for_scoring(server, pic_id)

        h0 = server.vault.db.run_task(lambda s: s.get(Picture, pic_id).metadata_hash)
        assert h0 is not None

        before = server.vault.db.run_task(
            lambda s: anomaly_state_signature(s, [pic_id])
        )
        persisted = server.vault.db.run_task(
            SmartScoreTask._persist_scores, {pic_id: 0.5}, before
        )
        assert persisted == [pic_id]
        assert _get_smart_score(server, pic_id) == 0.5

        # The Core UPDATE bypasses the ORM, so the metadata-hash after_flush hook never
        # fired; the old ORM loop would have recomputed the hash to this same value.
        h1 = server.vault.db.run_task(lambda s: s.get(Picture, pic_id).metadata_hash)
        assert h1 == h0
    finally:
        server.close()
        temp_dir.cleanup()


def test_persist_excludes_picture_deleted_mid_flight():
    """A picture whose row no longer exists at persist time is excluded, not announced."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        # Stop the planner after upload (import needs its workers) but before scoring, so
        # no background SmartScoreTask races our manual persist.
        server.vault._work_planner.stop()
        _prepare_for_scoring(server, pic_id)
        gone_id = 9_999_999  # never existed → stands in for a hard-deleted row

        before = server.vault.db.run_task(
            lambda s: anomaly_state_signature(s, [pic_id, gone_id])
        )
        persisted = server.vault.db.run_task(
            SmartScoreTask._persist_scores,
            {pic_id: 0.5, gone_id: 0.9},
            before,
        )
        # CAS passes for both (empty signatures match), but the missing row is dropped by
        # the existence check - preserving the old ``session.get(...) is None`` guard.
        assert persisted == [pic_id]
        assert _get_smart_score(server, pic_id) == 0.5
    finally:
        server.close()
        temp_dir.cleanup()


# ------------------------------------------------ penalised-tag config atomicity (S2)
#
# The penalised-tag score invalidation now runs INSIDE the same IMMEDIATE transaction
# that writes the user's config, not as a separate LOW follow-up task. A crash between a
# committed config write and a lost invalidation would leave scores stale forever; making
# them one transaction eliminates that window.


def test_penalised_tag_change_invalidates_atomically_no_second_task():
    """The reset is committed within the config write's transaction; no LOW task."""
    temp_dir, client, server = _setup()
    try:
        carrier = _upload_picture(client, "Bad1.png")
        bystander = _upload_picture(client, "Changed1.png")
        _set_smart_score(server, carrier, 0.5)
        _set_smart_score(server, bystander, 0.5)
        _seed_prediction(server, carrier, PENALISED_TAG, confidence=0.9)
        _seed_prediction(server, bystander, "bad anatomy", confidence=0.9)

        # Record the DB tasks enqueued during the PATCH so we can prove the invalidation
        # is NOT a separate follow-up task. Tasks are captured with their qualname and
        # filtered to the ones this route submits: the WorkPlanner's finders tick on
        # their own thread and enqueue unrelated reads of their own (e.g.
        # MissingWatchFolderImportFinder), so counting every task in flight made this
        # assertion depend on background timing rather than on the route's behaviour.
        tasks: list = []
        original_run_task = server.vault.db.run_task

        def _spy(func, *args, **kwargs):
            tasks.append((kwargs.get("priority"), getattr(func, "__qualname__", "")))
            return original_run_task(func, *args, **kwargs)

        server.vault.db.run_task = _spy
        try:
            new_table = dict(DEFAULT_SMART_SCORE_PENALIZED_TAGS)
            new_table[PENALISED_TAG] = 1 if new_table.get(PENALISED_TAG) != 1 else 5
            resp = client.patch(
                "/users/me/config", json={"smart_score_penalised_tags": new_table}
            )
            assert resp.status_code == 200
        finally:
            server.vault.db.run_task = original_run_task

        # Committed synchronously inside the PATCH - asserted WITHOUT polling, because the
        # invalidation shares the IMMEDIATE task's transaction rather than trailing it.
        assert _get_smart_score(server, carrier) is None
        assert _get_smart_score(server, bystander) == 0.5

        # No separate LOW task did the reset; exactly one IMMEDIATE task carried both the
        # config write and the invalidation.
        route_tasks = [
            priority for priority, name in tasks if "patch_me_config" in name
        ]
        assert route_tasks, f"config route enqueued no DB task; saw {tasks}"
        assert DBPriority.LOW not in route_tasks
        assert route_tasks.count(DBPriority.IMMEDIATE) == 1
    finally:
        server.close()
        temp_dir.cleanup()


# --- Tagger-plugin predictions are fenced out of the anomaly penalty --------------
# ``TagPrediction`` rows can now come from a third-party tagger plugin, stamped with a
# qualified ``model_version`` (``<plugin>@<version>``). Raw confidences are not
# comparable between models - the apply thresholds above are calibrated against the
# built-in tagger - so a plugin's score must never reach the penalty, or switching
# tagger would move every affected picture's smart score with nothing on screen to
# explain it. A *human* verdict still counts whichever row carries it.


def test_plugin_sourced_model_prediction_is_not_scored():
    """A plugin's confidence is dropped even when tagged and above the threshold."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "watermark")
        _seed_prediction(server, pic_id, "watermark", confidence=0.8)  # > 0.6
        _set_model_version(server, pic_id, "watermark", "joycaption@2026-01")

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

        # The ungated read drops it too: this is a source fence, not a threshold.
        raw, _ = server.vault.db.run_task(
            lambda s: fetch_anomaly_confidences(s, [pic_id])
        )
        assert "watermark" not in raw.get(pic_id, {})

        # Control: the identical row unqualified *is* scored, proving the
        # model_version is what removed it and not the seeding.
        _set_model_version(server, pic_id, "watermark", "v43")
        probs, _ = _probs(server, pic_id)
        assert probs[pic_id]["watermark"] == pytest.approx(0.8)
    finally:
        server.close()
        temp_dir.cleanup()


def test_human_decision_on_a_plugin_row_still_counts():
    """The fence drops model confidences, never a person's verdict."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_human_prediction(server, pic_id, "watermark", POS, confidence=0.1)
        _set_model_version(server, pic_id, "watermark", "joycaption@2026-01")

        probs, human = _probs(server, pic_id)
        assert probs[pic_id]["watermark"] == pytest.approx(1.0)
        assert "watermark" in human[pic_id]

        # And the negative direction, on the same plugin-stamped row.
        _set_label_state(server, pic_id, "watermark", NEG)
        probs, _ = _probs(server, pic_id)
        assert probs[pic_id]["watermark"] == pytest.approx(0.0)
    finally:
        server.close()
        temp_dir.cleanup()


def test_plugin_write_leaves_the_built_in_taggers_predictions_alone():
    """A plugin run must not delete or overwrite built-in rows as stale.

    One row per ``(picture, tag)`` is all the unique key allows, so without the
    built-in/plugin split in the stale-row delete, running a plugin would clear the
    built-in tagger's confidences and take the picture's anomaly penalty with them.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "watermark")
        _seed_prediction(server, pic_id, "watermark", confidence=0.8)

        written = server.vault.db.run_task(
            TagTask._write_predictions_from_tags,
            {pic_id: {"watermark": 0.05, "bad anatomy": 0.9}},
            {pic_id: {"watermark"}},
            "joycaption@2026-01",
        )

        rows = server.vault.db.run_task(
            lambda s: {
                r.tag: (r.model_version, r.confidence)
                for r in s.exec(
                    select(TagPrediction).where(TagPrediction.picture_id == pic_id)
                ).all()
            }
        )
        # The built-in row survives untouched, version and confidence.
        assert rows["watermark"] == ("test-v1", pytest.approx(0.8))
        # The plugin's own prediction for a tag nobody owned is still recorded.
        assert rows["bad anatomy"][0] == "joycaption@2026-01"
        assert written >= 1

        # And the surviving built-in confidence still drives the penalty.
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
        server.close()
        temp_dir.cleanup()


def test_model_version_helpers_split_built_in_from_plugin_rows():
    """The predicate the fence is built on, including the legacy shapes."""
    assert qualify_plugin_model_version("joycaption", "2026-01") == "joycaption@2026-01"
    assert qualify_plugin_model_version("joycaption", None) == "joycaption@unknown"
    assert qualify_plugin_model_version("joycaption", "  ") == "joycaption@unknown"

    # Every row written before plugins could predict at all must keep scoring.
    assert feeds_anomaly_score("v43")
    assert feeds_anomaly_score("unknown")
    assert feeds_anomaly_score("manual")
    assert not feeds_anomaly_score("joycaption@2026-01")
    assert not feeds_anomaly_score("joycaption@unknown")

"""Tests for Wave C: frozen eval slices, the freeze action, and the tiered
AP/F1 metric procedure.

See docs/reviews/tag-review-tagger-takeover-design.md §1.
"""

import gc
import io
import json
import os
import tempfile
from datetime import datetime

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from pixlstash.db_models import PictureSplit, TagEvalSlice, TagEvalSliceItem
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.server import Server
from pixlstash.services import tag_eval_slice_service, tag_prediction_service
from pixlstash.services.tag_eval_slice_service import average_precision, bootstrap_ap_ci
from pixlstash.tasks.tag_task import TagTask
from pixlstash.utils.service.caption_utils import sanitise_tag
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


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
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _teardown(temp_dir, server):
    server.vault.close()
    temp_dir.cleanup()
    gc.collect()


_distinct_counter = [0]


def _upload_named(client):
    _distinct_counter[0] += 1
    n = _distinct_counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return upload_pictures_and_wait(
        client, [("file", (f"evalslice{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _set_split(server, pic_id, split, component_key=None):
    def ins(session):
        session.add(
            PictureSplit(
                picture_id=pic_id,
                split=split,
                component_key=component_key if component_key is not None else pic_id,
            )
        )
        session.commit()

    server.vault.db.run_task(ins)


def _seed_prediction(
    server,
    pic_id,
    tag,
    confidence,
    *,
    model_version="v1",
    label_state=None,
    label_source=None,
    predicted_at=None,
):
    def ins(session):
        session.add(
            TagPrediction(
                picture_id=pic_id,
                tag=tag,
                confidence=confidence,
                model_version=model_version,
                status="CONFIRMED" if label_state == "POS" else "PENDING",
                predicted_at=predicted_at or datetime.utcnow(),
                label_state=label_state or "UNKNOWN",
                label_source=label_source,
                labeled_at=datetime.utcnow() if label_source else None,
            )
        )
        session.commit()

    server.vault.db.run_task(ins)


def _correct_label(server, pic_id, tag, new_state):
    """Simulate a later human correction to an already-labeled row."""

    def upd(session):
        row = session.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == pic_id, TagPrediction.tag == tag
            )
        ).first()
        row.label_state = new_state
        session.add(row)
        session.commit()

    server.vault.db.run_task(upd)


def _seed_eval_slice(server, tag, items, status="ACTIVE"):
    """Directly construct a TagEvalSlice + items, bypassing the freeze endpoint
    for full control over exactly which (picture, label_state) pairs are frozen."""

    def ins(session):
        s = TagEvalSlice(tag=tag, status=status)
        session.add(s)
        session.flush()
        for pid, state in items:
            session.add(
                TagEvalSliceItem(eval_slice_id=s.id, picture_id=pid, label_state=state)
            )
        session.commit()
        session.refresh(s)
        return s.id

    return server.vault.db.run_task(ins)


def _make_candidates(client, server, tag, n_pos, n_neg, *, model_version="v1"):
    """n_pos + n_neg human-labeled, EVAL-split candidates for *tag*, each its
    own singleton near-dup component (no conflicts)."""
    pics = []
    for i in range(n_pos):
        pid = _upload_named(client)
        _set_split(server, pid, "EVAL")
        _seed_prediction(
            server,
            pid,
            tag,
            0.9,
            model_version=model_version,
            label_state="POS",
            label_source="human",
        )
        pics.append(pid)
    for i in range(n_neg):
        pid = _upload_named(client)
        _set_split(server, pid, "EVAL")
        _seed_prediction(
            server,
            pid,
            tag,
            0.05,
            model_version=model_version,
            label_state="NEG",
            label_source="human",
        )
        pics.append(pid)
    return pics


# --------------------------------------------------------------------------- #
# (a) Freeze-then-refreeze: the frozen label_state snapshot is immutable
# across a later correction to the live TagPrediction row.
# --------------------------------------------------------------------------- #


def test_freeze_snapshot_is_immutable_across_later_correction_and_refreeze():
    temp_dir, client, server = _setup()
    try:
        tag = "immutable_tag"
        # 11 POS so that correcting one to NEG below still leaves exactly
        # MIN_EVAL_N_POS (10) -- clears the floor on refreeze.
        pos_pics = _make_candidates(client, server, tag, n_pos=11, n_neg=2)
        p1 = pos_pics[0]

        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] is True
        assert body["n_pos"] == 11
        slice_1_id = body["slice_id"]

        detail = client.get(f"{API}/tag_eval_slices/{slice_1_id}").json()
        item_p1 = next(i for i in detail["items"] if i["picture_id"] == p1)
        assert item_p1["label_state"] == "POS"

        # Simulate a later human correction to the LIVE TagPrediction row.
        _correct_label(server, p1, tag, "NEG")

        # The already-frozen slice's stored snapshot must NOT change.
        detail_again = client.get(f"{API}/tag_eval_slices/{slice_1_id}").json()
        item_p1_again = next(i for i in detail_again["items"] if i["picture_id"] == p1)
        assert item_p1_again["label_state"] == "POS"

        # Refreeze: supersedes slice_1, and the new slice picks up the
        # CORRECTED live label (p1 is now NEG, so only 10 POS survive -- still
        # exactly clears the floor).
        resp2 = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp2.status_code == 200, resp2.text
        body2 = resp2.json()
        assert body2["created"] is True
        assert body2["n_pos"] == 10
        slice_2_id = body2["slice_id"]
        assert slice_2_id != slice_1_id

        detail2 = client.get(f"{API}/tag_eval_slices/{slice_2_id}").json()
        item_p1_v2 = next(i for i in detail2["items"] if i["picture_id"] == p1)
        assert item_p1_v2["label_state"] == "NEG"

        # History: slice_1 SUPERSEDED, slice_2 ACTIVE, most-recent-first.
        history = client.get(f"{API}/tag_eval_slices", params={"tag": tag}).json()
        assert history[0]["id"] == slice_2_id and history[0]["status"] == "ACTIVE"
        assert history[1]["id"] == slice_1_id and history[1]["status"] == "SUPERSEDED"

        # slice_1's own item is STILL "POS" even now that it's superseded.
        detail_after_supersede = client.get(
            f"{API}/tag_eval_slices/{slice_1_id}"
        ).json()
        item_p1_final = next(
            i for i in detail_after_supersede["items"] if i["picture_id"] == p1
        )
        assert item_p1_final["label_state"] == "POS"
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (b) Freeze-time floor: n_pos < MIN_EVAL_N_POS -> no ACTIVE slice created.
# --------------------------------------------------------------------------- #


def test_freeze_respects_min_n_pos_floor():
    temp_dir, client, server = _setup()
    try:
        tag = "floor_tag"
        _make_candidates(client, server, tag, n_pos=5, n_neg=2)

        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] is False
        assert body["reason"] == "insufficient_positives"
        assert body["n_pos"] == 5
        assert body["slice_id"] is None

        # No ACTIVE slice exists; history is empty.
        history = client.get(f"{API}/tag_eval_slices", params={"tag": tag}).json()
        assert history == []

        # A tag with zero candidates at all -> "no_candidates", not a crash.
        resp2 = client.post(f"{API}/tag_eval_slices", json={"tag": "nonexistent_tag"})
        assert resp2.json()["reason"] == "no_candidates"
        assert resp2.json()["created"] is False
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (c) Freeze excludes candidates flagged by has_train_side_conflict.
# --------------------------------------------------------------------------- #


def test_freeze_excludes_train_side_conflicted_candidates():
    temp_dir, client, server = _setup()
    try:
        tag = "conflict_tag"
        clean_pics = _make_candidates(client, server, tag, n_pos=11, n_neg=1)

        # One more EVAL-side POS candidate sharing a component with a
        # TRAIN-split sibling -- has_train_side_conflict must flag+exclude it.
        conflicted_pid = _upload_named(client)
        train_sibling = _upload_named(client)
        shared_key = min(conflicted_pid, train_sibling)
        _set_split(server, conflicted_pid, "EVAL", component_key=shared_key)
        _set_split(server, train_sibling, "TRAIN", component_key=shared_key)
        _seed_prediction(
            server,
            conflicted_pid,
            tag,
            0.9,
            label_state="POS",
            label_source="human",
        )

        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] is True
        assert body["excluded_conflict_ids"] == [conflicted_pid]
        assert body["n_pos"] == 11  # the 11 clean candidates only
        assert body["n_total"] == 12  # 11 clean POS + 1 clean NEG

        detail = client.get(f"{API}/tag_eval_slices/{body['slice_id']}").json()
        frozen_ids = {i["picture_id"] for i in detail["items"]}
        assert conflicted_pid not in frozen_ids
        assert set(clean_pics) <= frozen_ids
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (d) Tier logic: none / insufficient_data / AP-no-CI / AP-with-CI /
# F1-carried-forward / F1-rederived / F1-uncalibrated-fallback.
# --------------------------------------------------------------------------- #


def test_tier_none_when_no_predictions_at_requested_model_version():
    temp_dir, client, server = _setup()
    try:
        tag = "tier_none"
        pics = [(_upload_named(client), "POS") for _ in range(10)] + [
            (_upload_named(client), "NEG") for _ in range(2)
        ]
        for pid, state in pics:
            _seed_prediction(server, pid, tag, 0.8 if state == "POS" else 0.1)
        slice_id = _seed_eval_slice(server, tag, pics)

        detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}",
            params={"model_version": "no_such_version"},
        ).json()
        assert detail["eval_metric_kind"] == "none"
        assert detail["eval_threshold_source"] == "none"
        assert detail["eval_n"] == 0
        assert detail["eval_n_pos"] == 0
    finally:
        _teardown(temp_dir, server)


def test_tier_insufficient_data_below_n_pos_floor():
    temp_dir, client, server = _setup()
    try:
        tag = "tier_insufficient"
        pics = [(_upload_named(client), "POS") for _ in range(5)] + [
            (_upload_named(client), "NEG") for _ in range(7)
        ]
        for pid, state in pics:
            _seed_prediction(server, pid, tag, 0.8 if state == "POS" else 0.1)
        slice_id = _seed_eval_slice(server, tag, pics)

        detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}", params={"model_version": "v1"}
        ).json()
        assert detail["eval_metric_kind"] == "insufficient_data"
        assert detail["eval_n"] == 12
        assert detail["eval_n_pos"] == 5
    finally:
        _teardown(temp_dir, server)


def test_tier_ap_point_estimate_below_ci_floor():
    temp_dir, client, server = _setup()
    try:
        tag = "tier_ap_no_ci"
        pics = []
        for i in range(15):
            pid = _upload_named(client)
            _seed_prediction(server, pid, tag, 0.5 + 0.01 * i)
            pics.append((pid, "POS"))
        for i in range(10):
            pid = _upload_named(client)
            _seed_prediction(server, pid, tag, 0.4 - 0.01 * i)
            pics.append((pid, "NEG"))
        slice_id = _seed_eval_slice(server, tag, pics)

        detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}", params={"model_version": "v1"}
        ).json()
        assert detail["eval_metric_kind"] == "AP"
        assert detail["eval_threshold_source"] == "none"
        assert detail["eval_n_pos"] == 15
        assert detail["eval_ap"] is not None
        assert 0.0 <= detail["eval_ap"] <= 1.0
        assert detail["eval_ap_ci_low"] is None
        assert detail["eval_ap_ci_high"] is None
    finally:
        _teardown(temp_dir, server)


def test_tier_ap_with_bootstrap_ci_above_floor():
    temp_dir, client, server = _setup()
    try:
        tag = "tier_ap_with_ci"
        pics = []
        for i in range(30):
            pid = _upload_named(client)
            _seed_prediction(server, pid, tag, 0.5 + 0.001 * i)
            pics.append((pid, "POS"))
        for i in range(15):
            pid = _upload_named(client)
            _seed_prediction(server, pid, tag, 0.4 - 0.001 * i)
            pics.append((pid, "NEG"))
        slice_id = _seed_eval_slice(server, tag, pics)

        detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}", params={"model_version": "v1"}
        ).json()
        assert detail["eval_metric_kind"] == "AP"
        assert detail["eval_n_pos"] == 30
        assert detail["eval_ap"] is not None
        # With 30/45 positive and well-separated confidences, degenerate
        # (zero-positive) resamples are astronomically unlikely -> a real CI.
        assert detail["eval_ap_ci_low"] is not None
        assert detail["eval_ap_ci_high"] is not None
        assert 0.0 <= detail["eval_ap_ci_low"] <= detail["eval_ap"] + 1e-9
        assert detail["eval_ap"] - 1e-9 <= detail["eval_ap_ci_high"] <= 1.0
    finally:
        _teardown(temp_dir, server)


def test_tier_f1_carried_forward_and_calibrated(monkeypatch):
    temp_dir, client, server = _setup()
    try:
        tag = "tier_carried_forward"
        # load_raw_label_thresholds' real implementation keys its returned
        # dict by sanitise_tag(raw_json_key) -- mirror that so the lookup in
        # _find_calibrated_threshold (which does the same sanitise_tag(tag))
        # actually hits.
        monkeypatch.setattr(
            tag_prediction_service,
            "load_raw_label_thresholds",
            lambda meta_path: {sanitise_tag(tag): 0.55},
        )

        pics = [(_upload_named(client), "POS") for _ in range(12)] + [
            (_upload_named(client), "NEG") for _ in range(3)
        ]
        t0 = datetime.utcnow()
        for pid, state in pics:
            _seed_prediction(
                server,
                pid,
                tag,
                0.9 if state == "POS" else 0.1,
                model_version="v1",
                predicted_at=t0,
            )
        slice_id = _seed_eval_slice(server, tag, pics)

        # A later, unrelated prediction on a different (tag, picture) makes
        # "v2" the vault's current model version, so scoring "v1" here is
        # explicitly a DIFFERENT generation than the current one.
        other_pid = _upload_named(client)
        _seed_prediction(
            server,
            other_pid,
            "unrelated",
            0.5,
            model_version="v2",
            predicted_at=datetime.utcnow(),
        )

        detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}", params={"model_version": "v1"}
        ).json()
        assert detail["eval_metric_kind"] == "F1"
        assert detail["eval_threshold_source"] == "carried_forward"
        assert detail["eval_precision"] is not None
        assert detail["eval_recall"] is not None
        assert detail["eval_f1"] is not None
        # Threshold 0.55: all 12 POS (conf 0.9) and no NEG (conf 0.1) predicted
        # positive -> perfect P/R/F1 on this fixture.
        assert detail["eval_precision"] == 1.0
        assert detail["eval_recall"] == 1.0
        assert detail["eval_f1"] == 1.0

        # Bonus: a second tag scored at exactly the vault's CURRENT generation
        # resolves the same kind of on-disk meta threshold to "calibrated"
        # instead of "carried_forward" -- same source (the one meta.json on
        # disk), different scored generation (see _find_calibrated_threshold's
        # documented judgment call).
        tag2 = "tier_calibrated"
        monkeypatch.setattr(
            tag_prediction_service,
            "load_raw_label_thresholds",
            lambda meta_path: {sanitise_tag(tag2): 0.55},
        )
        pics2 = [(_upload_named(client), "POS") for _ in range(10)] + [
            (_upload_named(client), "NEG") for _ in range(2)
        ]
        t1 = datetime.utcnow()
        for pid, state in pics2:
            _seed_prediction(
                server,
                pid,
                tag2,
                0.9 if state == "POS" else 0.1,
                model_version="v3",
                predicted_at=t1,
            )
        slice_2_id = _seed_eval_slice(server, tag2, pics2)
        # "v3" is now the most-recently-written prediction in the vault ->
        # the current model version.
        detail2 = client.get(
            f"{API}/tag_eval_slices/{slice_2_id}", params={"model_version": "v3"}
        ).json()
        assert detail2["eval_threshold_source"] == "calibrated"
        assert detail2["eval_metric_kind"] == "F1"
    finally:
        _teardown(temp_dir, server)


def test_tier_f1_rederived_from_disjoint_train_val_slice():
    temp_dir, client, server = _setup()
    try:
        tag = "tier_rederived"
        pics = [(_upload_named(client), "POS") for _ in range(12)] + [
            (_upload_named(client), "NEG") for _ in range(3)
        ]
        for pid, state in pics:
            _seed_prediction(
                server, pid, tag, 0.9 if state == "POS" else 0.1, model_version="v1"
            )
        slice_id = _seed_eval_slice(server, tag, pics)

        # Disjoint TRAIN-side human-labeled val slice, >= MIN_EVAL_N_POS,
        # scored at the current ("v1") model version with a confidence spread
        # so the sweep has real signal to derive a threshold from.
        for i in range(12):
            pid = _upload_named(client)
            _set_split(server, pid, "TRAIN")
            _seed_prediction(
                server,
                pid,
                tag,
                0.6 + 0.02 * i,
                model_version="v1",
                label_state="POS",
                label_source="human",
            )
        for i in range(4):
            pid = _upload_named(client)
            _set_split(server, pid, "TRAIN")
            _seed_prediction(
                server,
                pid,
                tag,
                0.2 - 0.02 * i,
                model_version="v1",
                label_state="NEG",
                label_source="human",
            )

        detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}", params={"model_version": "v1"}
        ).json()
        assert detail["eval_metric_kind"] == "F1"
        assert detail["eval_threshold_source"] == "rederived_disjoint_val"
        assert detail["eval_precision"] is not None
        assert detail["eval_recall"] is not None
        assert detail["eval_f1"] is not None
        assert 0.0 <= detail["eval_f1"] <= 1.0
    finally:
        _teardown(temp_dir, server)


def test_tier_f1_uncalibrated_fallback_is_opt_in_not_default():
    temp_dir, client, server = _setup()
    try:
        tag = "tier_uncalibrated"
        pics = [(_upload_named(client), "POS") for _ in range(12)] + [
            (_upload_named(client), "NEG") for _ in range(3)
        ]
        for pid, state in pics:
            _seed_prediction(
                server, pid, tag, 0.9 if state == "POS" else 0.1, model_version="v1"
            )
        slice_id = _seed_eval_slice(server, tag, pics)
        # No calibrated meta threshold (meta_path is None in tests), and no
        # TRAIN-side val data at all for this tag -> neither 3a nor 3b fire.

        # Default: AP, never the uncalibrated fallback.
        default_detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}", params={"model_version": "v1"}
        ).json()
        assert default_detail["eval_metric_kind"] == "AP"
        assert default_detail["eval_threshold_source"] == "none"

        # Explicit opt-in: fixed 0.5 threshold, flagged uncalibrated_fallback.
        fallback_detail = client.get(
            f"{API}/tag_eval_slices/{slice_id}",
            params={"model_version": "v1", "allow_uncalibrated_f1": "true"},
        ).json()
        assert fallback_detail["eval_metric_kind"] == "F1"
        assert fallback_detail["eval_threshold_source"] == "uncalibrated_fallback"
        # Threshold 0.5: all 12 POS (0.9) and no NEG (0.1) predicted positive.
        assert fallback_detail["eval_precision"] == 1.0
        assert fallback_detail["eval_recall"] == 1.0
        assert fallback_detail["eval_f1"] == 1.0
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (e) AP correctness: hand-computed fixture vs. the non-interpolated formula.
# --------------------------------------------------------------------------- #


def test_average_precision_matches_hand_computed_value():
    # 3 positives, 2 negatives, descending confidence: pos, neg, pos, pos, neg.
    pairs = [
        (0.9, True),
        (0.8, False),
        (0.7, True),
        (0.6, True),
        (0.5, False),
    ]
    # By hand (non-interpolated step formula):
    #   rank1 pos: P=1/1=1.0,        contributes (1/3-0)*1.0     = 0.33333333
    #   rank2 neg: no recall change, no contribution
    #   rank3 pos: P=2/3,            contributes (2/3-1/3)*2/3   = 0.22222222
    #   rank4 pos: P=3/4,            contributes (3/3-2/3)*3/4   = 0.25
    #   rank5 neg: no contribution
    expected = (1 / 3) * 1.0 + (1 / 3) * (2 / 3) + (1 / 3) * (3 / 4)
    ap = average_precision(pairs)
    assert abs(ap - expected) < 1e-9
    assert abs(ap - 0.8055555555555556) < 1e-9

    # A perfect ranking (all positives first) has AP == 1.0.
    assert average_precision([(0.9, True), (0.8, True), (0.1, False)]) == 1.0

    # No positives at all -> undefined.
    assert average_precision([(0.9, False), (0.1, False)]) is None


# --------------------------------------------------------------------------- #
# (f) Bootstrap CI degenerate-resample handling on an adversarial fixture.
# --------------------------------------------------------------------------- #


def test_bootstrap_ci_collapses_on_thin_positive_population():
    # 1 positive among 5 -- resampling 5-with-replacement from this pool has
    # a high (~33%) chance of drawing zero positives, comfortably above the
    # 10% degenerate-collapse threshold over many iterations.
    pairs = [(0.9, True), (0.4, False), (0.35, False), (0.3, False), (0.2, False)]
    rng = np.random.default_rng(1234)
    low, high = bootstrap_ap_ci(pairs, iterations=500, rng=rng)
    assert low is None and high is None


def test_bootstrap_ci_produces_sane_interval_on_a_well_populated_fixture():
    rng = np.random.default_rng(42)
    pairs = [(0.5 + 0.01 * i, True) for i in range(20)] + [
        (0.4 - 0.01 * i, False) for i in range(20)
    ]
    low, high = bootstrap_ap_ci(pairs, iterations=500, rng=rng)
    assert low is not None and high is not None
    assert 0.0 <= low <= high <= 1.0


# --------------------------------------------------------------------------- #
# (g) Authz: scoped READ token gets 403 on all three routes; owner succeeds.
# --------------------------------------------------------------------------- #


def test_scoped_token_gets_403_owner_succeeds():
    temp_dir, client, server = _setup()
    try:
        tag = "authz_tag"
        pics = _make_candidates(client, server, tag, n_pos=10, n_neg=1)
        set_id = client.post(f"{API}/picture_sets", json={"name": "Scoped set"}).json()[
            "picture_set"
        ]["id"]

        def _add_to_set(pid):
            def ins(session):
                from pixlstash.db_models import PictureSetMember

                session.add(PictureSetMember(set_id=set_id, picture_id=pid))
                session.commit()

            server.vault.db.run_task(ins)

        _add_to_set(pics[0])

        # A token scoped to a picture_set (not the whole vault) must be
        # rejected -- these are vault-wide curation routes, same pattern as
        # tag_health.py / reviews.py / picture_splits.py.
        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "set read",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": set_id,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}

        assert (
            bearer.post(
                f"{API}/tag_eval_slices", json={"tag": tag}, headers=headers
            ).status_code
            == 403
        )
        assert (
            bearer.get(
                f"{API}/tag_eval_slices", params={"tag": tag}, headers=headers
            ).status_code
            == 403
        )
        assert (
            bearer.get(f"{API}/tag_eval_slices/1", headers=headers).status_code == 403
        )

        # Owner (unscoped session) succeeds on all three.
        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.status_code == 200, resp.text
        slice_id = resp.json()["slice_id"]
        assert (
            client.get(f"{API}/tag_eval_slices", params={"tag": tag}).status_code == 200
        )
        assert client.get(f"{API}/tag_eval_slices/{slice_id}").status_code == 200

        # Unknown slice id -> 404, not a crash, still under owner auth.
        assert client.get(f"{API}/tag_eval_slices/999999").status_code == 404
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# Wave D: GET /tag_eval_slices/{tag}/picture_ids -- the entire id-discovery
# surface a downstream consumer (e.g. pixltagger) needs. See design doc §6.
# --------------------------------------------------------------------------- #


def test_active_slice_picture_ids_returns_active_slice_membership():
    temp_dir, client, server = _setup()
    try:
        tag = "picture_ids_tag"
        pics = _make_candidates(client, server, tag, n_pos=10, n_neg=2)

        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.status_code == 200, resp.text
        assert resp.json()["created"] is True

        r = client.get(f"{API}/tag_eval_slices/{tag}/picture_ids")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tag"] == tag
        assert body["total"] == 12
        assert set(body["picture_ids"]) == set(pics)
        # No label payload travels here -- ids only.
        assert "label_state" not in str(body["picture_ids"])
        for pid in body["picture_ids"]:
            assert isinstance(pid, int)
    finally:
        _teardown(temp_dir, server)


def test_active_slice_picture_ids_pagination():
    temp_dir, client, server = _setup()
    try:
        tag = "picture_ids_paginated"
        pics = _make_candidates(client, server, tag, n_pos=10, n_neg=5)
        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.json()["created"] is True

        all_ids = client.get(f"{API}/tag_eval_slices/{tag}/picture_ids").json()[
            "picture_ids"
        ]
        assert len(all_ids) == 15

        page1 = client.get(
            f"{API}/tag_eval_slices/{tag}/picture_ids",
            params={"limit": 5, "offset": 0},
        ).json()
        page2 = client.get(
            f"{API}/tag_eval_slices/{tag}/picture_ids",
            params={"limit": 5, "offset": 5},
        ).json()
        page3 = client.get(
            f"{API}/tag_eval_slices/{tag}/picture_ids",
            params={"limit": 5, "offset": 10},
        ).json()

        assert page1["total"] == page2["total"] == page3["total"] == 15
        assert len(page1["picture_ids"]) == 5
        assert len(page2["picture_ids"]) == 5
        assert len(page3["picture_ids"]) == 5
        # Pages are disjoint and their union recovers every id, unpaginated.
        paginated_union = (
            set(page1["picture_ids"])
            | set(page2["picture_ids"])
            | set(page3["picture_ids"])
        )
        assert paginated_union == set(all_ids) == set(pics)
        assert len(paginated_union) == 15  # disjoint, no overlap/duplication

        # A limit above the server-side cap is silently clamped, not rejected.
        clamped = client.get(
            f"{API}/tag_eval_slices/{tag}/picture_ids",
            params={"limit": 999999},
        ).json()
        assert len(clamped["picture_ids"]) == 15
    finally:
        _teardown(temp_dir, server)


def test_active_slice_picture_ids_404_when_no_active_slice():
    temp_dir, client, server = _setup()
    try:
        # No slice ever frozen for this tag.
        r = client.get(f"{API}/tag_eval_slices/never_frozen/picture_ids")
        assert r.status_code == 404

        # A SUPERSEDED-only slice (e.g. a re-freeze) is not ACTIVE -- also 404,
        # not a stale/incorrect id list.
        tag = "picture_ids_superseded_only"
        pics = _make_candidates(client, server, tag, n_pos=10, n_neg=2)
        _seed_eval_slice(
            server, tag, [(p, "POS") for p in pics[:2]], status="SUPERSEDED"
        )
        r2 = client.get(f"{API}/tag_eval_slices/{tag}/picture_ids")
        assert r2.status_code == 404
    finally:
        _teardown(temp_dir, server)


def test_active_slice_picture_ids_scoped_token_gets_403():
    temp_dir, client, server = _setup()
    try:
        tag = "picture_ids_authz_tag"
        _make_candidates(client, server, tag, n_pos=10, n_neg=1)
        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.json()["created"] is True

        set_id = client.post(f"{API}/picture_sets", json={"name": "Scoped set"}).json()[
            "picture_set"
        ]["id"]
        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "set read",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": set_id,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}

        assert (
            bearer.get(
                f"{API}/tag_eval_slices/{tag}/picture_ids", headers=headers
            ).status_code
            == 403
        )
        # Owner still succeeds.
        assert client.get(f"{API}/tag_eval_slices/{tag}/picture_ids").status_code == 200
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (h) Re-scoring a frozen slice against a new model generation: this is the
# concrete regression test for the confidence/model_version freeze bug. A
# human-labeled row's TagPrediction.confidence/model_version must stay live
# so a re-tag at a new model_version is actually joinable against the frozen
# slice, instead of eval_metric_kind getting stuck at "none".
# --------------------------------------------------------------------------- #


def test_retag_at_new_model_version_lets_frozen_slice_recover_from_none():
    temp_dir, client, server = _setup()
    try:
        tag = "eval_retag_recovers"
        # 10 human-labeled POS + 2 NEG, all written at model_version "v1".
        pics = _make_candidates(
            client, server, tag, n_pos=10, n_neg=2, model_version="v1"
        )
        pos_pics, neg_pics = pics[:10], pics[10:]

        resp = client.post(f"{API}/tag_eval_slices", json={"tag": tag})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] is True
        slice_id = body["slice_id"]

        # Sanity: scoring the frozen slice at "v1" (the generation it was
        # written against) is NOT stuck at "none" -- confirms the fixture
        # itself is sound before we test the retag path.
        before = server.vault.db.run_immediate_read_task(
            lambda session: tag_eval_slice_service.compute_eval_metrics_in_session(
                session, None, slice_id, "v1"
            )
        )
        assert before["eval_metric_kind"] != "none"

        # Simulate the natural TagTask entry point re-tagging these same
        # pictures at a NEW model_version "v2". Every row here is
        # label_source == "human" (from _make_candidates), so this is
        # exactly the path the freeze bug broke: the bundled guard refused
        # to update confidence/model_version on a human row, so no
        # TagPrediction row ever existed at "v2" for this tag.
        label_scores_by_pic_id = {pid: {tag: 0.85} for pid in pos_pics}
        label_scores_by_pic_id.update({pid: {tag: 0.1} for pid in neg_pics})
        tags_by_pic_id = {pid: {tag} for pid in pos_pics}
        tags_by_pic_id.update({pid: set() for pid in neg_pics})
        server.vault.db.run_task(
            TagTask._write_predictions_from_tags,
            label_scores_by_pic_id,
            tags_by_pic_id,
            "v2",
        )

        # Scoring the frozen slice against the NEW model_version now finds
        # the updated live confidences and computes a real metric -- no
        # longer stuck at "none".
        after = server.vault.db.run_immediate_read_task(
            lambda session: tag_eval_slice_service.compute_eval_metrics_in_session(
                session, None, slice_id, "v2"
            )
        )
        assert after["eval_metric_kind"] != "none"
        assert after["eval_n"] == 12
        assert after["eval_n_pos"] == 10
    finally:
        _teardown(temp_dir, server)

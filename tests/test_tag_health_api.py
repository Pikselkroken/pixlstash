"""Tests for the tag health board: signal aggregates on a small fixture vault,
the rebuild endpoint's background/progress reporting, and no-model-signal rows."""

import gc
import io
import json
import os
import tempfile
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from PIL import Image

from pixlstash.db_models import (
    Picture,
    PictureLikeness,
    PictureSet,
    PictureSetMember,
    PictureStack,
    Tag,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.server import Server
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
        "/login", json={"username": "testuser", "password": "testpassword"}
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
        client, [("file", (f"distinct{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _rebuild_and_wait(client, timeout_s=30):
    resp = client.post(f"{API}/tag_health/rebuild")
    assert resp.status_code == 200, resp.text
    start = time.time()
    while time.time() - start < timeout_s:
        body = client.get(f"{API}/tag_health").json()
        if not body["building"]:
            return body
        time.sleep(0.1)
    raise AssertionError("tag_health rebuild did not finish in time")


def test_tag_health_aggregates_on_fixture_vault():
    temp_dir, client, server = _setup()
    try:
        p1 = _upload_named(client)  # tagged "t", conf 0.05  → est_wrong + dispute
        p2 = _upload_named(client)  # untagged,   conf 0.95  → est_missing
        p3 = _upload_named(client)  # untagged,   conf 0.50  → boundary mass
        p4 = _upload_named(client)  # tagged "u", no predictions → no-model row

        now = datetime.utcnow()

        def seed(session):
            session.add(Tag(picture_id=p1, tag="t"))
            session.add(Tag(picture_id=p4, tag="u"))
            # Three predictions for "t", all on the current model version.
            session.add(
                TagPrediction(
                    picture_id=p1,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now - timedelta(minutes=2),
                    # Human froze POS but the live model is confidently negative:
                    # a model-disputes-human row (and a verified one).
                    label_state="POS",
                    label_source="human",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p2,
                    tag="t",
                    confidence=0.95,
                    model_version="v1",
                    predicted_at=now - timedelta(minutes=1),
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p3,
                    tag="t",
                    confidence=0.50,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            # Reviewed history for "t": one accepted, one dismissed → overturn 0.5.
            session.add(
                TagSuggestion(
                    picture_id=p1,
                    tag="t",
                    direction="remove",
                    source="near_neighbor",
                    score=1.0,
                    status="ACCEPTED",
                    reviewed_at=now,
                )
            )
            session.add(
                TagSuggestion(
                    picture_id=p2,
                    tag="t",
                    direction="add",
                    source="model",
                    score=1.0,
                    status="DISMISSED",
                )
            )
            # A stored high-likeness pair disagreeing on "t" → mismatch 1.
            a, b = PictureLikeness.canon_pair(p1, p2)
            session.add(
                PictureLikeness(
                    picture_id_a=a, picture_id_b=b, likeness=0.99, metric="cosine"
                )
            )
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        assert body["computed_at"] is not None
        assert body["progress"] == 1.0
        rows = {r["tag"]: r for r in body["rows"]}

        t = rows["t"]
        assert t["est_wrong"] == 1
        assert t["est_missing"] == 1
        assert t["mismatch"] == 1
        assert abs(t["verified_pct"] - 1 / 3) < 1e-9
        assert abs(t["boundary_pct"] - 1 / 3) < 1e-9
        assert t["overturn_rate"] == 0.5
        assert t["model_disputes"] == 1
        assert t["has_model"] is True
        assert t["last_reviewed_at"] is not None  # newest reviewed_at ISO

        # A tag with ground truth but zero predictions still gets a row, with
        # the explicit no-model-signal state.
        u = rows["u"]
        assert u["has_model"] is False
        assert u["est_wrong"] == 0
        assert u["est_missing"] == 0
        assert u["overturn_rate"] is None
        assert u["verified_pct"] == 0.0
        assert u["last_reviewed_at"] is None  # never reviewed → "never"
    finally:
        _teardown(temp_dir, server)


def test_tag_health_same_stack_mismatch_and_no_double_count():
    temp_dir, client, server = _setup()
    try:
        p1 = _upload_named(client)
        p2 = _upload_named(client)
        p3 = _upload_named(client)

        def seed(session):
            stack = PictureStack(name="s")
            session.add(stack)
            session.commit()
            session.refresh(stack)
            for pid in (p1, p2, p3):
                pic = session.get(Picture, pid)
                pic.stack_id = stack.id
                session.add(pic)
            # One of the three stacked versions carries "t" → 1×2 = 2 pairs.
            session.add(Tag(picture_id=p1, tag="t"))
            # A stored likeness pair INSIDE the same stack must not be counted
            # twice on top of the stack pair.
            a, b = PictureLikeness.canon_pair(p1, p2)
            session.add(
                PictureLikeness(
                    picture_id_a=a, picture_id_b=b, likeness=0.99, metric="cosine"
                )
            )
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}
        assert rows["t"]["mismatch"] == 2
        assert rows["t"]["has_model"] is False  # no predictions at all
    finally:
        _teardown(temp_dir, server)


def test_tag_health_scoped_restricts_signals_and_tag_list():
    """`GET /tag_health?set_id=` computes live rows restricted to the scope:
    counts include only in-scope pictures, and tags that never appear on an
    in-scope picture get no row at all. The vault-wide cache is untouched."""
    temp_dir, client, server = _setup()
    try:
        p_in = _upload_named(client)  # in the set:  tagged "t", conf 0.05
        p_out = _upload_named(client)  # outside:     untagged "t", conf 0.95
        p_other = _upload_named(client)  # outside:     tagged "only_out"

        now = datetime.utcnow()

        def seed(session):
            ps = PictureSet(name="scope_set")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_in))
            session.add(Tag(picture_id=p_in, tag="t"))
            session.add(Tag(picture_id=p_other, tag="only_out"))
            # In-scope est_wrong for "t"; out-of-scope est_missing for "t".
            session.add(
                TagPrediction(
                    picture_id=p_in,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_out,
                    tag="t",
                    confidence=0.95,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.commit()
            return ps.id

        set_id = server.vault.db.run_task(seed)

        # Vault-wide (cached) rows see both pictures and both tags.
        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}
        assert rows["t"]["est_wrong"] == 1
        assert rows["t"]["est_missing"] == 1
        assert "only_out" in rows

        # Scoped to the set: only the in-scope picture's signals, and the
        # out-of-scope-only tag disappears from the board entirely.
        scoped = client.get(f"{API}/tag_health", params={"set_id": set_id}).json()
        assert scoped["scoped"] is True
        assert scoped["building"] is False
        srows = {r["tag"]: r for r in scoped["rows"]}
        assert srows["t"]["est_wrong"] == 1
        assert srows["t"]["est_missing"] == 0  # p_out is outside the scope
        assert "only_out" not in srows

        # An unknown scope id is a valid empty scope — no rows, not an error.
        empty = client.get(f"{API}/tag_health", params={"set_id": 99999}).json()
        assert empty["scoped"] is True
        assert empty["rows"] == []

        # The unscoped cache is untouched by scoped reads.
        body2 = client.get(f"{API}/tag_health").json()
        assert {r["tag"] for r in body2["rows"]} == {"t", "only_out"}
    finally:
        _teardown(temp_dir, server)


def test_tag_health_empty_vault_and_rebuild_idempotence():
    temp_dir, client, server = _setup()
    try:
        body = client.get(f"{API}/tag_health").json()
        assert body["rows"] == []
        assert body["building"] is False
        assert body["computed_at"] is None

        body = _rebuild_and_wait(client)
        assert body["rows"] == []

        # Rebuild twice in a row: second call while idle just re-runs; rows
        # are replaced, not duplicated.
        p1 = _upload_named(client)

        def seed(session):
            session.add(Tag(picture_id=p1, tag="t"))
            session.commit()

        server.vault.db.run_task(seed)
        body = _rebuild_and_wait(client)
        assert [r["tag"] for r in body["rows"]] == ["t"]
        body = _rebuild_and_wait(client)
        assert [r["tag"] for r in body["rows"]] == ["t"]
    finally:
        _teardown(temp_dir, server)

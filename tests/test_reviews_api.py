"""Tests for the Review Sessions API: create/scan receipt, one-open-per-tag,
diff-insert + no-resurrection, re-parenting with include_reviewed, neighbour
capture, kind derivation, refresh, archive/abort, and scope freezing."""

import gc
import io
import json
import os
import tempfile

from fastapi.testclient import TestClient
from PIL import Image

import numpy as np
from sqlmodel import select

from pixlstash.db_models import Picture, PictureSetMember, PictureStack, Tag
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.server import Server
from pixlstash.services.review_service import derive_kind
from tests.utils import upload_pictures_and_wait

API = "/api/v1"
TAG = "malformed hand"


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
    """Upload a fresh, content-distinct in-memory PNG and return its id."""
    _distinct_counter[0] += 1
    n = _distinct_counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return upload_pictures_and_wait(
        client, [("file", (f"distinct{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _seed_tag(server, pic_id, tag=TAG):
    def insert(session):
        session.add(Tag(picture_id=pic_id, tag=tag))
        session.commit()

    server.vault.db.run_task(insert)


def _set_embedding(server, pic_id, vec):
    blob = np.asarray(vec, dtype=np.float32).tobytes()

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.image_embedding = blob
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def _set_phash(server, pic_id, phash_int):
    hex_str = f"{phash_int:016x}"

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.perceptual_hash = hex_str
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def _axis_vec(axis, value=1.0):
    vec = [0.0] * 512
    vec[axis] = value
    return vec


def _make_pair(client, server, axis=0, tag=TAG):
    """Two pictures with identical embeddings on the given axis; first tagged.

    Far-apart phashes so the pair is 'binary', not a perceptual near-duplicate.
    Returns (tagged_id, untagged_id).
    """
    a = _upload_named(client)
    b = _upload_named(client)
    vec = _axis_vec(axis)
    _set_embedding(server, a, vec)
    _set_embedding(server, b, vec)
    # 64 bits apart on distinct patterns per pair so no accidental near-dups.
    _set_phash(server, a, 0xFFFF_FFFF_FFFF_FFFF >> axis)
    _set_phash(server, b, (0xFFFF_FFFF_FFFF_FFFF >> axis) ^ 0xFFFF_FFFF_FFFF_FFFF)
    _seed_tag(server, a, tag)
    return a, b


def _get_suggestion(server, sid):
    return server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(TagSuggestion).where(TagSuggestion.id == sid)).first()
    )


def test_create_review_receipt_neighbors_and_progress():
    temp_dir, client, server = _setup()
    try:
        a, b = _make_pair(client, server)
        resp = client.post(f"{API}/reviews", json={"tag": TAG})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tag"] == TAG
        assert body["status"] == "OPEN"
        assert body["scope"] == {
            "project_id": None,
            "set_id": None,
            "character_id": None,
        }
        assert body["stats"]["scanned"] == 2
        assert body["stats"]["found"] == 1
        assert body["stats"]["prev_reviewed"] == 0
        # No tagger predictions on file → nothing is auto-resolvable.
        assert body["stats"]["auto_resolvable"] == 0
        rid = body["id"]

        rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
        assert len(rows) == 1
        row = rows[0]
        assert row["review_id"] == rid
        assert {row["picture_id"], row["twin_picture_id"]} == {a, b}
        assert row["direction"] in ("add", "remove")
        assert row["kind"] == "binary"  # no shared stack, far-apart dhash
        # Neighbour capture: with two pictures, k clamps to 1 — the one
        # neighbour is the other picture, with its has-the-tag flag.
        other = b if row["picture_id"] == a else a
        assert row["neighbors"] == [{"picture_id": other, "has": other == a}]

        # List view: progress and staleness.
        listed = client.get(f"{API}/reviews").json()
        assert [r["id"] for r in listed] == [rid]
        assert listed[0]["progress"] == {"done": 0, "pending": 1, "skipped": 0}
        assert listed[0]["stale"] is False

        detail = client.get(f"{API}/reviews/{rid}").json()
        assert detail["progress"] == {"done": 0, "pending": 1, "skipped": 0}
        assert detail["stats"]["scanned"] == 2
        assert "auto_resolvable" in detail["stats"]
        assert detail["receipt"] == {"removed": 0, "added": 0, "kept": 0, "skipped": 0}
    finally:
        _teardown(temp_dir, server)


def test_one_open_review_per_tag_conflict():
    temp_dir, client, server = _setup()
    try:
        _make_pair(client, server)
        first = client.post(f"{API}/reviews", json={"tag": TAG})
        assert first.status_code == 200
        rid = first.json()["id"]

        dup = client.post(f"{API}/reviews", json={"tag": TAG})
        assert dup.status_code == 409

        # A different tag is fine.
        other = client.post(f"{API}/reviews", json={"tag": "bad anatomy"})
        assert other.status_code == 200

        # Closing the first review frees the tag again.
        assert client.post(f"{API}/reviews/{rid}/archive").status_code == 200
        again = client.post(f"{API}/reviews", json={"tag": TAG})
        assert again.status_code == 200
    finally:
        _teardown(temp_dir, server)


def test_refresh_diff_inserts_and_never_resurrects_decided_rows():
    temp_dir, client, server = _setup()
    try:
        _make_pair(client, server, axis=0)
        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
        assert len(rows) == 1
        sid = rows[0]["id"]

        # Decide it (dismiss leaves the labels untouched, so a re-scan would
        # still flag the pair if resurrection were possible).
        assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200

        # Refresh: nothing new, and the decided row stays decided.
        refreshed = client.post(f"{API}/reviews/{rid}/refresh").json()
        assert refreshed["new_count"] == 0
        assert refreshed["found"] == 1
        assert refreshed["refreshed_at"] is not None
        row = _get_suggestion(server, sid)
        assert row.status == "DISMISSED"
        assert row.review_id == rid
        assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []

        # A genuinely new pair appended by refresh, without duplicating the old.
        _make_pair(client, server, axis=1)
        refreshed = client.post(f"{API}/reviews/{rid}/refresh").json()
        assert refreshed["new_count"] == 1
        assert refreshed["found"] == 2
        pending = client.get(f"{API}/reviews/{rid}/suggestions").json()
        assert len(pending) == 1
        assert pending[0]["id"] != sid
        # Still exactly one row for the decided pair — no duplicates anywhere.
        all_rows = client.get(
            f"{API}/reviews/{rid}/suggestions", params={"status": ""}
        ).json()
        assert len(all_rows) == 2
    finally:
        _teardown(temp_dir, server)


def test_include_reviewed_reparents_decided_rows():
    temp_dir, client, server = _setup()
    try:
        _make_pair(client, server)
        r1 = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        sid = client.get(f"{API}/reviews/{r1}/suggestions").json()[0]["id"]
        assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200
        assert client.post(f"{API}/reviews/{r1}/archive").status_code == 200

        # Default: previously-decided suspects stay suppressed, but are counted.
        r2_body = client.post(f"{API}/reviews", json={"tag": TAG}).json()
        assert r2_body["stats"]["found"] == 0
        assert r2_body["stats"]["prev_reviewed"] == 1
        assert client.post(f"{API}/reviews/{r2_body['id']}/abort").status_code == 200

        # include_reviewed: the SAME row is re-parented and reopened — not
        # duplicated (UNIQUE(picture_id, tag, source) intact) and not recreated.
        r3_body = client.post(
            f"{API}/reviews", json={"tag": TAG, "include_reviewed": True}
        ).json()
        r3 = r3_body["id"]
        assert r3_body["stats"]["found"] == 1
        assert r3_body["stats"]["prev_reviewed"] == 1
        rows = client.get(f"{API}/reviews/{r3}/suggestions").json()
        assert [r["id"] for r in rows] == [sid]
        assert rows[0]["status"] == "PENDING"
        row = _get_suggestion(server, sid)
        assert row.review_id == r3
        assert row.reviewed_at is None

        # The human-label ledger written by the dismissal is untouched: the
        # dismissal of a 'remove' suggestion asserted POS, and re-surfacing the
        # suspect must not erase that supervision.
        pred = server.vault.db.run_immediate_read_task(
            lambda s: s.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == row.picture_id,
                    TagPrediction.tag == TAG,
                )
            ).first()
        )
        assert pred is not None
        assert pred.label_source == "human"
        assert pred.label_state in ("POS", "NEG")
    finally:
        _teardown(temp_dir, server)


def test_kind_pair_for_dhash_near_duplicates_and_stacks():
    temp_dir, client, server = _setup()
    try:
        a = _upload_named(client)
        b = _upload_named(client)
        vec = _axis_vec(0)
        _set_embedding(server, a, vec)
        _set_embedding(server, b, vec)
        # 2-bit dhash Hamming → versions of one shot → "pair".
        _set_phash(server, a, 0xFFFF_FFFF_FFFF_FFFF)
        _set_phash(server, b, 0xFFFF_FFFF_FFFF_FFFC)
        _seed_tag(server, a)

        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
        assert len(rows) == 1
        assert rows[0]["kind"] == "pair"

        # Same-stack derivation (pure function; stack ids are what matter).
        assert derive_kind((7, "00" * 8), (7, "ff" * 8)) == "pair"
        assert derive_kind((7, None), (8, None)) == "binary"
        assert derive_kind((None, None), (None, None)) == "binary"
    finally:
        _teardown(temp_dir, server)


def test_kind_pair_for_same_stack_via_api():
    temp_dir, client, server = _setup()
    try:
        a, b = _make_pair(client, server)  # far-apart dhash

        def make_stack(session):
            stack = PictureStack(name="s")
            session.add(stack)
            session.commit()
            session.refresh(stack)
            for pid in (a, b):
                pic = session.get(Picture, pid)
                pic.stack_id = stack.id
                session.add(pic)
            session.commit()

        server.vault.db.run_task(make_stack)

        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
        assert len(rows) == 1
        assert rows[0]["kind"] == "pair"
    finally:
        _teardown(temp_dir, server)


def test_archive_and_abort_leave_rows_and_guard_transitions():
    temp_dir, client, server = _setup()
    try:
        _make_pair(client, server)
        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        sid = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]["id"]

        archived = client.post(f"{API}/reviews/{rid}/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "ARCHIVED"
        # Suggestion rows untouched by closing the session.
        row = _get_suggestion(server, sid)
        assert row.status == "PENDING"
        assert row.review_id == rid

        # Idempotent re-archive; conflicting transitions rejected.
        assert client.post(f"{API}/reviews/{rid}/archive").status_code == 200
        assert client.post(f"{API}/reviews/{rid}/abort").status_code == 409
        assert client.post(f"{API}/reviews/{rid}/refresh").status_code == 409

        # Status filter on the list endpoint.
        assert (
            client.get(f"{API}/reviews", params={"status": "ARCHIVED"}).json()[0]["id"]
            == rid
        )
        assert client.get(f"{API}/reviews", params={"status": "OPEN"}).json() == []

        assert client.get(f"{API}/reviews/999999").status_code == 404
        assert client.post(f"{API}/reviews/999999/refresh").status_code == 404
    finally:
        _teardown(temp_dir, server)


def test_review_scope_is_frozen_and_restricts_the_scan():
    temp_dir, client, server = _setup()
    try:
        in_a, in_b = _make_pair(client, server, axis=0)
        out_a, out_b = _make_pair(client, server, axis=1)

        r = client.post(f"{API}/picture_sets", json={"name": "Scope"})
        set_id = r.json()["picture_set"]["id"]

        def add_members(session):
            session.add(PictureSetMember(set_id=set_id, picture_id=in_a))
            session.add(PictureSetMember(set_id=set_id, picture_id=in_b))
            session.commit()

        server.vault.db.run_task(add_members)

        body = client.post(f"{API}/reviews", json={"tag": TAG, "set_id": set_id}).json()
        assert body["scope"]["set_id"] == set_id
        assert body["stats"]["scanned"] == 2  # only the in-set pair
        rows = client.get(f"{API}/reviews/{body['id']}/suggestions").json()
        assert len(rows) == 1
        assert {rows[0]["picture_id"], rows[0]["twin_picture_id"]} == {in_a, in_b}
        assert out_a not in {rows[0]["picture_id"], rows[0]["twin_picture_id"]}
    finally:
        _teardown(temp_dir, server)


def test_stale_flag_after_tagger_run():
    from datetime import datetime

    from pixlstash.db_models import TaggerRun

    temp_dir, client, server = _setup()
    try:
        _make_pair(client, server)
        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        assert client.get(f"{API}/reviews/{rid}").json()["stale"] is False

        # A tagger run completed after the scan makes the review stale...
        def add_run(session):
            session.add(TaggerRun(run="run-1", created_at=datetime.utcnow()))
            session.commit()

        server.vault.db.run_task(add_run)
        assert client.get(f"{API}/reviews/{rid}").json()["stale"] is True

        # ...and refreshing clears it.
        client.post(f"{API}/reviews/{rid}/refresh")
        assert client.get(f"{API}/reviews/{rid}").json()["stale"] is False
    finally:
        _teardown(temp_dir, server)


def test_auto_resolvable_counts_review_scoped_bulk_dry_run():
    temp_dir, client, server = _setup()
    try:
        a, b = _make_pair(client, server)

        def seed_preds(session):
            for pid in (a, b):
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag=TAG,
                        confidence=0.03,
                        model_version="test-v1",
                        status="PENDING",
                    )
                )
            session.commit()

        server.vault.db.run_task(seed_preds)

        body = client.post(f"{API}/reviews", json={"tag": TAG}).json()
        rid = body["id"]
        # The pair is a 'remove' with both taggers confidently negative — the
        # two independent signals agree, so the receipt offers it for bulk.
        assert body["stats"]["auto_resolvable"] == 1

        # Review-scoped bulk-accept applies exactly the review's rows.
        applied = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": TAG, "min_combined": 0.9, "review_id": rid},
        ).json()
        assert applied["count"] == 1
        assert (
            client.get(f"{API}/reviews/{rid}").json()["stats"]["auto_resolvable"] == 0
        )
        assert client.get(f"{API}/reviews/{rid}").json()["progress"] == {
            "done": 1,
            "pending": 0,
            "skipped": 0,
        }

        # Review-scoped bulk-reopen undoes it; a mismatched review_id is a no-op.
        noop = client.post(
            "/tag_suggestions/bulk-reopen",
            json={"ids": applied["accepted_ids"], "review_id": rid + 999},
        ).json()
        assert noop["count"] == 0
        undone = client.post(
            "/tag_suggestions/bulk-reopen",
            json={"ids": applied["accepted_ids"], "review_id": rid},
        ).json()
        assert undone["count"] == 1
        assert client.get(f"{API}/reviews/{rid}").json()["progress"] == {
            "done": 0,
            "pending": 1,
            "skipped": 0,
        }
    finally:
        _teardown(temp_dir, server)


def test_skip_records_no_decision_and_reopens():
    temp_dir, client, server = _setup()
    try:
        a, _b = _make_pair(client, server)
        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        sid = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]["id"]

        resp = client.post(f"/tag_suggestions/{sid}/skip")
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

        # No decision anywhere: labels untouched, no ledger entry written.
        row = _get_suggestion(server, sid)
        assert row.status == "SKIPPED"
        assert row.reviewed_at is not None
        tags = client.get(f"/pictures/{a}/tags").json()["tags"]
        assert any(t["tag"] == TAG for t in tags)  # suspect keeps its tag
        pred = server.vault.db.run_immediate_read_task(
            lambda s: s.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == row.picture_id,
                    TagPrediction.tag == TAG,
                )
            ).first()
        )
        assert pred is None  # skip never writes the human-label ledger

        # Skipped is out of the PENDING queue, reported separately, and never
        # re-inserted by refresh.
        assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []
        detail = client.get(f"{API}/reviews/{rid}").json()
        assert detail["progress"] == {"done": 0, "pending": 0, "skipped": 1}
        assert detail["receipt"] == {"removed": 0, "added": 0, "kept": 0, "skipped": 1}
        assert client.post(f"{API}/reviews/{rid}/refresh").json()["new_count"] == 0
        assert _get_suggestion(server, sid).status == "SKIPPED"

        # Reopen re-pends it (nothing to reverse).
        assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 200
        row = _get_suggestion(server, sid)
        assert row.status == "PENDING"
        assert row.reviewed_at is None

        assert client.post("/tag_suggestions/999999/skip").status_code == 404
    finally:
        _teardown(temp_dir, server)


def test_review_wide_bulk_reopen_undoes_decided_but_not_skipped():
    temp_dir, client, server = _setup()
    try:
        pair1_a, _ = _make_pair(client, server, axis=0)
        pair2_a, _ = _make_pair(client, server, axis=1)
        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
        assert len(rows) == 2
        by_suspect = {r["picture_id"]: r["id"] for r in rows}

        # Decide one (accept removes the suspect's tag), skip the other.
        accepted_sid = by_suspect[pair1_a]
        skipped_sid = by_suspect[pair2_a]
        assert client.post(f"/tag_suggestions/{accepted_sid}/accept").status_code == 200
        assert client.post(f"/tag_suggestions/{skipped_sid}/skip").status_code == 200
        detail = client.get(f"{API}/reviews/{rid}").json()
        assert detail["progress"] == {"done": 1, "pending": 0, "skipped": 1}
        assert detail["receipt"]["removed"] == 1

        # "Undo N changes": empty ids + review_id reopens ALL decided rows,
        # leaving SKIPPED alone (it made no changes to undo).
        undone = client.post(
            "/tag_suggestions/bulk-reopen", json={"ids": [], "review_id": rid}
        ).json()
        assert undone["count"] == 1
        assert _get_suggestion(server, accepted_sid).status == "PENDING"
        assert _get_suggestion(server, skipped_sid).status == "SKIPPED"
        # The accepted removal was reversed: the suspect has its tag back.
        tags = client.get(f"/pictures/{pair1_a}/tags").json()["tags"]
        assert any(t["tag"] == TAG for t in tags)

        # Abort leaves everything as-is (undo-then-abort is the caller's flow).
        assert client.post(f"{API}/reviews/{rid}/abort").status_code == 200
        assert _get_suggestion(server, skipped_sid).status == "SKIPPED"
    finally:
        _teardown(temp_dir, server)


def test_preview_reports_scope_and_prev_reviewed():
    temp_dir, client, server = _setup()
    try:
        in_a, in_b = _make_pair(client, server, axis=0)
        _make_pair(client, server, axis=1)  # out-of-scope pair

        r = client.post(f"{API}/picture_sets", json={"name": "Scope"})
        set_id = r.json()["picture_set"]["id"]

        def add_members(session):
            session.add(PictureSetMember(set_id=set_id, picture_id=in_a))
            session.add(PictureSetMember(set_id=set_id, picture_id=in_b))
            session.commit()

        server.vault.db.run_task(add_members)

        preview = client.get(f"{API}/reviews/preview", params={"tag": TAG}).json()
        assert preview == {"in_scope": 4, "prev_reviewed": 0}
        scoped = client.get(
            f"{API}/reviews/preview", params={"tag": TAG, "set_id": set_id}
        ).json()
        assert scoped == {"in_scope": 2, "prev_reviewed": 0}

        # Decide a suspect in an earlier review; preview now reports it.
        rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
        for row in client.get(f"{API}/reviews/{rid}/suggestions").json():
            assert (
                client.post(f"/tag_suggestions/{row['id']}/dismiss").status_code == 200
            )
        client.post(f"{API}/reviews/{rid}/archive")
        preview = client.get(f"{API}/reviews/preview", params={"tag": TAG}).json()
        assert preview["prev_reviewed"] == 2

        assert (
            client.get(f"{API}/reviews/preview", params={"tag": " "}).status_code == 400
        )
    finally:
        _teardown(temp_dir, server)

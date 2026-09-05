"""Tests for the Tag Suggestions API: list, accept (writeback), dismiss."""

import gc
import json
import os
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

from datetime import datetime

import numpy as np
from sqlalchemy import event as sa_event
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update
from sqlmodel import select

from pixlstash.db_models import Picture, Tag
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
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


def _upload_picture(client):
    img_path = os.path.join(PICTURES_DIR, "Bad1.png")
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(
            client, [("file", ("Bad1.png", f, "image/png"))]
        )
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


def _seed_tag(server, pic_id, tag):
    def insert(session):
        session.add(Tag(picture_id=pic_id, tag=tag))
        session.commit()

    server.vault.db.run_task(insert)


def _seed_suggestion(server, pic_id, tag, direction, score=1.0, source="near_neighbor"):
    def insert(session):
        s = TagSuggestion(
            picture_id=pic_id,
            tag=tag,
            direction=direction,
            source=source,
            score=score,
            reason="near-twin disagrees",
        )
        session.add(s)
        session.commit()
        session.refresh(s)
        return s.id

    return server.vault.db.run_task(insert)


def _has_tag(client, pic_id, tag):
    resp = client.get(f"/pictures/{pic_id}/tags")
    assert resp.status_code == 200
    return any(t["tag"] == tag for t in resp.json().get("tags", []))


def test_list_ranks_pending_by_score():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "malformed hand")
        _seed_suggestion(server, pic_id, "malformed hand", "remove", score=0.4)
        _seed_suggestion(server, pic_id, "bad anatomy", "add", score=0.9)

        resp = client.get("/tag_suggestions")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        # Highest score first.
        assert rows[0]["tag"] == "bad anatomy"
        assert rows[0]["score"] == 0.9
        assert all(r["status"] == "PENDING" for r in rows)

        # Filter by tag and direction.
        resp = client.get(
            "/tag_suggestions", params={"tag": "malformed hand", "direction": "remove"}
        )
        assert resp.status_code == 200
        filtered = resp.json()
        assert [r["tag"] for r in filtered] == ["malformed hand"]
        # The file extension is returned so the client can render full-res images.
        assert filtered[0]["picture_ext"] == "png"
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_accept_remove_deletes_tag():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "malformed hand")
        sid = _seed_suggestion(server, pic_id, "malformed hand", "remove")
        assert _has_tag(client, pic_id, "malformed hand")

        resp = client.post(f"/tag_suggestions/{sid}/accept")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["direction"] == "remove"

        # The wrongly-applied tag is gone, and the suggestion is no longer pending.
        assert not _has_tag(client, pic_id, "malformed hand")
        assert client.get("/tag_suggestions").json() == []
        accepted = client.get("/tag_suggestions", params={"status": "ACCEPTED"}).json()
        assert any(r["id"] == sid for r in accepted)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_accept_add_creates_tag():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        sid = _seed_suggestion(server, pic_id, "bad anatomy", "add")
        assert not _has_tag(client, pic_id, "bad anatomy")

        resp = client.post(f"/tag_suggestions/{sid}/accept")
        assert resp.status_code == 200
        assert resp.json()["direction"] == "add"

        assert _has_tag(client, pic_id, "bad anatomy")
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_reopen_undoes_accepted_remove():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "malformed hand")
        sid = _seed_suggestion(server, pic_id, "malformed hand", "remove")

        # Accept the removal: the tag goes away.
        assert client.post(f"/tag_suggestions/{sid}/accept").status_code == 200
        assert not _has_tag(client, pic_id, "malformed hand")

        # Reopen (undo): the tag is restored and the suggestion is pending again.
        resp = client.post(f"/tag_suggestions/{sid}/reopen")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reopened"
        assert _has_tag(client, pic_id, "malformed hand")
        pending = client.get("/tag_suggestions").json()
        assert any(r["id"] == sid for r in pending)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_fix_twin_tags_the_twin_and_undoes():
    temp_dir, client, server = _setup()
    try:
        suspect_id = _upload_picture(client)
        _seed_tag(server, suspect_id, "malformed hand")
        # A distinct twin picture (different image so it's a separate row).
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            twin_id = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]

        def insert(session):
            session.add(
                TagSuggestion(
                    picture_id=suspect_id,
                    tag="malformed hand",
                    direction="remove",
                    source="near_neighbor",
                    score=1.0,
                    twin_picture_id=twin_id,
                )
            )
            session.commit()

        sid = None

        def fetch_id(session):
            from sqlmodel import select as _select

            return (
                session.exec(
                    _select(TagSuggestion).where(TagSuggestion.picture_id == suspect_id)
                )
                .first()
                .id
            )

        server.vault.db.run_task(insert)
        sid = server.vault.db.run_immediate_read_task(fetch_id)

        # Twin starts untagged; fix-twin adds the tag to it, keeps the suspect's.
        assert not _has_tag(client, twin_id, "malformed hand")
        resp = client.post(f"/tag_suggestions/{sid}/fix-twin")
        assert resp.status_code == 200
        assert resp.json()["status"] == "twin_fixed"
        assert _has_tag(client, twin_id, "malformed hand")
        assert _has_tag(client, suspect_id, "malformed hand")  # suspect untouched

        # Undo removes the tag from the twin and re-opens the suggestion.
        assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 200
        assert not _has_tag(client, twin_id, "malformed hand")
        assert any(r["id"] == sid for r in client.get("/tag_suggestions").json())
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_swap_flips_both_labels_and_undoes():
    temp_dir, client, server = _setup()
    try:
        suspect = _upload_picture(client)  # tagged
        _seed_tag(server, suspect, "malformed hand")
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            twin = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]  # untagged

        def insert(session):
            session.add(
                TagSuggestion(
                    picture_id=suspect,
                    tag="malformed hand",
                    direction="remove",
                    source="near_neighbor",
                    score=1.0,
                    twin_picture_id=twin,
                )
            )
            session.commit()

        server.vault.db.run_task(insert)
        sid = server.vault.db.run_immediate_read_task(
            lambda s: (
                s.exec(select(TagSuggestion).where(TagSuggestion.picture_id == suspect))
                .first()
                .id
            )
        )

        # Swap: the tagged suspect becomes clean, the untagged twin gets the tag.
        resp = client.post(f"/tag_suggestions/{sid}/swap")
        assert resp.status_code == 200
        assert resp.json()["status"] == "swapped"
        assert not _has_tag(client, suspect, "malformed hand")
        assert _has_tag(client, twin, "malformed hand")

        # Undo restores the original labels.
        assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 200
        assert _has_tag(client, suspect, "malformed hand")
        assert not _has_tag(client, twin, "malformed hand")
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_dismiss_leaves_tag_untouched():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "malformed hand")
        sid = _seed_suggestion(server, pic_id, "malformed hand", "remove")

        resp = client.post(f"/tag_suggestions/{sid}/dismiss")
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

        # Tag stays; suggestion is dismissed, not pending.
        assert _has_tag(client, pic_id, "malformed hand")
        assert client.get("/tag_suggestions").json() == []
        dismissed = client.get(
            "/tag_suggestions", params={"status": "DISMISSED"}
        ).json()
        assert any(r["id"] == sid for r in dismissed)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_list_includes_tagger_confidence():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        _seed_tag(server, pic_id, "malformed hand")
        _seed_suggestion(server, pic_id, "malformed hand", "remove")

        def insert_pred(session):
            session.add(
                TagPrediction(
                    picture_id=pic_id,
                    tag="malformed hand",
                    confidence=0.42,
                    model_version="test-v1",
                    status="PENDING",
                    predicted_at=datetime.utcnow(),
                )
            )
            session.commit()

        server.vault.db.run_task(insert_pred)

        rows = client.get("/tag_suggestions").json()
        assert len(rows) == 1
        assert abs(rows[0]["tagger_confidence"] - 0.42) < 1e-6
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _seed_prediction(server, pic_id, tag, confidence):
    def insert(session):
        session.add(
            TagPrediction(
                picture_id=pic_id,
                tag=tag,
                confidence=confidence,
                model_version="test-v1",
                status="PENDING",
                predicted_at=datetime.utcnow(),
            )
        )
        session.commit()

    server.vault.db.run_task(insert)


def _seed_pair(client, server, tag, direction, score=1.0):
    """Create a suspect+twin near_neighbor suggestion, tagged as the scan would find it:
    remove → suspect tagged / twin clean; add → twin tagged / suspect clean.

    Returns ``(suspect_id, twin_id, suggestion_id)``.
    """
    suspect = _upload_picture(client)  # Bad1.png
    img_path = os.path.join(PICTURES_DIR, "Bad2.png")
    with open(img_path, "rb") as f:
        twin = upload_pictures_and_wait(
            client, [("file", ("Bad2.png", f, "image/png"))]
        )["results"][0]["picture_id"]
    _seed_tag(server, suspect if direction == "remove" else twin, tag)

    def insert(session):
        s = TagSuggestion(
            picture_id=suspect,
            tag=tag,
            direction=direction,
            source="near_neighbor",
            score=score,
            twin_picture_id=twin,
        )
        session.add(s)
        session.commit()
        session.refresh(s)
        return s.id

    return suspect, twin, server.vault.db.run_task(insert)


def test_bulk_accept_resolves_when_signals_agree():
    """remove + tagger agrees NEITHER image has it → the suspect's wrong tag is removed."""
    temp_dir, client, server = _setup()
    try:
        suspect, twin, sid = _seed_pair(client, server, "malformed hand", "remove")
        # Tagger corroborates the neighbour proposal: neither image has the tag.
        _seed_prediction(server, suspect, "malformed hand", 0.05)
        _seed_prediction(server, twin, "malformed hand", 0.03)

        # Margin is 0.95 (= min(0.95, 0.97)); 0.99 is too strict → nothing resolves.
        strict = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.99, "dry_run": True},
        ).json()
        assert strict["count"] == 0

        applied = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9},
        ).json()
        assert applied["count"] == 1
        assert applied["accepted_ids"] == [sid]
        assert not _has_tag(client, suspect, "malformed hand")  # wrong tag removed
        assert not _has_tag(client, twin, "malformed hand")  # twin untouched

        # Batch-undo restores the suspect's tag.
        reopened = client.post(
            "/tag_suggestions/bulk-reopen", json={"ids": applied["accepted_ids"]}
        ).json()
        assert reopened["count"] == 1
        assert _has_tag(client, suspect, "malformed hand")
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_accept_dry_run_counts_without_writing():
    """dry_run returns the would-resolve count but mutates nothing.

    Regression guard for the read-path dispatch: because the dry_run branch
    performs no writes it is dispatched via run_immediate_read_task, so it must
    return the correct count AND leave every suggestion PENDING and every tag in
    place. A count that changed, or any write leaking through, would break both
    the counting contract and the read-path assumption.
    """
    temp_dir, client, server = _setup()
    try:
        suspect, twin, sid = _seed_pair(client, server, "malformed hand", "remove")
        _seed_prediction(server, suspect, "malformed hand", 0.05)
        _seed_prediction(server, twin, "malformed hand", 0.03)

        dry = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9, "dry_run": True},
        ).json()
        assert dry["count"] == 1
        assert dry["accepted_ids"] == []  # dry_run resolves nothing

        # Nothing was written: the suggestion is still PENDING and the tag remains.
        def _status(session):
            return session.get(TagSuggestion, sid).status

        assert server.vault.db.run_immediate_read_task(_status) == "PENDING"
        assert _has_tag(client, suspect, "malformed hand")

        # The real apply still resolves the same single pair.
        applied = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9},
        ).json()
        assert applied["count"] == 1
        assert applied["accepted_ids"] == [sid]
        assert not _has_tag(client, suspect, "malformed hand")
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_accept_skips_when_tagger_contradicts_neighbour():
    """remove + tagger says BOTH have it → the two signals disagree, so bulk leaves it.

    This is the case the old confidence-only path got wrong: it placed the pair in the
    "both" corner and *added* the tag to the twin - for a suggestion that asked to
    *remove* it. The blend now requires the tagger to land in the neighbour's corner.
    """
    temp_dir, client, server = _setup()
    try:
        suspect, twin, _sid = _seed_pair(client, server, "malformed hand", "remove")
        # Tagger is loud the other way: both have it (corner "both" ≠ neighbour "neither").
        _seed_prediction(server, suspect, "malformed hand", 0.97)
        _seed_prediction(server, twin, "malformed hand", 0.96)

        # Even at the loosest threshold the corner mismatch keeps it out of bulk.
        res = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.5},
        ).json()
        assert res["count"] == 0
        # Labels untouched; the pair is left for human review.
        assert _has_tag(client, suspect, "malformed hand")
        assert not _has_tag(client, twin, "malformed hand")
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_accept_skips_when_neighbour_vote_is_weak():
    """Tagger agrees, but a weak neighbour vote (low score) fails the blend floor."""
    temp_dir, client, server = _setup()
    try:
        suspect, _twin, _sid = _seed_pair(
            client, server, "malformed hand", "remove", score=0.6
        )
        _seed_prediction(server, suspect, "malformed hand", 0.02)
        _seed_prediction(server, _twin, "malformed hand", 0.01)

        # Tagger margin clears 0.9 but the neighbour score (0.6) does not → skipped.
        high = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9},
        ).json()
        assert high["count"] == 0
        assert _has_tag(client, suspect, "malformed hand")  # untouched

        # Drop the bar below the neighbour score and both signals now clear it.
        low = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.5},
        ).json()
        assert low["count"] == 1
        assert not _has_tag(client, suspect, "malformed hand")
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_accept_adds_tag_when_signals_agree():
    """add + tagger agrees BOTH have it → the missing tag is added to the suspect."""
    temp_dir, client, server = _setup()
    try:
        suspect, twin, _sid = _seed_pair(client, server, "malformed hand", "add")
        # Suspect starts clean, twin tagged; tagger says both have it (corner "both").
        _seed_prediction(server, suspect, "malformed hand", 0.95)
        _seed_prediction(server, twin, "malformed hand", 0.97)

        applied = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9},
        ).json()
        assert applied["count"] == 1
        assert _has_tag(client, suspect, "malformed hand")  # missing tag added
        assert _has_tag(client, twin, "malformed hand")  # twin keeps it
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _set_embedding(server, pic_id, vec):
    blob = np.asarray(vec, dtype=np.float32).tobytes()

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.image_embedding = blob
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def _set_phash(server, pic_id, phash_int):
    """Store a 64-bit dhash as the 16-char lowercase hex string the worker writes."""
    hex_str = f"{phash_int:016x}"

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.perceptual_hash = hex_str
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def test_scan_tag_builds_and_rebuilds_queue():
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        a = _upload_picture(client)  # Bad1.png
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            b = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]

        # Identical unit embeddings → each other's nearest twin; only A is tagged.
        vec = [1.0] + [0.0] * 511
        _set_embedding(server, a, vec)
        _set_embedding(server, b, vec)
        _seed_tag(server, a, "malformed hand")

        res = tag_scan_service.scan_tag(server.vault, "malformed hand", project=None)
        assert res["scanned"] == 2
        assert res["count"] >= 1

        rows = client.get("/tag_suggestions").json()
        # The A/B disagreement is captured exactly once (reciprocal pair deduped),
        # in whichever direction scored higher.
        assert len(rows) == 1
        assert {rows[0]["picture_id"], rows[0]["twin_picture_id"]} == {a, b}

        # Re-scanning rebuilds the same pending set (idempotent), not duplicates it.
        res2 = tag_scan_service.scan_tag(server.vault, "malformed hand", project=None)
        assert res2["count"] == res["count"]
        rows2 = client.get("/tag_suggestions").json()
        assert len(rows2) == len(rows)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scan_tag_prefers_perceptual_near_duplicate_twin():
    """The displayed twin switches to the opposite-labelled perceptual near-duplicate even
    when a different picture is the CLIP-nearest opposite, and eligibility is unchanged."""
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        # Three pictures. A is the tagged suspect. C is A's CLIP-nearest opposite (highest
        # cosine). B is the perceptual near-duplicate of A (tiny dhash hamming) but a
        # LOWER cosine than C - without the override A's twin would be C, with it B wins.
        a = _upload_picture(client)  # Bad1.png
        b = _upload_named(client)  # distinct in-memory PNG
        c = _upload_named(client)  # distinct in-memory PNG

        # A points along axis 0. C is nearly parallel (cosine ~0.9999). B is further
        # off at cosine ~0.85 - deliberately BETWEEN the current 0.8 display floor
        # and the old, too-strict 0.9 one, pinning the eased threshold: a heavily
        # edited copy in this band must still be shown as the perceptual twin.
        _set_embedding(server, a, [1.0] + [0.0] * 511)
        _set_embedding(server, c, [0.9999, 0.0141] + [0.0] * 510)  # closest to A
        _set_embedding(server, b, [0.85, 0.526783] + [0.0] * 510)  # opposite, farther

        # A and B are perceptual near-duplicates (2-bit dhash hamming); C is far away.
        _set_phash(server, a, 0xFFFF_FFFF_FFFF_FFFF)
        _set_phash(server, b, 0xFFFF_FFFF_FFFF_FFFC)  # 2 bits from A
        _set_phash(server, c, 0x0000_0000_0000_0000)  # 64 bits from A

        _seed_tag(server, a, "malformed hand")  # only A is tagged

        res = tag_scan_service.scan_tag(server.vault, "malformed hand", project=None)
        assert res["scanned"] == 3

        rows = client.get("/tag_suggestions").json()
        # Find the suggestion whose pair involves the tagged suspect A.
        pair_rows = [r for r in rows if a in {r["picture_id"], r["twin_picture_id"]}]
        assert pair_rows, "expected a suggestion involving the tagged picture A"
        row = pair_rows[0]
        # The displayed twin is the perceptual near-dup B, not the CLIP-nearest C.
        assert {row["picture_id"], row["twin_picture_id"]} == {a, b}
        assert c not in {row["picture_id"], row["twin_picture_id"]}
        assert "dhash hamming" in row["reason"]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scan_tag_rejects_low_similarity_perceptual_override():
    """A dhash-near "perceptual duplicate" whose actual CLIP similarity is low is a likely
    hash collision, not a real "same shot" pair - the override must not fire, and the
    CLIP-nearest twin (with its higher, corroborated similarity) stays displayed."""
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        # Three pictures. A is the tagged suspect. C is A's CLIP-nearest opposite (cosine
        # 0.95, comfortably above both min_twin_sim and min_display_twin_sim). B has a
        # tiny dhash hamming distance to A (would trigger the perceptual-twin override)
        # but only a 0.55 cosine similarity to A - too low to trust as "same shot", so the
        # override must be rejected and C must stay the displayed twin.
        a = _upload_picture(client)  # Bad1.png
        b = _upload_named(client)  # distinct in-memory PNG
        c = _upload_named(client)  # distinct in-memory PNG

        _set_embedding(server, a, [1.0] + [0.0] * 511)
        _set_embedding(server, c, [0.95, 0.312249] + [0.0] * 510)  # cosine ~0.95
        _set_embedding(server, b, [0.55, 0.835165] + [0.0] * 510)  # cosine ~0.55

        # A and B are perceptual near-duplicates (2-bit dhash hamming); C is far away.
        _set_phash(server, a, 0xFFFF_FFFF_FFFF_FFFF)
        _set_phash(server, b, 0xFFFF_FFFF_FFFF_FFFC)  # 2 bits from A
        _set_phash(server, c, 0x0000_0000_0000_0000)  # 64 bits from A

        _seed_tag(server, a, "malformed hand")  # only A is tagged

        res = tag_scan_service.scan_tag(server.vault, "malformed hand", project=None)
        assert res["scanned"] == 3

        rows = client.get("/tag_suggestions").json()
        pair_rows = [r for r in rows if a in {r["picture_id"], r["twin_picture_id"]}]
        assert pair_rows, "expected a suggestion involving the tagged picture A"
        row = pair_rows[0]
        # The displayed twin stays the CLIP-nearest C - the low-similarity perceptual
        # "near-duplicate" B is rejected despite its tiny dhash hamming distance.
        assert {row["picture_id"], row["twin_picture_id"]} == {a, c}
        assert b not in {row["picture_id"], row["twin_picture_id"]}
        assert row["twin_sim"] >= tag_scan_service.MIN_DISPLAY_TWIN_SIM
        assert "dhash hamming" not in row["reason"]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# Fix 1 (confidence fallback) / Fix 2 (base-rate-relative thresholds)
# regression coverage - see docs/reviews and tag_scan_service.py's module
# constants for the bug reports and empirical tuning these guard against.
# ---------------------------------------------------------------------------


def test_scan_tag_confidence_fallback_for_new_tag():
    """Fix 1 regression: the exact reported bug. A brand-new tag with zero
    ``Tag`` rows produced ZERO suspects (not just few) because the kNN vote's
    ``has_concept`` mask is all-False, making pos_frac identically 0.0 for
    every picture vault-wide - add_threshold could never be met no matter how
    confident the model is. Below MIN_GROUND_TRUTH_FOR_VOTE ground-truth
    positives, scan_tag must fall back to TagPrediction confidence directly.
    """
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        a = _upload_picture(client)  # Bad1.png
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            b = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]
        # Embeddings are only needed to clear scan_tag's len(ids) < 2 guard -
        # the fallback path doesn't vote on them.
        _set_embedding(server, a, [1.0] + [0.0] * 511)
        _set_embedding(server, b, [0.0, 1.0] + [0.0] * 510)

        # Zero Tag rows for this tag anywhere in the vault, but the model is
        # confident on picture A.
        _seed_prediction(server, a, "compression artifacts", 0.93)

        res = tag_scan_service.scan_tag(
            server.vault, "compression artifacts", project=None
        )
        assert res["added"] == 1
        assert res["removed"] == 0
        assert res["count"] == 1

        rows = client.get("/tag_suggestions").json()
        assert len(rows) == 1
        assert rows[0]["picture_id"] == a
        assert rows[0]["direction"] == "add"
        assert rows[0]["score"] == 0.93
        assert "no confirmed examples yet" in rows[0]["reason"]
        assert "93%" in rows[0]["reason"]
        assert rows[0]["twin_picture_id"] is None
        assert rows[0]["twin_sim"] is None

        # B has no prediction at all, so it must not be surfaced.
        assert b not in {r["picture_id"] for r in rows}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scan_tag_confidence_fallback_ignores_stale_model_version():
    """The fallback pins to the current (most recent non-"manual") model
    version, exactly like tag_health_service's est_missing - a confident
    prediction from a superseded model version must not produce a suspect."""
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        a = _upload_picture(client)
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            b = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]
        _set_embedding(server, a, [1.0] + [0.0] * 511)
        _set_embedding(server, b, [0.0, 1.0] + [0.0] * 510)

        def insert_stale(session):
            session.add(
                TagPrediction(
                    picture_id=a,
                    tag="compression artifacts",
                    confidence=0.99,
                    model_version="old-v0",
                    status="PENDING",
                    predicted_at=datetime(2020, 1, 1),
                )
            )
            session.commit()

        server.vault.db.run_task(insert_stale)
        # A newer prediction (any tag) establishes "test-v1" as current.
        _seed_prediction(server, b, "some other tag", 0.5)

        res = tag_scan_service.scan_tag(
            server.vault, "compression artifacts", project=None
        )
        assert res["added"] == 0
        assert res["count"] == 0
        assert client.get("/tag_suggestions").json() == []
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scan_tag_confidence_fallback_excludes_human_rejected():
    """The cold-start fallback must not re-propose a tag a human already
    REJECTED via the tag-prediction reject endpoint. That reject writes a ledger
    NEG (``label_state="NEG"``) but keeps the tagger's high confidence and adds no
    ``Tag`` row and no ``TagSuggestion`` row - so ``scan_tag._write``'s
    suggestion-level dedup can't suppress it. Only the ``label_state == "UNKNOWN"``
    filter (mirroring est_missing) does: pre-fix, the rejected picture was
    re-surfaced as a PENDING "add" suspect; it must now be excluded, while an
    un-reviewed confident picture is still proposed.
    """
    from pixlstash.services import tag_scan_service
    from pixlstash.utils.service.label_ledger import NEG, record_human_label

    temp_dir, client, server = _setup()
    try:
        a = _upload_picture(client)  # un-reviewed confident → proposed
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            b = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]  # human-REJECTED confident → excluded
        _set_embedding(server, a, [1.0] + [0.0] * 511)
        _set_embedding(server, b, [0.0, 1.0] + [0.0] * 510)

        # Both get an equally confident current-version prediction; zero Tag rows
        # for the tag anywhere, so the near-zero-ground-truth fallback fires.
        _seed_prediction(server, a, "compression artifacts", 0.93)
        _seed_prediction(server, b, "compression artifacts", 0.93)

        # Human rejects the tag on B via the ledger (keeps conf 0.93, no Tag row,
        # no TagSuggestion) - the exact shape the suggestion-dedup can't catch.
        def reject_b(session):
            record_human_label(session, b, "compression artifacts", NEG)
            session.commit()

        server.vault.db.run_task(reject_b)

        res = tag_scan_service.scan_tag(
            server.vault, "compression artifacts", project=None
        )
        # Pre-fix this was 2 (both A and B proposed) - B is the re-proposal bug.
        assert res["added"] == 1
        assert res["count"] == 1

        rows = client.get("/tag_suggestions").json()
        picture_ids = {r["picture_id"] for r in rows}
        assert a in picture_ids  # un-reviewed confident candidate still proposed
        assert b not in picture_ids  # human-rejected candidate excluded
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scan_tag_base_rate_default_thresholds_vs_explicit_override():
    """Fix 2 regression: a minority-base-rate tag (p=0.25, well above the
    Fix-1 floor of 5 ground-truth positives) at whose true prevalence the old
    fixed 0.55/0.45 pair structurally favours 'remove' over 'add' (a
    threshold centered on 50% is systematically wrong for a 25% base rate).

    Twenty pictures, hand-constructed embeddings with exactly known pairwise
    cosine similarities (verified against the real kernel before being
    transcribed here - see the PR notes): one probe picture (``u``) whose
    kNN-vote positive fraction is 0.4722 - below the OLD fixed add_threshold
    (0.55) so the old code could never flag it, but above the NEW base-rate
    default (p + 0.15 = 0.40) so it should. A second picture (``t1``) is
    tagged with pos_frac 0.259 - above the NEW default remove_threshold
    (p - 0.15 = 0.10, so NOT flagged) but below the OLD fixed 0.45 (so an
    explicit legacy-threshold caller WOULD flag it) - exercising acceptance
    criterion (c): explicit overrides bypass the base-rate computation.
    """
    import io

    from PIL import Image

    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        names = (
            ["u"]
            + [f"t{i}" for i in range(1, 6)]
            + [f"d{i}" for i in range(1, 6)]
            + [f"f{i}" for i in range(1, 10)]
        )
        assert len(names) == 20
        files = []
        for n_idx, name in enumerate(names):
            img = Image.new("RGB", (16, 16), color=(n_idx * 11 % 256, 40, 80))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            files.append(("file", (f"{name}.png", buf.getvalue(), "image/png")))
        result = upload_pictures_and_wait(client, files)
        assert result["status"] == "completed"
        ids = {n: r["picture_id"] for n, r in zip(names, result["results"])}

        tag = "compression artifacts"
        for i in range(1, 6):
            _seed_tag(server, ids[f"t{i}"], tag)

        # 512-dim orthogonal-axis construction: every picture's vector uses a
        # small set of dedicated axes so each pairwise cosine similarity is
        # exactly controlled, and anything not explicitly connected is exactly
        # orthogonal (similarity 0 -> zero weight in the kNN vote). Verified
        # against pixlstash.utils.near_neighbor.knn_disagreement_with_neighbors
        # directly before being transcribed here.
        AXIS_TWIN = 0  # shared between u and t1
        AXIS_T1T2 = 1  # shared between t1 and t2
        AXIS_D = {i: 1 + i for i in range(1, 6)}  # u <-> d{i}, 2..6
        T1_PRIV = 50
        D_PRIV_BASE = 60  # t3,t4,t5 -> 60,61,62 (isolated)
        F_PRIV_BASE = 90  # f1..f9 -> 90..98 (isolated)

        def vec(components):
            v = [0.0] * 512
            for axis, val in components.items():
                v[axis] = val
            return v

        u1, b_dilution = 0.8947, 0.19
        _set_embedding(
            server,
            ids["u"],
            vec({AXIS_TWIN: u1, **{AXIS_D[i]: b_dilution for i in range(1, 6)}}),
        )

        t1_comp, t1t2_comp = 0.95, 0.3
        leftover = (1 - t1_comp**2 - t1t2_comp**2) ** 0.5
        _set_embedding(
            server,
            ids["t1"],
            vec({AXIS_TWIN: t1_comp, AXIS_T1T2: t1t2_comp, T1_PRIV: leftover}),
        )
        _set_embedding(server, ids["t2"], vec({AXIS_T1T2: 1.0}))
        for k, i in enumerate([3, 4, 5]):
            _set_embedding(server, ids[f"t{i}"], vec({D_PRIV_BASE + k: 1.0}))
        for i in range(1, 6):
            _set_embedding(server, ids[f"d{i}"], vec({AXIS_D[i]: 1.0}))
        for k, i in enumerate(range(1, 10)):
            _set_embedding(server, ids[f"f{i}"], vec({F_PRIV_BASE + k: 1.0}))

        # --- Run 1: default (base-rate-relative) thresholds ---
        res_default = tag_scan_service.scan_tag(server.vault, tag, project=None)
        assert res_default["scanned"] == 20
        assert res_default["added"] == 1
        assert res_default["removed"] == 0

        rows = client.get("/tag_suggestions").json()
        assert len(rows) == 1
        assert rows[0]["picture_id"] == ids["u"]
        assert rows[0]["direction"] == "add"

        # --- Run 2: explicit legacy fixed thresholds (override, same tag) ---
        res_old = tag_scan_service.scan_tag(
            server.vault,
            tag,
            project=None,
            add_threshold=0.55,
            remove_threshold=0.45,
        )
        # u no longer qualifies (pos_frac 0.4722 < 0.55); t1 newly does
        # (pos_frac 0.259 <= 0.45), demonstrating the override bypasses the
        # base-rate computation and reproduces the old skew.
        assert res_old["added"] == 0
        assert res_old["removed"] == 1

        rows_after = {r["picture_id"]: r for r in client.get("/tag_suggestions").json()}
        assert set(rows_after) == {ids["u"], ids["t1"]}
        assert rows_after[ids["u"]]["direction"] == "add"
        assert rows_after[ids["t1"]]["direction"] == "remove"
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scan_tag_base_rate_default_thresholds_majority_tag_regression():
    """Fix 2 majority-tag regression: the naive symmetric ``p ± margin`` formula
    shifts *every* tag uniformly by its own base rate, which helps minority
    tags (the population Fix 2 targeted) but actively hurts a majority tag -
    it raises add_threshold *above* the legacy fixed 0.55, making add
    *stricter* than before for no reason (majority tags were never the
    population this fix was meant to help).

    Twenty pictures, base rate p=12/20=0.6 (mirrors the real-vault
    reproduction: tag "man" at p=68/111=0.6126). One probe picture (``u``)
    has kNN-vote positive fraction 0.6498 and a twin (``p1``) at cosine
    similarity 0.9535 - clearing both the twin-similarity gate (>=0.85) and
    the legacy fixed add_threshold (0.55, so the legacy code catches it) -
    but BELOW the *uncapped* base-rate default (p + 0.15 = 0.75), so the
    regressed formula misses it entirely (verified against the real kernel
    directly before being transcribed here - see the PR notes). The
    corrected default caps add_threshold at the legacy 0.55 ceiling (never
    stricter than the legacy default), so it catches ``u`` again, matching
    the legacy behaviour.
    """
    import io

    from PIL import Image

    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        names = (
            ["u"]
            + [f"p{i}" for i in range(1, 9)]
            + [f"q{i}" for i in range(1, 5)]
            + [f"d{i}" for i in range(1, 5)]
            + [f"x{i}" for i in range(1, 4)]
        )
        assert len(names) == 20
        files = []
        for n_idx, name in enumerate(names):
            img = Image.new("RGB", (16, 16), color=(n_idx * 11 % 256, 40, 80))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            files.append(("file", (f"{name}.png", buf.getvalue(), "image/png")))
        result = upload_pictures_and_wait(client, files)
        assert result["status"] == "completed"
        ids = {n: r["picture_id"] for n, r in zip(names, result["results"])}

        tag = "man"
        # p1..p8 (near-u clique) + d1..d4 (isolated clique) = 12 tagged of 20 -> p=0.6
        for i in range(1, 9):
            _seed_tag(server, ids[f"p{i}"], tag)
        for i in range(1, 5):
            _seed_tag(server, ids[f"d{i}"], tag)

        # 512-dim orthogonal-axis construction, same technique as the minority-tag
        # test above. Verified against pixlstash.utils.near_neighbor.
        # knn_disagreement_with_neighbors directly before being transcribed here.
        AXIS_HUB = 0  # u <-> q1..q4 (weak - dilutes u's vote toward "no tag")
        AXIS_PGROUP = 1  # shared among p1..p8 - dominates their own vote (all tagged)
        AXIS_QGROUP = 2  # shared among q1..q4 - dominates their own vote (all untagged)
        AXIS_DGROUP = 3  # shared among d1..d4 - isolated from u, all tagged
        AXIS_TWIN = 4  # u <-> p1 only - strong, clears the min_twin_sim gate

        def vec(components):
            v = [0.0] * 512
            for axis, val in components.items():
                v[axis] = val
            return v

        HUB_U, TWIN_U = 1.0, 5.0
        HUB_Q, Q_GROUP = 1.04, 1.2
        P_GROUP, TWIN_P1 = 1.2, 5.0
        D_GROUP = 1.0

        _set_embedding(server, ids["u"], vec({AXIS_HUB: HUB_U, AXIS_TWIN: TWIN_U}))
        _set_embedding(
            server,
            ids["p1"],
            vec({AXIS_PGROUP: P_GROUP, AXIS_TWIN: TWIN_P1}),
        )
        for i in range(2, 9):
            _set_embedding(server, ids[f"p{i}"], vec({AXIS_PGROUP: P_GROUP}))
        for i in range(1, 5):
            _set_embedding(
                server,
                ids[f"q{i}"],
                vec({AXIS_HUB: HUB_Q, AXIS_QGROUP: Q_GROUP}),
            )
        for i in range(1, 5):
            _set_embedding(server, ids[f"d{i}"], vec({AXIS_DGROUP: D_GROUP}))
        for i in range(1, 4):
            _set_embedding(server, ids[f"x{i}"], vec({10 + i: 1.0}))

        # --- Run 1: default (base-rate-relative, now capped) thresholds ---
        res_default = tag_scan_service.scan_tag(server.vault, tag, project=None)
        assert res_default["scanned"] == 20
        assert res_default["added"] == 1
        assert res_default["removed"] == 0

        rows = client.get("/tag_suggestions").json()
        assert len(rows) == 1
        assert rows[0]["picture_id"] == ids["u"]
        assert rows[0]["direction"] == "add"

        # --- Run 2: the regressed, uncapped symmetric formula's thresholds ---
        # (p=0.6 -> add_threshold=p+0.15=0.75, remove_threshold=p-0.15=0.45).
        # u's pos_frac (0.6498) clears the legacy/corrected 0.55 but not this
        # uncapped 0.75 - reproducing the confirmed "man" regression directly.
        res_regressed = tag_scan_service.scan_tag(
            server.vault,
            tag,
            project=None,
            add_threshold=0.75,
            remove_threshold=0.45,
        )
        assert res_regressed["added"] == 0
        assert res_regressed["removed"] == 0
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_accept_missing_suggestion_returns_404():
    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        resp = client.post("/tag_suggestions/999999/accept")
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# Review-scope filters: project_id / set_id / character_id (AND together).
# These tests use the cookie-session client + unversioned paths (owner - no
# token scope), so they exercise the user-supplied filter narrowing only.
# ---------------------------------------------------------------------------

API = "/api/v1"

_distinct_counter = [0]


def _upload_named(client, name=None):
    """Upload a fresh, content-distinct in-memory PNG and return its id.

    A monotonically-sized solid PNG guarantees a unique content hash so the
    importer never dedupes two of these against each other.
    """
    import io

    from PIL import Image

    _distinct_counter[0] += 1
    n = _distinct_counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    fname = name or f"distinct{n}.png"
    return upload_pictures_and_wait(
        client, [("file", (fname, buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _add_to_project(server, pic_id, project_id):
    from pixlstash.db_models import PictureProjectMember

    def ins(session):
        session.add(PictureProjectMember(picture_id=pic_id, project_id=project_id))
        session.commit()

    server.vault.db.run_task(ins)


def _add_to_set(server, pic_id, set_id):
    from pixlstash.db_models import PictureSetMember

    def ins(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=pic_id))
        session.commit()

    server.vault.db.run_task(ins)


def _add_face(server, pic_id, character_id, face_index=0):
    from pixlstash.db_models import Face

    def ins(session):
        # The real upload pipeline may finish face detection before this
        # fixture runs. Reuse its deterministic first face when present instead
        # of racing it for the (picture, frame, face) unique key.
        face = session.exec(
            select(Face).where(
                Face.picture_id == pic_id,
                Face.frame_index == 0,
                Face.face_index == face_index,
            )
        ).first()
        if face is None:
            face = Face(
                picture_id=pic_id,
                frame_index=0,
                face_index=face_index,
            )
            session.add(face)
        face.character_id = character_id
        session.commit()

    server.vault.db.run_task(ins)


def _wait_for_task_runner_idle(server, timeout_s=60):
    """Wait until import-triggered CPU/GPU work is quiescent across two polls."""
    runner = server.vault._task_runner
    deadline = time.time() + timeout_s
    stable_polls = 0
    while time.time() < deadline:
        with runner._active_task_lock:
            active = bool(runner._active_tasks)
        idle = not active and runner._queue.empty() and runner._gpu_queue.empty()
        stable_polls = stable_polls + 1 if idle else 0
        if stable_polls >= 2:
            return
        time.sleep(0.1)
    raise AssertionError("TaskRunner did not become idle before face fixture setup")


def _clear_faces(server, picture_ids):
    from sqlmodel import delete

    from pixlstash.db_models import Face

    def clear(session):
        session.exec(delete(Face).where(Face.picture_id.in_(picture_ids)))
        session.commit()

    server.vault.db.run_task(clear)


def test_filter_by_project_returns_only_in_project_suspects():
    temp_dir, client, server = _setup()
    try:
        in_pic = _upload_picture(client)  # Bad1.png
        out_pic = _upload_named(client)
        _seed_suggestion(server, in_pic, "malformed hand", "remove")
        _seed_suggestion(server, out_pic, "malformed hand", "remove")
        # Create a project and add only in_pic to it.
        r = client.post(f"{API}/projects", json={"name": "Proj"})
        assert r.status_code in (200, 201), r.text
        project_id = r.json()["id"]
        _add_to_project(server, in_pic, project_id)

        rows = client.get("/tag_suggestions", params={"project_id": project_id}).json()
        assert {r["picture_id"] for r in rows} == {in_pic}
        # No filter still returns both (over-filtering would be a regression).
        assert {r["picture_id"] for r in client.get("/tag_suggestions").json()} == {
            in_pic,
            out_pic,
        }
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_filter_by_set_returns_only_in_set_suspects():
    temp_dir, client, server = _setup()
    try:
        in_pic = _upload_picture(client)
        out_pic = _upload_named(client)
        _seed_suggestion(server, in_pic, "malformed hand", "remove")
        _seed_suggestion(server, out_pic, "malformed hand", "remove")
        r = client.post(f"{API}/picture_sets", json={"name": "Set"})
        assert r.status_code in (200, 201), r.text
        set_id = r.json()["picture_set"]["id"]
        _add_to_set(server, in_pic, set_id)

        rows = client.get("/tag_suggestions", params={"set_id": set_id}).json()
        assert {r["picture_id"] for r in rows} == {in_pic}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_filter_by_character_numeric_and_unassigned():
    temp_dir, client, server = _setup()
    try:
        char_pic = _upload_picture(client)  # has a face with character 7
        unassigned_pic = _upload_named(client)  # face, no character
        other_pic = _upload_named(client)  # no face at all
        # Imports require the planner-backed face worker. Once all imports have
        # been accepted, stop new finder work and drain tasks before replacing
        # auto-detected faces with the exact filter fixture.
        server.vault._work_planner.stop()
        _wait_for_task_runner_idle(server)
        _clear_faces(server, [char_pic, unassigned_pic, other_pic])
        for p in (char_pic, unassigned_pic, other_pic):
            _seed_suggestion(server, p, "malformed hand", "remove")
        # Create a character row so character_id=<id> resolves.
        r = client.post(f"{API}/characters", json={"name": "Hero"})
        assert r.status_code in (200, 201), r.text
        char_id = r.json()["character"]["id"]
        _add_face(server, char_pic, char_id)
        _add_face(server, unassigned_pic, None)

        # Numeric character → only the picture with that character's face.
        rows = client.get(
            "/tag_suggestions", params={"character_id": str(char_id)}
        ).json()
        assert {r["picture_id"] for r in rows} == {char_pic}

        # UNASSIGNED → only the picture with an unassigned face and no assigned face.
        rows = client.get(
            "/tag_suggestions", params={"character_id": "UNASSIGNED"}
        ).json()
        assert {r["picture_id"] for r in rows} == {unassigned_pic}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_filters_and_together_intersection():
    temp_dir, client, server = _setup()
    try:
        both = _upload_picture(client)  # in project AND set
        proj_only = _upload_named(client)
        set_only = _upload_named(client)
        for p in (both, proj_only, set_only):
            _seed_suggestion(server, p, "malformed hand", "remove")

        r = client.post(f"{API}/projects", json={"name": "P"})
        project_id = r.json()["id"]
        r = client.post(f"{API}/picture_sets", json={"name": "S"})
        set_id = r.json()["picture_set"]["id"]
        _add_to_project(server, both, project_id)
        _add_to_project(server, proj_only, project_id)
        _add_to_set(server, both, set_id)
        _add_to_set(server, set_only, set_id)

        rows = client.get(
            "/tag_suggestions",
            params={"project_id": project_id, "set_id": set_id},
        ).json()
        # Only the picture in BOTH dimensions survives the intersection.
        assert {r["picture_id"] for r in rows} == {both}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_empty_scope_yields_no_rows_not_error():
    temp_dir, client, server = _setup()
    try:
        pic = _upload_picture(client)
        _seed_suggestion(server, pic, "malformed hand", "remove")
        r = client.post(f"{API}/picture_sets", json={"name": "Empty"})
        empty_set_id = r.json()["picture_set"]["id"]  # no members

        resp = client.get("/tag_suggestions", params={"set_id": empty_set_id})
        assert resp.status_code == 200
        assert resp.json() == []
        # An unknown id is likewise empty, not an error.
        assert client.get("/tag_suggestions", params={"set_id": 999999}).json() == []
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_accept_respects_filter_dry_run_and_apply():
    temp_dir, client, server = _setup()
    try:
        # Two confident remove pairs for the same tag, using content-distinct
        # pictures (so the importer doesn't dedupe the second pair onto the first).
        def _distinct_remove_pair():
            suspect = _upload_named(client)
            twin = _upload_named(client)
            _seed_tag(server, suspect, "malformed hand")  # remove → suspect tagged

            def ins(session):
                s = TagSuggestion(
                    picture_id=suspect,
                    tag="malformed hand",
                    direction="remove",
                    source="near_neighbor",
                    score=1.0,
                    twin_picture_id=twin,
                )
                session.add(s)
                session.commit()

            server.vault.db.run_task(ins)
            return suspect, twin

        in_suspect, in_twin = _distinct_remove_pair()
        out_suspect, out_twin = _distinct_remove_pair()
        for s, t in ((in_suspect, in_twin), (out_suspect, out_twin)):
            _seed_prediction(server, s, "malformed hand", 0.02)
            _seed_prediction(server, t, "malformed hand", 0.01)

        r = client.post(f"{API}/picture_sets", json={"name": "Set"})
        set_id = r.json()["picture_set"]["id"]
        _add_to_set(server, in_suspect, set_id)  # only the in-scope suspect

        # Unfiltered dry-run counts both confident pairs.
        unfiltered = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9, "dry_run": True},
        ).json()
        assert unfiltered["count"] == 2

        # Filtered dry-run counts only the in-scope suspect.
        filtered = client.post(
            "/tag_suggestions/bulk-accept",
            json={
                "tag": "malformed hand",
                "min_combined": 0.9,
                "dry_run": True,
                "set_id": set_id,
            },
        ).json()
        assert filtered["count"] == 1

        # Apply with the filter: only the in-scope suspect's tag is removed.
        applied = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9, "set_id": set_id},
        ).json()
        assert applied["count"] == 1
        assert not _has_tag(client, in_suspect, "malformed hand")
        assert _has_tag(
            client, out_suspect, "malformed hand"
        )  # out of scope, untouched
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# Security: a resource-scoped READ token must only see its own pictures' queue,
# and the user-supplied filter must never widen that scope. These use the
# versioned /api/v1 paths + a Bearer token so the auth middleware sets
# request.state.token_scope.
# ---------------------------------------------------------------------------


def _setup_scoped_token_env():
    """Two picture-sets, one suggestion each; a READ token scoped to Set A only.

    Returns ``(temp_dir, owner_client, server, set_a, set_b, pic_a, pic_b, token_a)``.
    The owner_client carries the cookie session; ``token_a`` is a Bearer value.
    """
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    cfg = os.path.join(temp_dir.name, "server-config.json")
    with open(cfg, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(cfg)
    client = TestClient(server.api)
    # Versioned login so the auth middleware establishes the owner session.
    assert (
        client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        ).status_code
        == 200
    )

    pic_a = _upload_picture(client)  # Bad1.png
    pic_b = _upload_named(client)
    _seed_suggestion(server, pic_a, "malformed hand", "remove")
    _seed_suggestion(server, pic_b, "bad anatomy", "add")

    r = client.post(f"{API}/picture_sets", json={"name": "Set A"})
    set_a = r.json()["picture_set"]["id"]
    r = client.post(f"{API}/picture_sets", json={"name": "Set B"})
    set_b = r.json()["picture_set"]["id"]
    _add_to_set(server, pic_a, set_a)
    _add_to_set(server, pic_b, set_b)

    r = client.post(
        f"{API}/users/me/token",
        json={
            "description": "set A read",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_a,
        },
    )
    assert r.status_code == 200, r.text
    token_a = r.json()["token"]
    return temp_dir, client, server, set_a, set_b, pic_a, pic_b, token_a


def test_scoped_token_list_only_sees_its_own_suspects():
    temp_dir, _client, server, set_a, set_b, pic_a, pic_b, token_a = (
        _setup_scoped_token_env()
    )
    try:
        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token_a}"}
        # No filter: scoped token still only sees Set A's suspect, NOT pic_b.
        rows = bearer.get(f"{API}/tag_suggestions", headers=headers).json()
        assert {r["picture_id"] for r in rows} == {pic_a}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scoped_token_filter_cannot_widen_to_other_set():
    temp_dir, _client, server, set_a, set_b, pic_a, pic_b, token_a = (
        _setup_scoped_token_env()
    )
    try:
        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token_a}"}
        # The token holder asks for Set B (which it cannot see): the scope
        # intersection wins and the out-of-scope suspect is NOT leaked.
        rows = bearer.get(
            f"{API}/tag_suggestions",
            params={"set_id": set_b},
            headers=headers,
        ).json()
        assert rows == []
        assert all(r["picture_id"] != pic_b for r in rows)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _suggestion_id_for(server, picture_id):
    """The id of the (single) seeded suggestion for *picture_id*."""

    def fetch(session):
        return session.exec(
            select(TagSuggestion.id).where(TagSuggestion.picture_id == picture_id)
        ).first()

    return server.vault.db.run_immediate_read_task(fetch)


def _set_twin(server, suggestion_id, twin_picture_id, twin_sim=0.97):
    """Point an existing suggestion at a twin, reason string included.

    The reason mirrors what ``tag_scan_service`` actually writes - it embeds the
    twin's id and similarity - because that free-text field is a twin attribute
    too and is part of what must be redacted.
    """

    def update(session):
        row = session.get(TagSuggestion, suggestion_id)
        row.twin_picture_id = twin_picture_id
        row.twin_sim = twin_sim
        row.reason = (
            f"near-twin {twin_picture_id} (sim {twin_sim:.3f}) disagrees; "
            "80% of nearest neighbours have the tag"
        )
        session.add(row)
        session.commit()

    server.vault.db.run_task(update)


def test_scoped_token_list_redacts_out_of_scope_twin():
    """A suggestion is a *pair*; the scope filter only constrains the suspect.

    Without redaction a Set-A token learns the id, existence, file type,
    perceptual similarity and model confidence of a Set-B picture - and the
    ``reason`` string spells the id out in prose. Iterating tags would enumerate
    picture ids across the vault. This is the same disclosure that made
    ``GET /reviews/{id}/suggestions`` owner-only; the reasoning is applied here
    rather than left at the module boundary.
    """
    temp_dir, client, server, _set_a, _set_b, pic_a, pic_b, token_a = (
        _setup_scoped_token_env()
    )
    try:
        # The in-scope suspect's twin is the out-of-scope picture.
        _set_twin(server, _suggestion_id_for(server, pic_a), pic_b)

        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token_a}"}
        resp = bearer.get(f"{API}/tag_suggestions", headers=headers)
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [r["picture_id"] for r in rows] == [pic_a]
        row = rows[0]
        # Every twin-derived attribute is withheld …
        for field in (
            "twin_picture_id",
            "twin_sim",
            "twin_ext",
            "twin_tagger_confidence",
        ):
            assert row[field] is None, f"{field} leaked an out-of-scope twin"
        # … including the one hiding in prose.
        assert str(pic_b) not in (row["reason"] or "")
        # The suspect's own fields are untouched - this is a redaction, not a
        # blanket blanking of the card.
        assert row["tag"] == "malformed hand"
        assert row["picture_ext"]

        # Owner sees the full pair - over-blocking is its own regression.
        owner_rows = client.get(
            f"{API}/tag_suggestions", params={"tag": "malformed hand"}
        ).json()
        assert owner_rows[0]["twin_picture_id"] == pic_b
        assert owner_rows[0]["twin_sim"] == 0.97
        assert str(pic_b) in owner_rows[0]["reason"]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_owner_filter_does_not_redact_own_twin():
    """Redaction keys on the TOKEN scope, never the user's filter narrowing.

    An owner narrowing the queue to Set A still owns Set B's picture, so their
    twin must survive. Guards against wiring the redaction to
    ``_resolve_review_picture_ids`` (scope ∩ filter) instead of the raw scope.
    """
    temp_dir, client, server, set_a, _set_b, pic_a, pic_b, _token = (
        _setup_scoped_token_env()
    )
    try:
        _set_twin(server, _suggestion_id_for(server, pic_a), pic_b)
        rows = client.get(
            f"{API}/tag_suggestions",
            params={"tag": "malformed hand", "set_id": set_a},
        ).json()
        assert rows[0]["twin_picture_id"] == pic_b
        assert str(pic_b) in rows[0]["reason"]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _force_variable_limit(server, limit=999):
    """Pin every DB connection's ``SQLITE_LIMIT_VARIABLE_NUMBER`` to *limit*.

    Registers a ``connect`` listener on the vault engine and disposes the pool
    so subsequent connections are recreated with the lowered ceiling. Used to
    reproduce the historical 999-variable ceiling regardless of the running
    SQLite build's much higher default, so a large ``picture_ids`` scope
    filtered by a plain ``.in_(ids)`` would raise ``OperationalError``. Call
    AFTER seeding.
    """
    engine = server.vault.db._engine

    def _set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)

    sa_event.listen(engine, "connect", _set_limit)
    engine.dispose()


def test_scan_tag_survives_large_picture_ids_scope():
    """A ~1500-picture explicit scope must scan without tripping SQLite's
    bound-parameter ceiling. Regression guard for the scale refactor: with the
    variable limit pinned to the historical 999 floor, the pre-refactor
    ``.in_(picture_ids)`` in ``_load`` (site 3) and ``_load_confidence_fallback``
    (site 4) would raise ``OperationalError: too many SQL variables``. The
    temp-table scope path keeps both alive and result-identical: three
    confidently-predicted, zero-ground-truth pictures still surface as ``add``
    suspects via the confidence fallback.
    """
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        n = 1500
        embedded = [1, 2, 3]  # get valid embeddings + confident predictions

        def seed(session):
            session.execute(
                sa_insert(Picture),
                [
                    {"id": i, "deleted": False, "file_path": f"/x/{i}.png"}
                    for i in range(1, n + 1)
                ],
            )
            # A handful of pictures carry a valid 512-d embedding (clears the
            # len(ids) < 2 guard so the scan reaches the fallback) and a
            # confident current-version prediction for the scanned tag, with NO
            # Tag row (zero ground truth → confidence fallback path).
            for i in embedded:
                blob = np.random.rand(512).astype(np.float32).tobytes()
                session.execute(
                    sa_update(Picture)
                    .where(Picture.id == i)
                    .values(image_embedding=blob)
                )
            session.execute(
                sa_insert(TagPrediction),
                [
                    {
                        "picture_id": i,
                        "tag": "scantag",
                        "confidence": 0.95,
                        "model_version": "v1",
                        "status": "PENDING",
                        "predicted_at": datetime.utcnow(),
                    }
                    for i in embedded
                ],
            )
            session.commit()

        server.vault.db.run_task(seed)

        # Pin the ceiling to 999 now that seeding is done under the default.
        _force_variable_limit(server, 999)

        scope = set(range(1, n + 1))
        res = tag_scan_service.scan_tag(
            server.vault, "scantag", project=None, picture_ids=scope
        )
        # Both _load and _load_confidence_fallback ran over the 1500-id scope
        # without OperationalError, and the three confident pictures surfaced.
        assert res["scanned"] == len(embedded)
        assert res["added"] == len(embedded)
        assert res["removed"] == 0
        assert res["count"] == len(embedded)

        rows = client.get("/tag_suggestions").json()
        assert {r["picture_id"] for r in rows} == set(embedded)
        assert all(r["direction"] == "add" for r in rows)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# F2: legacy-scan refresh-in-place (no purge) + accept guards (stale evidence).
# ---------------------------------------------------------------------------


def test_legacy_scan_refreshes_stale_pending_in_place():
    """F2(i): a legacy re-scan (review_id=None) must REFRESH the stored evidence
    on an existing PENDING row in place - same row id, fresh evidence, no delete
    and no duplicate. This is the no-purge replacement for the old rebuild."""
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        a = _upload_picture(client)  # Bad1.png
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            b = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]
        vec = [1.0] + [0.0] * 511
        _set_embedding(server, a, vec)
        _set_embedding(server, b, vec)
        _seed_tag(server, a, "malformed hand")

        assert (
            tag_scan_service.scan_tag(server.vault, "malformed hand", project=None)[
                "count"
            ]
            == 1
        )
        row = server.vault.db.run_immediate_read_task(
            lambda s: s.exec(
                select(TagSuggestion).where(TagSuggestion.tag == "malformed hand")
            ).first()
        )
        sid, orig_reason = row.id, row.reason

        # Corrupt the stored evidence to simulate staleness.
        def _corrupt(session):
            r = session.get(TagSuggestion, sid)
            r.reason, r.score, r.neighbors = "STALE", 0.0, None
            session.commit()

        server.vault.db.run_task(_corrupt)

        # Legacy re-scan refreshes in place (same row, no purge, no duplicate).
        assert (
            tag_scan_service.scan_tag(server.vault, "malformed hand", project=None)[
                "count"
            ]
            == 1
        )
        refreshed = server.vault.db.run_immediate_read_task(
            lambda s: s.get(TagSuggestion, sid)
        )
        assert refreshed is not None  # same row, not deleted/recreated
        assert refreshed.reason == orig_reason and refreshed.reason != "STALE"
        assert refreshed.score > 0.0 and refreshed.neighbors is not None
        assert (
            server.vault.db.run_immediate_read_task(
                lambda s: len(
                    s.exec(
                        select(TagSuggestion).where(
                            TagSuggestion.tag == "malformed hand"
                        )
                    ).all()
                )
            )
            == 1
        )
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_accept_refuses_when_a_manual_fix_contradicts_the_suggestion():
    """F2(ii): a human recorded the opposite label after the suggestion was
    raised (a manual fix). Accepting the stale suggestion would reverse it, so
    accept must refuse (loudly) and leave both the label and the row untouched."""
    import pytest

    from pixlstash.services.tag_suggestion_service import (
        SuggestionConflictError,
        accept_suggestion,
    )
    from pixlstash.utils.service.label_ledger import POS, record_human_label

    temp_dir, client, server = _setup()
    try:
        pic = _upload_picture(client)
        _seed_tag(server, pic, "malformed hand")
        sid = _seed_suggestion(server, pic, "malformed hand", "remove")

        # A human manually affirms the tag belongs (POS) - the manual fix a
        # stale "remove" suggestion would otherwise reverse.
        def _manual_pos(session):
            record_human_label(session, pic, "malformed hand", POS)
            session.commit()

        server.vault.db.run_task(_manual_pos)

        with pytest.raises(SuggestionConflictError):
            accept_suggestion(server.vault, sid)

        assert _has_tag(client, pic, "malformed hand")  # manual fix intact
        assert (
            server.vault.db.run_immediate_read_task(
                lambda s: s.get(TagSuggestion, sid).status
            )
            == "PENDING"
        )  # not flipped to ACCEPTED

        # A non-contradicting suggestion (add, matching the human POS; distinct
        # source so it doesn't collide on UNIQUE(picture_id, tag, source)) still
        # accepts - the guard is not over-blocking.
        add_sid = _seed_suggestion(server, pic, "malformed hand", "add", source="model")
        assert accept_suggestion(server.vault, add_sid)["direction"] == "add"
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()

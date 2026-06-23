"""Tests for the Tag Suggestions API: list, summary, accept (writeback), dismiss."""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from datetime import datetime

import numpy as np
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_summary_counts_by_tag_and_direction():
    temp_dir, client, server = _setup()
    try:
        pic_id = _upload_picture(client)
        # Two sources for the same (picture, tag) so the unique constraint holds;
        # the summary should aggregate both directions across sources.
        _seed_suggestion(
            server, pic_id, "malformed hand", "remove", source="near_neighbor"
        )
        _seed_suggestion(server, pic_id, "malformed hand", "add", source="model")

        resp = client.get("/tag_suggestions/summary")
        assert resp.status_code == 200
        summary = {row["tag"]: row for row in resp.json()}
        assert summary["malformed hand"]["remove"] == 1
        assert summary["malformed hand"]["add"] == 1
        assert summary["malformed hand"]["total"] == 2
    finally:
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_accept_skips_when_tagger_contradicts_neighbour():
    """remove + tagger says BOTH have it → the two signals disagree, so bulk leaves it.

    This is the case the old confidence-only path got wrong: it placed the pair in the
    "both" corner and *added* the tag to the twin — for a suggestion that asked to
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
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
        # LOWER cosine than C — without the override A's twin would be C, with it B wins.
        a = _upload_picture(client)  # Bad1.png
        b = _upload_named(client)  # distinct in-memory PNG
        c = _upload_named(client)  # distinct in-memory PNG

        # A points along axis 0. C is nearly parallel (cosine ~0.9999). B is further off.
        _set_embedding(server, a, [1.0] + [0.0] * 511)
        _set_embedding(server, c, [0.9999, 0.0141] + [0.0] * 510)  # closest to A
        _set_embedding(server, b, [0.9, 0.4359] + [0.0] * 510)  # opposite, farther

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
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_accept_missing_suggestion_returns_404():
    temp_dir, client, server = _setup()
    try:
        _upload_picture(client)
        resp = client.post("/tag_suggestions/999999/accept")
        assert resp.status_code == 404
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# Review-scope filters: project_id / set_id / character_id (AND together).
# These tests use the cookie-session client + unversioned paths (owner — no
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
        session.add(
            Face(
                picture_id=pic_id,
                character_id=character_id,
                face_index=face_index,
            )
        )
        session.commit()

    server.vault.db.run_task(ins)


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
        server.vault.close()
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
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_filter_by_character_numeric_and_unassigned():
    temp_dir, client, server = _setup()
    try:
        char_pic = _upload_picture(client)  # has a face with character 7
        unassigned_pic = _upload_named(client)  # face, no character
        other_pic = _upload_named(client)  # no face at all
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
        server.vault.close()
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
        server.vault.close()
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
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_summary_respects_filter():
    temp_dir, client, server = _setup()
    try:
        in_pic = _upload_picture(client)
        out_pic = _upload_named(client)
        _seed_suggestion(server, in_pic, "malformed hand", "remove")
        _seed_suggestion(server, out_pic, "bad anatomy", "add")
        r = client.post(f"{API}/picture_sets", json={"name": "Set"})
        set_id = r.json()["picture_set"]["id"]
        _add_to_set(server, in_pic, set_id)

        summary = client.get(
            "/tag_suggestions/summary", params={"set_id": set_id}
        ).json()
        tags = {row["tag"] for row in summary}
        assert tags == {"malformed hand"}  # out-of-scope tag excluded
        # Unfiltered summary sees both.
        all_tags = {row["tag"] for row in client.get("/tag_suggestions/summary").json()}
        assert all_tags == {"malformed hand", "bad anatomy"}
    finally:
        server.vault.close()
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
        server.vault.close()
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
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
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
        # Summary is likewise scoped: only Set A's tag.
        summary = bearer.get(f"{API}/tag_suggestions/summary", headers=headers).json()
        assert {row["tag"] for row in summary} == {"malformed hand"}
    finally:
        server.vault.close()
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
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()

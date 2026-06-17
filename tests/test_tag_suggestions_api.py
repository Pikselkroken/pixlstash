"""Tests for the Tag Suggestions API: list, summary, accept (writeback), dismiss."""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from datetime import datetime

from sqlmodel import select

from pixlstash.db_models import Tag
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

            return session.exec(
                _select(TagSuggestion).where(TagSuggestion.picture_id == suspect_id)
            ).first().id

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
            lambda s: s.exec(
                select(TagSuggestion).where(TagSuggestion.picture_id == suspect)
            ).first().id
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
        _seed_suggestion(server, pic_id, "malformed hand", "remove", source="near_neighbor")
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


def test_bulk_accept_resolves_by_confidence_and_reopens():
    temp_dir, client, server = _setup()
    try:
        # A "remove" pair: suspect is tagged, twin is not.
        suspect = _upload_picture(client)  # Bad1.png
        _seed_tag(server, suspect, "malformed hand")
        img_path = os.path.join(PICTURES_DIR, "Bad2.png")
        with open(img_path, "rb") as f:
            twin = upload_pictures_and_wait(
                client, [("file", ("Bad2.png", f, "image/png"))]
            )["results"][0]["picture_id"]

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
            lambda s: s.exec(
                select(TagSuggestion).where(TagSuggestion.picture_id == suspect)
            ).first().id
        )

        # The tagger is confident BOTH images have the malformation, so the decision is
        # "tag both" — regardless of which one currently carries the label.
        _seed_prediction(server, suspect, "malformed hand", 0.95)
        _seed_prediction(server, twin, "malformed hand", 0.92)

        # Too strict for 0.92 agreement → nothing resolves.
        strict = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.99, "dry_run": True},
        ).json()
        assert strict["count"] == 0

        # At 0.9 it resolves as "tag both" → the untagged twin gets the tag.
        applied = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.9},
        ).json()
        assert applied["count"] == 1
        assert applied["accepted_ids"] == [sid]
        assert _has_tag(client, twin, "malformed hand")  # tag added to the twin
        assert _has_tag(client, suspect, "malformed hand")  # suspect kept

        # Batch-undo removes the tag from the twin again.
        reopened = client.post(
            "/tag_suggestions/bulk-reopen", json={"ids": applied["accepted_ids"]}
        ).json()
        assert reopened["count"] == 1
        assert not _has_tag(client, twin, "malformed hand")
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

"""Object-scope (BOLA / CWE-639) tests for tag-PREDICTION mutation handlers.

Closes issue #504: the five mutating tag-prediction handlers (confirm, reject,
delete, reset_tags, reset_description) enforced no object-level scope, so a
resource-scoped token could confirm, reject, delete, or reset tag predictions
and descriptions on pictures outside its grant. Each now calls the deny-by-default
chokepoint ``enforce_picture_scope`` immediately after parsing the id and before
any DB read/branch/return. This is the same read-BOLA class as the 1.5.1
incidents; the vulnerable handlers shipped in 1.6.x.

These tests assert both directions per CLAUDE.md:
- a scoped token (simulated by patching the scope helper to allow only one
  picture id, exactly as ``test_picture_mutation_scope.py`` does) is **denied**
  (403) when the target picture is outside its grant;
- an owner / unscoped token (scope helper returns ``None``) still **succeeds**,
  so the guards do not over-block (that would be its own regression).

Patching ``enforce_picture_scope`` in the ``tag_predictions`` module namespace
exercises the handler's guard directly, independent of how a scoped token reaches
the handler (the middleware only populates ``token_scope`` for non-ALL tokens --
see ``docs/backend_architecture.md`` §16.2).
"""

import gc
import json
import os
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import pixlstash.routes.tag_predictions as tag_predictions_module
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures", "good")


@pytest.fixture
def env():
    """A live server with two imported pictures (in-scope + out-of-scope)."""
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    try:
        client = TestClient(server.api)
        r = client.post(
            "/login", json={"username": "testuser", "password": "testpassword"}
        )
        assert r.status_code == 200, r.text

        files = []
        for name in ("Good1.png", "Good2.jpg"):
            with open(os.path.join(PICTURES_DIR, name), "rb") as fh:
                ct = "image/png" if name.endswith(".png") else "image/jpeg"
                files.append(("file", (name, fh.read(), ct)))
        st = upload_pictures_and_wait(client, files, timeout_s=30)
        assert st["status"] == "completed", st

        r = client.get("/pictures")
        assert r.status_code == 200, r.text
        picture_ids = [p["id"] for p in r.json()]
        assert len(picture_ids) >= 2, "Need two pictures for the scope test"

        yield server, client, picture_ids
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


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


def _prediction_exists(server, pic_id, tag):
    """Read straight from the DB whether a (pic_id, tag) prediction row survives."""

    def query(session):
        from sqlmodel import select

        return (
            session.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == pic_id,
                    TagPrediction.tag == tag,
                )
            ).first()
            is not None
        )

    return server.vault.db.run_immediate_read_task(query)


def _scope_to(monkeypatch, allowed_ids):
    """Patch ``enforce_picture_scope`` to simulate a token scoped to *allowed_ids*.

    ``allowed_ids`` of ``None`` means owner/unscoped (no filtering). A set means
    only those picture ids are in scope; everything else is denied 403 -- the same
    technique ``test_picture_mutation_scope.py`` uses.
    """

    def fake_enforce(server, request, picture_id):
        if allowed_ids is None:
            return
        if int(picture_id) not in set(allowed_ids):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )

    monkeypatch.setattr(tag_predictions_module, "enforce_picture_scope", fake_enforce)


# ---------------------------------------------------------------------------
# Out-of-scope denied (403) AND in-scope still works (200) -- both directions.
# ---------------------------------------------------------------------------


def test_confirm_prediction_scope(env, monkeypatch):
    server, client, picture_ids = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _seed_prediction(server, in_scope, "sunny")
    _scope_to(monkeypatch, {in_scope})

    r_out = client.post(f"/pictures/{out_of_scope}/tag_predictions/sunny/confirm")
    assert r_out.status_code == 403, r_out.text
    r_in = client.post(f"/pictures/{in_scope}/tag_predictions/sunny/confirm")
    assert r_in.status_code == 200, r_in.text


def test_reject_prediction_scope(env, monkeypatch):
    server, client, picture_ids = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _seed_prediction(server, in_scope, "rainy")
    _scope_to(monkeypatch, {in_scope})

    r_out = client.post(f"/pictures/{out_of_scope}/tag_predictions/rainy/reject")
    assert r_out.status_code == 403, r_out.text
    r_in = client.post(f"/pictures/{in_scope}/tag_predictions/rainy/reject")
    assert r_in.status_code == 200, r_in.text


def test_delete_tag_predictions_scope(env, monkeypatch):
    server, client, picture_ids = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    # Seed a deletable prediction on the OUT-of-scope picture so we can prove the
    # 403 is fail-closed: the guard must run BEFORE the destructive delete.
    _seed_prediction(server, out_of_scope, "storm")
    _scope_to(monkeypatch, {in_scope})

    r_out = client.post(f"/pictures/{out_of_scope}/tag_predictions/delete")
    assert r_out.status_code == 403, r_out.text
    # Fail-closed: the out-of-scope prediction must still exist. If the guard were
    # placed after the service call, the row would be gone despite the 403.
    assert _prediction_exists(server, out_of_scope, "storm"), (
        "delete ran before the scope guard -- out-of-scope data was destroyed"
    )
    r_in = client.post(f"/pictures/{in_scope}/tag_predictions/delete")
    assert r_in.status_code == 200, r_in.text


def test_reset_tags_scope(env, monkeypatch):
    server, client, picture_ids = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    # Seed a prediction on the OUT-of-scope picture; reset_tags deletes non-manual
    # predictions, so a fail-open guard would wipe it despite the 403.
    _seed_prediction(server, out_of_scope, "gale")
    _scope_to(monkeypatch, {in_scope})

    r_out = client.post(f"/pictures/{out_of_scope}/reset_tags")
    assert r_out.status_code == 403, r_out.text
    assert _prediction_exists(server, out_of_scope, "gale"), (
        "reset_tags ran before the scope guard -- out-of-scope data was destroyed"
    )
    r_in = client.post(f"/pictures/{in_scope}/reset_tags")
    assert r_in.status_code == 200, r_in.text


def test_reset_description_scope(env, monkeypatch):
    server, client, picture_ids = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, {in_scope})

    r_out = client.post(f"/pictures/{out_of_scope}/reset_description")
    assert r_out.status_code == 403, r_out.text
    r_in = client.post(f"/pictures/{in_scope}/reset_description")
    assert r_in.status_code == 200, r_in.text


# ---------------------------------------------------------------------------
# Owner / unscoped token is not blocked by any of the five guards (no over-block).
# ---------------------------------------------------------------------------


def test_owner_unscoped_not_blocked(env, monkeypatch):
    server, client, picture_ids = env
    target = picture_ids[1]
    _seed_prediction(server, target, "clouds")
    _scope_to(monkeypatch, None)

    assert (
        client.post(f"/pictures/{target}/tag_predictions/clouds/confirm").status_code
        == 200
    )
    assert client.post(f"/pictures/{target}/reset_tags").status_code == 200
    assert client.post(f"/pictures/{target}/reset_description").status_code == 200
    assert client.post(f"/pictures/{target}/tag_predictions/delete").status_code == 200

"""Object-scope (BOLA / CWE-639) tests for tag-PREDICTION mutation handlers.

Issue #504: the five mutating tag-prediction handlers (confirm, reject, delete,
reset_tags, reset_description) must never let a resource-scoped share token
confirm, reject, delete, or reset tag predictions / descriptions on pictures
outside its grant.

**Post-refactor enforcement (backend refactor plan Steps 4-6).** The inline
``enforce_picture_scope`` calls these handlers used were removed in Step 5; object
authorization now lives in the centralised authz gate. Two facts make these routes
safe, both asserted / referenced here:

* **Live guard (asserted end-to-end below).** Every resource-scoped share token is
  a READ token, and these POST routes are NOT in ``READ_SAFE_POST_PATHS``, so the
  auth middleware blocks a scoped token from all of them (403) before any handler
  or DB work runs. A share token therefore cannot mutate tag predictions at all —
  in-scope or out.
* **Latent object-scope (proven elsewhere).** The routes are declared
  ``PICTURE_SCOPED`` in ``pixlstash/authz/registry.py``; the gate's per-object
  membership contract (a scoped principal reaching only its own pictures) is proven
  in ``tests/test_authz_gate_step4.py``.

Both directions per CLAUDE.md: a resource-scoped token is denied (403) and the
owner still succeeds (200) — over-blocking the owner would be its own regression.
The destructive handlers additionally assert fail-closed: the 403 leaves the
out-of-scope data intact.
"""

import gc
import json
import os
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures", "good")


@pytest.fixture
def env():
    """A live server with two imported pictures, the owner client, a cookie-less
    ``anon`` client, and a real resource-scoped READ (share) token."""
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

        # A real resource-scoped READ share token. create_token does not require
        # the resource to exist — the point is exercising the scoped-token path
        # through the middleware, which blocks it on every non-READ_SAFE POST.
        r = client.post(
            "/users/me/token",
            json={
                "description": "set share",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert r.status_code == 200, r.text
        scoped_token = r.json()["token"]

        # The auth middleware prefers a cookie session over a Bearer token, so a
        # Bearer request on the logged-in owner client would authenticate as the
        # owner. ``anon`` never logs in, so its Bearer token is the scoped token.
        anon = TestClient(server.api)

        yield server, client, anon, picture_ids, scoped_token
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


# ---------------------------------------------------------------------------
# A resource-scoped share token is denied (403) AND the owner still succeeds (200).
# ---------------------------------------------------------------------------


def test_confirm_prediction_scope(env):
    server, client, anon, picture_ids, scoped = env
    target = picture_ids[0]
    _seed_prediction(server, target, "sunny")

    r = anon.post(
        f"/pictures/{target}/tag_predictions/sunny/confirm", headers=_bearer(scoped)
    )
    assert r.status_code == 403, r.text
    r = client.post(f"/pictures/{target}/tag_predictions/sunny/confirm")
    assert r.status_code == 200, r.text


def test_reject_prediction_scope(env):
    server, client, anon, picture_ids, scoped = env
    target = picture_ids[0]
    _seed_prediction(server, target, "rainy")

    r = anon.post(
        f"/pictures/{target}/tag_predictions/rainy/reject", headers=_bearer(scoped)
    )
    assert r.status_code == 403, r.text
    r = client.post(f"/pictures/{target}/tag_predictions/rainy/reject")
    assert r.status_code == 200, r.text


def test_delete_tag_predictions_scope(env):
    server, client, anon, picture_ids, scoped = env
    target = picture_ids[1]
    # Seed a deletable prediction, then prove the scoped 403 is fail-closed: the
    # destructive delete must not run for a share token.
    _seed_prediction(server, target, "storm")

    r = anon.post(f"/pictures/{target}/tag_predictions/delete", headers=_bearer(scoped))
    assert r.status_code == 403, r.text
    assert _prediction_exists(server, target, "storm"), (
        "a scoped token's blocked delete must not destroy tag-prediction data"
    )
    r = client.post(f"/pictures/{target}/tag_predictions/delete")
    assert r.status_code == 200, r.text


def test_reset_tags_scope(env):
    server, client, anon, picture_ids, scoped = env
    target = picture_ids[1]
    _seed_prediction(server, target, "gale")

    r = anon.post(f"/pictures/{target}/reset_tags", headers=_bearer(scoped))
    assert r.status_code == 403, r.text
    assert _prediction_exists(server, target, "gale"), (
        "a scoped token's blocked reset_tags must not destroy tag-prediction data"
    )
    r = client.post(f"/pictures/{target}/reset_tags")
    assert r.status_code == 200, r.text


def test_reset_description_scope(env):
    server, client, anon, picture_ids, scoped = env
    target = picture_ids[0]

    r = anon.post(f"/pictures/{target}/reset_description", headers=_bearer(scoped))
    assert r.status_code == 403, r.text
    r = client.post(f"/pictures/{target}/reset_description")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# GET /pictures/{id}/tag_predictions is PICTURE_SCOPED and READ-reachable: a
# scoped token reaching its own picture would be allowed and an out-of-scope one
# 403'd by the gate. That object-scope contract is proven end-to-end in
# tests/test_detections_scope.py / test_authz_gate_step4.py; here we only assert
# the owner keeps full read access (no over-block).
# ---------------------------------------------------------------------------


def test_owner_reads_and_mutates_not_blocked(env):
    server, client, anon, picture_ids, scoped = env
    target = picture_ids[1]
    _seed_prediction(server, target, "clouds")

    assert client.get(f"/pictures/{target}/tag_predictions").status_code == 200
    assert (
        client.post(f"/pictures/{target}/tag_predictions/clouds/confirm").status_code
        == 200
    )
    assert client.post(f"/pictures/{target}/reset_tags").status_code == 200
    assert client.post(f"/pictures/{target}/reset_description").status_code == 200
    assert client.post(f"/pictures/{target}/tag_predictions/delete").status_code == 200

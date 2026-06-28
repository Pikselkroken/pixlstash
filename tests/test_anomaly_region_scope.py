"""Object-scope (BOLA) tests for GET /pictures/{id}/anomaly_region.

This is a per-object data endpoint, so it must enforce the scoping system before
returning any resource-derived data (CLAUDE.md §"Endpoint scope enforcement").
These assert both directions: out-of-scope is denied (403), in-scope still works
(200), and the owner/unscoped path is unaffected.

The anomaly tagger model is not loaded in the test environment, so the live
``pixlstash_tagger_service`` is replaced with a fake that reports loaded and
returns a canned localisation. That keeps the test focused on the scope check
(and the 404 / 422 contract) without a GPU or the real ConvNeXt model.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

import pixlstash.routes.pictures._anomaly as anomaly_module
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


class _FakeTaggerService:
    """Stand-in for PixlStashTaggerService used by the anomaly_region route."""

    def is_loaded(self):
        return True

    def version(self):
        return 1

    def resolve_label_index(self, label):
        # Knows "malformed hand"; everything else is unknown (-> 422).
        return 0 if (label or "").strip().lower() == "malformed hand" else None

    def localize_anomaly(self, pil_image, label):
        return {
            "boxes": [[0.1, 0.2, 0.3, 0.4], [0.6, 0.5, 0.2, 0.25]],
            "diffuse": False,
            "heatmap": "data:image/png;base64,FAKE",
        }


class _FakeEngine:
    def __init__(self):
        self.pixlstash_tagger_service = _FakeTaggerService()

    def close(self):
        # No-op: the real InferenceEngine releases models here at shutdown.
        pass


def _good_picture_files():
    pictures_dir = os.path.join(os.path.dirname(__file__), "..", "pictures", "good")
    results = []
    for name in sorted(os.listdir(pictures_dir)):
        path = os.path.join(pictures_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            ct = "image/png" if ext == ".png" else "image/jpeg"
            with open(path, "rb") as fh:
                results.append((name, fh.read(), ct))
    return results


def _setup_server_with_pictures():
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    client = TestClient(server.api, raise_server_exceptions=True)

    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert r.status_code == 200, r.text

    picture_files = _good_picture_files()
    assert picture_files, "No test pictures found in pictures/good/"
    files = [("file", (name, data, ct)) for name, data, ct in picture_files]
    import_status = upload_pictures_and_wait(client, files, timeout_s=30)
    assert import_status["status"] == "completed", import_status

    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    picture_ids = [p["id"] for p in r.json()]
    assert len(picture_ids) >= 2, "Need at least two pictures for the scope test"

    # Inject the fake tagger service so the route resolves a "loaded" model.
    server.vault._engine = _FakeEngine()

    # The region LRU cache is a module-global; clear it so picture ids reused
    # across tests can't produce a stale cross-test hit.
    anomaly_module._anomaly_region_cache.clear()

    return temp_dir, server, client, picture_ids


def _assign_face_to_character(server, picture_id, character_id):
    """Insert a Face row on ``picture_id`` assigned to ``character_id``.

    The character scope check (``_picture_id_in_scoped_character``) only reads
    ``Face.picture_id`` + ``Face.character_id``, so a directly-inserted row is
    enough to make the picture in-scope for a character token, without relying on
    background face extraction.
    """
    from pixlstash.db_models import Face

    def _insert(session):
        session.add(
            Face(
                picture_id=picture_id,
                frame_index=0,
                face_index=0,
                character_id=character_id,
                bbox=[0, 0, 10, 10],
            )
        )
        session.commit()

    server.vault.db.run_task(_insert)


def test_anomaly_region_scope_both_directions():
    """A resource-scoped READ token reads its picture but is 403 out of scope."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        in_scope, out_of_scope = picture_ids[0], picture_ids[1]

        # Scope a READ token to a picture set containing only the first picture.
        r = client.post(f"{API}/picture_sets", json={"name": "ScopedSet"})
        assert r.status_code == 200, r.text
        set_id = r.json()["picture_set"]["id"]
        r = client.post(f"{API}/picture_sets/{set_id}/members/{in_scope}")
        assert r.status_code == 200, r.text

        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "set read token",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": set_id,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        token_client = TestClient(server.api)

        # In-scope: 200 with the localisation result.
        r = token_client.get(
            f"{API}/pictures/{in_scope}/anomaly_region",
            params={"tag": "malformed hand"},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["picture_id"] == in_scope
        assert body["tag"] == "malformed hand"
        assert body["boxes"] == [[0.1, 0.2, 0.3, 0.4], [0.6, 0.5, 0.2, 0.25]]
        assert body["diffuse"] is False

        # Out-of-scope: 403 from enforce_picture_scope before any data is read.
        r = token_client.get(
            f"{API}/pictures/{out_of_scope}/anomaly_region",
            params={"tag": "malformed hand"},
            headers=auth,
        )
        assert r.status_code == 403, (
            f"out-of-scope anomaly_region must be 403, got {r.status_code}: {r.text}"
        )

        # Owner keeps full access.
        r = client.get(
            f"{API}/pictures/{out_of_scope}/anomaly_region",
            params={"tag": "malformed hand"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["picture_id"] == out_of_scope
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_anomaly_region_unknown_tag_is_422():
    """An unknown anomaly tag yields 422 with a clear message (owner path)."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        r = client.get(
            f"{API}/pictures/{picture_ids[0]}/anomaly_region",
            params={"tag": "definitely not a label"},
        )
        assert r.status_code == 422, r.text
        assert "Unknown anomaly tag" in r.json()["detail"]
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_anomaly_region_missing_picture_is_404():
    """A non-existent picture id yields 404 (owner path, known tag)."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        missing_id = max(picture_ids) + 100000
        r = client.get(
            f"{API}/pictures/{missing_id}/anomaly_region",
            params={"tag": "malformed hand"},
        )
        assert r.status_code == 404, r.text
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_anomaly_region_model_not_loaded_is_503():
    """When the tagger service is unavailable the route reports 503, not 500."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        server.vault._engine = None
        r = client.get(
            f"{API}/pictures/{picture_ids[0]}/anomaly_region",
            params={"tag": "malformed hand"},
        )
        assert r.status_code == 503, r.text
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_anomaly_region_cache_hit_is_scope_gated():
    """The scope check gates the cache-HIT path, not just the cold path.

    Regression pin for the BOLA class this codebase keeps reproducing: an
    owner request warms the LRU cache for (picture, tag); a scoped token whose
    grant does NOT include that picture must still get 403 on the same key,
    proving enforce_picture_scope runs before the cache lookup/return.
    """
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        target, other = picture_ids[0], picture_ids[1]

        # 1) Owner warms the cache for `target`.
        r = client.get(
            f"{API}/pictures/{target}/anomaly_region",
            params={"tag": "malformed hand"},
        )
        assert r.status_code == 200, r.text
        # Confirm the entry is actually cached (version() == 1 from the fake).
        assert (target, "malformed hand", 1) in anomaly_module._anomaly_region_cache

        # 2) A READ token scoped to a set that contains only `other` (NOT
        #    `target`) requests the same cached (picture, tag).
        r = client.post(f"{API}/picture_sets", json={"name": "OtherSet"})
        assert r.status_code == 200, r.text
        set_id = r.json()["picture_set"]["id"]
        r = client.post(f"{API}/picture_sets/{set_id}/members/{other}")
        assert r.status_code == 200, r.text

        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "other-set read token",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": set_id,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        token_client = TestClient(server.api)

        r = token_client.get(
            f"{API}/pictures/{target}/anomaly_region",
            params={"tag": "malformed hand"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, (
            "cache-hit path must be scope-gated; out-of-scope read of a warm "
            f"cache entry must be 403, got {r.status_code}: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_anomaly_region_character_scope_both_directions():
    """A character-scoped READ token reads its picture but is 403 elsewhere."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        in_scope, out_of_scope = picture_ids[0], picture_ids[1]

        r = client.post(f"{API}/characters", json={"name": "ScopedChar"})
        assert r.status_code == 200, r.text
        character_id = r.json()["character"]["id"]
        # Make `in_scope` belong to the character (a face assigned to it).
        _assign_face_to_character(server, in_scope, character_id)

        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "character read token",
                "scope": "READ",
                "resource_type": "character",
                "resource_id": character_id,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        token_client = TestClient(server.api)

        r = token_client.get(
            f"{API}/pictures/{in_scope}/anomaly_region",
            params={"tag": "malformed hand"},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        assert r.json()["picture_id"] == in_scope

        r = token_client.get(
            f"{API}/pictures/{out_of_scope}/anomaly_region",
            params={"tag": "malformed hand"},
            headers=auth,
        )
        assert r.status_code == 403, (
            f"out-of-scope character read must be 403, got {r.status_code}: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_anomaly_region_single_picture_scope_both_directions():
    """A single-picture-scoped READ token reads only that exact picture."""
    temp_dir, server, client, picture_ids = _setup_server_with_pictures()
    try:
        in_scope, out_of_scope = picture_ids[0], picture_ids[1]

        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "single-picture read token",
                "scope": "READ",
                "resource_type": "picture",
                "resource_id": in_scope,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        token_client = TestClient(server.api)

        r = token_client.get(
            f"{API}/pictures/{in_scope}/anomaly_region",
            params={"tag": "malformed hand"},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        assert r.json()["picture_id"] == in_scope

        r = token_client.get(
            f"{API}/pictures/{out_of_scope}/anomaly_region",
            params={"tag": "malformed hand"},
            headers=auth,
        )
        assert r.status_code == 403, (
            f"out-of-scope single-picture read must be 403, got {r.status_code}: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()

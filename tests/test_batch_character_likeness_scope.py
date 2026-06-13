"""Object-scope (BOLA) tests for ``POST /pictures/character_likeness/batch``.

This endpoint returns per-picture data (existence/`ready`/`eligible` signals and
the `character_likeness` score). Per CLAUDE.md §16.1 it must enforce object scope
before returning any resource-derived data, exactly like its single-id sibling
``GET /pictures/{id}/character_likeness`` (which calls ``enforce_picture_scope``).

The fix filters the client-supplied ``picture_ids`` through
``fetch_scope_allowed_picture_ids`` before any DB read: ids outside a scoped
token's grant are dropped from the work set and fall through to the deny path,
so they are indistinguishable from a missing/deleted id (no existence or score
leak). Owner/unscoped tokens (``token_scope is None``) keep full access.

These tests assert both directions:
- out-of-scope ids are denied (and indistinguishable from missing ids) under a
  scoped token;
- in-scope ids are still processed (over-blocking would be its own regression);
- the unscoped/owner path is unaffected;
- the endpoint stays OUT of READ_SAFE_POST_PATHS, so a READ resource token is
  blocked at the middleware before it can reach the handler at all.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

import pixlstash.routes.pictures._crud as crud_module
from pixlstash.auth import READ_SAFE_POST_PATHS
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


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

    # A reference character to score against. It need not have reference faces;
    # the scope behaviour is independent of whether a real score is produced.
    r = client.post(f"{API}/characters", json={"name": "RefChar"})
    assert r.status_code == 200, r.text
    reference_character_id = r.json()["character"]["id"]

    return temp_dir, server, client, picture_ids, reference_character_id


def _post_batch(client, reference_character_id, picture_ids):
    return client.post(
        f"{API}/pictures/character_likeness/batch",
        json={
            "reference_character_id": reference_character_id,
            "picture_ids": picture_ids,
            "character_id": "ALL",
        },
    )


def _deny_shape(entry):
    """A denied/missing id always reports this exact shape (no existence leak)."""
    return (
        entry["character_likeness"] is None
        and entry["eligible"] is False
        and entry["ready"] is True
    )


def test_endpoint_not_in_read_safe_post_paths():
    # A POST that returns per-object data must NOT be on the read-safe allowlist;
    # otherwise a READ-scoped token would reach it. Arithmetic guard, not judgement.
    assert f"{API}/pictures/character_likeness/batch" not in READ_SAFE_POST_PATHS


def test_read_resource_token_is_blocked_at_middleware():
    """A resource-scoped READ token can never reach the handler (403 at middleware)."""
    temp_dir, server, client, picture_ids, ref_char = _setup_server_with_pictures()
    try:
        # Scope the token to a picture set containing only the first picture.
        r = client.post(f"{API}/picture_sets", json={"name": "ScopedSet"})
        assert r.status_code == 200, r.text
        set_id = r.json()["picture_set"]["id"]
        r = client.post(f"{API}/picture_sets/{set_id}/members/{picture_ids[0]}")
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

        token_client = TestClient(server.api)
        r = token_client.post(
            f"{API}/pictures/character_likeness/batch",
            json={
                "reference_character_id": ref_char,
                "picture_ids": picture_ids,
                "character_id": "ALL",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, (
            f"READ resource token must be blocked from the batch POST, got "
            f"{r.status_code}: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_scoped_token_denies_out_of_scope_ids(monkeypatch):
    """Out-of-scope ids deny identically to missing ids; in-scope ids are processed.

    Simulates a scoped token by patching the scope helper to allow only the first
    picture id. This exercises the handler's scope filter directly, regardless of
    how a scoped token reaches the handler.
    """
    temp_dir, server, client, picture_ids, ref_char = _setup_server_with_pictures()
    try:
        in_scope = picture_ids[0]
        out_of_scope = picture_ids[1]
        missing_id = max(picture_ids) + 10_000

        # Pretend the request carries a token scoped to a single allowed picture.
        monkeypatch.setattr(
            crud_module,
            "fetch_scope_allowed_picture_ids",
            lambda server, request: {in_scope},
        )

        r = _post_batch(client, ref_char, [in_scope, out_of_scope, missing_id])
        assert r.status_code == 200, r.text
        results = {e["picture_id"]: e for e in r.json()["results"]}

        # Every requested id appears exactly once, in request order.
        assert [e["picture_id"] for e in r.json()["results"]] == [
            in_scope,
            out_of_scope,
            missing_id,
        ]

        # The out-of-scope id is denied with the SAME shape as a genuinely
        # missing id — no way to tell from the response that it exists.
        assert _deny_shape(results[out_of_scope]), results[out_of_scope]
        assert _deny_shape(results[missing_id]), results[missing_id]
        # The signal fields (everything but the echoed picture_id) must be
        # identical, so the response leaks nothing about whether the id exists.
        signal_fields = ("character_likeness", "eligible", "ready")
        assert {k: results[out_of_scope][k] for k in signal_fields} == {
            k: results[missing_id][k] for k in signal_fields
        }, "Out-of-scope id must be indistinguishable from a missing id"

        # The in-scope id is NOT auto-denied by scope: it goes through the real
        # classification path. It exists and is not soft-deleted, so under the
        # "ALL" filter it is classified as eligible — distinct from the deny shape
        # the out-of-scope/missing ids get (eligible:false, likeness:null),
        # whether or not face extraction has finished yet. Over-blocking an
        # in-scope id would be its own regression.
        assert results[in_scope]["eligible"] is True, results[in_scope]
        assert not _deny_shape(results[in_scope]), results[in_scope]
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


def test_unscoped_owner_token_sees_all_ids(monkeypatch):
    """An unscoped/owner token (scope helper returns None) keeps full access."""
    temp_dir, server, client, picture_ids, ref_char = _setup_server_with_pictures()
    try:
        # None == unscoped/owner: no id is filtered out.
        monkeypatch.setattr(
            crud_module,
            "fetch_scope_allowed_picture_ids",
            lambda server, request: None,
        )

        ids = picture_ids[:2]
        r = _post_batch(client, ref_char, ids)
        assert r.status_code == 200, r.text
        results = {e["picture_id"]: e for e in r.json()["results"]}

        # Both real pictures are processed (exist, not soft-deleted): under "ALL"
        # they classify as eligible, not denied on scope grounds. (ready may be
        # false while face extraction is still pending; that is not a scope
        # decision.)
        for pid in ids:
            assert results[pid]["eligible"] is True, results[pid]
            assert not _deny_shape(results[pid]), results[pid]
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()

"""API tests for the v1.9 tiered duplicate queue.

Every route is declared ``OWNER_ONLY`` in ``pixlstash/authz/registry.py`` and
enforced by the central authz gate, so these assert **both directions** per the
CLAUDE.md security review process:

* negative — a resource-scoped READ share token gets 403 on every route, via the
  ``Authorization`` header and via the ``?token=`` query-parameter path;
* positive — the owner cookie session reaches every route and gets a complete
  answer (over-blocking is its own regression).

Plus the contract the frontend reads: the policy is served rather than
hardcoded, the queue pages by confidence descending with the cover preselection
and both evidence layers, counts are live and scoped, and the verdict routes are
non-destructive.

Background workers are disabled and the pictures are inserted directly, so the
likeness worker cannot write rows underneath the assertions.
"""

import gc
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureSetMember
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from pixlstash.services import dedup_tier_service as tiers
from pixlstash.services.dedup_tier_service import TierPolicy
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"
POLICY_URL = f"{API}/dedup/policy"
GROUPS_URL = f"{API}/dedup/groups"
COUNTS_URL = f"{API}/dedup/counts"
SCAN_URL = f"{API}/dedup/scan"
STACK_URL = f"{API}/dedup/verdicts/stack"
KEEP_SEPARATE_URL = f"{API}/dedup/verdicts/keep-separate"
REOPEN_URL = f"{API}/dedup/verdicts/reopen"
AUTO_STACK_URL = f"{API}/dedup/auto-stack"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL could make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _run(server, fn, *args):
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _insert_pictures(server, specs):
    def insert(session):
        picture_ids = []
        for index, spec in enumerate(specs):
            pic = Picture(
                file_path=f"/vault/dedup_{index}.png",
                format="png",
                width=spec.get("width", 4000),
                height=spec.get("height", 3000),
                size_bytes=spec.get("size_bytes", 1000),
                score=spec.get("score"),
                pixel_sha=spec.get("pixel_sha"),
            )
            session.add(pic)
            session.flush()
            for tag in spec.get("tags", []):
                session.add(Tag(picture_id=int(pic.id), tag=tag))
            picture_ids.append(int(pic.id))
        session.commit()
        return picture_ids

    return _run(server, insert)


def _env():
    """Owner cookie client, one exact duplicate pair, and a set-scoped READ token.

    Pictures 0 and 1 share a ``pixel_sha`` and a size, so tier 1 finds exactly
    one group. Picture 2 is unique and never appears.
    """
    temp_dir = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    Server.DEFAULT_FORCE_CPU = True
    server = Server(config_path)
    client = TestClient(server.api)
    assert (
        client.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        ).status_code
        == 200
    )
    picture_ids = _insert_pictures(
        server,
        [
            {"pixel_sha": "aaa", "size_bytes": 100, "score": 5, "tags": ["portrait"]},
            {"pixel_sha": "aaa", "size_bytes": 100},
            {"pixel_sha": "ccc", "size_bytes": 300},
        ],
    )
    set_id = client.post(f"{API}/picture_sets", json={"name": "Set A"}).json()[
        "picture_set"
    ]["id"]

    def add_to_set(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=picture_ids[0]))
        session.commit()

    _run(server, add_to_set)
    token = client.post(
        f"{API}/users/me/token",
        json={
            "description": "set A read",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_id,
        },
    ).json()["token"]
    _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)
    return temp_dir, client, server, picture_ids, token, set_id


def _teardown(temp_dir, server):
    server.vault.close()
    temp_dir.cleanup()
    gc.collect()


def _signature(client) -> str:
    body = client.get(GROUPS_URL).json()
    assert body["groups"], body
    return body["groups"][0]["signature"]


# ── authorization, both directions ────────────────────────────────────────────


def test_scoped_read_token_is_denied_on_every_route():
    temp_dir, client, server, _ids, token, _set_id = _env()
    try:
        signature = _signature(client)
        scoped = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}
        assert scoped.get(POLICY_URL, headers=headers).status_code == 403
        assert scoped.get(GROUPS_URL, headers=headers).status_code == 403
        assert scoped.post(COUNTS_URL, json={}, headers=headers).status_code == 403
        assert scoped.post(SCAN_URL, json={}, headers=headers).status_code == 403
        assert (
            scoped.post(
                STACK_URL, json={"signature": signature}, headers=headers
            ).status_code
            == 403
        )
        assert (
            scoped.post(
                KEEP_SEPARATE_URL, json={"signature": signature}, headers=headers
            ).status_code
            == 403
        )
        assert (
            scoped.post(
                REOPEN_URL, json={"signature": signature}, headers=headers
            ).status_code
            == 403
        )
        assert scoped.post(AUTO_STACK_URL, json={}, headers=headers).status_code == 403

        # Same via the ?token= query-param path (no Authorization header).
        assert scoped.get(POLICY_URL, params={"token": token}).status_code == 403
        assert scoped.get(GROUPS_URL, params={"token": token}).status_code == 403
        assert (
            scoped.post(COUNTS_URL, params={"token": token}, json={}).status_code == 403
        )
        assert (
            scoped.post(SCAN_URL, params={"token": token}, json={}).status_code == 403
        )
        assert (
            scoped.post(
                STACK_URL, params={"token": token}, json={"signature": signature}
            ).status_code
            == 403
        )
        assert (
            scoped.post(
                KEEP_SEPARATE_URL,
                params={"token": token},
                json={"signature": signature},
            ).status_code
            == 403
        )
        assert (
            scoped.post(
                REOPEN_URL, params={"token": token}, json={"signature": signature}
            ).status_code
            == 403
        )
        assert (
            scoped.post(AUTO_STACK_URL, params={"token": token}, json={}).status_code
            == 403
        )
    finally:
        _teardown(temp_dir, server)


def test_a_denied_verdict_route_changed_nothing():
    """Fail-closed, not fail-late: the 403 happens before any write."""
    temp_dir, client, server, ids, token, _set_id = _env()
    try:
        signature = _signature(client)
        scoped = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}
        assert (
            scoped.post(
                STACK_URL, json={"signature": signature}, headers=headers
            ).status_code
            == 403
        )
        stacked = _run(
            server,
            lambda session: [session.get(Picture, pid).stack_id for pid in ids],
        )
        assert stacked == [None, None, None]
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1
    finally:
        _teardown(temp_dir, server)


def test_unauthenticated_is_denied():
    temp_dir, _client, server, _ids, _token, _set_id = _env()
    try:
        anonymous = TestClient(server.api)
        assert anonymous.get(POLICY_URL).status_code in (401, 403)
        assert anonymous.get(GROUPS_URL).status_code in (401, 403)
        assert anonymous.post(COUNTS_URL, json={}).status_code in (401, 403)
        assert anonymous.post(SCAN_URL, json={}).status_code in (401, 403)
        assert anonymous.post(AUTO_STACK_URL, json={}).status_code in (401, 403)
    finally:
        _teardown(temp_dir, server)


# ── policy ────────────────────────────────────────────────────────────────────


def test_owner_reads_the_tier_policy():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        response = client.get(POLICY_URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["defaults"]["threshold"] == pytest.approx(0.90)
        assert body["defaults"]["near_enabled"] is False
        assert body["defaults"]["embedding_enabled"] is False
        bounds = body["bounds"]
        assert bounds["min_threshold"] == pytest.approx(0.65)
        assert bounds["tiers"] == ["exact", "near", "embedding"]
        assert bounds["always_on_tiers"] == ["exact"]
        assert bounds["tier_requires"] == {
            "exact": None,
            "near": "exact",
            "embedding": "near",
        }
        assert set(bounds["verdicts"]) == {"stacked", "keep_separate"}
        assert "folder" in bounds["scope_types"]
    finally:
        _teardown(temp_dir, server)


def test_a_threshold_below_the_floor_is_rejected():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        assert client.get(GROUPS_URL, params={"threshold": 0.4}).status_code == 422
    finally:
        _teardown(temp_dir, server)


def test_enabling_the_embedding_tier_alone_is_rejected():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        response = client.get(GROUPS_URL, params={"embedding_enabled": True})
        assert response.status_code == 400, response.text
        assert "requires near_enabled" in response.json()["detail"]
    finally:
        _teardown(temp_dir, server)


# ── the queue ─────────────────────────────────────────────────────────────────


def test_the_queue_page_carries_cover_evidence_and_progress():
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        response = client.get(GROUPS_URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["offset"] == 0
        assert body["policy"]["threshold"] == pytest.approx(0.90)
        assert body["scope"]["key"] == "global"
        # No scan row has been written by a route yet, so the banner is idle
        # rather than an error.
        assert body["scan"]["status"] == "idle"

        group = body["groups"][0]
        assert group["tier"] == "exact"
        assert group["confidence"] == pytest.approx(1.0)
        assert group["member_count"] == 2
        assert group["cover_picture_id"] == ids[0]
        assert any(p["text"] == "Identical file hash" for p in group["why"])
        assert sorted(c["picture_id"] for c in group["candidates"]) == sorted(ids[:2])
        cover = next(c for c in group["candidates"] if c["picture_id"] == ids[0])
        assert cover["tag_count"] == 1
        assert cover["cover_score"] > 0
        assert any(p["text"] == "Preselected as cover" for p in cover["why"])
        # Managed-library pictures hide their path.
        assert all(c["file_path"] is None for c in group["candidates"])
    finally:
        _teardown(temp_dir, server)


def test_the_queue_page_size_is_honoured():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        body = client.get(GROUPS_URL, params={"limit": 1, "offset": 1}).json()
        assert body["limit"] == 1
        assert body["offset"] == 1
        assert body["groups"] == []
        assert body["total"] == 1
    finally:
        _teardown(temp_dir, server)


# ── counts ────────────────────────────────────────────────────────────────────


def test_counts_report_the_badge_the_tiers_and_the_scopes():
    temp_dir, client, server, _ids, _token, set_id = _env()
    try:
        response = client.post(
            COUNTS_URL,
            json={"scopes": [{"scope_type": "set", "scope_id": str(set_id)}]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["unresolved_groups"] == 1
        assert body["by_tier"] == {"exact": 1, "near": 0, "embedding": 0}
        assert len(body["scopes"]) == 1
        assert body["scopes"][0]["key"] == f"set:{set_id}"
        assert body["scopes"][0]["unresolved_groups"] == 1
    finally:
        _teardown(temp_dir, server)


def test_a_scope_without_an_id_is_rejected():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        response = client.post(COUNTS_URL, json={"scopes": [{"scope_type": "project"}]})
        assert response.status_code == 400, response.text
        assert "scope_id is required" in response.json()["detail"]
    finally:
        _teardown(temp_dir, server)


# ── scan ──────────────────────────────────────────────────────────────────────


def test_requesting_a_scan_returns_immediately_with_progress():
    temp_dir, client, server, _ids, _token, set_id = _env()
    try:
        response = client.post(
            SCAN_URL,
            json={
                "policy": {"near_enabled": True},
                "scope": {"scope_type": "set", "scope_id": str(set_id)},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert body["scope_key"] == f"set:{set_id}"
        assert body["tiers"] == ["exact", "near"]
        # And the queue for that scope now reports the scan rather than idle.
        queue = client.get(
            GROUPS_URL, params={"scope_type": "set", "scope_id": str(set_id)}
        ).json()
        assert queue["scan"]["scope_key"] == f"set:{set_id}"
    finally:
        _teardown(temp_dir, server)


# ── verdicts ──────────────────────────────────────────────────────────────────


def test_stacking_through_the_api_applies_the_union_and_clears_the_badge():
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        response = client.post(
            STACK_URL, json={"signature": signature, "cover_picture_id": ids[0]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verdict"] == "stacked"
        assert body["cover_picture_id"] == ids[0]
        assert sorted(body["picture_ids"]) == sorted(ids[:2])
        assert body["stack_id"] is not None
        assert body["metadata_union"]["tags_added"] == 1
        assert body["metadata_union"]["scores_lifted"] == 1
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        # Nothing was deleted: a stack is a grouping row plus a cover pointer.
        live = _run(
            server,
            lambda session: [
                int(row) for row in session.exec(select(Picture.id)).all()
            ],
        )
        assert sorted(live) == sorted(ids)
    finally:
        _teardown(temp_dir, server)


def test_keep_separate_then_reopen_round_trips_through_the_api():
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        kept = client.post(KEEP_SEPARATE_URL, json={"signature": signature})
        assert kept.status_code == 200, kept.text
        assert kept.json()["verdict"] == "keep_separate"
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        assert client.get(GROUPS_URL).json()["groups"] == []

        reopened = client.post(REOPEN_URL, json={"signature": signature})
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["previous_verdict"] == "keep_separate"
        assert reopened.json()["group_returned_to_queue"] is True
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1
        # No picture changed in either direction.
        stacked = _run(
            server,
            lambda session: [session.get(Picture, pid).stack_id for pid in ids],
        )
        assert stacked == [None, None, None]
    finally:
        _teardown(temp_dir, server)


def test_an_unknown_signature_is_a_400_not_a_500():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        for url in (STACK_URL, KEEP_SEPARATE_URL, REOPEN_URL):
            response = client.post(url, json={"signature": "0" * 64})
            assert response.status_code == 400, (url, response.text)
    finally:
        _teardown(temp_dir, server)


# ── bulk auto-stack ───────────────────────────────────────────────────────────


def test_auto_stack_defaults_to_a_dry_run():
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        response = client.post(AUTO_STACK_URL, json={})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is True
        assert body["groups"] == 1
        assert body["pictures"] == 2
        assert body["results"] == []
        stacked = _run(
            server,
            lambda session: [session.get(Picture, pid).stack_id for pid in ids],
        )
        assert stacked == [None, None, None]
    finally:
        _teardown(temp_dir, server)


def test_auto_stack_applies_under_one_batch_id():
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        response = client.post(AUTO_STACK_URL, json={"dry_run": False})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is False
        assert body["groups"] == 1
        assert body["batch_id"]
        assert body["failures"] == []
        assert {item["batch_id"] for item in body["results"]} == {body["batch_id"]}
        stacked = _run(
            server,
            lambda session: [session.get(Picture, pid).stack_id for pid in ids],
        )
        assert stacked[0] is not None and stacked[0] == stacked[1]
        assert stacked[2] is None
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
    finally:
        _teardown(temp_dir, server)

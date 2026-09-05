"""API tests for the v1.9 tiered duplicate queue.

Every route is declared ``OWNER_ONLY`` in ``pixlstash/authz/registry.py`` and
enforced by the central authz gate, so these assert **both directions** per the
CLAUDE.md security review process:

* negative - a resource-scoped READ share token gets 403 on every route, via the
  ``Authorization`` header and via the ``?token=`` query-parameter path;
* positive - the owner cookie session reaches every route and gets a complete
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
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureSet, PictureSetMember, PictureStack
from pixlstash.db_models.dedup import DedupGroup, DedupScan, DedupVerdict
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from pixlstash.services import dedup_tier_service as tiers
from pixlstash.services import dedup_verdict_service as verdicts
from pixlstash.services.dedup_tier_service import TierPolicy
from pixlstash.routes.dedup import MAX_COUNT_SCOPES
from pixlstash.utils.image_processing.image_utils import ImageUtils
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"
POLICY_URL = f"{API}/dedup/policy"
GROUPS_URL = f"{API}/dedup/groups"
COUNTS_URL = f"{API}/dedup/counts"
SCAN_URL = f"{API}/dedup/scan"
STACK_URL = f"{API}/dedup/verdicts/stack"
KEEP_SEPARATE_URL = f"{API}/dedup/verdicts/keep-separate"
BATCH_VERDICTS_URL = f"{API}/dedup/verdicts/batch"
REOPEN_URL = f"{API}/dedup/verdicts/reopen"
AUTO_STACK_URL = f"{API}/dedup/auto-stack"


def _stack_members_url(stack_id) -> str:
    return f"{API}/dedup/stacks/{stack_id}/members"


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
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
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
    server.close()
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
                BATCH_VERDICTS_URL,
                json={
                    "actions": [{"verdict": "keep_separate", "signature": signature}]
                },
                headers=headers,
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
        # The deck expansion is deny-by-default too, and the gate answers
        # before the handler, so a scoped token is refused whether or not
        # the stack exists - it never learns which.
        assert scoped.get(_stack_members_url(1), headers=headers).status_code == 403

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
                BATCH_VERDICTS_URL,
                params={"token": token},
                json={
                    "actions": [{"verdict": "keep_separate", "signature": signature}]
                },
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
        assert (
            scoped.get(_stack_members_url(1), params={"token": token}).status_code
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
        assert anonymous.post(BATCH_VERDICTS_URL, json={}).status_code in (401, 403)
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
        # The ranking signals ship null-safe: nothing in this env has a smart
        # score or a quality row yet, so both serve null - a dash in Compare,
        # never a fake zero.
        assert "smart_score" in cover and cover["smart_score"] is None
        assert "sharpness" in cover and cover["sharpness"] is None
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


def test_an_active_scan_coalesces_equivalent_requests_and_rejects_policy_changes():
    temp_dir, client, server, _ids, _token, set_id = _env()
    try:
        payload = {
            "policy": {"near_enabled": True},
            "scope": {"scope_type": "set", "scope_id": str(set_id)},
        }
        first = client.post(SCAN_URL, json=payload)
        assert first.status_code == 200, first.text
        second = client.post(SCAN_URL, json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["scan_id"] == first.json()["scan_id"]
        assert second.json()["started_at"] == first.json()["started_at"]

        changed = client.post(
            SCAN_URL,
            json={
                "policy": {"near_enabled": True, "threshold": 0.8},
                "scope": {"scope_type": "set", "scope_id": str(set_id)},
            },
        )
        assert changed.status_code == 409, changed.text
        detail = changed.json()["detail"]
        assert detail["code"] == "dedup_scan_busy"
        assert detail["active_scan"]["scan_id"] == first.json()["scan_id"]
        assert detail["active_scan"]["threshold"] == 0.9
        persisted = _run(
            server,
            lambda session: session.exec(
                select(DedupScan).where(DedupScan.scope_key == f"set:{set_id}")
            ).one(),
        )
        assert persisted.threshold == 0.9
        assert persisted.tiers == '["exact", "near"]'
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


def test_scrapheaping_a_member_below_two_drops_the_group_from_the_counts():
    """The badge follows a soft-delete immediately, not at the next scan.

    prune_stale_groups only runs on a verdict or a scan, so the counts and
    the open queue filter on LIVE membership instead of waiting for it - and
    a restore brings the group straight back, no rescan needed.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1

        deleted = client.delete(f"{API}/pictures/{ids[0]}")
        assert deleted.status_code == 200, deleted.text
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        assert client.get(GROUPS_URL).json()["groups"] == []

        restored = client.post(
            f"{API}/pictures/scrapheap/restore", json={"picture_ids": [ids[0]]}
        )
        assert restored.status_code == 200, restored.text
        counts = client.post(COUNTS_URL, json={})
        assert counts.json()["unresolved_groups"] == 1
        assert client.get(GROUPS_URL).json()["groups"][0]["signature"] == signature
    finally:
        _teardown(temp_dir, server)


def test_a_group_already_stacked_together_stops_posing_a_decision():
    """Members stacked BY HAND leave the queue and the counts at once.

    The grid's own stack actions never touch dedupgroup, so an exact pair the
    user stacked from the grid stayed "unresolved" and was re-offered forever
    (the owner's #670/#1746 report). A group where a stack would still fold
    something in - a stack plus a loner - keeps counting.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1

        def stack_by_hand(session):
            from pixlstash.db_models import PictureStack

            stack = PictureStack(name=None)
            session.add(stack)
            session.commit()
            session.refresh(stack)
            for position, pid in enumerate(ids):
                picture = session.get(Picture, pid)
                picture.stack_id = stack.id
                picture.stack_position = position
                session.add(picture)
            session.commit()
            return stack.id

        stack_id = _run(server, stack_by_hand)

        # All members share one stack: no decision left, nothing offered.
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        assert client.get(GROUPS_URL).json()["groups"] == []

        # Pull ONE member back out: a stack plus a loner is a decision again.
        def unstack_one(session):
            picture = session.get(Picture, ids[0])
            picture.stack_id = None
            picture.stack_position = None
            session.add(picture)
            session.commit()

        _run(server, unstack_one)
        assert stack_id is not None
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1
        assert len(client.get(GROUPS_URL).json()["groups"]) == 1
    finally:
        _teardown(temp_dir, server)


def test_the_decided_page_lists_the_verdict_and_clears_via_reopen():
    """`GET /dedup/groups?decided=true` is the review-and-clear surface.

    A decided group appears there with its live verdict and decided_at, stays
    there under a policy that would hide it from the open queue (decisions do
    not vanish when the threshold moves), and leaves when reopened.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        assert client.get(GROUPS_URL, params={"decided": True}).json()["groups"] == []

        kept = client.post(KEEP_SEPARATE_URL, json={"signature": signature})
        assert kept.status_code == 200, kept.text

        decided = client.get(GROUPS_URL, params={"decided": True}).json()
        assert decided["total"] == 1
        (row,) = decided["groups"]
        assert row["signature"] == signature
        assert row["verdict"] == "keep_separate"
        assert row["decided_at"] is not None
        assert len(row["candidates"]) >= 2

        # The decided page ignores the tier gate and the threshold: the open
        # queue at the strictest policy is empty, the decision is still shown.
        strict = client.get(
            GROUPS_URL, params={"decided": True, "threshold": 0.99999}
        ).json()
        assert strict["total"] == 1

        reopened = client.post(REOPEN_URL, json={"signature": signature})
        assert reopened.status_code == 200, reopened.text
        assert client.get(GROUPS_URL, params={"decided": True}).json()["groups"] == []
        # The open queue lists a verdict field too, null there by construction.
        open_page = client.get(GROUPS_URL).json()
        assert open_page["groups"][0]["verdict"] is None
    finally:
        _teardown(temp_dir, server)


def test_the_decided_page_orders_by_recent_activity_first():
    """The Decided flip puts the most recently active decision on top.

    It used to reuse the queue's `(confidence DESC, id ASC)` ordering, which is
    meaningless for a review list (every exact group ties at 1.0, so the list
    came out in group-id order regardless of when anything was decided - the
    user's "very weird" report, 2026-07-30). Both directions are asserted: the
    Initially that is `decided_at` across both verdict kinds; a later stack
    change has its own explicit regression below. The OPEN queue keeps its
    confidence ordering.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=2)
        _rescan(server)
        open_order = [g["signature"] for g in client.get(GROUPS_URL).json()["groups"]]
        assert len(open_order) == 3

        # The other direction first: the OPEN queue still orders by
        # (confidence DESC, id ASC) - the fix must not leak into it.
        def db_order(session):
            rows = session.exec(
                select(DedupGroup).where(DedupGroup.resolved.is_(False))
            ).all()
            return [
                row.signature
                for row in sorted(
                    rows, key=lambda r: (-float(r.confidence or 0.0), int(r.id))
                )
            ]

        assert open_order == _run(server, db_order)

        # Decide in a deliberate order, mixing both verdict kinds. Decision
        # order is deliberately NOT queue order, so the assertion below cannot
        # pass by accident of id ordering.
        first, second, third = open_order
        for url, signature in (
            (KEEP_SEPARATE_URL, second),
            (STACK_URL, first),
            (KEEP_SEPARATE_URL, third),
        ):
            response = client.post(url, json={"signature": signature})
            assert response.status_code == 200, response.text

        decided = client.get(GROUPS_URL, params={"decided": True}).json()["groups"]
        assert [g["signature"] for g in decided] == [third, first, second]
        stamps = [g["decided_at"] for g in decided]
        assert stamps == sorted(stamps, reverse=True)

        # A fresh decision lands on top: reopen the oldest and re-decide it.
        assert client.post(REOPEN_URL, json={"signature": second}).status_code == 200
        assert (
            client.post(KEEP_SEPARATE_URL, json={"signature": second}).status_code
            == 200
        )
        decided = client.get(GROUPS_URL, params={"decided": True}).json()["groups"]
        assert [g["signature"] for g in decided] == [second, third, first]
    finally:
        _teardown(temp_dir, server)


def test_decided_compare_groups_follow_the_latest_stack_change():
    """A changed stack returns to the top of the Decided comparison sequence.

    The cursor must use that same activity stamp, or page two would repeat or
    skip a group even when page one appears correctly sorted.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=1)
        _rescan(server)
        stacked_signature, separate_signature = _signatures(client)

        stacked = client.post(STACK_URL, json={"signature": stacked_signature})
        assert stacked.status_code == 200, stacked.text
        kept = client.post(KEEP_SEPARATE_URL, json={"signature": separate_signature})
        assert kept.status_code == 200, kept.text

        initial = client.get(GROUPS_URL, params={"decided": True}).json()["groups"]
        assert [group["signature"] for group in initial] == [
            separate_signature,
            stacked_signature,
        ]

        def touch_stack_after_latest_decision(session):
            latest_decision = session.exec(
                select(DedupVerdict.decided_at).where(
                    DedupVerdict.signature == separate_signature
                )
            ).one()
            stack = session.get(PictureStack, stacked.json()["stack_id"])
            stack.updated_at = latest_decision + timedelta(seconds=1)
            session.add(stack)
            session.commit()

        _run(server, touch_stack_after_latest_decision)

        first_page = client.get(GROUPS_URL, params={"decided": True, "limit": 1}).json()
        assert [group["signature"] for group in first_page["groups"]] == [
            stacked_signature
        ]
        assert first_page["next_cursor"]

        second_page = client.get(
            GROUPS_URL,
            params={
                "decided": True,
                "limit": 1,
                "cursor": first_page["next_cursor"],
            },
        ).json()
        assert [group["signature"] for group in second_page["groups"]] == [
            separate_signature
        ]
    finally:
        _teardown(temp_dir, server)


def test_the_decided_page_filters_by_verdict_in_both_directions():
    """`verdict=` narrows the decided page to one kind of decision.

    The tier gate is meaningless on the decided page (a decision was made under
    whatever policy was live then), so the Duplicates filter menu offers the two
    verdicts there instead: stacked, kept separate, or both. Every direction is
    asserted, because over-filtering is its own regression: each filter serves
    exactly its own rows AND still serves them all with the filter off, `total`
    is counted under the same filter as the page, `by_verdict` reports both
    counts regardless of the filter (so the menu can say what turning one back
    on would add), and the filter is refused on the open queue rather than
    silently emptying it.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=2)
        _rescan(server)
        stacked, separate_a, separate_b = sorted(_signatures(client))
        for url, signature in (
            (STACK_URL, stacked),
            (KEEP_SEPARATE_URL, separate_a),
            (KEEP_SEPARATE_URL, separate_b),
        ):
            assert client.post(url, json={"signature": signature}).status_code == 200

        def decided(**params):
            response = client.get(GROUPS_URL, params={"decided": True, **params})
            assert response.status_code == 200, response.text
            return response.json()

        both = decided()
        assert sorted(g["signature"] for g in both["groups"]) == sorted(
            [stacked, separate_a, separate_b]
        )
        assert both["total"] == 3
        assert both["verdicts"] == []

        only_stacked = decided(verdict="stacked")
        assert [g["signature"] for g in only_stacked["groups"]] == [stacked]
        assert only_stacked["total"] == 1
        assert only_stacked["verdicts"] == ["stacked"]

        only_separate = decided(verdict="keep_separate")
        assert sorted(g["signature"] for g in only_separate["groups"]) == sorted(
            [separate_a, separate_b]
        )
        assert only_separate["total"] == 2

        # Both verdicts asked for is no filter at all, not an intersection.
        everything = decided(verdict=["stacked", "keep_separate"])
        assert everything["total"] == 3

        # The counts are the menu's, so they ignore the filter in force.
        for body in (both, only_stacked, only_separate):
            assert body["by_verdict"] == {"stacked": 1, "keep_separate": 2}

        # The open queue carries neither the counts nor the filter.
        open_page = client.get(GROUPS_URL).json()
        assert open_page["by_verdict"] == {}
        assert open_page["verdicts"] == []
        refused = client.get(GROUPS_URL, params={"verdict": "stacked"})
        assert refused.status_code == 400, refused.text
        assert "decided=true" in refused.json()["detail"]
    finally:
        _teardown(temp_dir, server)


def test_redo_restamps_the_decision_so_it_returns_to_the_top():
    """Redo means "I re-decide this NOW", so it gets a fresh `decided_at`.

    Non-vacuous by construction: the undone verdict's stamp is backdated an
    hour below its sibling's, so a redo that restored the old stamp would sort
    it UNDER the sibling - only the 2026-07-30 re-stamp puts it on top, which
    is where the user who just pressed redo looks for it.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=1)
        _rescan(server)
        sig_a, sig_b = sorted(_signatures(client))
        assert client.post(STACK_URL, json={"signature": sig_a}).status_code == 200
        assert (
            client.post(KEEP_SEPARATE_URL, json={"signature": sig_b}).status_code == 200
        )

        def backdate(session):
            row = session.exec(
                select(DedupVerdict).where(DedupVerdict.signature == sig_b)
            ).first()
            row.decided_at = row.decided_at - timedelta(hours=1)
            session.add(row)
            session.commit()

        _run(server, backdate)

        def decided_order():
            body = client.get(GROUPS_URL, params={"decided": True}).json()
            return [g["signature"] for g in body["groups"]]

        assert decided_order() == [sig_a, sig_b]

        # Undo (the keep-separate on B is the newest operation) removes it...
        assert client.post(f"{API}/operations/undo", json={}).status_code == 200
        assert decided_order() == [sig_a]

        # ...and redo returns it TO THE TOP: decided_at now means "when this
        # decision last became live", freshly stamped on redo.
        assert client.post(f"{API}/operations/redo", json={}).status_code == 200
        assert decided_order() == [sig_b, sig_a]
        assert _verdict_row(server, sig_b).reopened_at is None
    finally:
        _teardown(temp_dir, server)


def test_decided_rows_carry_the_display_ready_decision_stamp():
    """`decided_at` on a decided row is the verdict's stamp, ready to display.

    The frontend binds the Decided view's "decided when" column to this field,
    so its contract is pinned end to end: the serialized value equals the
    verdict row's stamp exactly, in the API's house format (naive-UTC ISO 8601,
    microseconds, NO offset suffix); a redo's re-stamp is what the listing then
    serves; the stale edge (a resolved group with no live verdict) serves
    `null` rather than an invented stamp; and open-queue rows carry `null` in
    both verdict fields as before.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=1)
        _rescan(server)
        sig_a, sig_b = sorted(_signatures(client))

        # Open-queue rows: both verdict fields null (no live verdict there).
        for row in client.get(GROUPS_URL).json()["groups"]:
            assert row["verdict"] is None
            assert row["decided_at"] is None

        assert client.post(STACK_URL, json={"signature": sig_a}).status_code == 200
        assert (
            client.post(KEEP_SEPARATE_URL, json={"signature": sig_b}).status_code == 200
        )

        def decided_rows():
            body = client.get(GROUPS_URL, params={"decided": True}).json()
            return {g["signature"]: g for g in body["groups"]}

        # Both verdict kinds serve the stamp, byte-equal to the verdict row's
        # isoformat: naive UTC, microseconds, no "Z", no "+00:00".
        rows = decided_rows()
        for signature in (sig_a, sig_b):
            stamp = rows[signature]["decided_at"]
            assert stamp == _verdict_row(server, signature).decided_at.isoformat()
            assert not stamp.endswith("Z") and "+" not in stamp
            assert datetime.fromisoformat(stamp).tzinfo is None

        # A redo's fresh stamp is what the listing serves afterwards.
        stamp_before = _verdict_row(server, sig_b).decided_at
        assert client.post(f"{API}/operations/undo", json={}).status_code == 200
        assert client.post(f"{API}/operations/redo", json={}).status_code == 200
        restamped = _verdict_row(server, sig_b).decided_at
        assert restamped > stamp_before
        assert decided_rows()[sig_b]["decided_at"] == restamped.isoformat()

        # The stale edge: a resolved group whose verdict is no longer live
        # (reopened directly, group left resolved) still lists - in the tail -
        # with BOTH fields null. The server never invents a stamp.
        def go_stale(session):
            row = session.exec(
                select(DedupVerdict).where(DedupVerdict.signature == sig_a)
            ).first()
            row.reopened_at = datetime.utcnow()
            session.add(row)
            session.commit()

        _run(server, go_stale)
        body = client.get(GROUPS_URL, params={"decided": True}).json()
        assert [g["signature"] for g in body["groups"]] == [sig_b, sig_a]
        stale = body["groups"][-1]
        assert stale["verdict"] is None
        assert stale["decided_at"] is None
    finally:
        _teardown(temp_dir, server)


def test_decided_page_paging_is_stable_across_seams():
    """Cursor and offset paging of the decided list neither skip nor repeat.

    Covers the ordinary case (distinct stamps), the tie case (a bulk run's
    same-instant stamps, where the `id DESC` tie-break is what keeps the seam
    stable), and the cursor-family separation: a queue cursor on the decided
    page (and vice versa) is a 400, never a silently wrong resume.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=4)
        _rescan(server)
        open_order = [g["signature"] for g in client.get(GROUPS_URL).json()["groups"]]
        assert len(open_order) == 5
        # A queue-family cursor, captured while the queue still has rows.
        queue_cursor = client.get(GROUPS_URL, params={"limit": 2}).json()["next_cursor"]
        assert queue_cursor

        for signature in open_order:
            response = client.post(KEEP_SEPARATE_URL, json={"signature": signature})
            assert response.status_code == 200, response.text
        expected = list(reversed(open_order))

        def collect(params):
            collected, cursor = [], None
            for _ in range(10):
                query = dict(params)
                if cursor:
                    query["cursor"] = cursor
                body = client.get(GROUPS_URL, params=query).json()
                collected.extend(g["signature"] for g in body["groups"])
                cursor = body.get("next_cursor")
                if not cursor:
                    break
            return collected

        assert collect({"decided": True, "limit": 2}) == expected

        # The deprecated offset path pages the same ordering.
        flat = [
            signature
            for off in (0, 2, 4)
            for signature in (
                g["signature"]
                for g in client.get(
                    GROUPS_URL, params={"decided": True, "limit": 2, "offset": off}
                ).json()["groups"]
            )
        ]
        assert flat == expected

        # The tie case: flatten every stamp to one instant (a bulk auto-stack
        # writes a same-instant run) and re-page. The id tie-break must carry
        # the seam: nothing skipped, nothing repeated, deterministic order.
        def flatten(session):
            stamp = datetime.utcnow()
            for row in session.exec(select(DedupVerdict)).all():
                row.decided_at = stamp
                session.add(row)
            session.commit()

        _run(server, flatten)
        tied = collect({"decided": True, "limit": 2})
        assert len(tied) == len(set(tied)) == 5, "a seam skipped or repeated a row"

        def id_desc(session):
            rows = session.exec(
                select(DedupGroup).where(DedupGroup.resolved.is_(True))
            ).all()
            return [row.signature for row in sorted(rows, key=lambda r: -int(r.id))]

        assert tied == _run(server, id_desc)

        # Cursor families are mutually unreadable.
        decided_cursor = client.get(
            GROUPS_URL, params={"decided": True, "limit": 2}
        ).json()["next_cursor"]
        assert decided_cursor
        rejected = client.get(
            GROUPS_URL, params={"decided": True, "cursor": queue_cursor}
        )
        assert rejected.status_code == 400, rejected.text
        rejected = client.get(GROUPS_URL, params={"cursor": decided_cursor})
        assert rejected.status_code == 400, rejected.text
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


# ── resource hardening ────────────────────────────────────────────────────────


def test_a_non_numeric_scope_id_is_a_400_on_every_route():
    """Regression for the CSO's D4.

    ``picture_predicate()`` calls ``int(scope_id)`` for project / set / character.
    Leaving that unvalidated turned a bad request into an unhandled 500 on three
    read routes, and `POST /dedup/scan` returned 200 while **persisting** the
    unparseable scope - a self-inflicted poison row that made every later
    `GET /dedup/groups` for that scope 500 too. Validation now happens at the
    boundary, before any write.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        for scope_type in ("project", "set", "character"):
            params = {"scope_type": scope_type, "scope_id": "not-an-int"}
            body = {"scope_type": scope_type, "scope_id": "not-an-int"}
            assert client.get(GROUPS_URL, params=params).status_code == 400
            assert client.post(COUNTS_URL, json={"scopes": [body]}).status_code == 400
            assert client.post(SCAN_URL, json={"scope": body}).status_code == 400
            assert client.post(AUTO_STACK_URL, json={"scope": body}).status_code == 400
        # And nothing was persisted by the rejected scan requests.
        scans = _run(server, lambda session: session.exec(select(DedupScan)).all())
        assert scans == []
    finally:
        _teardown(temp_dir, server)


def test_a_folder_scope_does_not_treat_wildcards_as_wildcards():
    """A "Find duplicates in this folder" entry must not silently mean everywhere.

    The folder predicate is a LIKE prefix match; unescaped, a scope_id of "%"
    matches every path in the vault.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        # The seeded duplicate pair lives under /vault/, so a literal "%" would
        # match it if the metacharacter were not escaped.
        wild = client.post(
            COUNTS_URL, json={"scopes": [{"scope_type": "folder", "scope_id": "%"}]}
        )
        assert wild.status_code == 200, wild.text
        assert wild.json()["scopes"][0]["unresolved_groups"] == 0

        # The real folder still matches.
        real = client.post(
            COUNTS_URL,
            json={"scopes": [{"scope_type": "folder", "scope_id": "/vault"}]},
        )
        assert real.json()["scopes"][0]["unresolved_groups"] == 1
        assert len(ids) == 3
    finally:
        _teardown(temp_dir, server)


def test_the_counts_scope_list_is_capped():
    """One request must not become thousands of correlated COUNT subqueries."""
    temp_dir, client, server, _ids, _token, set_id = _env()
    try:
        scopes = [{"scope_type": "set", "scope_id": str(set_id)}] * (
            MAX_COUNT_SCOPES + 1
        )
        assert client.post(COUNTS_URL, json={"scopes": scopes}).status_code == 422
        ok = client.post(COUNTS_URL, json={"scopes": scopes[:MAX_COUNT_SCOPES]})
        assert ok.status_code == 200, ok.text
    finally:
        _teardown(temp_dir, server)


# ── frontend contract additions ───────────────────────────────────────────────


def test_every_candidate_carries_a_thumbnail_cache_version():
    """The queue must be able to bust a stale thumbnail like the grid does."""
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        candidates = client.get(GROUPS_URL).json()["groups"][0]["candidates"]
        # Unprocessed pictures report the "0" sentinel rather than omitting it.
        assert all(c["thumbnail_version"] == "0" for c in candidates)

        def set_thumbnail(session):
            pic = session.get(Picture, ids[0])
            pic.thumbnail_width = 320
            pic.thumbnail_height = 240
            session.add(pic)
            session.commit()

        _run(server, set_thumbnail)
        candidates = client.get(GROUPS_URL).json()["groups"][0]["candidates"]
        version = next(
            c["thumbnail_version"] for c in candidates if c["picture_id"] == ids[0]
        )
        # Exactly the version the batch-thumbnail endpoint puts in its ?v=.
        assert version == ImageUtils.thumbnail_cache_version(320, 240) == "320x240"
    finally:
        _teardown(temp_dir, server)


def test_the_auto_stack_dry_run_carries_the_consent_aggregates():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        body = client.post(AUTO_STACK_URL, json={}).json()
        summary = body["dry_run_summary"]
        assert summary["groups"] == body["groups"] == 1
        assert summary["pictures"] == body["pictures"] == 2
        assert summary["groups_by_tier"] == {"exact": 1, "near": 0, "embedding": 0}
        # The seeded cover carries the only tag and the only score, so it gains
        # nothing from the union.
        assert summary["covers_gaining_metadata"] == 0
    finally:
        _teardown(temp_dir, server)


def test_a_partially_blocked_auto_stack_returns_its_batch_id():
    """R2 at the HTTP boundary, restated for the withholding filter.

    The original fixture reached the run with a locked group in it and asserted
    the 423 was reported rather than raised. Since a group with fewer than two
    stackable members is withheld from the queue, the counts AND the auto-stack
    plan (owner call, 2026-07-30), that group no longer enters the run: the right
    HTTP-boundary assertion is now that the run applies the decidable group,
    reports no failure, and still hands back its undo handle. The
    "an HTTPException mid-run does not abort" invariant itself is pinned at the
    service level by
    ``test_dedup_verdict_service.test_an_http_exception_mid_run_does_not_abort_the_bulk_run``,
    which injects the refusal instead of relying on a lock to produce one.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:

        def add_second_group_and_lock_it(session):
            created = []
            for _ in range(2):
                pic = Picture(
                    file_path=f"/vault/locked_{len(created)}.png",
                    format="png",
                    width=10,
                    height=10,
                    size_bytes=42,
                    pixel_sha="locked",
                )
                session.add(pic)
                session.flush()
                created.append(int(pic.id))
            picture_set = PictureSet(name="Frozen", locked=True)
            session.add(picture_set)
            session.commit()
            session.refresh(picture_set)
            session.add(
                PictureSetMember(set_id=int(picture_set.id), picture_id=created[0])
            )
            session.commit()
            return created

        locked_ids = _run(server, add_second_group_and_lock_it)
        _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)

        response = client.post(AUTO_STACK_URL, json={"dry_run": False})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["batch_id"]
        assert body["groups"] == 1
        assert body["blocked"] == 0, "the locked group was filtered out, not refused"
        assert body["failures"] == []
        # The unlocked group was applied, the locked one was not.
        assert (
            _run(server, lambda session: session.get(Picture, ids[0]).stack_id)
            is not None
        )
        assert (
            _run(server, lambda session: session.get(Picture, locked_ids[0]).stack_id)
            is None
        )
    finally:
        _teardown(temp_dir, server)


# ── undo reopens the verdict, not only the pictures ───────────────────────────


def _add_exact_groups(server, count, members=2, sha_prefix="extra"):
    """Insert *count* more exact groups of *members* byte-identical pictures."""

    def insert(session):
        created = []
        for index in range(count):
            group = []
            for member in range(members):
                pic = Picture(
                    file_path=f"/vault/{sha_prefix}_{index}_{member}.png",
                    format="png",
                    width=100,
                    height=100,
                    size_bytes=500 + index,
                    pixel_sha=f"{sha_prefix}-{index}",
                )
                session.add(pic)
                session.flush()
                group.append(int(pic.id))
            created.append(group)
        session.commit()
        return created

    return _run(server, insert)


def _rescan(server):
    _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)


def _signatures(client) -> set:
    return {group["signature"] for group in client.get(GROUPS_URL).json()["groups"]}


def _verdict_row(server, signature):
    return _run(
        server,
        lambda session: session.exec(
            select(DedupVerdict).where(DedupVerdict.signature == signature)
        ).first(),
    )


def test_undo_returns_the_stacked_group_to_the_queue():
    """QA blocker 1, single verdict.

    Undo restored every picture facet but left the ``DedupVerdict`` decided and
    the ``DedupGroup`` resolved, so the group never came back to the queue, was
    not counted, and survived a rescan (the signature still carried a live
    verdict). The only way back was a ``POST /dedup/verdicts/reopen`` no user
    could discover. The post-restore hook now reopens both rows inside the undo's
    own transaction.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1

        stacked = client.post(STACK_URL, json={"signature": signature})
        assert stacked.status_code == 200, stacked.text
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        assert _signatures(client) == set()

        undone = client.post(f"{API}/operations/undo", json={})
        assert undone.status_code == 200, undone.text

        # The pictures are unstacked ...
        assert _run(
            server, lambda session: [session.get(Picture, pid).stack_id for pid in ids]
        ) == [None, None, None]
        # ... and so is the decision.
        assert _signatures(client) == {signature}
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1
        verdict = _verdict_row(server, signature)
        assert verdict is not None, "the verdict row is kept, only reopened"
        assert verdict.reopened_at is not None

        # A rescan does not re-decide it either: it is genuinely back in the queue.
        _rescan(server)
        assert _signatures(client) == {signature}

        redone = client.post(f"{API}/operations/redo", json={})
        assert redone.status_code == 200, redone.text
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        assert _signatures(client) == set()
        assert _verdict_row(server, signature).reopened_at is None
    finally:
        _teardown(temp_dir, server)


def test_batch_undo_after_auto_stack_returns_every_group():
    """QA blocker 1, QA's exact repro: bulk auto-stack then batch undo.

    Every duplicate vanished from the queue permanently - one undo click, and the
    whole vault's worth of duplicate decisions was unrecoverable without
    hand-reopening each signature.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=3)
        _rescan(server)
        before = _signatures(client)
        assert len(before) == 4
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 4

        applied = client.post(AUTO_STACK_URL, json={"dry_run": False})
        assert applied.status_code == 200, applied.text
        batch_id = applied.json()["batch_id"]
        assert applied.json()["groups"] == 4
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0

        undone = client.post(f"{API}/operations/batches/{batch_id}/undo", json={})
        assert undone.status_code == 200, undone.text

        assert _signatures(client) == before
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 4
        # And the whole batch is redoable, back to nothing outstanding.
        assert client.post(f"{API}/operations/redo", json={}).status_code == 200
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
    finally:
        _teardown(temp_dir, server)


def test_an_undo_does_not_reopen_a_group_it_never_touched():
    """The hook is scoped to the undone batch, not to every decided group.

    The two verdicts here share no gesture id, so each sits in its own
    server-minted batch: undoing the newest (the stack) reverses only the stack.
    The keep-separate stands - not because it is irreversible (it has been
    undoable since 2026-07-30), but because its own operation was not undone.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=1)
        _rescan(server)
        signatures = sorted(_signatures(client))
        assert len(signatures) == 2
        kept_separate, stacked = signatures

        keep = client.post(KEEP_SEPARATE_URL, json={"signature": kept_separate})
        assert keep.status_code == 200, keep.text
        response = client.post(STACK_URL, json={"signature": stacked})
        assert response.status_code == 200, response.text
        assert _signatures(client) == set()

        assert client.post(f"{API}/operations/undo", json={}).status_code == 200

        # Only the stacked group came back; the keep-separate decision stands,
        # and a rescan does not re-ask it.
        assert _signatures(client) == {stacked}
        _rescan(server)
        assert _signatures(client) == {stacked}
    finally:
        _teardown(temp_dir, server)


def test_undo_returns_a_kept_separate_group_to_the_queue():
    """Keep-separate is undoable since the owner's 2026-07-30 override of #644.

    Both directions at the HTTP boundary: the response carries the operation's
    ``batch_id`` (the undo handle, mirroring the stack response), undo returns
    the group to the queue with the verdict row kept and reopened, and redo
    re-decides it. No picture row changes in any direction.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        kept = client.post(KEEP_SEPARATE_URL, json={"signature": signature})
        assert kept.status_code == 200, kept.text
        body = kept.json()
        assert body["verdict"] == "keep_separate"
        # The undo handle, exactly as the stack response exposes it. No client
        # batch was supplied, so the server minted a namespaced srv- id.
        batch_id = body["batch_id"]
        assert batch_id and batch_id.startswith("srv-")
        assert _verdict_row(server, signature).batch_id == batch_id
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0

        # The recorded operation is discoverable through the standard log.
        state = client.get(f"{API}/operations/undo-state").json()
        assert state["can_undo"] is True
        assert state["next_undo"]["op_type"] == "dedup.keep_separate"

        undone = client.post(f"{API}/operations/undo", json={})
        assert undone.status_code == 200, undone.text
        assert _signatures(client) == {signature}
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1
        verdict = _verdict_row(server, signature)
        assert verdict is not None, "the verdict row is kept, only reopened"
        assert verdict.reopened_at is not None
        # It is genuinely back in the queue: a rescan does not re-decide it.
        _rescan(server)
        assert _signatures(client) == {signature}

        redone = client.post(f"{API}/operations/redo", json={})
        assert redone.status_code == 200, redone.text
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        assert _verdict_row(server, signature).reopened_at is None
        # No picture row changed in either direction.
        assert _run(
            server, lambda session: [session.get(Picture, pid).stack_id for pid in ids]
        ) == [None, None, None]
    finally:
        _teardown(temp_dir, server)


def test_an_undo_of_a_shared_gesture_reverses_both_verdict_kinds():
    """One gesture id across a stack and a keep-separate is ONE undo unit.

    Until 2026-07-30 the keep-separate half recorded no operation and the stack
    hook deliberately left it standing (CSO R5: nothing may be reversed
    *silently*). The owner's override makes keep-separate record its own
    operation, so the shared batch now reverses both halves - each through its
    own operation, explicitly listed in the undo response, which is what R5
    actually demanded. Redo re-applies both.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=1)
        _rescan(server)
        signatures = sorted(_signatures(client))
        assert len(signatures) == 2
        kept_separate, stacked = signatures

        gesture = "cli-one-gesture-644"
        keep = client.post(
            KEEP_SEPARATE_URL,
            json={"signature": kept_separate, "batch_id": gesture},
        )
        assert keep.status_code == 200, keep.text
        assert keep.json()["batch_id"] == gesture
        response = client.post(
            STACK_URL, json={"signature": stacked, "batch_id": gesture}
        )
        assert response.status_code == 200, response.text
        assert _signatures(client) == set()
        assert _verdict_row(server, kept_separate).batch_id == gesture

        undone = client.post(f"{API}/operations/undo", json={})
        assert undone.status_code == 200, undone.text
        # Explicit, not silent: both operations are named in the undo response.
        assert {op["op_type"] for op in undone.json()["operations"]} == {
            "dedup.stack",
            "dedup.keep_separate",
        }

        # Both groups are back in the queue, both verdict rows reopened.
        assert _signatures(client) == {kept_separate, stacked}
        assert _verdict_row(server, kept_separate).reopened_at is not None
        assert _verdict_row(server, stacked).reopened_at is not None
        _rescan(server)
        assert _signatures(client) == {kept_separate, stacked}

        # Redo re-applies the whole gesture.
        assert client.post(f"{API}/operations/redo", json={}).status_code == 200
        assert _signatures(client) == set()
        assert _verdict_row(server, kept_separate).reopened_at is None
        assert _verdict_row(server, stacked).reopened_at is None
    finally:
        _teardown(temp_dir, server)


def test_a_failed_atomic_verdict_batch_rolls_back_every_action():
    """A refusal after a valid first action leaves no verdict or undo fragment."""
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=1)
        _rescan(server)
        signatures = sorted(_signatures(client))
        gesture = "cli-atomic-rollback"

        response = client.post(
            BATCH_VERDICTS_URL,
            json={
                "batch_id": gesture,
                "actions": [
                    {"verdict": "stacked", "signature": signatures[0]},
                    {"verdict": "stacked", "signature": "missing-signature"},
                ],
            },
        )

        assert response.status_code == 400, response.text
        assert _signatures(client) == set(signatures)
        assert (
            client.get(f"{API}/operations", params={"batch_id": gesture}).json() == []
        )
        assert all(_verdict_row(server, signature) is None for signature in signatures)
    finally:
        _teardown(temp_dir, server)


def test_two_clients_cannot_interleave_inside_an_atomic_verdict_gesture(
    monkeypatch,
):
    """A queued B lands after A1/A2, leaving strict LIFO with a legal frontier."""
    temp_dir, client_a, server, _ids, _token, _set_id = _env()
    client_b = TestClient(server.api)
    try:
        assert (
            client_b.post(
                f"{API}/login",
                json={"username": "owner", "password": "example-owner-password"},
            ).status_code
            == 200
        )
        _add_exact_groups(server, count=2)
        _rescan(server)
        first, second, foreign = sorted(_signatures(client_a))
        gesture = "cli-two-client-atomic"

        first_action_finished = Event()
        foreign_task_queued = Event()
        original_stack = verdicts.apply_stack_verdict_in_session
        original_submit = server.vault.db.submit_task
        stack_calls = 0

        def pause_between_batch_actions(*args, **kwargs):
            nonlocal stack_calls
            result = original_stack(*args, **kwargs)
            stack_calls += 1
            if stack_calls == 1:
                first_action_finished.set()
                assert foreign_task_queued.wait(5), (
                    "second client never queued its write"
                )
            return result

        def observe_submit(func, *args, **kwargs):
            future = original_submit(func, *args, **kwargs)
            if func is verdicts.apply_keep_separate_in_session:
                # Set only after PriorityQueue.put returned: B is genuinely
                # waiting behind A's still-running database task.
                foreign_task_queued.set()
            return future

        monkeypatch.setattr(
            verdicts, "apply_stack_verdict_in_session", pause_between_batch_actions
        )
        monkeypatch.setattr(server.vault.db, "submit_task", observe_submit)

        with ThreadPoolExecutor(max_workers=2) as pool:
            batch_future = pool.submit(
                client_a.post,
                BATCH_VERDICTS_URL,
                json={
                    "batch_id": gesture,
                    "actions": [
                        {"verdict": "stacked", "signature": first},
                        {"verdict": "stacked", "signature": second},
                    ],
                },
            )
            assert first_action_finished.wait(5), "batch never reached its midpoint"
            foreign_future = pool.submit(
                client_b.post,
                KEEP_SEPARATE_URL,
                json={"signature": foreign},
            )
            batch_response = batch_future.result(timeout=10)
            foreign_response = foreign_future.result(timeout=10)

        assert batch_response.status_code == 200, batch_response.text
        assert foreign_response.status_code == 200, foreign_response.text
        foreign_batch = foreign_response.json()["batch_id"]
        batch_rows = client_a.get(
            f"{API}/operations", params={"batch_id": gesture}
        ).json()
        assert len(batch_rows) == 2
        batch_ids = sorted(row["id"] for row in batch_rows)
        assert batch_ids[1] == batch_ids[0] + 1

        history = client_a.get(f"{API}/operations", params={"limit": 3}).json()
        assert [row["batch_id"] for row in history] == [
            foreign_batch,
            gesture,
            gesture,
        ]
        # Strict LIFO still rejects skipping B; after B is undone, the whole
        # real frontend gesture is exactly one legal undo.
        stale = client_a.post(f"{API}/operations/batches/{gesture}/undo", json={})
        assert stale.status_code == 409, stale.text
        first_undo = client_a.post(f"{API}/operations/undo", json={})
        assert first_undo.status_code == 200, first_undo.text
        assert {row["batch_id"] for row in first_undo.json()["operations"]} == {
            foreign_batch
        }
        second_undo = client_a.post(f"{API}/operations/undo", json={})
        assert second_undo.status_code == 200, second_undo.text
        assert len(second_undo.json()["operations"]) == 2
        assert {row["batch_id"] for row in second_undo.json()["operations"]} == {
            gesture
        }
        assert _signatures(client_a) == {first, second, foreign}
    finally:
        client_b.close()
        _teardown(temp_dir, server)


def test_clear_decision_returns_the_stacked_group_to_the_queue():
    """The owner-reported 2026-07-30 bug, at the HTTP boundary.

    "Clear decision" left the Decided page but never returned the group to the
    open queue: reopen only cleared the verdict memory while the pictures kept
    sharing one stack, and the queue's live filter requires two stack units.
    Clearing a stacked verdict must dissolve its stack, return the group to
    the listing AND the badge count, and hand back an undo handle; undoing the
    clear restacks and re-decides, redo clears again.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        stacked = client.post(STACK_URL, json={"signature": signature})
        assert stacked.status_code == 200, stacked.text
        stack_id = stacked.json()["stack_id"]
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0

        cleared = client.post(REOPEN_URL, json={"signature": signature})
        assert cleared.status_code == 200, cleared.text
        body = cleared.json()
        assert body["previous_verdict"] == "stacked"
        assert body["group_returned_to_queue"] is True
        # The clear unstacked pictures, so it is one undoable operation and the
        # response carries the batch handle, like every other verdict path.
        clear_batch = body["batch_id"]
        assert clear_batch and clear_batch.startswith("srv-")
        assert sorted(body["unstacked_picture_ids"]) == sorted(ids[:2])
        # Back in the open listing and in the badge count, pictures unstacked.
        assert _signatures(client) == {signature}
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1
        assert _run(
            server,
            lambda session: [session.get(Picture, pid).stack_id for pid in ids[:2]],
        ) == [None, None]
        # And it stays back across a rescan: the verdict really is reopened.
        _rescan(server)
        assert _signatures(client) == {signature}

        # Undo-of-clear: restacked AND re-decided, off the queue, on Decided.
        undone = client.post(f"{API}/operations/batches/{clear_batch}/undo", json={})
        assert undone.status_code == 200, undone.text
        assert _signatures(client) == set()
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 0
        verdict = _verdict_row(server, signature)
        assert verdict.reopened_at is None
        assert _run(
            server,
            lambda session: {session.get(Picture, pid).stack_id for pid in ids[:2]},
        ) == {stack_id}

        # Redo-of-clear: cleared again, back on the queue, no empty stack rows.
        redone = client.post(f"{API}/operations/redo", json={})
        assert redone.status_code == 200, redone.text
        assert _signatures(client) == {signature}
        assert _verdict_row(server, signature).reopened_at is not None
        assert _run(
            server,
            lambda session: [session.get(Picture, pid).stack_id for pid in ids[:2]],
        ) == [None, None]
        orphaned = _run(
            server,
            lambda session: session.exec(
                select(PictureStack).where(PictureStack.id == stack_id)
            ).all(),
        )
        assert orphaned == [], "the emptied stack row must not be left behind"
    finally:
        _teardown(temp_dir, server)


def test_verdicts_announce_pictures_changed_on_the_ws_envelope():
    """Both verdict kinds emit the standard refresh signal; so do undo and redo.

    Verdicts used to emit nothing, so a second tab's grid, queue and counts had
    no signal to refresh on. Both paths now raise the same
    ``pictures_changed``-family event every other mutation raises, with the
    caller's ``origin_client_id`` carried in the event data (§15) - and the
    op-log restore announces the keep-separate's targets on undo/redo even
    though its recorded diff is empty.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=1)
        _rescan(server)
        signatures = sorted(_signatures(client))
        assert len(signatures) == 2
        kept_separate, stacked = signatures
        by_signature = {
            group["signature"]: sorted(c["picture_id"] for c in group["candidates"])
            for group in client.get(GROUPS_URL).json()["groups"]
        }

        emitted: list[dict] = []
        real_notify = server.vault.notify

        def _capture(event_type, data=None):
            if isinstance(data, dict) and "change_kind" in data:
                emitted.append({"event": event_type, **data})
            return real_notify(event_type, data)

        server.vault.notify = _capture
        headers = {"X-Client-Id": "tab-1"}

        response = client.post(STACK_URL, json={"signature": stacked}, headers=headers)
        assert response.status_code == 200, response.text
        assert emitted, "a stack verdict must announce itself"
        assert emitted[-1]["change_kind"] == "updated"
        assert emitted[-1]["picture_ids"] == by_signature[stacked]
        assert emitted[-1]["origin_client_id"] == "tab-1"

        emitted.clear()
        response = client.post(
            KEEP_SEPARATE_URL, json={"signature": kept_separate}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert emitted, "a keep-separate verdict must announce itself"
        assert emitted[-1]["change_kind"] == "updated"
        assert emitted[-1]["picture_ids"] == by_signature[kept_separate]
        assert emitted[-1]["origin_client_id"] == "tab-1"

        # Undo (the keep-separate is newest) and redo announce the same targets
        # through the op-log's own emit, empty recorded diff notwithstanding.
        emitted.clear()
        assert client.post(f"{API}/operations/undo", headers=headers).status_code == 200
        undo_ids = {pid for kind in emitted for pid in (kind.get("picture_ids") or [])}
        assert undo_ids == set(by_signature[kept_separate]), emitted
        assert all("origin_client_id" in kind for kind in emitted)

        emitted.clear()
        assert client.post(f"{API}/operations/redo", headers=headers).status_code == 200
        redo_ids = {pid for kind in emitted for pid in (kind.get("picture_ids") or [])}
        assert redo_ids == set(by_signature[kept_separate]), emitted

        # A clear announces itself too - it changes state other tabs render
        # (the stacked clear also unstacks pictures).
        emitted.clear()
        response = client.post(REOPEN_URL, json={"signature": stacked}, headers=headers)
        assert response.status_code == 200, response.text
        assert emitted, "a clear must announce itself"
        assert emitted[-1]["change_kind"] == "updated"
        assert emitted[-1]["picture_ids"] == by_signature[stacked]
        assert emitted[-1]["origin_client_id"] == "tab-1"
    finally:
        _teardown(temp_dir, server)


# ── keyset paging ─────────────────────────────────────────────────────────────


def test_a_verdict_between_pages_makes_offset_skip_and_the_cursor_not():
    """QA 3: a decided page-1 row shifts every later row's offset by one.

    Both halves are asserted: offset paging demonstrably loses a group (so the
    test cannot pass vacuously), and the cursor delivers it.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=3)
        _rescan(server)
        everything = _signatures(client)
        assert len(everything) == 4

        # Offset paging: read page 1, decide it, read page 2 at offset=2.
        page_one = client.get(GROUPS_URL, params={"limit": 2}).json()
        delivered = [group["signature"] for group in page_one["groups"]]
        assert len(delivered) == 2
        decided = client.post(STACK_URL, json={"signature": delivered[0]})
        assert decided.status_code == 200, decided.text
        offset_page = client.get(GROUPS_URL, params={"limit": 2, "offset": 2}).json()
        seen_by_offset = set(delivered) | {
            group["signature"] for group in offset_page["groups"]
        }
        skipped = everything - seen_by_offset
        assert skipped, "offset paging is expected to skip after a verdict"

        # Same situation, cursor paging: nothing is skipped.
        assert page_one["next_cursor"], page_one
        cursor_page = client.get(
            GROUPS_URL, params={"limit": 2, "cursor": page_one["next_cursor"]}
        ).json()
        seen_by_cursor = set(delivered) | {
            group["signature"] for group in cursor_page["groups"]
        }
        assert skipped <= seen_by_cursor
        assert seen_by_cursor == everything
    finally:
        _teardown(temp_dir, server)


def test_the_cursor_walks_every_group_when_confidences_tie():
    """Exact groups all sit at the same confidence, so the tie-break is the id.

    ``confidence < c`` alone would drop the rest of the tied run; ``<=`` would
    repeat it forever. Walking one row at a time exercises the boundary on every
    step.
    """
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        _add_exact_groups(server, count=4)
        _rescan(server)
        everything = _signatures(client)
        assert len(everything) == 5
        confidences = {
            group["confidence"] for group in client.get(GROUPS_URL).json()["groups"]
        }
        assert len(confidences) == 1, "the tie-break is what is under test"

        walked = []
        cursor = None
        for _ in range(len(everything) + 2):
            params = {"limit": 1}
            if cursor:
                params["cursor"] = cursor
            body = client.get(GROUPS_URL, params=params).json()
            walked.extend(group["signature"] for group in body["groups"])
            cursor = body["next_cursor"]
            if not cursor:
                break
        assert cursor is None, "paging must terminate"
        assert len(walked) == len(set(walked)), "no group is delivered twice"
        assert set(walked) == everything
    finally:
        _teardown(temp_dir, server)


def test_cursor_and_offset_together_are_rejected():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        cursor = client.get(GROUPS_URL, params={"limit": 1}).json()["next_cursor"]
        response = client.get(GROUPS_URL, params={"cursor": cursor or "x", "offset": 0})
        assert response.status_code == 400, response.text
        assert "mutually exclusive" in response.text
    finally:
        _teardown(temp_dir, server)


def test_a_malformed_cursor_is_a_400_not_a_silent_restart():
    """Silently paging from the top would hand the client page 1 forever."""
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        import base64 as _b64

        non_finite = [
            _b64.urlsafe_b64encode(f"1|{value}|0".encode()).decode().rstrip("=")
            for value in ("inf", "-inf", "nan")
        ]
        # CSO R6: float() parses "inf"/"nan"; a non-finite confidence makes the
        # keyset predicate match everything - the silent restart-from-the-top
        # this endpoint's contract refuses.
        for bad in ("not-base64!!", "AAAA", "MXwxLjB8", *non_finite):
            response = client.get(GROUPS_URL, params={"cursor": bad})
            assert response.status_code == 400, (bad, response.text)
    finally:
        _teardown(temp_dir, server)


# ── batch id namespacing and folder scope normalisation ───────────────────────


def test_a_server_shaped_batch_id_from_a_client_is_rejected():
    """CSO M1: a verbatim body batch_id let a client impersonate a server batch."""
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        for bad in ("srv-deadbeef", "batch-42", "cli-ab", "cli-" + "a" * 200, ""):
            response = client.post(
                STACK_URL, json={"signature": signature, "batch_id": bad}
            )
            assert response.status_code == 400, (bad, response.text)
            assert (
                client.post(
                    KEEP_SEPARATE_URL, json={"signature": signature, "batch_id": bad}
                ).status_code
                == 400
            ), bad
            assert (
                client.post(
                    AUTO_STACK_URL, json={"dry_run": False, "batch_id": bad}
                ).status_code
                == 400
            ), bad
        # Nothing was decided by any of the rejected calls.
        assert client.post(COUNTS_URL, json={}).json()["unresolved_groups"] == 1

        # A client-namespaced id is accepted and used verbatim.
        response = client.post(
            STACK_URL, json={"signature": signature, "batch_id": "cli-gesture-01"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["batch_id"] == "cli-gesture-01"
    finally:
        _teardown(temp_dir, server)


def test_a_server_minted_batch_id_is_namespaced():
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        signature = _signature(client)
        response = client.post(STACK_URL, json={"signature": signature})
        assert response.status_code == 200, response.text
        assert response.json()["batch_id"].startswith("srv-")
    finally:
        _teardown(temp_dir, server)


def test_a_folder_scope_that_normalises_to_nothing_is_rejected():
    """CSO W2: "/" rstripped to "" and became a LIKE of "%" - silently global."""
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        for bad in ("/", "\\", "///", "\\\\", "/\\/"):
            body = {"scope_type": "folder", "scope_id": bad}
            assert (
                client.get(
                    GROUPS_URL, params={"scope_type": "folder", "scope_id": bad}
                ).status_code
                == 400
            ), bad
            assert client.post(COUNTS_URL, json={"scopes": [body]}).status_code == 400
            assert client.post(SCAN_URL, json={"scope": body}).status_code == 400
            assert client.post(AUTO_STACK_URL, json={"scope": body}).status_code == 400
        # No poison scan row was persisted by the rejected requests.
        assert _run(server, lambda session: session.exec(select(DedupScan)).all()) == []

        # A real folder still works, with or without a trailing separator, and
        # both spellings are the same scope.
        for spelling in ("/vault", "/vault/"):
            counts = client.post(
                COUNTS_URL,
                json={"scopes": [{"scope_type": "folder", "scope_id": spelling}]},
            )
            assert counts.status_code == 200, counts.text
            assert counts.json()["scopes"][0]["unresolved_groups"] == 1
            assert counts.json()["scopes"][0]["key"] == "folder:/vault"
    finally:
        _teardown(temp_dir, server)


# ── stack units: the deck and its lazy expansion ──────────────────────────────


def _stack_over_http(server, picture_ids, thumbnails=None):
    """Stack *picture_ids* in order by hand; return the stack id."""

    def build(session):
        stack = PictureStack(name=None)
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for position, picture_id in enumerate(picture_ids):
            picture = session.get(Picture, int(picture_id))
            picture.stack_id = int(stack.id)
            picture.stack_position = position
            size = (thumbnails or {}).get(int(picture_id))
            if size is not None:
                picture.thumbnail_width, picture.thumbnail_height = size
            session.add(picture)
        session.commit()
        return int(stack.id)

    return _run(server, build)


def test_a_queue_row_carries_the_stack_truth_behind_each_deck():
    """A `stack_id` alone cannot render a deck, so every group ships a `stacks`
    block with the stack's REAL depth and its leader.

    Picture 2 (unique) leads a stack that also contains picture 0, one half of
    the exact pair. The group therefore names ONE member of a 2-stack, and the
    row has to say the deck is 2 deep and led by a picture the group never
    mentions.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        stack_id = _stack_over_http(
            server, [ids[2], ids[0]], thumbnails={ids[2]: (800, 600)}
        )
        body = client.get(GROUPS_URL).json()
        assert len(body["groups"]) == 1, body
        group = body["groups"][0]
        assert sorted(c["picture_id"] for c in group["candidates"]) == sorted(ids[:2])

        assert list(group["stacks"]) == [str(stack_id)]
        deck = group["stacks"][str(stack_id)]
        assert deck == {
            "stack_id": stack_id,
            "member_count": 2,
            "leader_picture_id": ids[2],
            "leader_thumbnail_version": "800x600",
            "matched_picture_ids": [ids[0]],
            "stackable": True,
            "blocked_by_sets": [],
        }
        # The loose half of the pair is its own unit and adds no entry.
        assert len(group["stacks"]) == 1
    finally:
        _teardown(temp_dir, server)


def test_a_group_with_no_stacked_member_carries_an_empty_stacks_block():
    """The field is always present, so the client never branches on absence."""
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        group = client.get(GROUPS_URL).json()["groups"][0]
        assert group["stacks"] == {}
    finally:
        _teardown(temp_dir, server)


def test_the_owner_expands_a_deck_and_a_scoped_token_cannot():
    """Both directions on the new route: the owner gets the stack's members,
    a resource-scoped READ token gets 403 on the same URL.

    Over-blocking would be its own regression, so the positive half asserts a
    complete answer rather than merely a non-403.
    """
    temp_dir, client, server, ids, token, _set_id = _env()
    try:
        stack_id = _stack_over_http(
            server, [ids[2], ids[0]], thumbnails={ids[2]: (800, 600)}
        )
        url = _stack_members_url(stack_id)

        response = client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stack_id"] == stack_id
        assert body["member_count"] == 2
        assert body["leader_picture_id"] == ids[2]
        assert body["leader_thumbnail_version"] == "800x600"
        assert body["stackable"] is True
        assert body["blocked_by_sets"] == []
        assert body["offset"] == 0
        assert body["next_offset"] is None
        assert [m["picture_id"] for m in body["members"]] == [ids[2], ids[0]]
        assert [m["position"] for m in body["members"]] == [0, 1]
        assert [m["is_leader"] for m in body["members"]] == [True, False]
        # The tile fields are the queue candidate's, so the strip reuses it.
        leader = body["members"][0]
        assert leader["thumbnail_version"] == "800x600"
        assert leader["why"] == []
        assert leader["stackable"] is True

        scoped = TestClient(server.api)
        assert (
            scoped.get(url, headers={"Authorization": f"Bearer {token}"}).status_code
            == 403
        )
        assert scoped.get(url, params={"token": token}).status_code == 403
        # ids[0] IS inside the token's granted set, so this is a refusal of the
        # route, not an accident of which pictures the token can reach.
        assert ids[0] in [m["picture_id"] for m in body["members"]]
    finally:
        _teardown(temp_dir, server)


def test_expanding_a_deck_pages_and_clamps():
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        extra = _insert_pictures(server, [{"pixel_sha": f"deck-{i}"} for i in range(3)])
        stack_id = _stack_over_http(server, [ids[2], *extra])
        url = _stack_members_url(stack_id)

        first = client.get(url, params={"limit": 2}).json()
        assert [m["picture_id"] for m in first["members"]] == [ids[2], extra[0]]
        assert first["limit"] == 2
        assert first["next_offset"] == 2

        second = client.get(url, params={"limit": 2, "offset": first["next_offset"]})
        assert second.status_code == 200
        assert [m["picture_id"] for m in second.json()["members"]] == extra[1:]
        assert second.json()["next_offset"] is None
        assert [m["position"] for m in second.json()["members"]] == [2, 3]

        # The cap is a 422 at the boundary, not a silent clamp.
        over = client.get(url, params={"limit": tiers.MAX_STACK_MEMBER_PAGE_SIZE + 1})
        assert over.status_code == 422, over.text
        assert client.get(url, params={"offset": -1}).status_code == 422
    finally:
        _teardown(temp_dir, server)


def test_expanding_a_stack_with_no_live_members_is_a_404():
    """Never an empty stack that appears to exist."""
    temp_dir, client, server, _ids, _token, _set_id = _env()
    try:
        assert client.get(_stack_members_url(987654)).status_code == 404
    finally:
        _teardown(temp_dir, server)


def test_a_locked_sibling_outside_the_group_makes_the_whole_deck_unstackable():
    """A stack cannot be partially stacked, so one frozen member freezes the
    deck: even a member the group never names.

    The group keeps two other stackable units (the two loose duplicates), so it
    is still served; the deck inside it is marked instead of the row vanishing.
    """
    temp_dir, client, server, ids, _token, _set_id = _env()
    try:
        loose = _insert_pictures(
            server, [{"pixel_sha": "aaa", "size_bytes": 100}]
        )  # a third copy of the exact pair
        stack_id = _stack_over_http(server, [ids[2], ids[0]])

        def lock_the_leader(session):
            frozen = PictureSet(name="Frozen", locked=True)
            session.add(frozen)
            session.commit()
            session.refresh(frozen)
            session.add(PictureSetMember(set_id=int(frozen.id), picture_id=ids[2]))
            session.commit()
            return int(frozen.id)

        set_id = _run(server, lock_the_leader)
        _run(server, tiers.run_scan_now_in_session, TierPolicy(), None)

        groups = client.get(GROUPS_URL).json()["groups"]
        assert len(groups) == 1, groups
        deck = groups[0]["stacks"][str(stack_id)]
        assert deck["matched_picture_ids"] == [ids[0]]
        assert deck["stackable"] is False
        assert deck["blocked_by_sets"] == [{"id": set_id, "name": "Frozen"}]

        # The two loose units are untouched: over-blocking is its own regression.
        by_id = {c["picture_id"]: c for c in groups[0]["candidates"]}
        assert by_id[ids[1]]["stackable"] is True
        assert by_id[loose[0]]["stackable"] is True

        # The expansion reports the same unit-level verdict.
        expanded = client.get(_stack_members_url(stack_id)).json()
        assert expanded["stackable"] is False
        assert expanded["blocked_by_sets"] == [{"id": set_id, "name": "Frozen"}]
        assert all(member["stackable"] is False for member in expanded["members"])
    finally:
        _teardown(temp_dir, server)

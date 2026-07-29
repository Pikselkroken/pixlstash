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
from pixlstash.db_models import Picture, PictureSet, PictureSetMember
from pixlstash.db_models.dedup import DedupScan, DedupVerdict
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from pixlstash.services import dedup_tier_service as tiers
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


# ── resource hardening ────────────────────────────────────────────────────────


def test_a_non_numeric_scope_id_is_a_400_on_every_route():
    """Regression for the CSO's D4.

    ``picture_predicate()`` calls ``int(scope_id)`` for project / set / character.
    Leaving that unvalidated turned a bad request into an unhandled 500 on three
    read routes, and `POST /dedup/scan` returned 200 while **persisting** the
    unparseable scope — a self-inflicted poison row that made every later
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


def test_every_candidate_carries_a_thumbnail_cache_token():
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
        token = next(
            c["thumbnail_version"] for c in candidates if c["picture_id"] == ids[0]
        )
        # Exactly the token the batch-thumbnail endpoint puts in its ?v=.
        assert token == ImageUtils.thumbnail_cache_token(320, 240) == "320x240"
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
    """R2 at the HTTP boundary: a 423 mid-run must not swallow the undo handle."""
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
        assert body["blocked"] == 1
        assert body["failures"][0]["status_code"] == 423
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
    """The hook is scoped to the undone batch, not to every decided group."""
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
        for bad in ("not-base64!!", "AAAA", "MXwxLjB8"):
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

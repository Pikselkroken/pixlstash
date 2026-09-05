"""API tests for the near-duplicate sweep dry-run surface.

Both routes are declared ``OWNER_ONLY`` in ``pixlstash/authz/registry.py`` and
enforced by the central authz gate, so these assert **both directions** per the
CLAUDE.md security review process:

* negative - a resource-scoped READ share token gets 403 on both routes;
* positive - the owner cookie session gets 200 and a complete report
  (over-blocking is its own regression).

Plus the contract the future UI reads: the policy is a request parameter object,
the report carries the auto/review split with reason codes, a group spanning
several stacks is reported rather than skipped, the Lane-B batch id round-trips,
and an invalid policy is a 400 rather than a silently retuned sweep.

Background workers are disabled and the pictures are inserted directly. A real
upload would let the likeness worker write its own ``PictureLikeness`` rows a few
seconds later (measured: three uploaded PNGs produce three pairs at 0.90-0.96),
which silently changes group membership and every count under assertion here.
"""

import gc
import json
import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureSetMember, PictureStack
from pixlstash.db_models.picture_likeness import PictureLikeness
from pixlstash.server import Server
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"
POLICY_URL = f"{API}/dedup/sweep/policy"
DRY_RUN_URL = f"{API}/dedup/sweep/dry-run"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL could make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _run(server, fn, *args):
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _insert_pictures(server, specs):
    """Insert one minimal ``Picture`` row per spec; return the ids in order."""

    def insert(session):
        picture_ids = []
        for spec in specs:
            token = uuid.uuid4().hex
            pic = Picture(
                file_path=f"/tmp/pixlstash-dedup-test/{token}.png",
                format="png",
                width=64,
                height=64,
                pixel_sha=token,
                score=spec.get("score"),
                smart_score=spec.get("smart_score"),
                size_bytes=spec["size_bytes"],
            )
            session.add(pic)
            session.flush()
            picture_ids.append(int(pic.id))
        session.commit()
        return picture_ids

    return _run(server, insert)


def _link(server, picture_id_a: int, picture_id_b: int, likeness: float) -> None:
    def insert(session):
        first, second = PictureLikeness.canon_pair(picture_id_a, picture_id_b)
        session.add(
            PictureLikeness(
                picture_id_a=first,
                picture_id_b=second,
                likeness=likeness,
                metric="test",
            )
        )
        session.commit()

    _run(server, insert)


def _stack(server, picture_ids: list[int], name: str) -> int:
    def create(session):
        stack = PictureStack(name=name)
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for position, picture_id in enumerate(picture_ids):
            pic = session.get(Picture, picture_id)
            pic.stack_id = stack.id
            pic.stack_position = position
            session.add(pic)
        session.commit()
        return stack.id

    return _run(server, create)


def _add_to_set(server, picture_id: int, set_id: int) -> None:
    def insert(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=picture_id))
        session.commit()

    _run(server, insert)


def _env():
    """Owner cookie client, three seeded pictures, and a set-scoped READ token.

    Pictures 0 and 1 are linked at 0.99 and separated by the human score, so the
    default policy resolves them into exactly one auto-collapse group. Picture 2
    is unlinked, so it never appears in a report.
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
            {"score": 9, "size_bytes": 300},
            {"score": 3, "size_bytes": 700},
            {"score": 1, "size_bytes": 1100},
        ],
    )
    _link(server, picture_ids[0], picture_ids[1], 0.99)

    set_id = client.post(f"{API}/picture_sets", json={"name": "Set A"}).json()[
        "picture_set"
    ]["id"]
    _add_to_set(server, picture_ids[0], set_id)
    token = client.post(
        f"{API}/users/me/token",
        json={
            "description": "set A read",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_id,
        },
    ).json()["token"]
    return temp_dir, client, server, picture_ids, token


def _teardown(temp_dir, server):
    server.close()
    temp_dir.cleanup()
    gc.collect()


# ── authorization, both directions ────────────────────────────────────────────


def test_scoped_read_token_is_denied_on_both_routes():
    temp_dir, _client, server, _picture_ids, token = _env()
    try:
        scoped = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}
        assert scoped.get(POLICY_URL, headers=headers).status_code == 403
        assert scoped.post(DRY_RUN_URL, json={}, headers=headers).status_code == 403
        # Same via the ?token= query-param path (no Authorization header).
        assert scoped.get(POLICY_URL, params={"token": token}).status_code == 403
        assert (
            scoped.post(DRY_RUN_URL, params={"token": token}, json={}).status_code
            == 403
        )
    finally:
        _teardown(temp_dir, server)


def test_unauthenticated_is_denied():
    temp_dir, _client, server, _picture_ids, _token = _env()
    try:
        anonymous = TestClient(server.api)
        assert anonymous.get(POLICY_URL).status_code in (401, 403)
        assert anonymous.post(DRY_RUN_URL, json={}).status_code in (401, 403)
    finally:
        _teardown(temp_dir, server)


def test_owner_reaches_both_routes():
    """The positive direction: over-blocking the owner is its own regression."""
    temp_dir, client, server, picture_ids, _token = _env()
    try:
        policy = client.get(POLICY_URL)
        assert policy.status_code == 200, policy.text
        body = policy.json()
        assert body["defaults"]["likeness_threshold"] == pytest.approx(0.9)
        assert body["defaults"]["cross_stack"] == "report"
        assert body["bounds"]["min_likeness"] == pytest.approx(0.5)
        assert "spans_multiple_stacks" in body["bounds"]["review_reasons"]
        assert set(body["bounds"]["outcomes"]) == {
            "create_stack",
            "add_to_stack",
            "merge_stacks",
        }

        dry_run = client.post(DRY_RUN_URL, json={})
        assert dry_run.status_code == 200, dry_run.text
        report = dry_run.json()
        assert report["scanned_edges"] == 1
        assert report["groups_total"] == 1
        assert report["auto_collapse_groups"] == 1
        assert report["needs_review_groups"] == 0
        assert report["listing_truncated"] is False
        group = report["groups"][0]
        assert group["picture_ids"] == picture_ids[:2]
        assert group["keeper_id"] == picture_ids[0]
        assert group["outcome"] == "create_stack"
        assert group["verdict"] == "auto_collapse"
        assert group["reasons"] == []
        assert group["keeper_margin_basis"] == "score"
    finally:
        _teardown(temp_dir, server)


# ── the policy is a request parameter object ──────────────────────────────────


def test_policy_in_the_body_changes_the_verdict():
    temp_dir, client, server, _picture_ids, _token = _env()
    try:
        # Auto-resolve bar above the 0.99 edge -> the same group needs review.
        strict = client.post(
            DRY_RUN_URL,
            json={
                "policy": {"likeness_threshold": 0.9, "auto_resolve_likeness": 0.995}
            },
        )
        assert strict.status_code == 200, strict.text
        report = strict.json()
        assert report["needs_review_groups"] == 1
        assert report["auto_collapse_groups"] == 0
        assert report["groups"][0]["reasons"] == ["weak_likeness"]
        assert report["reason_counts"] == {"weak_likeness": 1}
        assert report["policy"]["auto_resolve_likeness"] == pytest.approx(0.995)

        # A candidate threshold above the edge drops the group entirely. Raising
        # it past the default auto bar is legal: the unset auto bar follows.
        response = client.post(
            DRY_RUN_URL, json={"policy": {"likeness_threshold": 0.995}}
        )
        assert response.status_code == 200, response.text
        empty = response.json()
        assert empty["policy"]["auto_resolve_likeness"] == pytest.approx(0.995)
        assert empty["scanned_edges"] == 0
        assert empty["groups_total"] == 0
    finally:
        _teardown(temp_dir, server)


def test_auto_bar_below_the_candidate_threshold_is_a_400():
    temp_dir, client, server, _picture_ids, _token = _env()
    try:
        response = client.post(
            DRY_RUN_URL,
            json={"policy": {"likeness_threshold": 0.95, "auto_resolve_likeness": 0.9}},
        )
        assert response.status_code == 400, response.text
        assert "auto_resolve_likeness" in response.json()["detail"]
    finally:
        _teardown(temp_dir, server)


def test_out_of_range_and_unknown_policy_fields_are_rejected():
    temp_dir, client, server, _picture_ids, _token = _env()
    try:
        assert (
            client.post(
                DRY_RUN_URL, json={"policy": {"likeness_threshold": 2.0}}
            ).status_code
            == 422
        )
        assert (
            client.post(DRY_RUN_URL, json={"policy": {"nonsense": 1}}).status_code
            == 422
        )
    finally:
        _teardown(temp_dir, server)


# ── merge-or-report + the Lane-B seam ─────────────────────────────────────────


def test_group_spanning_stacks_is_reported_as_a_merge():
    temp_dir, client, server, picture_ids, _token = _env()
    try:
        left = _stack(server, [picture_ids[0]], "left")
        right = _stack(server, [picture_ids[1]], "right")

        report = client.post(DRY_RUN_URL, json={}).json()
        assert report["groups_total"] == 1
        group = report["groups"][0]
        assert group["outcome"] == "merge_stacks"
        assert group["verdict"] == "needs_review"
        assert group["reasons"] == ["spans_multiple_stacks"]
        assert {group["target_stack_id"], *group["merged_stack_ids"]} == {left, right}
        assert report["outcome_counts"] == {"merge_stacks": 1}

        # The merge disposition moves the very same group into the auto lane.
        merged = client.post(
            DRY_RUN_URL, json={"policy": {"cross_stack": "merge"}}
        ).json()
        assert merged["groups"][0]["verdict"] == "auto_collapse"
        assert merged["auto_collapse_groups"] == 1
    finally:
        _teardown(temp_dir, server)


def test_operation_batch_id_round_trips_and_nothing_is_written():
    temp_dir, client, server, picture_ids, _token = _env()
    try:
        response = client.post(
            DRY_RUN_URL, json={"operation_batch_id": "sweep-batch-7"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["operation_batch_id"] == "sweep-batch-7"

        # Dry run: no stack was created and no picture was stacked.
        def read(session):
            stacks = session.exec(select(PictureStack.id)).all()
            stacked = [
                session.get(Picture, picture_id).stack_id for picture_id in picture_ids
            ]
            return list(stacks), stacked

        stacks, stacked = _run(server, read)
        assert stacks == []
        assert stacked == [None, None, None]
    finally:
        _teardown(temp_dir, server)


def test_held_bytes_reports_the_non_keeper_weight():
    temp_dir, client, server, _picture_ids, _token = _env()
    try:
        report = client.post(DRY_RUN_URL, json={}).json()
        # Keeper is the score-9 picture, so the held weight is the other one's.
        assert report["held_bytes_auto"] == 700
        assert report["held_bytes_review"] == 0
        assert report["groups"][0]["held_bytes"] == 700
    finally:
        _teardown(temp_dir, server)

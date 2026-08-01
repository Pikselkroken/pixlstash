"""Mixed stacks: cohesion scoring, the ``Keep`` dismissal, split and unstack.

A **mixed stack** is a live stack whose members do not form one connected
cluster at the queue's similarity threshold (``docs/design/
mixed-stacks-and-stack-units.md``, D5/B5). These cover the contract in both
directions, because over-listing is its own regression:

* a genuinely mixed stack **is** listed, and a cohesive one is **not**;
* the list is bound to the threshold, not to a constant — the same stack is
  mixed at 0.90 and one clean cluster at 0.65;
* a ``Keep`` drops a stack off the list and a membership change re-raises it;
* split and unstack are each **one** operation, so a single undo puts every
  picture back in its original stack at its original position;
* every new route is ``OWNER_ONLY`` at the central gate — a resource-scoped
  READ token is refused via the ``Authorization`` header *and* via ``?token=``,
  and the owner still reaches all five.

Background workers are disabled and the pictures are inserted directly, so no
worker can rewrite ``perceptual_hash`` underneath the assertions.

The hashes are chosen so the Hamming distances are exact and legible.
``max_hamming = int((1 - threshold) * 64)`` — 6 at 0.90, 22 at 0.65:

===============  ==========================  ===============================
name             hex                         popcount vs ``_H_ZERO``
===============  ==========================  ===============================
``_H_ZERO``      ``0000000000000000``        0
``_H_NEAR``      ``0000000000000001``        1   (edge at every threshold)
``_H_MID``       ``00000000000003ff``        10  (edge at 0.65 only)
``_H_FAR``       ``ffffffff00000000``        32  (edge at no threshold)
===============  ==========================  ===============================
"""

import gc
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureSetMember, PictureStack
from pixlstash.db_models.mixed_stack import MixedStackDismissal, StackCohesion
from pixlstash.server import Server
from pixlstash.services import mixed_stack_service
from pixlstash.tasks.missing_stack_cohesion_finder import MissingStackCohesionFinder
from pixlstash.tasks.stack_cohesion_task import StackCohesionTask
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"
MIXED_URL = f"{API}/dedup/mixed-stacks"
UNDO_URL = f"{API}/operations/undo"

_H_ZERO = "0000000000000000"
_H_NEAR = "0000000000000001"
_H_MID = "00000000000003ff"
_H_FAR = "ffffffff00000000"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL could make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _split_url(stack_id) -> str:
    return f"{MIXED_URL}/{stack_id}/split"


def _unstack_url(stack_id) -> str:
    return f"{MIXED_URL}/{stack_id}/unstack"


def _keep_url(stack_id) -> str:
    return f"{MIXED_URL}/{stack_id}/keep"


def _run(server, fn, *args):
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _make_stack(server, hashes: list[str]) -> tuple[int, list[int]]:
    """Insert one stack whose members carry *hashes*, in order. Leader first."""

    def insert(session):
        stack = PictureStack(name=None)
        session.add(stack)
        session.flush()
        picture_ids = []
        for position, phash in enumerate(hashes):
            picture = Picture(
                file_path=f"/vault/mixed_{int(stack.id)}_{position}.png",
                format="png",
                width=1000,
                height=1000,
                size_bytes=1000,
                perceptual_hash=phash,
                stack_id=int(stack.id),
                stack_position=position,
            )
            session.add(picture)
            session.flush()
            picture_ids.append(int(picture.id))
        session.commit()
        return int(stack.id), picture_ids

    return _run(server, insert)


def _stack_state(server, picture_ids: list[int]) -> dict[int, tuple]:
    """``{picture_id: (stack_id, stack_position)}`` for the given pictures."""

    def read(session):
        rows = session.exec(
            select(Picture.id, Picture.stack_id, Picture.stack_position).where(
                Picture.id.in_(picture_ids)
            )
        ).all()
        return {int(pid): (sid, pos) for pid, sid, pos in rows}

    return _run(server, read)


def _env():
    """Owner cookie client plus a resource-scoped READ share token.

    Three stacks, each testing one thing:

    * ``cohesive`` — three members within one bit of each other. One cluster at
      every threshold; **must never be listed**.
    * ``mixed`` — a tight pair and a picture 32 bits away. Two components at
      every threshold, with exactly one stranded member, so its suggested
      action is ``split``.
    * ``threshold`` — two members ten bits apart. Two components at 0.90, one
      at 0.65: the same stack, two answers, which is the point of D5's
      "bind the list to the threshold, never a constant".
    """
    temp_dir = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as handle:
        handle.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    Server.DEFAULT_FORCE_CPU = True
    server = Server(config_path)
    client = TestClient(server.api)
    assert (
        client.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        ).status_code
        == 200
    )

    cohesive_id, cohesive_pics = _make_stack(server, [_H_ZERO, _H_NEAR, _H_NEAR])
    mixed_id, mixed_pics = _make_stack(server, [_H_ZERO, _H_NEAR, _H_FAR])
    threshold_id, threshold_pics = _make_stack(server, [_H_ZERO, _H_MID])

    set_id = client.post(f"{API}/picture_sets", json={"name": "Set A"}).json()[
        "picture_set"
    ]["id"]

    def add_to_set(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=cohesive_pics[0]))
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

    stacks = {
        "cohesive": (cohesive_id, cohesive_pics),
        "mixed": (mixed_id, mixed_pics),
        "threshold": (threshold_id, threshold_pics),
    }
    return temp_dir, client, server, stacks, token


def _teardown(temp_dir, server):
    server.vault.close()
    temp_dir.cleanup()
    gc.collect()


def _rows_by_stack(body) -> dict:
    return {row["stack_id"]: row for row in body["stacks"]}


# ── authorization, both directions ───────────────────────────────────────────


def test_scoped_read_token_is_denied_on_every_mixed_stack_route():
    """Negative direction: the gate refuses a scoped token on all five routes.

    Both reachability paths, because the ``?token=`` query parameter is a
    separate entry point from the ``Authorization`` header and a gate that
    covered only one would be a hole rather than a policy.
    """
    temp_dir, client, server, stacks, token = _env()
    try:
        stack_id = stacks["mixed"][0]
        scoped = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}

        assert scoped.get(MIXED_URL, headers=headers).status_code == 403
        assert (
            scoped.post(_split_url(stack_id), json={}, headers=headers).status_code
            == 403
        )
        assert (
            scoped.post(_unstack_url(stack_id), json={}, headers=headers).status_code
            == 403
        )
        assert scoped.post(_keep_url(stack_id), headers=headers).status_code == 403
        assert scoped.delete(_keep_url(stack_id), headers=headers).status_code == 403

        assert scoped.get(MIXED_URL, params={"token": token}).status_code == 403
        assert (
            scoped.post(
                _split_url(stack_id), params={"token": token}, json={}
            ).status_code
            == 403
        )
        assert (
            scoped.post(
                _unstack_url(stack_id), params={"token": token}, json={}
            ).status_code
            == 403
        )
        assert (
            scoped.post(_keep_url(stack_id), params={"token": token}).status_code == 403
        )
        assert (
            scoped.delete(_keep_url(stack_id), params={"token": token}).status_code
            == 403
        )
    finally:
        _teardown(temp_dir, server)


def test_scoped_read_token_denial_is_fail_closed_before_any_write():
    """Fail-closed, not fail-late: the refused split changed nothing."""
    temp_dir, client, server, stacks, token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        before = _stack_state(server, picture_ids)
        scoped = TestClient(server.api)
        assert (
            scoped.post(
                _split_url(stack_id),
                json={},
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 403
        )
        assert (
            scoped.post(
                _unstack_url(stack_id),
                json={},
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 403
        )
        assert _stack_state(server, picture_ids) == before
    finally:
        _teardown(temp_dir, server)


def test_owner_reaches_every_mixed_stack_route():
    """Positive direction: over-blocking is its own regression."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id = stacks["mixed"][0]
        assert client.get(MIXED_URL).status_code == 200
        assert client.post(_keep_url(stack_id)).status_code == 200
        assert client.delete(_keep_url(stack_id)).status_code == 200
        assert client.post(_split_url(stack_id), json={}).status_code == 200
        # The split above dissolved nothing, so the remainder is still a stack.
        assert (
            client.post(_unstack_url(stacks["cohesive"][0]), json={}).status_code == 200
        )
    finally:
        _teardown(temp_dir, server)


# ── the list: both directions ────────────────────────────────────────────────


def test_mixed_stack_is_listed_and_cohesive_stack_is_not():
    """The whole point, in both directions.

    A stack whose members do not connect is listed with the numbers behind the
    claim; a stack whose members do connect is absent. A list that flagged the
    cohesive one would be a warning field, which is exactly what D5 refuses.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        body = client.get(MIXED_URL, params={"threshold": 0.90}).json()
        rows = _rows_by_stack(body)

        assert stacks["cohesive"][0] not in rows, (
            "a stack whose members are one bit apart is one cluster and must "
            f"never be listed: {body}"
        )

        mixed_id, mixed_pics = stacks["mixed"]
        assert mixed_id in rows
        row = rows[mixed_id]
        assert row["component_count"] == 2
        assert row["component_sizes"] == [2, 1]
        assert row["stranded_picture_ids"] == [mixed_pics[2]]
        assert row["largest_component_size"] == 2
        assert row["suggested_action"] == "split"
        assert row["unhashed_picture_ids"] == []
        assert row["member_count"] == 3
        assert row["leader_picture_id"] == mixed_pics[0]
        # The tight pair is 1 bit apart -> 1 - 1/64.
        assert row["weakest_edge"] == pytest.approx(1.0 - 1.0 / 64.0)
        assert body["live_stack_count"] == 3
    finally:
        _teardown(temp_dir, server)


def test_the_list_follows_the_threshold_rather_than_a_constant():
    """Same stack, two answers — D5's "bind it to the slider" requirement."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id = stacks["threshold"][0]

        strict = _rows_by_stack(
            client.get(MIXED_URL, params={"threshold": 0.90}).json()
        )
        assert stack_id in strict
        assert strict[stack_id]["component_count"] == 2
        assert strict[stack_id]["weakest_edge"] is None, (
            "no pair is close enough to be an edge at 0.90, so there is no "
            "weakest edge to report"
        )

        loose = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.65}).json())
        assert stack_id not in loose, (
            "ten bits apart is inside the 0.65 cut (max_hamming 22), so these "
            "two members are one cluster and the stack is not mixed"
        )
        # The genuinely mixed stack (32 bits) survives the loosening.
        assert stacks["mixed"][0] in loose
    finally:
        _teardown(temp_dir, server)


def test_ranking_puts_the_least_held_together_stack_first():
    """Stranded members desc, component count desc, weakest edge asc."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        # A third mixed stack with TWO stranded members outranks the one with
        # a single stranger, whatever their weakest edges say.
        worse_id, _pics = _make_stack(server, [_H_ZERO, _H_FAR])
        body = client.get(MIXED_URL, params={"threshold": 0.90}).json()
        order = [row["stack_id"] for row in body["stacks"]]
        assert order.index(worse_id) < order.index(stacks["mixed"][0]), order
    finally:
        _teardown(temp_dir, server)


def test_a_member_without_a_perceptual_hash_is_reported_not_stranded():
    """ "Not yet comparable" is a different fact from "does not belong"."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]

        def clear_hash(session):
            picture = session.get(Picture, picture_ids[2])
            picture.perceptual_hash = None
            session.add(picture)
            session.commit()

        _run(server, clear_hash)
        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert row["unhashed_picture_ids"] == [picture_ids[2]]
        assert row["stranded_picture_ids"] == [picture_ids[2]]
    finally:
        _teardown(temp_dir, server)


# ── the Keep dismissal ───────────────────────────────────────────────────────


def test_keep_drops_the_stack_and_a_membership_change_re_raises_it():
    """The dismissal is keyed on membership, not just on the stack id."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        assert stack_id in _rows_by_stack(client.get(MIXED_URL).json())

        kept = client.post(_keep_url(stack_id))
        assert kept.status_code == 200
        assert kept.json()["created"] is True
        fingerprint = kept.json()["membership_fingerprint"]

        body = client.get(MIXED_URL).json()
        assert stack_id not in _rows_by_stack(body)
        assert body["kept_total"] == 1

        # include_kept brings it back, marked, rather than hiding it from a
        # client that wants to review its own dismissals.
        shown = _rows_by_stack(
            client.get(MIXED_URL, params={"include_kept": True}).json()
        )
        assert shown[stack_id]["kept"] is True

        # Idempotent: pressing Keep again writes nothing.
        again = client.post(_keep_url(stack_id))
        assert again.status_code == 200
        assert again.json()["created"] is False

        # Adding a member changes the fingerprint, so no dismissal matches.
        def add_member(session):
            picture = Picture(
                file_path="/vault/mixed_extra.png",
                format="png",
                width=1000,
                height=1000,
                size_bytes=1000,
                perceptual_hash=_H_FAR,
                stack_id=stack_id,
                stack_position=3,
            )
            session.add(picture)
            session.commit()
            return int(picture.id)

        _run(server, add_member)
        after = client.get(MIXED_URL).json()
        assert stack_id in _rows_by_stack(after), (
            "adding a member must re-raise a kept stack: the user approved "
            "those pictures, not every future version of the stack"
        )
        assert after["kept_total"] == 0
        assert _rows_by_stack(after)[stack_id]["membership_fingerprint"] != fingerprint
    finally:
        _teardown(temp_dir, server)


def test_deleting_the_keep_lists_the_stack_again():
    """The way back from a mis-pressed Keep, and it is idempotent."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id = stacks["mixed"][0]
        client.post(_keep_url(stack_id))
        assert stack_id not in _rows_by_stack(client.get(MIXED_URL).json())

        cleared = client.delete(_keep_url(stack_id))
        assert cleared.status_code == 200
        assert cleared.json()["removed"] == 1
        assert cleared.json()["dismissed"] is False
        assert stack_id in _rows_by_stack(client.get(MIXED_URL).json())

        # Clearing a stack that was never kept is a no-op, not an error.
        assert client.delete(_keep_url(stack_id)).json()["removed"] == 0
    finally:
        _teardown(temp_dir, server)


def test_keep_on_a_stack_with_no_live_members_is_a_400():
    temp_dir, client, server, _stacks, _token = _env()
    try:
        response = client.post(_keep_url(999999))
        assert response.status_code == 400
        assert "no live members" in response.json()["detail"]
    finally:
        _teardown(temp_dir, server)


# ── split and unstack, and their undo ────────────────────────────────────────


def test_split_removes_the_stranded_member_and_is_undoable_in_one_step():
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        before = _stack_state(server, picture_ids)

        response = client.post(_split_url(stack_id), json={"threshold": 0.90})
        assert response.status_code == 200
        payload = response.json()
        assert payload["split_picture_ids"] == [picture_ids[2]]
        assert payload["remaining_picture_ids"] == sorted(picture_ids[:2])
        assert payload["stack_dissolved"] is False
        assert payload["batch_id"]

        after = _stack_state(server, picture_ids)
        assert after[picture_ids[2]] == (None, None)
        assert after[picture_ids[0]][0] == stack_id
        assert after[picture_ids[1]][0] == stack_id
        # The stack is no longer mixed, so it leaves the list.
        assert stack_id not in _rows_by_stack(client.get(MIXED_URL).json())

        assert client.post(UNDO_URL, json={}).status_code == 200
        assert _stack_state(server, picture_ids) == before, (
            "one split is one operation, so a single undo must restore every "
            "picture's stack id AND its position"
        )
        assert stack_id in _rows_by_stack(client.get(MIXED_URL).json())
    finally:
        _teardown(temp_dir, server)


def test_split_honours_an_explicit_picture_id_list():
    """The client sends the ids the row showed, so the split matches the row."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["cohesive"]
        response = client.post(
            _split_url(stack_id), json={"picture_ids": [picture_ids[1]]}
        )
        assert response.status_code == 200
        assert response.json()["split_picture_ids"] == [picture_ids[1]]
        assert response.json()["remaining_picture_ids"] == sorted(
            [picture_ids[0], picture_ids[2]]
        )
    finally:
        _teardown(temp_dir, server)


def test_split_that_would_leave_one_member_dissolves_the_stack_and_says_so():
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["threshold"]
        before = _stack_state(server, picture_ids)
        response = client.post(
            _split_url(stack_id), json={"picture_ids": [picture_ids[0]]}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["stack_dissolved"] is True
        assert payload["remaining_picture_ids"] == []
        assert sorted(payload["split_picture_ids"]) == sorted(picture_ids)

        def stack_row(session):
            return session.get(PictureStack, stack_id)

        assert _run(server, stack_row) is None

        # Undo recreates the stack row under its original id.
        assert client.post(UNDO_URL, json={}).status_code == 200
        assert _stack_state(server, picture_ids) == before
        assert _run(server, stack_row) is not None
    finally:
        _teardown(temp_dir, server)


def test_split_with_nothing_stranded_is_a_400():
    temp_dir, client, server, stacks, _token = _env()
    try:
        response = client.post(
            _split_url(stacks["cohesive"][0]), json={"threshold": 0.90}
        )
        assert response.status_code == 400
        assert "stranded" in response.json()["detail"]
    finally:
        _teardown(temp_dir, server)


def test_split_naming_no_live_member_is_a_400_and_writes_nothing():
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        before = _stack_state(server, picture_ids)
        response = client.post(_split_url(stack_id), json={"picture_ids": [999999]})
        assert response.status_code == 400
        assert _stack_state(server, picture_ids) == before
    finally:
        _teardown(temp_dir, server)


def test_unstack_dissolves_the_stack_and_is_undoable_in_one_step():
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        before = _stack_state(server, picture_ids)

        response = client.post(_unstack_url(stack_id), json={})
        assert response.status_code == 200
        payload = response.json()
        assert sorted(payload["split_picture_ids"]) == sorted(picture_ids)
        assert payload["remaining_picture_ids"] == []
        assert payload["stack_dissolved"] is True
        assert payload["batch_id"]
        assert all(
            state == (None, None)
            for state in _stack_state(server, picture_ids).values()
        )

        undo = client.post(
            f"{API}/operations/batches/{payload['batch_id']}/undo", json={}
        )
        assert undo.status_code == 200, undo.text
        assert _stack_state(server, picture_ids) == before
    finally:
        _teardown(temp_dir, server)


def test_unstack_of_a_stack_with_no_live_members_is_a_400():
    temp_dir, client, server, _stacks, _token = _env()
    try:
        response = client.post(_unstack_url(999999), json={})
        assert response.status_code == 400
    finally:
        _teardown(temp_dir, server)


# ── the cohesion cache and its finder ────────────────────────────────────────


def test_the_cache_is_keyed_on_its_inputs_and_the_finder_refreshes_it():
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]

        def stale(session):
            return mixed_stack_service.stale_cohesion_stack_ids_in_session(session, 100)

        assert sorted(_run(server, stale)) == sorted(
            [entry[0] for entry in stacks.values()]
        ), "every stack starts with no cache row, so every stack is stale"

        finder = MissingStackCohesionFinder(database=server.vault.db)
        task = finder.find_task()
        assert isinstance(task, StackCohesionTask)
        result = task.run()
        assert result["changed_count"] == 3
        assert _run(server, stale) == []
        assert finder.find_task() is None, "nothing left to do once the cache is warm"

        def cached_rows(session):
            return {
                int(row.stack_id): row.content_fingerprint
                for row in session.exec(select(StackCohesion)).all()
            }

        fingerprints = _run(server, cached_rows)
        assert set(fingerprints) == {entry[0] for entry in stacks.values()}

        # A cached answer must equal the uncached one, or the cache is a bug
        # generator rather than a cache.
        cached_body = _rows_by_stack(client.get(MIXED_URL).json())
        assert cached_body[stack_id]["component_sizes"] == [2, 1]
        assert cached_body[stack_id]["stranded_picture_ids"] == [picture_ids[2]]

        # A membership change invalidates by construction: the fingerprint moves.
        def drop_member(session):
            picture = session.get(Picture, picture_ids[2])
            picture.stack_id = None
            picture.stack_position = None
            session.add(picture)
            session.commit()

        _run(server, drop_member)
        assert _run(server, stale) == [stack_id]
        assert stack_id not in _rows_by_stack(client.get(MIXED_URL).json()), (
            "the list must recompute a stale stack inline rather than serve the "
            "cached (now wrong) answer"
        )
    finally:
        _teardown(temp_dir, server)


def test_a_hash_arriving_after_the_cache_invalidates_it():
    """The cache key covers the hashes, not only the membership.

    The embedding worker fills ``perceptual_hash`` for a picture that had none.
    Membership does not move, so a membership-keyed cache would go on reporting
    that member as stranded forever — the exact false positive the flag must
    never produce.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["cohesive"]

        def clear_hash(session):
            picture = session.get(Picture, picture_ids[2])
            picture.perceptual_hash = None
            session.add(picture)
            session.commit()

        _run(server, clear_hash)
        MissingStackCohesionFinder(database=server.vault.db).find_task().run()
        row = _rows_by_stack(client.get(MIXED_URL).json())[stack_id]
        assert row["stranded_picture_ids"] == [picture_ids[2]]
        assert row["unhashed_picture_ids"] == [picture_ids[2]]

        def stale(session):
            return mixed_stack_service.stale_cohesion_stack_ids_in_session(session, 100)

        assert _run(server, stale) == [], "the cache is warm for this membership"

        def set_hash(session):
            picture = session.get(Picture, picture_ids[2])
            picture.perceptual_hash = _H_NEAR
            session.add(picture)
            session.commit()

        _run(server, set_hash)
        assert _run(server, stale) == [stack_id], (
            "a hash arriving without a membership change must still invalidate "
            "the cached edge list"
        )
        assert stack_id not in _rows_by_stack(client.get(MIXED_URL).json()), (
            "with its hash in place the member connects, so the stack is one "
            "cluster again and must leave the list"
        )
    finally:
        _teardown(temp_dir, server)


def test_dissolving_a_stack_takes_its_cache_and_dismissals_with_it():
    """Cascade FK hygiene: neither table outlives the stack it describes."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, _picture_ids = stacks["threshold"]
        assert client.post(_keep_url(stack_id)).status_code == 200
        MissingStackCohesionFinder(database=server.vault.db).find_task().run()

        def counts(session):
            return (
                len(
                    session.exec(
                        select(StackCohesion).where(StackCohesion.stack_id == stack_id)
                    ).all()
                ),
                len(
                    session.exec(
                        select(MixedStackDismissal).where(
                            MixedStackDismissal.stack_id == stack_id
                        )
                    ).all()
                ),
            )

        assert _run(server, counts) == (1, 1)
        assert client.post(_unstack_url(stack_id), json={}).status_code == 200
        assert _run(server, counts) == (0, 0)
    finally:
        _teardown(temp_dir, server)


def test_membership_fingerprint_is_order_independent_and_membership_sensitive():
    assert mixed_stack_service.membership_fingerprint(
        [3, 1, 2]
    ) == mixed_stack_service.membership_fingerprint([1, 2, 3])
    assert mixed_stack_service.membership_fingerprint(
        [1, 2, 3]
    ) != mixed_stack_service.membership_fingerprint([1, 2, 3, 4])


def test_content_fingerprint_moves_when_a_hash_moves():
    """The two keys answer different questions and must not be interchanged."""
    ids = [1, 2, 3]
    before = {1: 0, 2: 1, 3: 255}
    after = {1: 0, 2: 1, 3: 256}
    assert mixed_stack_service.content_fingerprint(
        ids, before
    ) == mixed_stack_service.content_fingerprint(list(reversed(ids)), before)
    assert mixed_stack_service.content_fingerprint(
        ids, before
    ) != mixed_stack_service.content_fingerprint(ids, after)
    # A member losing its hash is a state worth invalidating on too.
    assert mixed_stack_service.content_fingerprint(
        ids, before
    ) != mixed_stack_service.content_fingerprint(ids, {1: 0, 2: 1})
    # ...while the Keep dismissal's key is deliberately blind to all of it.
    assert mixed_stack_service.membership_fingerprint(
        ids
    ) == mixed_stack_service.membership_fingerprint(ids)

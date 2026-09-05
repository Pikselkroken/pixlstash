"""Mixed stacks: cohesion scoring, the ``Keep`` dismissal, split and unstack.

A **mixed stack** is a live stack whose members do not form one connected
cluster at the queue's similarity threshold (``docs/design/
mixed-stacks-and-stack-units.md``, D5/B5). These cover the contract in both
directions, because over-listing is its own regression:

* a genuinely mixed stack **is** listed, and a cohesive one is **not**;
* the list is bound to the threshold, not to a constant, the same stack is
  mixed at 0.90 and one clean cluster at 0.65;
* a ``Keep`` drops a stack off the list and a membership change re-raises it;
* split moves **the members the user marked**, which may be any live member of
  the stack, and refuses everything that is not one (another stack's picture, a
  picture in no stack, a soft-deleted member) as well as the whole stack when a
  locked set freezes it;
* split and unstack are each **one** operation, so a single undo puts every
  picture back in its original stack at its original position;
* every new route is ``OWNER_ONLY`` at the central gate, a resource-scoped
  READ token is refused via the ``Authorization`` header *and* via ``?token=``,
  and the owner still reaches all five.

Background workers are disabled and the pictures are inserted directly, so no
worker can rewrite ``perceptual_hash`` underneath the assertions.

The hashes are chosen so the Hamming distances are exact and legible.
``max_hamming = int((1 - threshold) * 64)``, 6 at 0.90, 22 at 0.65:

===============  ==========================  ===============================
name             hex                         popcount vs ``_H_ZERO``
===============  ==========================  ===============================
``_H_ZERO``      ``0000000000000000``        0
``_H_NEAR``      ``0000000000000001``        1   (edge at every threshold)
``_H_MID``       ``00000000000003ff``        10  (edge at 0.65 only)
``_H_ALMOST``    ``000000000000007f``        7   (ONE bit outside the 0.90 cut,
                                             so 89% similar and stranded: the
                                             case the page used to describe as
                                             matching nothing)
``_H_FAR``       ``ffffffff00000000``        32  (edge at no threshold)
``_H_OTHER``     ``00000000ffffffff``        32  (edge at no threshold, and
                                             64 from ``_H_FAR``, so the two
                                             strangers are strangers to each
                                             other as well)
``_H_FAR_NEAR``  ``ffffffff00000001``        33  (1 bit from ``_H_FAR``, so
                                             the two of them are a second
                                             tight cluster with no edge to
                                             the first: the *soft* case,
                                             mixed with nobody stranded)
===============  ==========================  ===============================
"""

import gc
import json
import os
import tempfile
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
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
_H_ALMOST = "000000000000007f"
_H_FAR = "ffffffff00000000"
_H_OTHER = "00000000ffffffff"
_H_FAR_NEAR = "ffffffff00000001"

_TIGHT = 1.0 - 1.0 / 64.0
"""Similarity of a one-bit pair, the edge every fixture cluster is built from."""

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

    * ``cohesive``: three members within one bit of each other. One cluster at
      every threshold; **must never be listed**.
    * ``mixed``: a tight pair and a picture 32 bits away. Two components at
      every threshold, with exactly one stranded member, so its suggested
      action is ``split``.
    * ``threshold``: two members ten bits apart. Two components at 0.90, one
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
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
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
    server.close()
    temp_dir.cleanup()
    gc.collect()


def _rows_by_stack(body) -> dict:
    return {row["stack_id"]: row for row in body["stacks"]}


def _edges_by_picture(row) -> dict:
    """``{picture_id: (strongest_edge, closest_picture_id)}`` from one row."""
    return {
        entry["picture_id"]: (entry["strongest_edge"], entry["closest_picture_id"])
        for entry in row["member_edges"]
    }


def _nearest_by_picture(row) -> dict:
    """``{picture_id: (nearest_edge, nearest_picture_id)}`` from one row.

    The *unconditional* half of the evidence: what ``_edges_by_picture`` reports
    is thresholded and is ``None`` for a stranded member by construction, which
    is precisely why it cannot be the number the page shows.
    """
    return {
        entry["picture_id"]: (entry["nearest_edge"], entry["nearest_picture_id"])
        for entry in row["member_edges"]
    }


def _hamming(left: str, right: str) -> int:
    """Bits differing between two hex dHashes, computed the slow honest way.

    Deliberately **not** the service's SWAR popcount: a test that reuses the
    implementation it is checking can only confirm the code is self-consistent.
    """
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def _brute_force_cohesion(hashes: list[str], threshold: float) -> tuple[list, list]:
    """An independent components fold over *hashes*, by breadth-first search.

    The oracle for "the stranded decision did not move". It answers the same
    question as ``_fold_components`` from the same inputs with none of the same
    code: no union-find, no numpy, no cached edge list and no pruning constant.

    Returns:
        ``(component_sizes_largest_first, stranded_indices)`` over the positions
        of *hashes*.
    """
    max_hamming = int((1.0 - threshold) * 64)
    neighbours = {index: set() for index in range(len(hashes))}
    for left in range(len(hashes)):
        for right in range(left + 1, len(hashes)):
            if _hamming(hashes[left], hashes[right]) <= max_hamming:
                neighbours[left].add(right)
                neighbours[right].add(left)
    seen: set[int] = set()
    components: list[list[int]] = []
    for start in range(len(hashes)):
        if start in seen:
            continue
        queue, group = [start], []
        seen.add(start)
        while queue:
            node = queue.pop()
            group.append(node)
            for peer in neighbours[node]:
                if peer not in seen:
                    seen.add(peer)
                    queue.append(peer)
        components.append(sorted(group))
    components.sort(key=lambda group: (-len(group), group[0]))
    stranded = sorted(index for index in range(len(hashes)) if not neighbours[index])
    return [len(group) for group in components], stranded


def _pill_texts(row) -> list[tuple[str, bool]]:
    """``[(text, against), ...]`` from one row's ``why``."""
    return [(pill["text"], pill["against"]) for pill in row["why"]]


def _clear_hash(server, picture_id: int) -> None:
    """Blank one picture's ``perceptual_hash``, as an unfinished import has."""

    def clear(session):
        picture = session.get(Picture, picture_id)
        picture.perceptual_hash = None
        session.add(picture)
        session.commit()

    _run(server, clear)


@contextmanager
def _counted_queries(server):
    """Count every statement the engine executes inside the block.

    The N+1 guard for the page: per-member edges are folded out of an edge list
    the page already loads, so a page of six rows must cost the same number of
    statements as a page of one. Counting is the only honest way to assert that;
    an eyeball on the code is what lets an N+1 back in.
    """
    counter = {"n": 0}
    engine = server.vault.db._engine

    def _count(_conn, _cursor, _statement, _parameters, _context, _executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _count)


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
    """Same stack, two answers: D5's "bind it to the slider" requirement."""
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


# ── per-member evidence, and the row's why-pills ─────────────────────────────


def test_member_edges_report_a_real_edge_and_a_stranger_s_absence():
    """Both directions of the evidence column, on one row.

    Compare's other metrics answer "which copy is the better file"; this column
    answers "which of these does not belong". The two members of the tight pair
    must report the edge they really have, and the stranger must report none,
    because that absence is the whole evidence the page offers.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert [entry["picture_id"] for entry in row["member_edges"]] == row[
            "member_ids"
        ], "member_edges must be parallel to member_ids, canonical order"

        edges = _edges_by_picture(row)
        # The cohesive direction: each of the pair names the other, at the real
        # one-bit similarity, not at the threshold and not at 1.0.
        assert edges[picture_ids[0]] == (pytest.approx(_TIGHT), picture_ids[1])
        assert edges[picture_ids[1]] == (pytest.approx(_TIGHT), picture_ids[0])
        # The stranded direction: no qualifying edge, and no sibling to name.
        assert edges[picture_ids[2]] == (None, None)
        assert row["stranded_picture_ids"] == [picture_ids[2]]
    finally:
        _teardown(temp_dir, server)


def test_a_cohesive_stack_reports_every_member_s_strongest_edge():
    """A stack that is never listed still scores, and every member has an edge.

    Read through the service, because the list deliberately excludes cohesive
    stacks: Compare opens on stacks the queue folded in too, and a column that
    only worked for mixed stacks would be blank exactly where the user is
    checking a suspicion. The duplicate-hash pair reports a perfect 1.0, and the
    leader's tie between two equally close siblings resolves to the lower id so
    the payload is reproducible.
    """
    temp_dir, _client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["cohesive"]

        def score(session):
            return mixed_stack_service.cohesion_for_stacks(session, [stack_id], 0.90)

        report = _run(server, score)[stack_id]
        assert not report.is_mixed
        edges = {edge.picture_id: edge for edge in report.member_edges}
        assert [edge.picture_id for edge in report.member_edges] == list(
            report.member_ids
        )
        # Members 1 and 2 carry the same hash, so their edge is exact.
        assert edges[picture_ids[1]].strongest_edge == pytest.approx(1.0)
        assert edges[picture_ids[1]].closest_picture_id == picture_ids[2]
        assert edges[picture_ids[2]].strongest_edge == pytest.approx(1.0)
        assert edges[picture_ids[2]].closest_picture_id == picture_ids[1]
        # The leader is one bit from both; the tie resolves to the lower id.
        assert edges[picture_ids[0]].strongest_edge == pytest.approx(_TIGHT)
        assert edges[picture_ids[0]].closest_picture_id == picture_ids[1]
    finally:
        _teardown(temp_dir, server)


def test_member_edges_follow_the_threshold_rather_than_a_constant():
    """The same pair: an edge at 0.65, none at 0.90.

    The evidence column is only true for the threshold the row was read at, so
    it has to move with the slider exactly as the list does.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["threshold"]

        tight = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())
        assert _edges_by_picture(tight[stack_id]) == {
            picture_ids[0]: (None, None),
            picture_ids[1]: (None, None),
        }

        def score(session):
            return mixed_stack_service.cohesion_for_stacks(session, [stack_id], 0.65)

        # At 0.65 the ten-bit pair connects, so the stack is no longer listed at
        # all and its members each name the other.
        loose = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.65}).json())
        assert stack_id not in loose
        edges = {
            edge.picture_id: edge for edge in _run(server, score)[stack_id].member_edges
        }
        assert edges[picture_ids[0]].strongest_edge == pytest.approx(1.0 - 10.0 / 64.0)
        assert edges[picture_ids[0]].closest_picture_id == picture_ids[1]
    finally:
        _teardown(temp_dir, server)


def test_a_single_member_stack_reports_no_edge_and_is_not_listed():
    """One member has no sibling, so its `null` is arithmetic, not a verdict.

    Nothing on the page can reach this (the list floors at two live members),
    but Compare and the split route score whatever stack they are handed, so the
    shape has to be defined rather than crash or claim strandedness.
    """
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, picture_ids = _make_stack(server, [_H_ZERO])

        def score(session):
            return mixed_stack_service.cohesion_for_stacks(session, [stack_id], 0.90)

        report = _run(server, score)[stack_id]
        assert [
            (edge.picture_id, edge.strongest_edge, edge.closest_picture_id)
            for edge in report.member_edges
        ] == [(picture_ids[0], None, None)]
        assert report.weakest_edge is None
        # The lone member is "stranded" by arithmetic (no sibling to have an
        # edge to), so the pills must refuse to turn that into an accusation.
        assert report.stranded_picture_ids == (picture_ids[0],)
        assert report.as_dict()["why"] == []
        assert stack_id not in _rows_by_stack(
            client.get(MIXED_URL, params={"threshold": 0.90}).json()
        )
    finally:
        _teardown(temp_dir, server)


def test_a_member_without_a_hash_reports_no_edge_without_being_a_stranger():
    """`null` here means "not comparable", and the row already has that word.

    The member is unavoidably in ``stranded_picture_ids`` (it can carry no
    edge), so the pills are what have to tell the two apart: no "matches nothing
    else", and an explicit "not comparable yet".
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        _clear_hash(server, picture_ids[2])

        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert row["unhashed_picture_ids"] == [picture_ids[2]]
        assert _edges_by_picture(row)[picture_ids[2]] == (None, None)
        # The other direction of the fix: an unhashed member reports NO figure.
        # This is the one absence that survives, and it has to, because there is
        # genuinely nothing to measure, not a small number to print.
        assert _nearest_by_picture(row)[picture_ids[2]] == (None, None)

        texts = _pill_texts(row)
        assert ("1 picture not comparable yet", False) in texts
        assert not [text for text, _against in texts if "matches nothing else" in text]
        assert not [text for text, _against in texts if "like the rest" in text], (
            "a picture nothing has compared yet must never be described by how "
            f"unlike the others it is: {texts}"
        )
        assert ("1 picture differs from the rest", True) in texts
        assert ("Weakest match 98%", False) in texts
    finally:
        _teardown(temp_dir, server)


def test_a_stranded_member_reports_the_real_similarity_it_just_missed():
    """The reported bug, at the boundary: 7 bits out of 64 where the cut is 6.

    ``strongest_edge`` is ``None`` here by construction, and that is correct: it
    is the best edge that SURVIVES at this threshold and none does. What was
    wrong was making it the only number, so the page printed an en dash and
    said the picture matched nothing about a member 89% like its neighbour.
    ``nearest_edge`` is the unconditional answer and must be there.
    """
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, picture_ids = _make_stack(server, [_H_ZERO, _H_ALMOST])
        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        # The decision is unchanged: 7 bits is outside the 6-bit cut.
        assert row["stranded_picture_ids"] == sorted(picture_ids)
        assert row["component_sizes"] == [1, 1]
        assert row["weakest_edge"] is None
        assert _edges_by_picture(row) == {
            picture_ids[0]: (None, None),
            picture_ids[1]: (None, None),
        }

        nearest = _nearest_by_picture(row)
        assert nearest[picture_ids[0]] == (
            pytest.approx(1.0 - 7.0 / 64.0),
            picture_ids[1],
        )
        assert nearest[picture_ids[1]] == (
            pytest.approx(1.0 - 7.0 / 64.0),
            picture_ids[0],
        )
        assert _pill_texts(row)[0] == ("2 pictures are only 89% like the rest", True)
    finally:
        _teardown(temp_dir, server)


def test_strangers_that_differ_are_named_as_a_range():
    """One number for several strangers would misdescribe all but one of them.

    Three strangers here: two are 7 bits apart (89%) and the third is 32 bits
    from its own closest sibling (50%). Quoting only the best would flatter the
    stack and only the worst would libel two pictures, so the pill spans them.
    """
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, _picture_ids = _make_stack(server, [_H_ZERO, _H_ALMOST, _H_FAR])
        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert row["component_sizes"] == [1, 1, 1]
        assert _pill_texts(row)[0] == (
            "3 pictures are only 50-89% like the rest",
            True,
        )
    finally:
        _teardown(temp_dir, server)


def test_a_strangers_similarity_survives_the_cached_edge_lists_floor():
    """The other half of the bug: distances the edge cache never stored.

    The cached edge list is pruned at ``MAX_CACHED_HAMMING`` (22 bits), which is
    right for edges and wrong for closeness: a member whose nearest sibling is
    32 bits away has a real answer, 50%, and the prune used to throw it away
    before anything could show it. Read through a **warm** cache, because that
    is the path where the number went missing.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        MissingStackCohesionFinder(database=server.vault.db).find_task().run()

        def cached(session):
            row = session.exec(
                select(StackCohesion).where(StackCohesion.stack_id == stack_id)
            ).first()
            return json.loads(row.edges), json.loads(row.nearest_edges)

        edges, nearest_rows = _run(server, cached)
        far_pair = {picture_ids[0], picture_ids[2]}
        assert not [pair for pair in edges if set(pair[:2]) == far_pair], (
            "the 32-bit pair is beyond the cache floor and must stay pruned "
            f"from the edge list; keeping it would be O(n^2) storage: {edges}"
        )
        assert [row for row in nearest_rows if row[0] == picture_ids[2]] == [
            [picture_ids[2], picture_ids[0], 32]
        ], "the stranger's real distance is stored per member, unpruned"

        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert row["stranded_picture_ids"] == [picture_ids[2]]
        assert _nearest_by_picture(row)[picture_ids[2]] == (
            pytest.approx(0.5),
            picture_ids[0],
        )
    finally:
        _teardown(temp_dir, server)


def test_every_cached_row_carries_a_closest_sibling_for_every_member():
    """The writer can never produce the empty column the page used to show.

    Cache validity is now presence: database triggers drop a stack's derived row
    whenever an input moves, so a present row is trusted without rereading a
    hash, and `0093_invalidate_stackcohesion_inputs` starts cold so no row
    written before this column existed survives the upgrade. What that leaves to
    check here is the other half: every row the writer produces covers every
    comparable member, or a trusted row would serve a gap.
    """
    temp_dir, _client, server, stacks, _token = _env()
    try:
        MissingStackCohesionFinder(database=server.vault.db).find_task().run()

        def cached(session):
            return {
                int(row.stack_id): (
                    json.loads(row.member_ids),
                    json.loads(row.unhashed_picture_ids),
                    json.loads(row.nearest_edges),
                )
                for row in session.exec(select(StackCohesion)).all()
            }

        rows = _run(server, cached)
        assert set(rows) == {entry[0] for entry in stacks.values()}
        for stack_id, (member_ids, unhashed, nearest) in rows.items():
            comparable = [pid for pid in member_ids if pid not in unhashed]
            expected = set(comparable) if len(comparable) > 1 else set()
            assert {entry[0] for entry in nearest} == expected, (
                f"stack {stack_id} cached a closest sibling for "
                f"{[entry[0] for entry in nearest]}, but {sorted(expected)} "
                "are comparable; a member missing from this list is a member "
                "the page can only show an en dash for"
            )
            for _picture_id, sibling, distance in nearest:
                assert sibling in member_ids
                assert 0 <= distance <= 64
    finally:
        _teardown(temp_dir, server)


def test_a_stack_nothing_can_be_compared_in_says_exactly_that():
    """Its own wording, because none of the other pills would be true.

    Two members, neither hashed: every "component" is a singleton because of the
    missing hashes alone. Reporting the structure or a weakest edge here would
    turn an absence of data into a verdict about the pictures, which is the
    false positive this feature cannot afford.
    """
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, picture_ids = _make_stack(server, [_H_ZERO, _H_NEAR])
        for picture_id in picture_ids:
            _clear_hash(server, picture_id)

        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert row["unhashed_picture_ids"] == sorted(picture_ids)
        assert _pill_texts(row) == [("Nothing here can be compared yet", False)]
        assert _nearest_by_picture(row) == {
            picture_ids[0]: (None, None),
            picture_ids[1]: (None, None),
        }
    finally:
        _teardown(temp_dir, server)


def test_a_member_with_nothing_hashed_to_compare_against_reports_no_figure():
    """One hashed member and one not: comparable, but to nothing.

    The remaining `null` case, and it must not be confused with the 89% one: the
    hashed member is not far from its sibling, it has no sibling to be far from.
    """
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, picture_ids = _make_stack(server, [_H_ZERO, _H_FAR])
        _clear_hash(server, picture_ids[1])

        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert _nearest_by_picture(row) == {
            picture_ids[0]: (None, None),
            picture_ids[1]: (None, None),
        }
        assert _pill_texts(row) == [("Nothing here can be compared yet", False)]
    finally:
        _teardown(temp_dir, server)


def test_the_stranded_decision_is_unchanged_at_every_threshold():
    """The regression that would matter most, checked against an oracle.

    This change is about what the page KNOWS and SAYS, never about where the cut
    is. So the components, the stranded set and the weakest edge are checked at
    every threshold against a brute-force fold that shares no code with the
    service: no union-find, no numpy, no cached edge list, no pruning constant.
    If the new unconditional number ever leaked into the decision, this is what
    catches it.

    The same sweep pins the two-field invariant: the unconditional number is
    never *worse* than the thresholded one, and is exactly equal whenever the
    thresholded one exists, because the closest pair is the first to survive any
    cut.
    """
    temp_dir, _client, server, _stacks, _token = _env()
    try:
        hashes = [_H_ZERO, _H_NEAR, _H_ALMOST, _H_MID, _H_FAR, _H_FAR_NEAR, _H_OTHER]
        stack_id, picture_ids = _make_stack(server, hashes)
        # Warm the cache, so the sweep covers the cached read path too: it is
        # the pruned one, and the prune is what this change touches.
        MissingStackCohesionFinder(database=server.vault.db).find_task().run()

        for threshold in (0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99):

            def score(session, threshold=threshold):
                return mixed_stack_service.cohesion_for_stacks(
                    session, [stack_id], threshold
                )

            report = _run(server, score)[stack_id]
            sizes, stranded = _brute_force_cohesion(hashes, threshold)
            assert [len(group) for group in report.components] == sizes, (
                f"components moved at threshold {threshold}"
            )
            assert list(report.stranded_picture_ids) == sorted(
                picture_ids[index] for index in stranded
            ), f"the stranded set moved at threshold {threshold}"

            surviving = [
                1.0 - _hamming(hashes[left], hashes[right]) / 64.0
                for left in range(len(hashes))
                for right in range(left + 1, len(hashes))
                if _hamming(hashes[left], hashes[right]) <= int((1.0 - threshold) * 64)
            ]
            assert report.weakest_edge == (
                pytest.approx(min(surviving)) if surviving else None
            ), f"the weakest edge moved at threshold {threshold}"

            for edge in report.member_edges:
                assert edge.nearest_edge is not None, (
                    "every member of this stack has a hashed sibling, so every "
                    "one of them has a measured closest match"
                )
                if edge.strongest_edge is None:
                    assert edge.nearest_edge <= threshold
                else:
                    assert edge.nearest_edge == pytest.approx(edge.strongest_edge)
                    assert edge.nearest_picture_id == edge.closest_picture_id
    finally:
        _teardown(temp_dir, server)


def test_why_pills_name_the_strong_case():
    """One stranger: the red pill the page exists for, in the shipped shape."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, _picture_ids = stacks["mixed"]
        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert _pill_texts(row) == [
            # The stranger is 32 bits from the nearest member of the pair, so
            # the pill names 50% rather than claiming it matches nothing: the
            # number is the fact the user needs in order to disagree with the
            # cut, and "matches nothing else" is not a statement about a
            # picture at all, only about a threshold.
            ("1 picture is only 50% like the rest", True),
            ("1 picture differs from the rest", True),
            ("Weakest match 98%", False),
        ]
        # Same contract as a duplicate group's `why`, so the shipped pill
        # component renders it with no second code path.
        assert all({"text", "against"} <= set(pill) for pill in row["why"])
        structure = next(
            pill
            for pill in row["why"]
            if pill["text"] == "1 picture differs from the rest"
        )
        assert structure["accessible_text"] == (
            "2 groups: 1 group of 2 pictures and 1 single-picture group."
        )
    finally:
        _teardown(temp_dir, server)


@pytest.mark.parametrize(
    ("sizes", "member_count", "expected"),
    [
        ([1, 1, 1], 3, "All pictures differ"),
        ([2, 1, 1, 1], 5, "Most pictures differ"),
        ([5, 1], 6, "1 picture differs from the rest"),
        ([5, 2, 1], 8, "3 pictures differ from the main group"),
        ([3, 3], 6, "2 groups don't match each other"),
        # Exactly half is deliberately not a majority.
        ([3, 2, 1], 6, "Several groups don't overlap"),
    ],
)
def test_component_structure_uses_qualitative_majority_rules(
    sizes, member_count, expected
):
    pill = mixed_stack_service._component_structure_pill(
        len(sizes), sizes, member_count
    )
    assert pill is not None
    assert pill["text"] == expected
    assert pill["against"] is True


def test_component_structure_keeps_the_exact_distribution_accessible():
    sizes = [5, 5, 3, 2, 2, *([1] * 29)]
    pill = mixed_stack_service._component_structure_pill(34, sizes, 46)
    assert pill is not None
    assert pill["text"] == "Most pictures differ"
    assert pill["accessible_text"] == (
        "34 groups: 2 groups of 5 pictures, 1 group of 3 pictures, "
        "2 groups of 2 pictures, and 29 single-picture groups."
    )


@pytest.mark.parametrize(
    ("sizes", "member_count"),
    [
        (None, 3),
        ([2], 2),
        ([2, 1], 4),
        ([2, 0], 2),
        ([2, True], 3),
    ],
)
def test_component_structure_does_not_infer_from_malformed_sizes(sizes, member_count):
    pill = mixed_stack_service._component_structure_pill(2, sizes, member_count)
    assert pill == {
        "text": "2 groups don't overlap",
        "against": True,
        "accessible_text": "2 groups don't overlap.",
    }


def test_component_structure_omits_a_valid_cohesive_report():
    assert mixed_stack_service._component_structure_pill(1, [3], 3) is None


def test_why_pills_name_the_soft_case_without_calling_anyone_a_stranger():
    """Two tight pairs that do not match each other strand nobody.

    D5's soft case: legitimate often enough that marking it would train the user
    to ignore the colour, so it must surface in words. The structure pill is the
    only thing that can carry it, and the stranger pill must be absent.
    """
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, _picture_ids = _make_stack(
            server, [_H_ZERO, _H_NEAR, _H_FAR, _H_FAR_NEAR]
        )
        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert row["stranded_picture_ids"] == []
        assert row["component_sizes"] == [2, 2]
        assert _pill_texts(row) == [
            ("2 groups don't match each other", True),
            ("Weakest match 98%", False),
        ]
    finally:
        _teardown(temp_dir, server)


def test_why_pills_say_so_when_no_two_pictures_match_at_all():
    """No surviving edge is the extreme of the same scale, not missing data."""
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, _picture_ids = _make_stack(server, [_H_ZERO, _H_FAR])
        row = _rows_by_stack(client.get(MIXED_URL, params={"threshold": 0.90}).json())[
            stack_id
        ]
        assert row["weakest_edge"] is None
        assert _pill_texts(row) == [
            ("2 pictures are only 50% like the rest", True),
            ("All pictures differ", True),
            ("No two pictures match", True),
        ]
    finally:
        _teardown(temp_dir, server)


def test_the_page_costs_the_same_number_of_queries_however_many_rows_it_has():
    """The anti-N+1 guard, arithmetically.

    Per-member edges are folded out of an edge list the page already loads, so
    adding rows must add no statements at all. Measured rather than reasoned
    about: this is exactly the kind of column that becomes a per-row lookup the
    first time someone needs one more field on it.
    """
    temp_dir, client, server, _stacks, _token = _env()
    try:
        with _counted_queries(server) as counter:
            first = client.get(MIXED_URL, params={"threshold": 0.90})
        assert first.status_code == 200
        one_row = counter["n"]
        assert len(first.json()["stacks"]) == 2

        for _ in range(6):
            _make_stack(server, [_H_ZERO, _H_NEAR, _H_FAR])
        with _counted_queries(server) as counter:
            wider = client.get(MIXED_URL, params={"threshold": 0.90})
        assert wider.status_code == 200
        assert len(wider.json()["stacks"]) == 8
        assert counter["n"] == one_row, (
            "the mixed-stacks page must cost a constant number of statements; "
            f"2 rows took {one_row} and 8 rows took {counter['n']}"
        )
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


def test_split_honours_an_explicit_picture_id_list(monkeypatch):
    """The client sends the ids the user marked, so the split matches the row.

    A four-member stack with a tight pair and **two** mutual strangers: the row
    opens with both marked, the user keeps one of them, and exactly that one
    leaves.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = _make_stack(
            server, [_H_ZERO, _H_NEAR, _H_FAR, _H_OTHER]
        )
        row = _rows_by_stack(client.get(MIXED_URL).json())[stack_id]
        assert row["stranded_picture_ids"] == sorted(picture_ids[2:])

        def should_not_score(*_args, **_kwargs):
            raise AssertionError("an explicit split must not compute cohesion")

        monkeypatch.setattr(
            mixed_stack_service, "cohesion_for_stacks", should_not_score
        )

        response = client.post(
            _split_url(stack_id), json={"picture_ids": [picture_ids[2]]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["split_picture_ids"] == [picture_ids[2]]
        assert response.json()["remaining_picture_ids"] == sorted(
            [picture_ids[0], picture_ids[1], picture_ids[3]]
        )
    finally:
        _teardown(temp_dir, server)


def test_split_accepts_any_live_member_the_user_marked():
    """The user's marks are the input; the engine's marks are only the default.

    This deliberately reverses security-review finding F7 (2026-08-01), which
    had required an explicit ``picture_ids`` to be a subset of the stranded set
    at ``threshold``. The Mixed stacks page now lets the user mark which members
    are strangers, starting from the engine's marks and adjusting them, so a
    bound that refuses any mark the engine did not make refuses the feature.
    F7 was rated LOW on the grounds that this is not a privilege boundary (the
    route is ``OWNER_ONLY`` and ``DELETE /stacks/{id}/members`` already gives
    the same principal an unrestricted remove), so nothing is granted here that
    the caller could not already do.

    Both cases F7 named are asserted to work, and asserted to actually move the
    picture rather than merely answer 200.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        # (1) The case F7 was written about: the leader of a perfectly cohesive
        # stack, which this page would never list and the engine never strands.
        cohesive_id, cohesive_pics = stacks["cohesive"]
        response = client.post(
            _split_url(cohesive_id), json={"picture_ids": [cohesive_pics[0]]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["split_picture_ids"] == [cohesive_pics[0]]
        assert response.json()["stack_dissolved"] is False
        after = _stack_state(server, cohesive_pics)
        assert after[cohesive_pics[0]] == (None, None), (
            "a marked member must actually leave the stack, not just be answered 200"
        )
        assert after[cohesive_pics[1]][0] == cohesive_id
        assert after[cohesive_pics[2]][0] == cohesive_id

        # (2) A member of the majority cluster of a genuinely mixed stack: the
        # row lists it, the engine does not strand it, the user marked it.
        mixed_id, mixed_pics = _make_stack(server, [_H_ZERO, _H_NEAR, _H_NEAR, _H_FAR])
        row = _rows_by_stack(client.get(MIXED_URL).json())[mixed_id]
        assert row["stranded_picture_ids"] == [mixed_pics[3]]
        response = client.post(
            _split_url(mixed_id), json={"picture_ids": [mixed_pics[1]]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["split_picture_ids"] == [mixed_pics[1]]
        assert _stack_state(server, mixed_pics)[mixed_pics[1]] == (None, None)
        # ...and the stranded member the user did NOT mark stayed put, so the
        # explicit list still replaces the engine's set rather than adding to it.
        assert _stack_state(server, mixed_pics)[mixed_pics[3]][0] == mixed_id
    finally:
        _teardown(temp_dir, server)


def test_split_refuses_an_id_that_is_not_a_live_member_of_this_stack():
    """The bound that replaced F7: live membership of the stack in the path.

    Weaker than the stranded-subset rule and stronger than nothing. Three
    refusals, each writing nothing:

    * a picture id that does not exist at all;
    * a real picture that belongs to a **different** stack, which is the id a
      client with a stale row is actually likely to send;
    * a **soft-deleted** member of this very stack. The caller cannot see those
      rows on the row it marked, so moving one blind is not something a mark can
      mean, and the refusal says "Scrapheap" rather than "not a member" so a
      client is not sent hunting for a bug that is not there.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        mixed_id, mixed_pics = stacks["mixed"]
        other_id, other_pics = stacks["cohesive"]
        before = _stack_state(server, mixed_pics + other_pics)

        # (1) No such picture.
        response = client.post(_split_url(mixed_id), json={"picture_ids": [999999]})
        assert response.status_code == 400, response.text
        assert "not members of stack" in response.json()["detail"]

        # (2) A live picture, but a member of another stack.
        response = client.post(
            _split_url(mixed_id), json={"picture_ids": [other_pics[0]]}
        )
        assert response.status_code == 400, response.text
        assert str(other_pics[0]) in response.json()["detail"]

        # (3) A member of THIS stack that is in the Scrapheap.
        def scrapheap(session):
            picture = session.get(Picture, mixed_pics[1])
            picture.deleted = True
            session.add(picture)
            session.commit()

        _run(server, scrapheap)
        response = client.post(
            _split_url(mixed_id), json={"picture_ids": [mixed_pics[1]]}
        )
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert "Scrapheap" in detail, detail
        assert str(mixed_pics[1]) in detail, detail

        # Nothing above wrote anything, including to the scrapheaped row.
        assert _stack_state(server, mixed_pics + other_pics) == before

        # Over-blocking guard: a live member of the named stack still splits.
        # Asserted on the three-member stack so the answer is a plain removal
        # rather than the dissolve a two-live-member stack would give.
        response = client.post(
            _split_url(other_id), json={"picture_ids": [other_pics[0]]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["split_picture_ids"] == [other_pics[0]]
        assert response.json()["stack_dissolved"] is False
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


def test_dissolve_receipt_includes_a_scrapheaped_member_that_was_detached():
    """The receipt lists every row moved, including invisible stack members."""
    temp_dir, client, server, _stacks, _token = _env()
    try:
        stack_id, picture_ids = _make_stack(server, [_H_ZERO, _H_NEAR, _H_FAR])

        def scrapheap(session):
            picture = session.get(Picture, picture_ids[2])
            picture.deleted = True
            session.add(picture)
            session.commit()

        _run(server, scrapheap)
        response = client.post(
            _split_url(stack_id), json={"picture_ids": [picture_ids[0]]}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["stack_dissolved"] is True
        assert payload["split_picture_ids"] == sorted(picture_ids)
        assert all(
            state == (None, None)
            for state in _stack_state(server, picture_ids).values()
        )
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


def test_a_warm_list_does_not_reread_picture_hashes(monkeypatch):
    """A cache hit folds stored edges; validating it must not rescan pictures."""
    temp_dir, client, server, _stacks, _token = _env()
    try:
        MissingStackCohesionFinder(database=server.vault.db).find_task().run()

        def should_not_read(*_args, **_kwargs):
            raise AssertionError("a warm cohesion list reread picture hashes")

        monkeypatch.setattr(
            mixed_stack_service, "_load_perceptual_hashes", should_not_read
        )
        response = client.get(MIXED_URL, params={"threshold": 0.65})
        assert response.status_code == 200, response.text
    finally:
        _teardown(temp_dir, server)


def test_a_hash_arriving_after_the_cache_invalidates_it():
    """The cache key covers the hashes, not only the membership.

    The embedding worker fills ``perceptual_hash`` for a picture that had none.
    Membership does not move, so a membership-keyed cache would go on reporting
    that member as stranded forever: the exact false positive the flag must
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


# ── locked sets: the row says so, and both actions refuse the whole stack ─────


def _set_member_ids(server, set_id) -> set[int]:
    def read(session):
        return {
            int(pid)
            for pid in session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.set_id == set_id
                )
            ).all()
        }

    return _run(server, read)


def _lock_a_set_over(client, server, name: str, picture_id: int) -> int:
    """Create a locked picture set whose only member is *picture_id*.

    The membership row is written directly, and the docstring above is asserted
    rather than assumed. ``POST /picture_sets/{id}/members/{picture_id}`` is
    stack-atomic: it expands to every member of the picture's stack, so through
    that route a set over a member of a 3-stack has three members and the
    through-stack-only state these tests are named for never exists. A test
    seeded that way passes against a guard narrowed to the named ids, which is
    the exact regression it is supposed to catch.
    """
    set_id = client.post(f"{API}/picture_sets", json={"name": name}).json()[
        "picture_set"
    ]["id"]

    def add_member_only(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=int(picture_id)))
        session.commit()

    _run(server, add_member_only)
    assert (
        client.patch(f"{API}/picture_sets/{set_id}", json={"locked": True}).status_code
        == 200
    )
    assert _set_member_ids(server, set_id) == {int(picture_id)}
    return set_id


def test_a_locked_member_marks_the_row_and_refuses_split_and_unstack():
    """The lock is reported on the row AND enforced on both actions.

    Reported so the primary button can be disabled with a reason rather than
    pressed into a 423; enforced because a locked set freezes a stack's
    siblings *through* the stack, so detaching one severs the freeze.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        before = _stack_state(server, picture_ids)
        # The frozen picture is the stack LEADER; the stranded member that split
        # would move is not itself in the set, only frozen through the stack.
        set_id = _lock_a_set_over(client, server, "Frozen", picture_ids[0])

        row = _rows_by_stack(client.get(MIXED_URL).json())[stack_id]
        assert row["stackable"] is False, row
        assert row["blocked_by_sets"] == [{"id": set_id, "name": "Frozen"}], row

        for response in (
            client.post(_split_url(stack_id), json={"threshold": 0.90}),
            client.post(_split_url(stack_id), json={"picture_ids": [picture_ids[2]]}),
            # A live but NOT stranded member: the id the widened contract newly
            # accepts. The lock guard runs before any of that and still refuses
            # the whole stack, which is the hole the widening must not open.
            client.post(_split_url(stack_id), json={"picture_ids": [picture_ids[1]]}),
            client.post(_unstack_url(stack_id), json={}),
        ):
            assert response.status_code == 423, response.text
            detail = response.json()["detail"]
            assert detail["code"] == "pictures_locked", detail
            assert [entry["id"] for entry in detail["sets"]] == [set_id], detail

        assert _stack_state(server, picture_ids) == before
    finally:
        _teardown(temp_dir, server)


def test_an_unlocked_row_is_stackable_and_still_acts():
    """Over-blocking regression: an untouched row carries the free values and
    both actions still work."""
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        row = _rows_by_stack(client.get(MIXED_URL).json())[stack_id]
        assert row["stackable"] is True, row
        assert row["blocked_by_sets"] == [], row

        assert (
            client.post(_split_url(stack_id), json={"threshold": 0.90}).status_code
            == 200
        )

        other_id, _other_pics = _make_stack(server, [_H_ZERO, _H_FAR])
        assert client.post(_unstack_url(other_id), json={}).status_code == 200
    finally:
        _teardown(temp_dir, server)


def test_a_scrapheaped_frozen_member_freezes_the_row_and_the_actions_alike():
    """Both read surfaces and the server agree on the soft-deleted arm.

    A stack whose only frozen member is soft-deleted is the case where they
    drift, and it is genuinely reachable: the set is seeded row-by-row (see
    ``_lock_a_set_over``) so no LIVE member is in it, and only every-member-row
    reads (``set_lock_service._stack_member_ids``) find the freeze at all.
    Filter ``deleted`` in that helper and every assertion below flips.

    * ``GET /dedup/mixed-stacks`` says `stackable: false`;
    * ``GET /dedup/stacks/{id}/members`` says the same, which it did NOT until
      it stopped rolling the unit answer up from its live member ids;
    * split and unstack both answer 423.

    The live siblings are deliberately still unfrozen at the picture level: a
    scrapheaped locked-set member projects no freeze onto them. The two rules
    differ here on purpose, and ``_stack_member_ids`` carries the reasoning.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        set_id = _lock_a_set_over(client, server, "FrozenHeap", picture_ids[1])

        # Scrapheap the frozen member directly: the API refuses (that is the
        # lock working), and the state under test is a database written before
        # the set was locked.
        def scrapheap(session):
            picture = session.get(Picture, picture_ids[1])
            picture.deleted = True
            session.add(picture)
            session.commit()

        _run(server, scrapheap)

        row = _rows_by_stack(client.get(MIXED_URL).json())[stack_id]
        assert row["stackable"] is False, row
        assert row["blocked_by_sets"] == [{"id": set_id, "name": "FrozenHeap"}], row
        assert picture_ids[1] not in row["member_ids"], (
            "the frozen member is soft-deleted, so it is not a live member: "
            "this is exactly the id a live-members-only rollup would miss"
        )

        # The second read surface reports the same pair with the same meaning.
        members = client.get(f"{API}/dedup/stacks/{stack_id}/members")
        assert members.status_code == 200, members.text
        body = members.json()
        assert body["stackable"] is False, body
        assert body["blocked_by_sets"] == [{"id": set_id, "name": "FrozenHeap"}], body
        # ...while each LIVE member is individually unfrozen, which is the
        # narrower per-picture question and the answer the picture-level guards
        # give. A unit that is false over members that are all true is the
        # scrapheaped case, not a bug.
        assert [m["stackable"] for m in body["members"]] == [True] * len(
            body["members"]
        ), body
        assert all(m["blocked_by_sets"] == [] for m in body["members"]), body

        for response in (
            client.post(_split_url(stack_id), json={"threshold": 0.90}),
            client.post(_unstack_url(stack_id), json={}),
        ):
            assert response.status_code == 423, response.text
            assert response.json()["detail"]["picture_ids"] == [picture_ids[1]]
    finally:
        _teardown(temp_dir, server)


def test_a_scrapheaped_member_of_an_unlocked_set_leaves_the_row_stackable():
    """Over-blocking twin: a scrapheap entry alone freezes nothing.

    Same shape as the test above with the lock left off, so a guard that read
    "this stack has a scrapheaped member in some set" rather than "in a LOCKED
    set" fails here. Both read surfaces stay `stackable: true` and unstack
    still works.
    """
    temp_dir, client, server, stacks, _token = _env()
    try:
        stack_id, picture_ids = stacks["mixed"]
        set_id = client.post(f"{API}/picture_sets", json={"name": "OpenHeap"}).json()[
            "picture_set"
        ]["id"]

        def seed(session):
            session.add(PictureSetMember(set_id=set_id, picture_id=int(picture_ids[1])))
            picture = session.get(Picture, picture_ids[1])
            picture.deleted = True
            session.add(picture)
            session.commit()

        _run(server, seed)

        row = _rows_by_stack(client.get(MIXED_URL).json())[stack_id]
        assert row["stackable"] is True, row
        assert row["blocked_by_sets"] == [], row

        body = client.get(f"{API}/dedup/stacks/{stack_id}/members").json()
        assert body["stackable"] is True, body
        assert body["blocked_by_sets"] == [], body

        assert client.post(_unstack_url(stack_id), json={}).status_code == 200
    finally:
        _teardown(temp_dir, server)

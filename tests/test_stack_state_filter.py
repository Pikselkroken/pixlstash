"""End-to-end tests for the grid's ``stack_state`` filter.

The unit tests in ``test_predicate_filter.py`` pin the compiled predicate. These
pin the thing that was actually broken: the *wiring*.

The frontend has sent ``stack_state=stacked|unstacked|unresolved`` since
``9b6aabc0`` (the Dedup → Stacks release), but no Python code read it. The
parameter reached ``Picture.find`` as an ad-hoc kwarg, where the
``if hasattr(cls, attr)`` fallthrough for unknown search kwargs dropped it
silently: no 4xx, no log line, no console error. The user clicked a segment,
the grid dutifully refetched, and returned exactly the same pictures.

That failure mode is invisible to a predicate unit test, so these drive the real
routes and assert both directions: the filter narrows to the right set, and the
unfiltered listing is unchanged.
"""

from __future__ import annotations

import gc
import json
import os
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    DedupGroup,
    DedupGroupMember,
    Picture,
    PictureStack,
)
from pixlstash.server import Server
from tests.authz_guard import no_spa_fallback  # noqa: F401

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL can make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _setup_server():
    tmp = tempfile.TemporaryDirectory()
    image_root = os.path.join(tmp.name, "images")
    os.makedirs(image_root, exist_ok=True)
    config_path = os.path.join(tmp.name, "server-config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"port": 0}))
    server = Server(config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return tmp, client, server


def _seed(server):
    """Seed one stack of two, two loose pictures, and two dedup groups.

    Returns a dict of the ids the assertions name.
    """

    def _insert(session):
        stack = PictureStack(id=1)
        session.add(stack)
        session.flush()

        def _pic(name, **kwargs):
            pic = Picture(file_path=name, score=0, imported_at=datetime.now(), **kwargs)
            session.add(pic)
            session.flush()
            return pic

        leader = _pic("leader.jpg", stack_id=1, stack_position=0)
        member = _pic("member.jpg", stack_id=1, stack_position=1)
        pending_a = _pic("pending_a.jpg")
        pending_b = _pic("pending_b.jpg")
        settled = _pic("settled.jpg")

        # One group still waiting for a decision, one already ruled on. Both are
        # made of UNSTACKED pictures on purpose: it is what proves "unresolved"
        # is dedup-queue state and not a synonym for "stacked".
        waiting = DedupGroup(
            signature="sig-waiting",
            tier="exact",
            confidence=1.0,
            member_count=2,
            resolved=False,
        )
        ruled = DedupGroup(
            signature="sig-ruled",
            tier="exact",
            confidence=1.0,
            member_count=1,
            resolved=True,
        )
        session.add(waiting)
        session.add(ruled)
        session.flush()
        session.add(
            DedupGroupMember(group_id=waiting.id, picture_id=pending_a.id, position=0)
        )
        session.add(
            DedupGroupMember(group_id=waiting.id, picture_id=pending_b.id, position=1)
        )
        session.add(
            DedupGroupMember(group_id=ruled.id, picture_id=settled.id, position=0)
        )
        session.commit()
        return {
            "leader": leader.id,
            "member": member.id,
            "pending_a": pending_a.id,
            "pending_b": pending_b.id,
            "settled": settled.id,
        }

    return server.vault.db.run_task(_insert, priority=DBPriority.IMMEDIATE)


def _stream_ids(client, extra_params: str = "") -> set[int]:
    """The grid's own call shape. ``fields=grid`` implies ``stack_leaders_only``,
    so a stack contributes ONE row here: the tile the user sees."""
    resp = client.get(
        f"/pictures/stream?offset=0&batch_limit=500&fields=grid{extra_params}"
    )
    assert resp.status_code == 200, resp.text
    return {p["id"] for p in resp.json()["pictures"]}


def _stream_ids_uncollapsed(client, extra_params: str = "") -> set[int]:
    """The same listing without stack collapsing, so every member is a row.

    This is what separates "the filter selects stack members" from "the grid
    draws one tile per stack": the two are different layers and only this call
    shape can see the first one.
    """
    resp = client.get(f"/pictures/stream?offset=0&batch_limit=500{extra_params}")
    assert resp.status_code == 200, resp.text
    return {p["id"] for p in resp.json()["pictures"]}


def _count(client, extra_params: str = "") -> int:
    """``/pictures/count`` takes ``stack_leaders_only`` explicitly (it has no
    ``fields`` to infer it from), so the grid passes it and so does this."""
    resp = client.get(f"/pictures/count?stack_leaders_only=true{extra_params}")
    assert resp.status_code == 200, resp.text
    return resp.json()["count"]


def test_stack_state_stacked_narrows_the_stream():
    tmp, client, server = _setup_server()
    try:
        ids = _seed(server)
        # Collapsed, as the grid draws it: one tile for the stack.
        assert _stream_ids(client, "&stack_state=stacked") == {ids["leader"]}
        # Uncollapsed, the filter's own answer: every member of a stack, not
        # just its leader. Collapsing is the listing's job, not the filter's.
        assert _stream_ids_uncollapsed(client, "&stack_state=stacked") == {
            ids["leader"],
            ids["member"],
        }
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_stack_state_unstacked_narrows_the_stream():
    tmp, client, server = _setup_server()
    try:
        ids = _seed(server)
        assert _stream_ids(client, "&stack_state=unstacked") == {
            ids["pending_a"],
            ids["pending_b"],
            ids["settled"],
        }
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def _scrapheap(server, picture_id: int) -> None:
    """Soft-delete one picture, leaving its ``stack_id`` in place.

    That is what the app really does: a scrapheaped picture keeps its stack_id so
    a restore can put it back, and so undoing a collapse is a flag flip.
    """

    def _delete(session):
        pic = session.get(Picture, picture_id)
        pic.deleted = True
        session.commit()

    server.vault.db.run_task(_delete, priority=DBPriority.IMMEDIATE)


def test_a_stack_whose_only_sibling_is_scrapheaped_is_not_stacked():
    """The survivor of a collapsed stack is not in a stack any more.

    It still carries a ``stack_id``, deliberately, so a restore can put its
    sibling back. But one live picture is not a stack, and every other surface
    already agreed: ``_enrich_stack_counts`` counts live members only and
    ``StackBadge`` hides below two, so the grid draws that picture plain. The
    filter used to key on the column alone and serve it under ``stacked``, which
    is what the owner hit after collapsing a stack to its cover.
    """
    tmp, client, server = _setup_server()
    try:
        ids = _seed(server)
        _scrapheap(server, ids["member"])

        # The stack it was in is gone, so the survivor is not stacked...
        assert ids["leader"] not in _stream_ids_uncollapsed(
            client, "&stack_state=stacked"
        )
        # ...and it is not lost either: it now answers to the other half.
        assert ids["leader"] in _stream_ids_uncollapsed(
            client, "&stack_state=unstacked"
        )
        # The filter and what the grid draws must agree, which is the assertion
        # that would have caught this: stack_count is what decides the badge.
        resp = client.get("/pictures/stream?offset=0&batch_limit=500&fields=grid")
        assert resp.status_code == 200, resp.text
        survivor = next(p for p in resp.json()["pictures"] if p["id"] == ids["leader"])
        assert (survivor.get("stack_count") or 0) < 2
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_a_stack_with_two_live_members_is_still_stacked():
    """The over-blocking twin: scrapheaping must not empty the filter wholesale."""
    tmp, client, server = _setup_server()
    try:
        ids = _seed(server)
        _scrapheap(server, ids["pending_a"])  # not a stack member at all

        assert _stream_ids_uncollapsed(client, "&stack_state=stacked") == {
            ids["leader"],
            ids["member"],
        }
        assert ids["leader"] not in _stream_ids_uncollapsed(
            client, "&stack_state=unstacked"
        )
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_stack_state_unresolved_returns_only_undecided_group_members():
    tmp, client, server = _setup_server()
    try:
        ids = _seed(server)
        got = _stream_ids(client, "&stack_state=unresolved")
        assert got == {ids["pending_a"], ids["pending_b"]}
        # A group somebody already ruled on is not waiting for a decision.
        assert ids["settled"] not in got
        # Nor is a picture the detector never grouped.
        assert ids["leader"] not in got
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_stack_state_absent_returns_everything():
    """The other direction: over-filtering is its own regression.

    "All" is spelled as the absence of the parameter, so the unfiltered listing
    must be untouched by this change.
    """
    tmp, client, server = _setup_server()
    try:
        ids = _seed(server)
        collapsed = set(ids.values()) - {ids["member"]}
        assert _stream_ids(client) == collapsed
        assert _stream_ids_uncollapsed(client) == set(ids.values())
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_stack_state_counts_match_the_stream():
    """``/pictures/count`` and ``/pictures/stream`` build their queries in two
    separate places, so a filter can easily reach one and not the other. A count
    that disagrees with the grid is what drives the "infinite scroll never
    finishes" class of bug.
    """
    tmp, client, server = _setup_server()
    try:
        _seed(server)
        for state in ("stacked", "unstacked", "unresolved"):
            params = f"&stack_state={state}"
            assert _count(client, params) == len(_stream_ids(client, params)), state
        assert _count(client) == len(_stream_ids(client))
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_unrecognised_stack_state_does_not_empty_the_grid():
    """A junk value must fall back to "no filter", not to "no pictures".

    It is also the guard on the wiring: an unknown value is dropped in
    ``parse_request_params`` so it can never reach ``Picture.find`` as a stray
    kwarg.
    """
    tmp, client, server = _setup_server()
    try:
        _seed(server)
        unfiltered = _stream_ids(client)
        assert _stream_ids(client, "&stack_state=nonsense") == unfiltered
        # "all" is the frontend's word for "no filter" and is never sent, but it
        # must behave as the absence of the parameter if it ever is.
        assert _stream_ids(client, "&stack_state=all") == unfiltered
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()

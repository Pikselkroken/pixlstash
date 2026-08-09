"""``GET /picture_sets`` costs a constant number of queries (issue #651).

The endpoint used to read the COMPLETE member id list of every listed set into
Python, pass it back as an ``IN`` bind list to filter hidden tags, count it with
``len(set(...))``, and pass it back a third time to pick the top 3 previews.
Two consequences, both covered here:

* the query count grew with the number of sets, so the sidebar's set list was
  the dominant read on a large library;
* the bind list was unbounded, so a set with more visible members than
  ``SQLITE_LIMIT_VARIABLE_NUMBER`` (a compile-time constant: 250k on the build
  this was measured on, but as low as 32766 on others) failed the request
  outright.

The counts, the preview ids and the hidden-tag rule are unchanged, so the tests
below assert the OUTPUT is identical as well as the cost, per CLAUDE.md's rule
that a performance fix must not quietly change behaviour.
"""

import gc
import json
import os
import tempfile
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    PictureSetProjectMember,
    Project,
    Tag,
)
from pixlstash.server import Server


def _setup_server():
    tmp = tempfile.TemporaryDirectory()
    image_root = os.path.join(tmp.name, "images")
    os.makedirs(image_root, exist_ok=True)
    config_path = os.path.join(tmp.name, "server-config.json")
    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return tmp, client, server


def _seed_sets(server, set_count: int, members_per_set: int, hidden_every=None):
    """Create ``set_count`` sets, each with ``members_per_set`` pictures.

    Scores descend with the member index, so the expected top-3 preview for a
    set is simply its first three members. Every ``hidden_every``-th member of
    each set gets a ``hide_me`` tag, spelled in mixed case so the
    case-insensitive match is exercised rather than assumed.
    """

    def _insert(session):
        created = {}
        hidden = set()
        base = datetime(2026, 1, 1)
        for set_index in range(set_count):
            picture_set = PictureSet(name=f"Set {set_index:02d}")
            session.add(picture_set)
            session.flush()
            member_ids = []
            for member_index in range(members_per_set):
                picture = Picture(
                    file_path=f"s{set_index}_p{member_index}.jpg",
                    score=members_per_set - member_index,
                    imported_at=base + timedelta(minutes=member_index),
                )
                session.add(picture)
                session.flush()
                session.add(
                    PictureSetMember(set_id=picture_set.id, picture_id=picture.id)
                )
                member_ids.append(picture.id)
                if hidden_every and member_index % hidden_every == 0:
                    session.add(Tag(picture_id=picture.id, tag="Hide_Me"))
                    hidden.add(picture.id)
            created[picture_set.id] = member_ids
        session.commit()
        return created, hidden

    return server.vault.db.run_task(_insert, priority=DBPriority.IMMEDIATE)


def _seed_scoped_sets(server):
    """Seed the two member conditions the listing applies but nothing pinned.

    ``member_conditions`` in ``GET /picture_sets`` carries three filters. The
    hidden-tag one is covered by the tests above; the other two, ``deleted IS
    FALSE`` and the ``project_id`` membership predicate, survived deletion by
    every test in this file (R13 and R14 of the PR #706 QA review), because the
    fixture seeded no soft-deleted member and no project.

    Two sets, so both branches of the project filter are reachable:

    * ``Scoped Set`` belongs to a project, so it is the set listed under
      ``?project_id=<id>``. Members: one in the project, one in the project but
      soft-deleted, one outside it.
    * ``Unscoped Set`` belongs to no project, so it is the set listed under
      ``?project_id=UNASSIGNED``. That sentinel is a second branch of the same
      handler, and its character equivalent was a live scope bypass once, so it
      gets its own case rather than riding on the numeric one. Members mirror
      the above: one unassigned, one in the project, one unassigned but
      soft-deleted.

    Scores descend in the order the members are written, and no two are equal,
    so the expected preview list is simply the surviving members in that order.
    """

    def _insert(session):
        project = Project(name="Scoped Project")
        session.add(project)
        session.flush()
        base = datetime(2026, 1, 1)
        ids = {"project_id": project.id}

        def _picture(name, score, in_project, deleted):
            picture = Picture(
                file_path=f"{name}.jpg",
                score=score,
                imported_at=base + timedelta(minutes=score),
                deleted=deleted,
            )
            session.add(picture)
            session.flush()
            if in_project:
                session.add(
                    PictureProjectMember(picture_id=picture.id, project_id=project.id)
                )
            return picture.id

        scoped = PictureSet(name="Scoped Set")
        unscoped = PictureSet(name="Unscoped Set")
        session.add(scoped)
        session.add(unscoped)
        session.flush()
        session.add(PictureSetProjectMember(set_id=scoped.id, project_id=project.id))
        ids["scoped_set"] = scoped.id
        ids["unscoped_set"] = unscoped.id

        ids["in_project"] = _picture("in_project", 9, True, False)
        ids["in_project_deleted"] = _picture("in_project_deleted", 8, True, True)
        ids["out_of_project"] = _picture("out_of_project", 7, False, False)
        ids["unassigned"] = _picture("unassigned", 6, False, False)
        ids["assigned"] = _picture("assigned", 5, True, False)
        ids["unassigned_deleted"] = _picture("unassigned_deleted", 4, False, True)

        for key in ("in_project", "in_project_deleted", "out_of_project"):
            session.add(PictureSetMember(set_id=scoped.id, picture_id=ids[key]))
        for key in ("unassigned", "assigned", "unassigned_deleted"):
            session.add(PictureSetMember(set_id=unscoped.id, picture_id=ids[key]))

        session.commit()
        return ids

    return server.vault.db.run_task(_insert, priority=DBPriority.IMMEDIATE)


def test_soft_deleted_members_are_excluded_from_counts_and_previews():
    """R14: the listing must not count or preview a soft-deleted member.

    Asserted in both directions. A count of 3 means ``deleted IS FALSE`` was
    dropped from ``member_conditions``; a count of 1 means it over-filtered and
    took a live member with it.
    """
    tmp, client, server = _setup_server()
    try:
        ids = _seed_scoped_sets(server)

        resp = client.get("/picture_sets")
        assert resp.status_code == 200
        by_id = {row["id"]: row for row in resp.json()}

        scoped = by_id[ids["scoped_set"]]
        assert scoped["picture_count"] == 2
        assert scoped["top_picture_ids"] == [ids["in_project"], ids["out_of_project"]]

        unscoped = by_id[ids["unscoped_set"]]
        assert unscoped["picture_count"] == 2
        assert unscoped["top_picture_ids"] == [ids["unassigned"], ids["assigned"]]
    finally:
        server.close()
        tmp.cleanup()
        gc.collect()


def test_project_filter_applies_to_counts_and_previews():
    """R13: ``project_id`` narrows the members, not just the listed sets.

    The set-level filter and the member-level filter are separate conditions
    built from the same parameter, so a set can be correctly listed while its
    count and previews still come from every project. Both branches are
    covered: a numeric id and the ``UNASSIGNED`` sentinel.
    """
    tmp, client, server = _setup_server()
    try:
        ids = _seed_scoped_sets(server)

        resp = client.get(
            "/picture_sets", params={"project_id": str(ids["project_id"])}
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert [row["id"] for row in rows] == [ids["scoped_set"]]
        # Not 2: the out-of-project member is a member of this set, and the
        # soft-deleted one is in the project. Not 0: the in-project member must
        # survive, or the filter is excluding everything.
        assert rows[0]["picture_count"] == 1
        assert rows[0]["top_picture_ids"] == [ids["in_project"]]

        resp = client.get("/picture_sets", params={"project_id": "UNASSIGNED"})
        assert resp.status_code == 200
        rows = resp.json()
        assert [row["id"] for row in rows] == [ids["unscoped_set"]]
        assert rows[0]["picture_count"] == 1
        assert rows[0]["top_picture_ids"] == [ids["unassigned"]]
    finally:
        server.close()
        tmp.cleanup()
        gc.collect()


class _MemberQueryCounter:
    """Count the endpoint's set-membership reads while active.

    Deliberately NOT a count of every statement on the engine. The listener
    fires for all threads, and the WorkPlanner is running finders throughout a
    test, so a raw total measures background work rather than this request (an
    early version of this test failed for exactly that reason: 5 statements for
    2 sets and 28 for 10, none of the growth from the endpoint).

    The two queries this fix introduced are the only ones that read
    ``picturesetmember`` through a ``DISTINCT`` subquery, which is what makes
    them separable from a finder's per-picture membership lookup.
    """

    def __init__(self, server):
        self._engine = server.vault.db._engine
        self.count = 0

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        collapsed = " ".join(statement.split()).lower()
        if "picturesetmember" in collapsed and "distinct" in collapsed:
            self.count += 1

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info):
        event.remove(self._engine, "before_cursor_execute", self._on_execute)
        return False


def test_query_count_does_not_grow_with_the_number_of_sets():
    """The N+1 itself: two membership reads, whether there are 2 sets or 10.

    The old shape issued one members query per listed set (plus a hidden-tag
    query per set when the filter was on), so this count tracked the set count
    exactly.
    """
    tmp, client, server = _setup_server()
    try:
        _seed_sets(server, set_count=2, members_per_set=6)
        with _MemberQueryCounter(server) as small:
            resp = client.get("/picture_sets")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        _seed_sets(server, set_count=8, members_per_set=6)
        with _MemberQueryCounter(server) as large:
            resp = client.get("/picture_sets")
        assert resp.status_code == 200
        assert len(resp.json()) == 10

        assert small.count == 2, (
            f"expected 2 membership reads (counts + previews), got {small.count}"
        )
        assert large.count == 2, (
            f"membership reads grew with set count: 2 for 2 sets, "
            f"{large.count} for 10. The per-set loop is back."
        )
    finally:
        server.close()
        tmp.cleanup()
        gc.collect()


def test_counts_and_previews_are_unchanged():
    """Same numbers and same top-3 ordering as the per-set loop produced."""
    tmp, client, server = _setup_server()
    try:
        created, _ = _seed_sets(server, set_count=3, members_per_set=5)

        resp = client.get("/picture_sets")
        assert resp.status_code == 200
        by_id = {row["id"]: row for row in resp.json()}

        for set_id, member_ids in created.items():
            row = by_id[set_id]
            assert row["picture_count"] == 5
            # Scores descend with the member index, so the previews are the
            # first three members, in that order.
            assert row["top_picture_ids"] == member_ids[:3]
    finally:
        server.close()
        tmp.cleanup()
        gc.collect()


def test_hidden_tags_are_excluded_from_counts_and_previews():
    """The hidden-tag rule moved from a Python post-filter into SQL.

    Same rule either way: a picture is hidden when it carries ANY tag whose
    lowercased value is in the user's hidden list. The seeded tag is spelled
    ``Hide_Me`` and the configured one ``hide_me``, so a case-sensitive
    comparison would fail this test rather than pass it silently.
    """
    tmp, client, server = _setup_server()
    try:
        created, hidden = _seed_sets(
            server, set_count=2, members_per_set=6, hidden_every=2
        )
        assert hidden, "seed produced no hidden pictures"

        resp = client.patch("/users/me/config", json={"hidden_tags": ["hide_me"]})
        assert resp.status_code == 200

        # Without the flag the filter is off and every member still counts.
        resp = client.get("/picture_sets")
        assert resp.status_code == 200
        assert all(row["picture_count"] == 6 for row in resp.json())

        resp = client.get("/picture_sets?apply_tag_filter=true")
        assert resp.status_code == 200
        by_id = {row["id"]: row for row in resp.json()}

        for set_id, member_ids in created.items():
            visible = [pid for pid in member_ids if pid not in hidden]
            row = by_id[set_id]
            assert row["picture_count"] == len(visible)
            assert row["top_picture_ids"] == visible[:3]
            assert not set(row["top_picture_ids"]) & hidden
    finally:
        server.close()
        tmp.cleanup()
        gc.collect()


def test_set_thumbnail_applies_the_same_hidden_tag_rule():
    """The single-set thumbnail path had the same shape and the same fix.

    It picks the top 3 for the composite, so a hidden picture leaking into that
    selection would render a thumbnail the list endpoint says is not there.
    """
    tmp, client, server = _setup_server()
    try:
        created, hidden = _seed_sets(
            server, set_count=1, members_per_set=6, hidden_every=2
        )
        set_id, member_ids = next(iter(created.items()))
        visible = [pid for pid in member_ids if pid not in hidden]

        resp = client.patch("/users/me/config", json={"hidden_tags": ["hide_me"]})
        assert resp.status_code == 200

        assert visible, "seed produced no visible pictures"

        # These synthetic rows have no image files, so the composite itself
        # cannot render. What is assertable, and what the fix had to preserve,
        # is the SELECTION: the endpoint 404s only when it resolves no visible
        # member, so a 404 here would mean the new condition hides everything.
        resp = client.get(f"/picture_sets/{set_id}/thumbnail?apply_tag_filter=true")
        assert resp.status_code != 404, (
            "the thumbnail path found no visible members; the hidden-tag "
            "condition is excluding too much"
        )
    finally:
        server.close()
        tmp.cleanup()
        gc.collect()

"""Sidebar counts come from the list endpoints, at constant query cost (#651).

The sidebar used to fire one ``GET /characters/{id}/summary`` per character AND
one ``GET /projects/{id}/summary`` per project on every refresh, each request
carrying a single integer. ``GET /characters?include_counts=true`` and
``GET /projects?include_counts=true`` now serve those numbers inline, in a
constant number of grouped queries, so the fan-out can be deleted.

A performance fix must not quietly change behaviour (CLAUDE.md), and here the
behaviour IS a number, so the tests below pin the inline counts against the
per-id summary endpoints they replace - including the cases where a naive
implementation drifts:

* a picture in **several** projects (a per-project count must not be a share of
  a whole, and the global count must not double-count it);
* two faces of the **same** character on one picture (``count(distinct
  picture_id)``, not a row count);
* a character whose primary project is **NULL** (its scope is "belongs to no
  project", not "no filter");
* a **soft-deleted** picture, which counts nowhere.

Scope is pinned in both directions, because over-blocking is its own regression:
a character-scoped token still sees its own row *with* a global count that
matches the summary it is already granted (and no project-scoped number, which
it may ask for nowhere, issue #718), a picture_set-scoped token still gets
``[]``, and a project-scoped token still gets a real number counted over its own
project, consistent with the list it already gets.
"""

import gc
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Face,
    Picture,
    PictureProjectMember,
)
from pixlstash.server import Server
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"

# Every positive assertion here must reach a real route: the SPA catch-all
# answers unmatched GETs with 200, which would make a scope test vacuous.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


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
        f"{API}/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200, resp.text
    return tmp, client, server


def _mint(client, resource_type, resource_id):
    resp = client.post(
        f"{API}/users/me/token",
        json={
            "description": f"{resource_type}:{resource_id}",
            "scope": "READ",
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _add_pictures(server, specs):
    """Insert pictures with project memberships and character faces.

    Args:
        server: The running Server.
        specs: One ``(file_path, deleted, [project_id, ...], [character_id, ...])``
            per picture. A character id repeated in the list gets a second face
            on the same picture, which is how the ``distinct`` in the count is
            exercised rather than assumed.

    Returns:
        The created picture ids, in the order of *specs*.
    """

    def _insert(session):
        created = []
        for file_path, deleted, project_ids, character_ids in specs:
            picture = Picture(file_path=file_path, deleted=deleted)
            session.add(picture)
            session.flush()
            for project_id in project_ids:
                session.add(
                    PictureProjectMember(
                        picture_id=picture.id, project_id=int(project_id)
                    )
                )
            for face_index, character_id in enumerate(character_ids):
                session.add(
                    Face(
                        picture_id=picture.id,
                        frame_index=0,
                        face_index=face_index,
                        character_id=int(character_id),
                    )
                )
            created.append(picture.id)
        session.commit()
        return created

    return server.vault.db.run_task(_insert, priority=DBPriority.IMMEDIATE)


def _create_project(client, name):
    resp = client.post(f"{API}/projects", json={"name": name})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _create_character(client, name, project_ids=None):
    payload = {"name": name}
    if project_ids is not None:
        payload["project_ids"] = list(project_ids)
    resp = client.post(f"{API}/characters", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["character"]["id"]


class _GroupedCountQueryCounter:
    """Count the endpoints' inline-count reads while active.

    Deliberately NOT a count of every statement on the engine: the listener
    fires for every thread and the WorkPlanner runs its finders throughout a
    test, so a raw total measures background work rather than this request (the
    sibling suite ``test_picture_sets_query_cost`` records an early version
    failing for exactly that reason).

    Each needle is EVERY substring that must be present, not just one. Grouping
    alone is too loose to identify these statements: the same endpoint's
    has-reference-faces rollup also groups by ``face.character_id`` (it is
    written that way so SQLite reliably picks the partial index over
    ``ix_face_character_id``), and matching it here made this counter register
    reads that are not inline counts at all. Pairing the grouping with the
    aggregate is what actually distinguishes them, since only the count queries
    aggregate.

    This is the failure mode to watch: the needles describe the codebase as it
    is, not a property of the tests, so a new query elsewhere that happens to
    match turns the exact-equality assertions below into load-dependent
    failures. If that happens, tighten the needle rather than relaxing the
    assertion.
    """

    # ``count(distinct(...))`` with the inner parentheses, because the query
    # builds it as ``func.count(func.distinct(...))`` and SQLAlchemy renders
    # that nested call rather than the ``COUNT(DISTINCT x)`` keyword form.
    CHARACTER = ("group by face.character_id", "count(distinct(face.picture_id))")
    PROJECT = ("group by pictureprojectmember.project_id", "count(")

    def __init__(self, server, needle):
        self._engine = server.vault.db._engine
        self._needles = (needle,) if isinstance(needle, str) else tuple(needle)
        self.count = 0

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        collapsed = " ".join(statement.split()).lower()
        if all(needle in collapsed for needle in self._needles):
            self.count += 1

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info):
        event.remove(self._engine, "before_cursor_execute", self._on_execute)
        return False


@pytest.fixture
def env():
    """Owner client with the awkward shapes the counts have to survive.

    * ``P1``/``P2``/``P3`` - three projects; ``P3`` holds nothing, so a missing
      group is proven to default to 0 rather than to disappear.
    * ``shared`` - a character in P1 and P2 (primary P1).
    * ``loner`` - a character in no project at all (primary NULL).
    * ``p2_only`` - a character in P2 only.
    * pictures: one in BOTH P1 and P2, one in P1 only, two in P2 only, one
      unassigned, one soft-deleted, and one carrying two faces of the same
      character.

    ``shared`` deliberately has a DIFFERENT count in P1 (2) and P2 (3). An
    earlier fixture gave it 2 in both, and a P2-scoped token was then served
    P1's number without any assertion noticing.
    """
    tmp, client, server = _setup_server()
    try:
        p1 = _create_project(client, "P1")
        p2 = _create_project(client, "P2")
        p3 = _create_project(client, "P3")
        shared = _create_character(client, "SharedChar", [p1, p2])
        loner = _create_character(client, "LonerChar")
        p2_only = _create_character(client, "P2OnlyChar", [p2])

        picture_ids = _add_pictures(
            server,
            [
                # In both projects, and two faces of the same character on it.
                ("both.jpg", False, [p1, p2], [shared, shared]),
                ("p1.jpg", False, [p1], [shared]),
                ("p2.jpg", False, [p2], [shared, p2_only]),
                # P2 only, so shared's P2 count differs from its P1 count.
                ("p2b.jpg", False, [p2], [shared]),
                ("noproject.jpg", False, [], [shared, loner]),
                ("noproject2.jpg", False, [], [loner]),
                # Soft-deleted: counts nowhere, in any scope.
                ("deleted.jpg", True, [p1, p2], [shared, loner, p2_only]),
            ],
        )

        yield {
            "client": client,
            "server": server,
            "projects": {"P1": p1, "P2": p2, "P3": p3},
            "characters": {
                "shared": shared,
                "loner": loner,
                "p2_only": p2_only,
            },
            "picture_ids": picture_ids,
        }
    finally:
        server.close()
        tmp.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# The numbers: identical to the per-id summaries they replace
# ---------------------------------------------------------------------------


def _summary_count(client, character_id, project_id=None, headers=None):
    params = {} if project_id is None else {"project_id": project_id}
    resp = client.get(
        f"{API}/characters/{character_id}/summary", params=params, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["image_count"]


def test_character_counts_equal_the_per_id_summary(env):
    """Every listed row, both scopes, against the endpoint it replaces."""
    client = env["client"]
    resp = client.get(f"{API}/characters", params={"include_counts": "true"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert {row["name"] for row in rows} == {
        "SharedChar",
        "LonerChar",
        "P2OnlyChar",
    }

    for row in rows:
        assert row["image_count"] == _summary_count(client, row["id"]), (
            f"global count drifted from the summary for {row['name']}"
        )
        scope = row["project_id"] if row["project_id"] is not None else "UNASSIGNED"
        assert row["project_image_count"] == _summary_count(
            client, row["id"], project_id=scope
        ), f"project count drifted from the summary for {row['name']}"


def test_character_counts_are_the_expected_numbers(env):
    """Pinned literally, so a change that keeps both sides equally wrong fails.

    ``SharedChar`` is on 5 non-deleted pictures (one of them twice, via two
    faces) and its primary project is P1, which holds 2 of them - not P2's 3,
    and not the whole 5. ``LonerChar`` has no project, so its project scope is
    "belongs to no project": 2 of its 2 non-deleted pictures.
    """
    client = env["client"]
    rows = client.get(f"{API}/characters", params={"include_counts": "true"}).json()
    by_name = {row["name"]: row for row in rows}

    assert by_name["SharedChar"]["image_count"] == 5
    assert by_name["SharedChar"]["project_image_count"] == 2
    assert by_name["LonerChar"]["image_count"] == 2
    assert by_name["LonerChar"]["project_image_count"] == 2
    assert by_name["P2OnlyChar"]["image_count"] == 1
    assert by_name["P2OnlyChar"]["project_image_count"] == 1


def test_project_counts_equal_the_per_id_summary(env):
    """Including the empty project, whose group is absent from the SQL result."""
    client = env["client"]
    resp = client.get(f"{API}/projects", params={"include_counts": "true"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 3

    for row in rows:
        summary = client.get(f"{API}/projects/{row['id']}/summary")
        assert summary.status_code == 200, summary.text
        assert row["image_count"] == summary.json()["image_count"], (
            f"inline count drifted from the summary for project {row['name']}"
        )

    by_name = {row["name"]: row for row in rows}
    assert by_name["P1"]["image_count"] == 2
    assert by_name["P2"]["image_count"] == 3
    assert by_name["P3"]["image_count"] == 0


def test_counts_are_absent_unless_requested(env):
    """Opt-in: the default response is unchanged and pays for no counting."""
    client = env["client"]
    with _GroupedCountQueryCounter(
        env["server"], _GroupedCountQueryCounter.CHARACTER
    ) as counter:
        rows = client.get(f"{API}/characters").json()
    assert counter.count == 0, "counted without being asked to"
    assert all(row["image_count"] is None for row in rows)
    assert all(row["project_image_count"] is None for row in rows)

    with _GroupedCountQueryCounter(
        env["server"], _GroupedCountQueryCounter.PROJECT
    ) as counter:
        rows = client.get(f"{API}/projects").json()
    assert counter.count == 0, "counted without being asked to"
    assert all(row["image_count"] is None for row in rows)


# ---------------------------------------------------------------------------
# The cost: constant in the number of rows
# ---------------------------------------------------------------------------


def test_character_count_queries_do_not_grow_with_the_number_of_characters(env):
    """The fan-out itself: the same query count for 3 characters and 11.

    The replaced shape was one HTTP request *and* one COUNT query per
    character, so this number tracked the character count exactly.
    """
    client, server = env["client"], env["server"]
    with _GroupedCountQueryCounter(
        server, _GroupedCountQueryCounter.CHARACTER
    ) as small:
        resp = client.get(f"{API}/characters", params={"include_counts": "true"})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 3

    for index in range(8):
        _create_character(client, f"Extra{index:02d}", [env["projects"]["P1"]])

    with _GroupedCountQueryCounter(
        server, _GroupedCountQueryCounter.CHARACTER
    ) as large:
        resp = client.get(f"{API}/characters", params={"include_counts": "true"})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 11

    assert small.count == large.count, (
        f"count queries grew with the character count: {small.count} for 3 "
        f"characters, {large.count} for 11. The per-character loop is back."
    )
    # Exactly three: one global, one for the characters counted against their
    # own primary project, one for the character that has none. Pinned as an
    # equality so a needle that stops matching cannot pass this test with 0.
    assert small.count == 3, (
        f"expected 3 grouped counts (global + primary-project + no-project), "
        f"got {small.count}"
    )


def test_project_count_queries_do_not_grow_with_the_number_of_projects(env):
    """One grouped count for the whole listing, at 3 projects and at 11."""
    client, server = env["client"], env["server"]
    with _GroupedCountQueryCounter(server, _GroupedCountQueryCounter.PROJECT) as small:
        resp = client.get(f"{API}/projects", params={"include_counts": "true"})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 3

    for index in range(8):
        _create_project(client, f"Extra{index:02d}")

    with _GroupedCountQueryCounter(server, _GroupedCountQueryCounter.PROJECT) as large:
        resp = client.get(f"{API}/projects", params={"include_counts": "true"})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 11

    assert small.count == large.count == 1, (
        f"expected exactly 1 grouped count either way, got {small.count} for 3 "
        f"projects and {large.count} for 11"
    )


# ---------------------------------------------------------------------------
# Scope, both directions
# ---------------------------------------------------------------------------


def test_character_scoped_token_sees_only_its_own_row_with_a_matching_count(env):
    """In-scope 200 with the right global number; nothing else in the list.

    ``image_count`` must equal what the *same token* gets from
    ``/characters/{id}/summary`` - the endpoint it is already granted - which
    is the whole authorization argument for serving it inline: no new fact is
    disclosed. That cross-check is the point of this test and must keep
    working.

    ``project_image_count`` is ``null``, and the rule is the complement of the
    one above: a character-scoped token may learn no project id at all (issue
    #125 / R1b), so it may name no project scope on that summary endpoint, so
    there is no project-scoped number it could have obtained there and none is
    served here (issue #718).

    **Both halves of that complement are asserted, on purpose.** Suppressing
    here is only sound while the summary endpoint really does refuse the same
    question, so the 403 is pinned below beside the ``None``. An adversarial
    review of #718 caught this test asserting only its own half: on the base it
    ran against, ``enforce_project_filter_scope`` had not landed yet and that
    summary call still answered ``1``, so the "complement" argument in
    ``_may_learn_a_project_scoped_count`` was false and nothing here said so.
    Keep the pair. If a future change relaxes the filter gate, this test is
    where the two endpoints stop agreeing.

    """
    client, server = env["client"], env["server"]
    shared = env["characters"]["shared"]
    headers = _mint(client, "character", shared)
    anon = TestClient(server.api)

    resp = anon.get(
        f"{API}/characters", params={"include_counts": "true"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [row["id"] for row in rows] == [shared], (
        "a character-scoped token must see exactly its own character"
    )

    row = rows[0]
    assert row["project_id"] is None
    assert row["project_ids"] == []
    assert row["image_count"] == _summary_count(anon, shared, headers=headers)
    # In-scope and still correct: 5 non-deleted pictures, one of them via two
    # faces. Over-blocking the global count would be its own regression.
    assert row["image_count"] == 5
    assert row["project_image_count"] is None, (
        "a token with no project visibility must be told nothing about projects"
    )
    # The other half of the complement: the same question asked directly is
    # refused (#708). Without this, suppressing above would look sound while the
    # summary endpoint quietly served the number anyway.
    refused = anon.get(
        f"{API}/characters/{shared}/summary",
        params={"project_id": "UNASSIGNED"},
        headers=headers,
    )
    assert refused.status_code == 403, refused.text

    # Out of scope: the sibling summary for another character is still refused.
    other = anon.get(
        f"{API}/characters/{env['characters']['loner']}/summary", headers=headers
    )
    assert other.status_code == 403, other.text


def test_character_scoped_token_learns_nothing_about_invisible_projects(env):
    """The suppression keys off the TOKEN, not off what the row turned out to be.

    ``SharedChar`` is in P1 and P2; ``LonerChar`` is in no project at all. A
    character-scoped token must get the same answer for both, which is no
    answer, because a rule that suppressed only the row with hidden memberships
    would make the *presence* of a number the oracle it was meant to remove: a
    served count would say "no project you cannot see holds this character".

    It must also be impossible to recover the hidden number by arithmetic:
    ``image_count - project_image_count`` was previously the count of the
    token's own pictures held by projects invisible to it (issue #718).
    """
    client, server = env["client"], env["server"]
    anon = TestClient(server.api)

    for name in ("shared", "loner"):
        character_id = env["characters"][name]
        headers = _mint(client, "character", character_id)
        resp = anon.get(
            f"{API}/characters", params={"include_counts": "true"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [row["id"] for row in rows] == [character_id]
        assert rows[0]["project_image_count"] is None, (
            f"{name}: a project-derived aggregate reached a token with no "
            "project visibility"
        )
        assert rows[0]["image_count"] is not None, (
            f"{name}: the global count is in scope and must still be served"
        )


def test_owner_still_gets_every_project_count_including_the_unassigned_one(env):
    """The complement direction: full visibility keeps every number it had.

    The owner's ``visible_project_ids`` is ``None`` (no restriction), so the
    suppression must not fire, including for ``LonerChar``, whose count comes
    from the genuine "in no project at all" bucket that the fix leaves intact
    for callers entitled to it.
    """
    client = env["client"]
    rows = client.get(f"{API}/characters", params={"include_counts": "true"}).json()
    by_name = {row["name"]: row for row in rows}

    assert all(row["project_image_count"] is not None for row in rows), (
        "the owner lost a project count it is entitled to"
    )
    assert by_name["LonerChar"]["project_image_count"] == 2
    assert by_name["SharedChar"]["project_image_count"] == 2
    assert by_name["P2OnlyChar"]["project_image_count"] == 1


def test_picture_set_scoped_token_still_gets_an_empty_list(env):
    """Adding a query param must not open a door: a set token sees no character."""
    client, server = env["client"], env["server"]
    resp = client.post(f"{API}/picture_sets", json={"name": "SomeSet"})
    assert resp.status_code in (200, 201), resp.text
    set_id = resp.json()["picture_set"]["id"]

    headers = _mint(client, "picture_set", set_id)
    anon = TestClient(server.api)

    for params in ({"include_counts": "true"}, {}):
        resp = anon.get(f"{API}/characters", params=params, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == [], f"picture_set token got characters with {params}"

    # The projects listing is refused outright for a non-project scope.
    resp = anon.get(
        f"{API}/projects", params={"include_counts": "true"}, headers=headers
    )
    assert resp.status_code == 403, resp.text


def test_project_scoped_token_counts_match_its_own_summaries(env):
    """The project token's list is filtered, and every count it does get holds.

    P2's token sees the two characters in P2 (never ``LonerChar``), and both
    the global and the project-scoped inline counts equal the summaries the
    same token may call. The project listing is narrowed to P2 and its
    ``image_count`` equals P2's own summary.
    """
    client, server = env["client"], env["server"]
    p2 = env["projects"]["P2"]
    headers = _mint(client, "project", p2)
    anon = TestClient(server.api)

    resp = anon.get(
        f"{API}/characters", params={"include_counts": "true"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert {row["name"] for row in rows} == {"SharedChar", "P2OnlyChar"}, (
        "a project token must see its project's characters and no others"
    )

    for row in rows:
        assert row["project_id"] == p2, (
            "a project token may only learn its own project id"
        )
        # A project token HAS project visibility, so the #718 suppression must
        # not fire for it: this is the number a careless fix blanks.
        assert row["project_image_count"] is not None, (
            "a project-scoped token lost the count for its own project"
        )
        assert row["image_count"] == _summary_count(anon, row["id"], headers=headers)
        assert row["project_image_count"] == _summary_count(
            anon, row["id"], project_id=p2, headers=headers
        )

    # SharedChar's primary project is P1 (2 pictures), but this token is told
    # P2 - so the count it gets must be P2's 3, not the primary project's 2.
    by_name = {row["name"]: row for row in rows}
    assert by_name["SharedChar"]["project_image_count"] == 3

    resp = anon.get(
        f"{API}/projects", params={"include_counts": "true"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert [row["id"] for row in listed] == [p2]
    summary = anon.get(f"{API}/projects/{p2}/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    assert listed[0]["image_count"] == summary.json()["image_count"]

"""Child sets (#2124): a picture set under another set or under a person.

One server for the module: every test makes its own sets and people and
asserts on their ids, so what earlier tests left behind does not matter.
"""

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from pixlstash.server import Server
from pixlstash.utils.service.filter_helpers import narrow_project_fields


@pytest.fixture(scope="module")
def client():
    with tempfile.TemporaryDirectory() as temp_dir:
        os.makedirs(os.path.join(temp_dir, "images"), exist_ok=True)
        config_path = os.path.join(temp_dir, "server-config.json")
        with open(config_path, "w") as f:
            json.dump({"port": 8000}, f)
        with Server(config_path) as server:
            c = TestClient(server.api)
            r = c.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert r.status_code == 200, r.text
            yield c


def _set(client, name, **parent):
    r = client.post("/picture_sets", json={"name": name, **parent})
    assert r.status_code == 200, r.text
    return r.json()["picture_set"]["id"]


def _person(client, name):
    r = client.post("/characters", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["character"]["id"]


def _row(client, set_id):
    rows = client.get("/picture_sets").json()
    return next((s for s in rows if s["id"] == set_id), None)


def test_a_set_is_created_under_a_set_or_a_person(client):
    wren = _person(client, "Wren")
    parent = _set(client, "Rainy neon streets")
    under_person = _set(client, "LoRA v3", parent_character_id=wren)
    under_set = _set(client, "Neon concept v1", parent_set_id=parent)

    assert _row(client, under_person)["parent_character_id"] == wren
    assert _row(client, under_person)["parent_set_id"] is None
    assert _row(client, under_set)["parent_set_id"] == parent
    assert _row(client, parent)["parent_set_id"] is None

    # One parent at most, and it has to exist.
    r = client.post(
        "/picture_sets",
        json={"name": "both", "parent_set_id": parent, "parent_character_id": wren},
    )
    assert r.status_code == 400, r.text
    assert (
        client.post(
            "/picture_sets", json={"name": "gone", "parent_set_id": 99999}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/picture_sets", json={"name": "nobody", "parent_character_id": 99999}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/picture_sets", json={"name": "text", "parent_set_id": "1"}
        ).status_code
        == 400
    )


def test_a_set_moves_and_cannot_become_its_own_ancestor(client):
    juno = _person(client, "Juno")
    top = _set(client, "Top")
    mid = _set(client, "Mid", parent_set_id=top)
    leaf = _set(client, "Leaf", parent_set_id=mid)

    # Any depth is fine; a loop is not.
    for bad in (top, mid, leaf):
        r = client.patch(f"/picture_sets/{top}", json={"parent_set_id": bad})
        assert r.status_code == 400, (bad, r.text)
    assert _row(client, top)["parent_set_id"] is None

    # Moving into a person replaces the set parent: the pair is given whole.
    r = client.patch(f"/picture_sets/{leaf}", json={"parent_character_id": juno})
    assert r.status_code == 200, r.text
    row = _row(client, leaf)
    assert (row["parent_set_id"], row["parent_character_id"]) == (None, juno)

    # Move out: both null is the top level.
    r = client.patch(
        f"/picture_sets/{mid}",
        json={"parent_set_id": None, "parent_character_id": None},
    )
    assert r.status_code == 200, r.text
    assert _row(client, mid)["parent_set_id"] is None

    # A patch that names no parent leaves it alone.
    r = client.patch(f"/picture_sets/{leaf}", json={"description": "x"})
    assert r.status_code == 200, r.text
    assert _row(client, leaf)["parent_character_id"] == juno


def test_a_locked_set_does_not_move(client):
    parent = _set(client, "Home")
    locked = _set(client, "Frozen", parent_set_id=parent)
    assert (
        client.patch(f"/picture_sets/{locked}", json={"locked": True}).status_code
        == 200
    )
    r = client.patch(f"/picture_sets/{locked}", json={"parent_set_id": None})
    assert r.status_code == 423, r.text
    assert _row(client, locked)["parent_set_id"] == parent
    client.patch(f"/picture_sets/{locked}", json={"locked": False})


def test_deleting_a_parent_lifts_its_children_to_the_top(client):
    bo = _person(client, "Bo")
    parent = _set(client, "Doomed")
    child = _set(client, "Survivor", parent_set_id=parent)
    persons_child = _set(client, "Bo LoRA", parent_character_id=bo)

    assert client.delete(f"/picture_sets/{parent}").status_code == 200
    assert _row(client, child)["parent_set_id"] is None

    # Deleting a person never deletes a set.
    assert client.delete(f"/characters/{bo}").status_code == 200
    row = _row(client, persons_child)
    assert row is not None
    assert row["parent_character_id"] is None
    # SQLite reuses the id: the next person must not inherit the set.
    newcomer = _person(client, "Newcomer")
    assert _row(client, persons_child)["parent_character_id"] != newcomer


def test_a_scoped_reader_does_not_learn_a_parent():
    payload = {"id": 1, "parent_set_id": 7, "parent_character_id": 3}
    narrow_project_fields(payload, [], {5})
    assert (payload["parent_set_id"], payload["parent_character_id"]) == (None, None)
    owner = {"id": 1, "parent_set_id": 7, "parent_character_id": 3}
    narrow_project_fields(owner, [], None)
    assert (owner["parent_set_id"], owner["parent_character_id"]) == (7, 3)
    # Rows that are not sets gain no parent keys.
    character = {"id": 2}
    narrow_project_fields(character, [], {5})
    assert "parent_set_id" not in character


def test_sets_nest_three_levels_at_most(client):
    ash = _person(client, "Ash")
    a = _set(client, "A", parent_character_id=ash)
    b = _set(client, "B", parent_set_id=a)
    # Ash > A > B is three levels; a fourth is refused.
    r = client.post("/picture_sets", json={"name": "C", "parent_set_id": b})
    assert r.status_code == 400, r.text

    # Under a top-level set the same: T > U > V and no deeper.
    t = _set(client, "T")
    u = _set(client, "U", parent_set_id=t)
    _set(client, "V", parent_set_id=u)
    assert (
        client.post("/picture_sets", json={"name": "W", "parent_set_id": u}).status_code
        == 200
    )

    # A moved set brings its children: X > Y fits under Ash, not under A.
    x = _set(client, "X")
    _set(client, "Y", parent_set_id=x)
    r = client.patch(f"/picture_sets/{x}", json={"parent_set_id": a})
    assert r.status_code == 400, r.text
    assert _row(client, x)["parent_set_id"] is None
    r = client.patch(f"/picture_sets/{x}", json={"parent_character_id": ash})
    assert r.status_code == 200, r.text
    # A set two deep (P > Q > R) has nowhere to go but the top level.
    p = _set(client, "P")
    q = _set(client, "Q", parent_set_id=p)
    _set(client, "R", parent_set_id=q)
    r = client.patch(f"/picture_sets/{p}", json={"parent_character_id": ash})
    assert r.status_code == 400, r.text

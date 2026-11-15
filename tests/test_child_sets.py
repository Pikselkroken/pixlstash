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
from pixlstash.services import comfyui_service
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
    # Nor what a training set being built holds: it names other pictures.
    building = {"id": 1, "building": {"run_keys": ["run-x"]}}
    narrow_project_fields(building, [], {5})
    assert building["building"] is None
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


def test_a_training_set_keeps_its_build_until_it_is_finished(client):
    mira = _person(client, "Mira")
    build = {"v": 1, "stage": "generate", "run_keys": []}
    set_id = _set(client, "Mira training set", parent_character_id=mira, building=build)
    assert _row(client, set_id)["building"] == build

    later = {**build, "stage": "pick", "line": {"picked": 40, "passing": 52}}
    r = client.patch(f"/picture_sets/{set_id}", json={"building": later})
    assert r.status_code == 200, r.text
    assert _row(client, set_id)["building"] == later
    # A PATCH that does not name it leaves it alone.
    client.patch(f"/picture_sets/{set_id}", json={"description": "x"})
    assert _row(client, set_id)["building"] == later

    for bad in ([1], "pick", 3):
        r = client.patch(f"/picture_sets/{set_id}", json={"building": bad})
        assert r.status_code == 400, (bad, r.text)
    too_big = {"blob": "x" * (300 * 1024)}
    r = client.patch(f"/picture_sets/{set_id}", json={"building": too_big})
    assert r.status_code == 400, r.text
    assert _row(client, set_id)["building"] == later

    assert (
        client.patch(f"/picture_sets/{set_id}", json={"building": None}).status_code
        == 200
    )
    assert _row(client, set_id)["building"] is None
    # Every other set is finished from the start.
    assert _row(client, _set(client, "Plain"))["building"] is None


def test_deleting_a_set_being_built_stops_its_sheet_runs_and_no_others(client):
    noor = _person(client, "Noor")
    ours = comfyui_service.new_run_id()
    theirs = comfyui_service.new_run_id()
    comfyui_service.hold_run(ours, "http://comfy.invalid", label="Character sheet")
    comfyui_service.hold_run(theirs, "http://comfy.invalid", label="Something else")
    try:
        building = {"v": 1, "stage": "generate", "run_keys": [ours]}
        set_id = _set(
            client, "Noor training set", parent_character_id=noor, building=building
        )
        # A finished set names no runs: deleting it stops nothing.
        plain = _set(client, "Noor finished")
        r = client.delete(f"/picture_sets/{plain}")
        assert r.status_code == 200, r.text
        assert r.json()["stopped_runs"] == 0
        assert comfyui_service.run_outcome(ours)["status"] == "waiting"

        r = client.delete(f"/picture_sets/{set_id}")
        assert r.status_code == 200, r.text
        assert r.json()["stopped_runs"] == 1
        assert _row(client, set_id) is None
        outcome = comfyui_service.run_outcome(ours)
        assert (outcome["status"], outcome["stopped"]) == ("failed", True)
        assert comfyui_service.run_outcome(theirs)["status"] == "waiting"
    finally:
        comfyui_service.cancel_held_runs(ours)
        comfyui_service.cancel_held_runs(theirs)


def test_cancel_runs_takes_only_its_prompts_off_comfyui(monkeypatch):
    calls = []

    class Answer:
        status_code = 200
        text = ""

    def post(url, json=None, timeout=None):
        calls.append((url, json))
        return Answer()

    monkeypatch.setattr(comfyui_service.requests, "post", post)
    base = "http://comfy.invalid"
    live = {
        "p-running": {"status": "running"},
        "p-queued": {"status": "queued"},
        "p-other": {"status": "queued"},
    }
    with comfyui_service._run_outcomes_lock:
        for pid, extra in live.items():
            comfyui_service._live_runs[pid] = {
                "run_id": f"run-{pid}",
                "prompt_id": pid,
                "base_url": base,
                **extra,
            }
            comfyui_service._run_ids[f"run-{pid}"] = pid
    try:
        stopped = comfyui_service.cancel_runs(["run-p-running", "p-queued", "run-gone"])
    finally:
        with comfyui_service._run_outcomes_lock:
            for pid in live:
                comfyui_service._live_runs.pop(pid, None)
                comfyui_service._run_ids.pop(f"run-{pid}", None)
    assert stopped == 2
    assert calls == [
        (f"{base}/interrupt", {"prompt_id": "p-running"}),
        (f"{base}/queue", {"delete": ["p-running"]}),
        (f"{base}/queue", {"delete": ["p-queued"]}),
    ]

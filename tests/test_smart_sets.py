"""Smart picture sets (#2148): sets whose members come from a saved filter.

One server for the module. Every test makes its own pictures and sets and
asserts on their ids, and each rule names a tag no other test uses, so what
earlier tests left behind does not matter. Background workers are off so no
tagger rewrites the seeded tags; the refresh is called directly, except in the
one test that proves a library event schedules it.
"""

import json
import os
import tempfile
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from pixlstash.db_models import Picture, PictureSetMember, Tag
from pixlstash.server import Server
from pixlstash.services.smart_sets import SmartFilterError, normalise_filter
from pixlstash.utils.service import smart_set_refresher
from pixlstash.utils.service.filter_helpers import narrow_project_fields
from pixlstash.utils.service.smart_set_refresher import refresh_smart_sets


@pytest.fixture(scope="module")
def env():
    with tempfile.TemporaryDirectory() as temp_dir:
        os.makedirs(os.path.join(temp_dir, "images"), exist_ok=True)
        config_path = os.path.join(temp_dir, "server-config.json")
        with open(config_path, "w") as f:
            json.dump({"port": 8000, "disable_background_workers": True}, f)
        with Server(config_path) as server:
            client = TestClient(server.api)
            r = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert r.status_code == 200, r.text
            yield server, client


def _pictures(server, tag, scores):
    """One picture per score, each tagged ``tag``; their ids in order."""

    def insert(session):
        ids = []
        for score in scores:
            token = uuid.uuid4().hex
            pic = Picture(
                file_path=f"/tmp/pixlstash-smart-test/{token}.png",
                format="png",
                width=64,
                height=64,
                pixel_sha=token,
                score=score,
            )
            session.add(pic)
            session.flush()
            session.add(Tag(picture_id=pic.id, tag=tag))
            ids.append(int(pic.id))
        session.commit()
        return ids

    return server.vault.db.run_task(insert)


def _set_score(server, picture_id, score):
    def write(session):
        session.get(Picture, picture_id).score = score
        session.commit()

    server.vault.db.run_task(write)


def _smart(client, name, query, **extra):
    r = client.post(
        "/picture_sets", json={"name": name, "smart_filter": query, **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()["picture_set"]["id"]


def _members(client, set_id):
    r = client.get(f"/picture_sets/{set_id}/members")
    assert r.status_code == 200, r.text
    return set(r.json()["picture_ids"])


def _row(client, set_id):
    return next(s for s in client.get("/picture_sets").json() if s["id"] == set_id)


def test_the_rule_keeps_only_what_says_which_pictures():
    stored = normalise_filter(
        "tag=portrait&min_score=4&sort=DATE&descending=true&fields=grid"
        "&project_id=3&apply_tag_filter=true&query=cat&only_deleted=true"
    )
    assert stored == "tag=portrait&min_score=4"
    for nothing in ("", "sort=DATE&fields=grid", "project_id=2"):
        with pytest.raises(SmartFilterError):
            normalise_filter(nothing)


def test_a_smart_set_holds_what_its_filter_matches_and_follows_it(env):
    server, client = env
    low, high, top = _pictures(server, "smart-follow", [2, 4, 5])
    set_id = _smart(
        client,
        "Best follow",
        "tag=smart-follow&min_score=4&sort=DATE",
        smart_summary="Tag smart-follow · Score 4+",
    )

    assert _members(client, set_id) == {high, top}
    row = _row(client, set_id)
    assert row["picture_count"] == 2
    assert row["smart"]["filter"] == "tag=smart-follow&min_score=4"
    assert row["smart"]["summary"] == "Tag smart-follow · Score 4+"
    assert row["smart"]["custom"] is False

    _set_score(server, low, 5)
    _set_score(server, top, 1)
    refresh_smart_sets(server)
    assert _members(client, set_id) == {low, high}


def test_an_empty_filter_is_refused(env):
    _, client = env
    r = client.post(
        "/picture_sets", json={"name": "Nothing", "smart_filter": "sort=DATE"}
    )
    assert r.status_code == 400, r.text


def test_the_owners_edits_win_until_reset(env):
    server, client = env
    a, b, outsider = _pictures(server, "smart-edits", [5, 5, 1])
    set_id = _smart(client, "Edited", "tag=smart-edits&min_score=4")
    assert _members(client, set_id) == {a, b}

    assert client.delete(f"/picture_sets/{set_id}/members/{a}").status_code == 200
    r = client.post(f"/picture_sets/{set_id}/members", json={"picture_ids": [outsider]})
    assert r.status_code == 200, r.text
    refresh_smart_sets(server)
    # Taken out stays out, put in stays in, though the filter says otherwise.
    assert _members(client, set_id) == {b, outsider}
    assert _row(client, set_id)["smart"]["custom"] is True

    # The owner's add is theirs even when the filter stops matching a picture
    # it also matched.
    _set_score(server, b, 1)
    refresh_smart_sets(server)
    assert _members(client, set_id) == {outsider}

    r = client.patch(f"/picture_sets/{set_id}", json={"smart_reset": True})
    assert r.status_code == 200, r.text
    assert _members(client, set_id) == {a}
    assert _row(client, set_id)["smart"]["custom"] is False


def test_putting_back_a_removed_picture_lets_the_filter_keep_it(env):
    server, client = env
    (a,) = _pictures(server, "smart-putback", [5])
    set_id = _smart(client, "Put back", "tag=smart-putback")
    client.delete(f"/picture_sets/{set_id}/members/{a}")
    client.post(f"/picture_sets/{set_id}/members", json={"picture_ids": [a]})
    refresh_smart_sets(server)
    assert _members(client, set_id) == {a}
    assert _row(client, set_id)["smart"]["removed"] == []


def test_a_locked_smart_set_is_a_snapshot(env):
    server, client = env
    a, b = _pictures(server, "smart-lock", [5, 2])
    set_id = _smart(client, "Frozen", "tag=smart-lock&min_score=4")
    assert (
        client.patch(f"/picture_sets/{set_id}", json={"locked": True}).status_code
        == 200
    )

    _set_score(server, b, 5)
    refresh_smart_sets(server)
    assert _members(client, set_id) == {a}
    r = client.patch(f"/picture_sets/{set_id}", json={"smart_reset": True})
    assert r.status_code == 200, r.text  # nothing to reset, so not an edit

    # Unlocking brings it up to date at once.
    assert (
        client.patch(f"/picture_sets/{set_id}", json={"locked": False}).status_code
        == 200
    )
    assert _members(client, set_id) == {a, b}


def test_a_locked_custom_set_refuses_a_reset(env):
    server, client = env
    a, b = _pictures(server, "smart-lockreset", [5, 5])
    set_id = _smart(client, "Frozen custom", "tag=smart-lockreset")
    client.delete(f"/picture_sets/{set_id}/members/{a}")
    client.patch(f"/picture_sets/{set_id}", json={"locked": True})
    r = client.patch(f"/picture_sets/{set_id}", json={"smart_reset": True})
    assert r.status_code == 423, r.text
    client.patch(f"/picture_sets/{set_id}", json={"locked": False})
    assert _members(client, set_id) == {b}


def test_a_sets_projects_scope_its_filter(env):
    server, client = env
    inside, outside = _pictures(server, "smart-project", [5, 5])
    r = client.post("/projects", json={"name": "Smart scope"})
    assert r.status_code == 200, r.text
    project_id = r.json()["id"]
    r = client.patch(
        "/pictures/project", json={"picture_ids": [inside], "project_id": project_id}
    )
    assert r.status_code == 200, r.text

    set_id = _smart(
        client,
        "Scoped",
        f"tag=smart-project&project_id={project_id}",
        project_ids=[project_id],
    )
    assert _members(client, set_id) == {inside}
    assert "project_id" not in _row(client, set_id)["smart"]["filter"]

    # Out of every project, the filter covers the whole library.
    client.patch(f"/picture_sets/{set_id}", json={"project_ids": []})
    assert _members(client, set_id) == {inside, outside}


def test_a_library_event_refreshes_smart_sets(env, monkeypatch):
    server, client = env
    a, b = _pictures(server, "smart-event", [5, 1])
    set_id = _smart(client, "Live", "tag=smart-event&min_score=4")
    monkeypatch.setattr(smart_set_refresher, "REFRESH_WINDOW_S", 0.05)

    r = client.patch(f"/pictures/{b}", json={"score": 5})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10
    while _members(client, set_id) != {a, b} and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _members(client, set_id) == {a, b}

    def rule_rows(session):
        return {
            int(m.picture_id)
            for m in session.query(PictureSetMember)
            .filter(PictureSetMember.set_id == set_id)
            .filter(PictureSetMember.smart_auto.is_(True))
        }

    assert server.vault.db.run_task(rule_rows) == {a, b}


def test_a_scoped_caller_does_not_see_the_rule():
    payload = {"smart": {"filter": "character_id=3", "removed": []}}
    narrow_project_fields(payload, [1], visible={1})
    assert payload["smart"] is None

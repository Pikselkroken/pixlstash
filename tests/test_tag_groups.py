"""Tests for tag groups: the library-wide tag-name -> group map (issue #2469).

Covers:

1. **The map** - create (trim, length, case-insensitive duplicates), reorder
   (exact id set), assign / reassign / ungroup, refused input, and the no-op
   assign that records nothing.
2. **Undo and redo** - a move is one operation through the real
   ``/operations/undo`` and ``/operations/redo`` routes, restoring the exact
   previous groups, including a tag that was Ungrouped before.
3. **The filter** - ``tag=__group:<id>`` / ``rejected_tag=__group:<id>`` /
   ``__group:none`` select exactly the right pictures (asserted by identity).
4. **Authorization** - a READ share token is refused on all four routes, and the
   owner's call on the same path succeeds (so the 403 is not a dead route).
"""

import gc
import io
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import delete, select

from pixlstash.db_models import Operation, Tag, TagGroup, TagGroupMember
from pixlstash.db_models.tag import TAG_PENDING_SENTINEL
from pixlstash.server import Server
from pixlstash.services import operation_log_service
from pixlstash.tasks import TaskType
from tests.utils import bearer_client, mint_read_token, upload_pictures_and_wait

API = "/api/v1"

# Backfill finders that would write Tag rows into the pictures this module seeds
# by hand, and then the filter assertions would read the finder's labels.
_CONFLICTING_FINDERS = (TaskType.TAGGER, TaskType.TAG_PREDICTION_BACKFILL)


@pytest.fixture(scope="module")
def _env():
    """One logged-in Server + TestClient shared by every test in this module.

    Building a Server and minting the login credential are the expensive parts,
    so they are paid once here; per-test isolation comes from the autouse reset.
    """
    temp_dir = tempfile.TemporaryDirectory()
    try:
        os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
        server_config_path = os.path.join(temp_dir.name, "server-config.json")
        with open(server_config_path, "w") as fh:
            fh.write(json.dumps({"port": 8000}))
        server = Server(server_config_path)
        for task_type in _CONFLICTING_FINDERS:
            server.vault._planner_work_finders.pop(task_type)
        disabled = server.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)
        try:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            yield client, server, disabled
        finally:
            server.close()
    finally:
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture
def client(_env):
    return _env[0]


@pytest.fixture
def server(_env):
    return _env[1]


@pytest.fixture(autouse=True)
def reset_tag_groups(_env):
    """Start every test from no groups, no filed tags and an empty operation log.

    Children are deleted before parents (``tag_group_member`` before
    ``tag_group``). The three checks live here, not in a trailing canary test,
    because the CI gate shards tests individually.
    """
    _client, server, disabled = _env

    def _reset(session):
        session.exec(delete(Operation))
        session.exec(delete(TagGroupMember))
        session.exec(delete(TagGroup))
        session.commit()

    server.vault.db.run_task(_reset)

    running = server.vault._work_planner.registered_finder_names()
    assert running.isdisjoint(disabled), (
        "a finder that writes this module's Tag rows is running again: "
        f"{sorted(running & disabled)}"
    )
    assert _map(server)["groups"] == [] and _map(server)["tags"] == {}
    assert _operations(server) == []
    yield


_counter = [0]


def _upload(client):
    """Upload a fresh, content-distinct PNG and return its picture id."""
    _counter[0] += 1
    n = _counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return upload_pictures_and_wait(
        client, [("file", (f"tg{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _seed_tags(server, picture_id, names):
    def _write(session):
        for name in names:
            session.add(Tag(picture_id=picture_id, tag=name))
        session.commit()

    server.vault.db.run_task(_write)


def _map(server):
    def _read(session):
        groups = session.exec(
            select(TagGroup).order_by(TagGroup.position, TagGroup.id)
        ).all()
        members = session.exec(select(TagGroupMember)).all()
        return {
            "groups": [
                {"id": g.id, "name": g.name, "position": g.position} for g in groups
            ],
            "tags": {m.tag: m.group_id for m in members},
        }

    return server.vault.db.run_task(_read)


def _operations(server):
    return operation_log_service.list_operations(server.vault, limit=100)


def _create(client, name):
    resp = client.post(f"{API}/tag_groups", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["group"]["id"]


def _assign(client, tags, group_id):
    resp = client.post(
        f"{API}/tag_groups/assign", json={"tags": tags, "group_id": group_id}
    )
    return resp


def _filter_ids(client, pictures, *, tag=None, rejected=None):
    """Ids, among *pictures*, that the listing returns for the given group terms.

    The shared server holds pictures from earlier tests, so the assertion is
    identity over this test's own pictures, not the listing's total.
    """
    params = {"limit": 500}
    if tag is not None:
        params["tag"] = tag
    if rejected is not None:
        params["rejected_tag"] = rejected
    resp = client.get(f"{API}/pictures", params=params)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    listed = payload["pictures"] if isinstance(payload, dict) else payload
    returned = {int(pic["id"]) for pic in listed}
    return sorted(returned & set(pictures))


def test_create_trims_appends_and_refuses_bad_or_duplicate_names(client):
    resp = client.post(f"{API}/tag_groups", json={"name": "  Meta  "})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["group"] == {"id": body["group"]["id"], "name": "Meta", "position": 0}
    assert body["groups"] == [body["group"]]

    general = client.post(f"{API}/tag_groups", json={"name": "General"}).json()
    assert general["group"]["position"] == 1
    assert [g["name"] for g in general["groups"]] == ["Meta", "General"]

    assert client.post(f"{API}/tag_groups", json={"name": "meta"}).status_code == 409
    assert client.post(f"{API}/tag_groups", json={"name": "   "}).status_code == 400
    assert client.post(f"{API}/tag_groups", json={"name": "x" * 65}).status_code == 400

    get = client.get(f"{API}/tag_groups").json()
    assert [g["name"] for g in get["groups"]] == ["Meta", "General"]
    assert get["tags"] == {}


def test_reorder_requires_exactly_the_existing_ids(client):
    meta = _create(client, "Meta")
    general = _create(client, "General")

    assert (
        client.put(f"{API}/tag_groups/order", json={"ids": [meta]}).status_code == 400
    )
    assert (
        client.put(
            f"{API}/tag_groups/order", json={"ids": [meta, general, 999]}
        ).status_code
        == 400
    )
    assert (
        client.put(f"{API}/tag_groups/order", json={"ids": [meta, meta]}).status_code
        == 400
    )

    resp = client.put(f"{API}/tag_groups/order", json={"ids": [general, meta]})
    assert resp.status_code == 200, resp.text
    assert [g["id"] for g in resp.json()["groups"]] == [general, meta]


def test_assign_files_reassigns_ungroups_and_refuses_bad_input(client):
    meta = _create(client, "Meta")
    general = _create(client, "General")

    resp = _assign(client, ["film grain", " bokeh ", "bokeh", ""], meta)
    assert resp.status_code == 200, resp.text
    assert resp.json()["tags"] == {"film grain": meta, "bokeh": meta}

    resp = _assign(client, ["film grain"], general)
    assert resp.json()["tags"] == {"film grain": general, "bokeh": meta}

    resp = _assign(client, ["bokeh"], None)
    assert resp.json()["tags"] == {"film grain": general}

    # A group that does not exist, a pending-retag marker, and an over-long list.
    assert _assign(client, ["x"], 999999).status_code == 404
    assert _assign(client, [TAG_PENDING_SENTINEL], meta).status_code == 400
    too_many = [f"t{i}" for i in range(5001)]
    assert _assign(client, too_many, meta).status_code == 400
    # A tag need not exist on any picture to be filed.
    assert _assign(client, ["never-seen-anywhere"], meta).status_code == 200


def test_undo_and_redo_restore_the_exact_previous_groups(client, server):
    meta = _create(client, "Meta")
    general = _create(client, "General")
    _assign(client, ["grain"], meta)
    before = client.get(f"{API}/tag_groups").json()
    assert before["tags"] == {"grain": meta}

    # One gesture: "grain" moves Meta -> General, "rain" moves Ungrouped -> General.
    resp = _assign(client, ["grain", "rain"], general)
    assert resp.status_code == 200
    after = resp.json()
    assert after["tags"] == {"grain": general, "rain": general}

    operations = [
        op for op in _operations(server) if op["op_type"] == "tag_groups.move"
    ]
    assert len(operations) == 2
    newest = operations[0]
    assert newest["target_type"] == "tag_group"
    assert newest["target_ids"] == []
    assert newest["summary"] == "Move 2 tags to General"

    undone = client.post(f"{API}/operations/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["operations"][0]["target_type"] == "tag_group"
    assert client.get(f"{API}/tag_groups").json() == before

    redone = client.post(f"{API}/operations/redo")
    assert redone.status_code == 200, redone.text
    assert client.get(f"{API}/tag_groups").json() == after


def test_undo_of_a_single_move_names_it_and_restores_ungrouped(client, server):
    meta = _create(client, "Meta")
    _assign(client, ["film grain"], meta)
    assert client.get(f"{API}/tag_groups").json()["tags"] == {"film grain": meta}

    _assign(client, ["film grain"], None)
    newest = [op for op in _operations(server) if op["op_type"] == "tag_groups.move"][0]
    assert newest["summary"] == "Move “film grain” to Ungrouped"

    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    assert client.get(f"{API}/tag_groups").json()["tags"] == {"film grain": meta}


def test_a_no_op_assign_records_no_operation(client, server):
    meta = _create(client, "Meta")
    _assign(client, ["grain"], meta)
    recorded = len(_operations(server))

    resp = _assign(client, ["grain"], meta)
    assert resp.status_code == 200
    assert resp.json()["tags"] == {"grain": meta}
    assert len(_operations(server)) == recorded

    # Ungrouping a tag that is already Ungrouped is also a no-op.
    assert _assign(client, ["never-filed"], None).status_code == 200
    assert len(_operations(server)) == recorded


def test_group_terms_select_exactly_the_right_pictures(client, server):
    meta = _create(client, "Meta")
    general = _create(client, "General")
    _assign(client, ["grain-f"], meta)
    _assign(client, ["zed-f"], general)
    token_grain = "grain-f"
    token_rain = "rain-f"
    token_zed = "zed-f"

    a = _upload(client)  # Meta tag + unfiled tag
    b = _upload(client)  # Meta tag only
    c = _upload(client)  # no user tags: the upload's own pending-retag marker only
    d = _upload(client)  # unfiled tag only
    e = _upload(client)  # Meta tag + General tag
    _seed_tags(server, a, [token_grain, token_rain])
    _seed_tags(server, b, [token_grain])
    _seed_tags(server, d, [token_rain])
    _seed_tags(server, e, [token_grain, token_zed])
    pictures = [a, b, c, d, e]

    assert _filter_ids(client, pictures, tag=f"__group:{meta}") == sorted([a, b, e])
    assert _filter_ids(client, pictures, rejected=f"__group:{meta}") == sorted([c, d])
    assert _filter_ids(client, pictures, tag=f"__group:{general}") == [e]
    assert _filter_ids(client, pictures, tag="__group:none") == sorted([a, d])
    assert _filter_ids(client, pictures, rejected="__group:none") == sorted([b, c, e])
    # Has one group and lacks another in one query (the design's own example:
    # any Character tag, no Copyright tag): both terms bind side by side.
    assert _filter_ids(
        client, pictures, tag=f"__group:{meta}", rejected=f"__group:{general}"
    ) == sorted([a, b])
    assert (
        _filter_ids(client, pictures, tag="__group:none", rejected="__group:none") == []
    )

    # A malformed term is a literal tag name, exactly as before; it matches nothing.
    assert _filter_ids(client, pictures, tag="__group:abc") == []
    # A well-formed id that names no group matches nothing either.
    assert _filter_ids(client, pictures, tag="__group:999999") == []


def test_a_read_token_is_refused_on_every_route_and_the_owner_is_not(client, server):
    token = mint_read_token(client, "reader")
    reader = bearer_client(server, token)
    meta = _create(client, "Meta")
    _assign(client, ["grain"], meta)

    # Owner control on each path first: proves the route exists, so the 403 below
    # is the owner-only refusal and not a dead path.
    assert client.get(f"{API}/tag_groups").status_code == 200
    assert client.post(f"{API}/tag_groups", json={"name": "Owner"}).status_code == 200
    ids = [g["id"] for g in client.get(f"{API}/tag_groups").json()["groups"]]
    assert client.put(f"{API}/tag_groups/order", json={"ids": ids}).status_code == 200
    assert _assign(client, ["grain"], None).status_code == 200

    before = client.get(f"{API}/tag_groups").json()
    assert reader.get(f"{API}/tag_groups").status_code == 403
    assert reader.post(f"{API}/tag_groups", json={"name": "Nope"}).status_code == 403
    assert reader.put(f"{API}/tag_groups/order", json={"ids": ids}).status_code == 403
    assert (
        reader.post(
            f"{API}/tag_groups/assign", json={"tags": ["grain"], "group_id": meta}
        ).status_code
        == 403
    )
    assert client.get(f"{API}/tag_groups").json() == before


def test_one_batch_of_moves_on_the_same_tag_undoes_and_redoes_in_order(client, server):
    """Two moves of one tag in one batch: undo must land on the OLDEST before.

    Undo walks the batch newest-first and redo oldest-first. Applying them the
    other way round leaves the tag in the wrong group, which is what this test
    catches.
    """
    meta = _create(client, "Meta")
    general = _create(client, "General")
    _assign(client, ["grain"], meta)
    headers = {"X-Operation-Batch-Id": "cli-tgbatch01"}

    first = client.post(
        f"{API}/tag_groups/assign",
        json={"tags": ["grain"], "group_id": general},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"{API}/tag_groups/assign",
        json={"tags": ["grain"], "group_id": None},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["tags"] == {}

    undone = client.post(f"{API}/operations/undo", json={})
    assert undone.status_code == 200, undone.text
    assert len(undone.json()["operations"]) == 2
    assert client.get(f"{API}/tag_groups").json()["tags"] == {"grain": meta}

    redone = client.post(f"{API}/operations/redo")
    assert redone.status_code == 200, redone.text
    assert client.get(f"{API}/tag_groups").json()["tags"] == {}

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
4. **Authorization** - a READ share token is refused on all five routes, and the
   owner's call on the same path succeeds (so the 403 is not a dead route).
5. **Default groups** - seeding on open: the groups in order, the filings as
   defaults, a re-seed that does nothing, a user filing that seeding never touches,
   and a bumped file that files only new tags.
6. **Import** - a Danbooru tag file (xlsx and csv): dry run, the file winning over
   defaults and Ungrouped, user conflicts with and without ``overwrite_user``,
   skips, new groups, refusal of DTDs, bad types and oversize, and undo/redo of the
   exact groups and provenance.
7. **Provenance** - a user move turns a default filing into a user one, and undo
   turns it back; an undo written by the older bare-group payload still restores.

Every test that needs a defaults file writes its own small one and points
``tag_group_service.DEFAULT_TAGS_FILE`` at it, so no test depends on the real file.
"""

import gc
import io
import json
import os
import tempfile
import zipfile
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import delete, select

from pixlstash.db_models import (
    LibrarySettings,
    Operation,
    Tag,
    TagGroup,
    TagGroupMember,
)
from pixlstash.db_models.tag import TAG_PENDING_SENTINEL
from pixlstash.server import Server
from pixlstash.services import operation_log_service, tag_group_service
from pixlstash.services.operation_log_service import record_payload_operation_in_session
from pixlstash.services.tag_group_defaults import DEFAULT_GROUP_NAMES
from pixlstash.tasks import TaskType
from pixlstash.utils import xlsx_reader
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
    ``tag_group``). The checks live here, not in a trailing canary test, because
    the CI gate shards tests individually.
    """
    _client, server, disabled = _env

    def _reset(session):
        session.exec(delete(Operation))
        session.exec(delete(TagGroupMember))
        session.exec(delete(TagGroup))
        # Forget any seeding, so a test that wants defaults seeds them itself.
        for settings in session.exec(select(LibrarySettings)).all():
            settings.tag_groups_seeded_version = None
            session.add(settings)
        session.commit()

    server.vault.db.run_task(_reset)

    running = server.vault._work_planner.registered_finder_names()
    assert running.isdisjoint(disabled), (
        "a finder that writes this module's Tag rows is running again: "
        f"{sorted(running & disabled)}"
    )
    assert _map(server)["groups"] == [] and _map(server)["tags"] == {}
    assert _operations(server) == []
    assert _seeded_version(server) is None
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


def _library_tags(server):
    """Every distinct tag a picture carries, sentinels excluded: the library's set."""

    def _read(session):
        names = session.exec(select(Tag.tag).distinct()).all()
        return {n for n in names if not n.startswith("__tag")}

    return server.vault.db.run_task(_read)


def _operations(server):
    return operation_log_service.list_operations(server.vault, limit=100)


def _seeded_version(server):
    def _read(session):
        return session.exec(select(LibrarySettings.tag_groups_seeded_version)).first()

    return server.vault.db.run_task(_read)


def _provenance(server):
    """Every group and every filing, with its is_default flag: the exact state."""

    def _read(session):
        groups = {g.id: g.name for g in session.exec(select(TagGroup)).all()}
        members = {
            m.tag: (m.group_id, m.is_default)
            for m in session.exec(select(TagGroupMember)).all()
        }
        return {"groups": groups, "members": members}

    return server.vault.db.run_task(_read)


def _listing(client, **params):
    """``GET /tag_groups/tags`` with *params*; the response, unchecked."""
    return client.get(f"{API}/tag_groups/tags", params=params)


def _seed(server):
    return tag_group_service.seed_default_groups(server.vault.db)


@pytest.fixture
def use_defaults(tmp_path, monkeypatch):
    """Write a defaults file with *groups* ({name: [tags]}) and point the service at it."""

    def _use(groups, version=1):
        path = tmp_path / f"default_tag_groups_v{version}.json"
        payload = {
            "version": version,
            "source": "test",
            "groups": [{"name": n, "tags": t} for n, t in groups.items()],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(tag_group_service, "DEFAULT_TAGS_FILE", path)
        return path

    return _use


def _xlsx(rows):
    """A minimal one-sheet .xlsx with inline strings, built in memory."""

    def _ref(column, row):
        return f"{chr(65 + column)}{row}"

    body = "".join(
        f'<row r="{r + 1}">'
        + "".join(
            f'<c r="{_ref(c, r + 1)}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
            for c, v in enumerate(row)
        )
        + "</row>"
        for r, row in enumerate(rows)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{body}</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _csv(rows):
    return "\n".join(",".join(row) for row in rows).encode("utf-8")


def _import(client, data, filename, *, dry_run=False, overwrite=False):
    return client.post(
        f"{API}/tag_groups/import",
        files={"file": (filename, data, "application/octet-stream")},
        data={
            "dry_run": str(dry_run).lower(),
            "overwrite_user": str(overwrite).lower(),
        },
    )


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
    assert body["group"] == {
        "id": body["group"]["id"],
        "name": "Meta",
        "position": 0,
        "tag_count": 0,
    }
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


def test_assign_files_reassigns_ungroups_and_refuses_bad_input(client, server):
    # The endpoint lists library tags only; these names are on no picture, so the
    # filing is read from the stored map.
    meta = _create(client, "Meta")
    general = _create(client, "General")

    resp = _assign(client, ["film grain", " bokeh ", "bokeh", ""], meta)
    assert resp.status_code == 200, resp.text
    assert _map(server)["tags"] == {"film grain": meta, "bokeh": meta}

    resp = _assign(client, ["film grain"], general)
    assert _map(server)["tags"] == {"film grain": general, "bokeh": meta}

    resp = _assign(client, ["bokeh"], None)
    assert _map(server)["tags"] == {"film grain": general}

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
    before = _map(server)
    assert before["tags"] == {"grain": meta}

    # One gesture: "grain" moves Meta -> General, "rain" moves Ungrouped -> General.
    resp = _assign(client, ["grain", "rain"], general)
    assert resp.status_code == 200
    after = _map(server)
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
    assert _map(server) == before

    redone = client.post(f"{API}/operations/redo")
    assert redone.status_code == 200, redone.text
    assert _map(server) == after


def test_undo_of_a_single_move_names_it_and_restores_ungrouped(client, server):
    meta = _create(client, "Meta")
    _assign(client, ["film grain"], meta)
    assert _map(server)["tags"] == {"film grain": meta}

    _assign(client, ["film grain"], None)
    newest = [op for op in _operations(server) if op["op_type"] == "tag_groups.move"][0]
    assert newest["summary"] == "Move “film grain” to Ungrouped"

    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    assert _map(server)["tags"] == {"film grain": meta}


def test_a_no_op_assign_records_no_operation(client, server):
    meta = _create(client, "Meta")
    _assign(client, ["grain"], meta)
    recorded = len(_operations(server))

    resp = _assign(client, ["grain"], meta)
    assert resp.status_code == 200
    assert _map(server)["tags"] == {"grain": meta}
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
    assert _map(server)["tags"] == {}

    undone = client.post(f"{API}/operations/undo", json={})
    assert undone.status_code == 200, undone.text
    assert len(undone.json()["operations"]) == 2
    assert _map(server)["tags"] == {"grain": meta}

    redone = client.post(f"{API}/operations/redo")
    assert redone.status_code == 200, redone.text
    assert _map(server)["tags"] == {}


def test_a_group_term_of_any_length_never_fails(client, server):
    meta = _create(client, "Meta")
    _assign(client, ["grain-z"], meta)
    pic = _upload(client)
    _seed_tags(server, pic, ["grain-z"])

    # Leading zeros name the same group; thousands of digits name none, and
    # neither turns the listing into a 500 (int() caps digits on 3.11+).
    padded = "__group:" + "0" * 5000 + str(meta)
    assert _filter_ids(client, [pic], tag=padded) == [pic]
    assert _filter_ids(client, [pic], tag="__group:" + "9" * 5000) == []
    assert _filter_ids(client, [pic], rejected="__group:" + "9" * 5000) == [pic]


def test_group_filtered_stats_follow_a_move_and_its_undo(client, server):
    meta = _create(client, "Meta")
    pic = _upload(client)
    _seed_tags(server, pic, ["grain-s"])
    params = {"tag": f"__group:{meta}"}

    def stats():
        resp = client.get(f"{API}/pictures/stats", params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    empty = stats()
    _assign(client, ["grain-s"], meta)
    filed = stats()
    assert filed != empty, "a cached pre-move answer was served after the move"
    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    assert stats() == empty, "a cached post-move answer was served after the undo"


# ---------------------------------------------------------------------------
# Default groups: seeding on open
# ---------------------------------------------------------------------------


def test_seeding_files_the_defaults_in_order_as_defaults(server, use_defaults):
    use_defaults(
        {
            "Anomaly": ["seed-defect"],
            "People": ["seed-girl", "seed-solo"],
            "Hair": ["seed-hair"],
        }
    )
    assert _seed(server)["groups_created"] == len(DEFAULT_GROUP_NAMES)

    names = [g["name"] for g in _map(server)["groups"]]
    assert names == list(DEFAULT_GROUP_NAMES)
    ids = {g["name"]: g["id"] for g in _map(server)["groups"]}
    state = _provenance(server)["members"]
    assert state == {
        "seed-defect": (ids["Anomaly"], True),
        "seed-girl": (ids["People"], True),
        "seed-solo": (ids["People"], True),
        "seed-hair": (ids["Hair"], True),
    }
    assert _seeded_version(server) == 1


def test_reseeding_at_the_same_version_is_a_noop(server, use_defaults):
    use_defaults({"People": ["noop-girl"], "Hair": ["noop-hair"]})
    _seed(server)
    hair = next(g for g in _map(server)["groups"] if g["name"] == "Hair")
    # Deleting a seeded group cascades to its filings. Re-running seeding at the
    # same version must not bring the group or its filing back: the version is
    # the only thing that decides it.
    server.vault.db.run_task(
        lambda session: (
            session.exec(delete(TagGroup).where(TagGroup.id == hair["id"])),
            session.commit(),
        )
    )
    before = _provenance(server)

    assert _seed(server) is None
    assert _provenance(server) == before
    assert "Hair" not in [g["name"] for g in _map(server)["groups"]]


def test_seeding_never_touches_a_user_filing(server, use_defaults, client):
    use_defaults({"People": ["keep-girl"]})
    mine = _create(client, "Mine")
    _assign(client, ["keep-girl"], mine)
    # The user's own group with a name that differs only by case from a default
    # group: seeding reuses it rather than adding a second "hair".
    user_hair = _create(client, "hair")
    _assign(client, ["keep-hair"], user_hair)
    use_defaults({"People": ["keep-girl"], "Hair": ["keep-hair", "keep-new"]})

    _seed(server)

    state = _provenance(server)["members"]
    assert state["keep-girl"] == (mine, False)
    assert state["keep-hair"] == (user_hair, False)
    assert state["keep-new"][1] is True
    hair_groups = [g for g in _map(server)["groups"] if g["name"].casefold() == "hair"]
    assert [g["id"] for g in hair_groups] == [user_hair]


def test_a_bumped_version_files_only_the_new_tags(server, use_defaults, client):
    use_defaults({"People": ["bump-girl", "bump-old"]}, version=1)
    _seed(server)
    _assign(client, ["bump-girl"], None)  # the user ungroups a default filing
    people = next(g for g in _map(server)["groups"] if g["name"] == "People")
    _assign(client, ["bump-girl"], people["id"])  # and files it again, by hand

    use_defaults({"People": ["bump-girl", "bump-old", "bump-new"]}, version=2)
    assert _seed(server)["tags_filed"] == 1

    state = _provenance(server)["members"]
    assert state["bump-girl"] == (people["id"], False)
    assert state["bump-old"] == (people["id"], True)
    assert state["bump-new"] == (people["id"], True)
    assert _seeded_version(server) == 2


def test_a_bumped_version_refiles_a_default_the_user_made_ungrouped(
    server, use_defaults, client
):
    """Documents the rule: no tombstones, so an Ungrouped default is filed again."""
    use_defaults({"People": ["ungr-girl"]}, version=1)
    _seed(server)
    _assign(client, ["ungr-girl"], None)
    assert "ungr-girl" not in _provenance(server)["members"]

    use_defaults({"People": ["ungr-girl"]}, version=2)
    assert _seed(server)["tags_filed"] == 1

    people = next(g["id"] for g in _map(server)["groups"] if g["name"] == "People")
    assert _provenance(server)["members"]["ungr-girl"] == (people, True)


def test_opening_the_library_seeds_it(server, use_defaults):
    use_defaults({"People": ["open-girl"]})
    server.reconcile_library_settings(server.vault)

    assert [g["name"] for g in _map(server)["groups"]] == list(DEFAULT_GROUP_NAMES)
    assert _provenance(server)["members"]["open-girl"][1] is True


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

_DRY_ROWS = [
    ["english", "category", "subcategory"],
    ["dry-girl", "Person", "Subject"],
    ["dry-shirt", "Clothing", "Clothing Styles"],
]


def test_dry_run_reports_the_plan_and_changes_nothing(client, server, use_defaults):
    use_defaults({"People": ["dry-girl"]})
    pic = _upload(client)
    _seed_tags(server, pic, ["dry-shirt"])
    before = _provenance(server)
    recorded = len(_operations(server))

    resp = _import(client, _csv(_DRY_ROWS), "dryrun.csv", dry_run=True)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "plan": {
            "rows": 2,
            "filed": 2,
            "from_defaults": 0,
            "from_ungrouped": 2,
            "user_conflicts": 0,
            "user_conflict_sample": [],
            "user_overwritten": 0,
            "groups_created": ["People", "Clothing"],
            "skipped_uncategorised": 0,
        }
    }
    assert _provenance(server) == before
    assert len(_operations(server)) == recorded


def test_real_run_files_creates_groups_and_records_one_operation(
    client, server, use_defaults
):
    use_defaults({"People": ["real-girl"]})
    pic = _upload(client)
    _seed_tags(server, pic, ["real-shirt"])
    rows = [
        ["english", "category", "subcategory"],
        ["real-girl", "Person", "Subject"],
        ["real-shirt", "Clothing", "Clothing Styles"],
    ]

    resp = _import(client, _csv(rows), "real.csv")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {g["name"]: g["id"] for g in body["groups"]}
    assert _map(server)["tags"] == {
        "real-girl": names["People"],
        "real-shirt": names["Clothing"],
    }
    assert body["plan"]["groups_created"] == ["People", "Clothing"]
    assert [g["name"] for g in body["groups"]] == ["People", "Clothing"]
    assert _provenance(server)["members"] == {
        "real-girl": (names["People"], False),
        "real-shirt": (names["Clothing"], False),
    }
    newest = _operations(server)[0]
    assert newest["op_type"] == "tag_groups.move"
    assert newest["summary"] == "Import 2 tags from real.csv"


def test_defaults_and_ungrouped_tags_are_refiled_by_the_file(
    client, server, use_defaults
):
    use_defaults({"People": ["refile-girl"]})
    _seed(server)
    pic = _upload(client)
    _seed_tags(server, pic, ["refile-shirt"])
    rows = [
        ["english", "category", "subcategory"],
        ["refile-girl", "Person", "Hairstyles"],  # a default, re-filed to Hair
        ["refile-shirt", "Clothing", "Clothing Styles"],  # Ungrouped, filed
    ]

    resp = _import(client, _csv(rows), "refile.csv")

    plan = resp.json()["plan"]
    assert (plan["from_defaults"], plan["from_ungrouped"], plan["filed"]) == (1, 1, 2)
    hair = next(g["id"] for g in _map(server)["groups"] if g["name"] == "Hair")
    clothing = next(g["id"] for g in _map(server)["groups"] if g["name"] == "Clothing")
    assert _provenance(server)["members"]["refile-girl"] == (hair, False)
    assert _provenance(server)["members"]["refile-shirt"] == (clothing, False)


def test_a_default_already_under_the_files_group_is_left_alone(
    client, server, use_defaults
):
    use_defaults({"People": ["same-girl"]})
    _seed(server)
    before = _provenance(server)
    recorded = len(_operations(server))
    rows = [
        ["english", "category", "subcategory"],
        ["same-girl", "Person", "Subject"],  # the default's own group: no move
    ]

    resp = _import(client, _csv(rows), "same.csv")

    plan = resp.json()["plan"]
    assert (plan["filed"], plan["from_defaults"], plan["user_conflicts"]) == (0, 0, 0)
    assert _provenance(server) == before
    assert len(_operations(server)) == recorded


def test_a_user_filing_is_a_conflict_kept_unless_overwritten(
    client, server, use_defaults
):
    use_defaults({"People": ["conflict-girl"]})
    mine = _create(client, "Mine")
    _assign(client, ["conflict-girl"], mine)
    rows = [
        ["english", "category", "subcategory"],
        ["conflict-girl", "Person", "Subject"],
    ]

    kept = _import(client, _csv(rows), "conflict.csv")
    plan = kept.json()["plan"]
    assert plan["user_conflicts"] == 1
    assert plan["user_conflict_sample"] == ["conflict-girl"]
    assert (plan["filed"], plan["user_overwritten"]) == (0, 0)
    assert _provenance(server)["members"]["conflict-girl"] == (mine, False)
    assert _operations(server)[0]["op_type"] == "tag_groups.move"
    recorded = len(_operations(server))

    moved = _import(client, _csv(rows), "conflict.csv", overwrite=True)
    plan = moved.json()["plan"]
    assert (plan["user_overwritten"], plan["filed"]) == (1, 1)
    people = next(g["id"] for g in moved.json()["groups"] if g["name"] == "People")
    assert _provenance(server)["members"]["conflict-girl"] == (people, False)
    assert len(_operations(server)) == recorded + 1


def test_tags_outside_the_library_are_filed_too(client, server, use_defaults):
    """The file is the vocabulary: a row no picture carries is filed like any other."""
    use_defaults({"People": ["skip-girl"]})
    rows = [
        ["english", "category", "subcategory"],
        ["skip-girl", "Person", "Subject"],
        ["skip-hat-nowhere", "Clothing", "Head Accessories"],
    ]

    dry = _import(client, _csv(rows), "skip.csv", dry_run=True).json()["plan"]
    assert "skipped_not_in_library" not in dry
    assert dry["filed"] == 2

    real = _import(client, _csv(rows), "skip.csv")
    assert real.status_code == 200, real.text
    assert _map(server)["tags"]["skip-hat-nowhere"] == real.json()["groups"][-1]["id"]
    library = _library_tags(server)
    filed = set(_map(server)["tags"])
    assert real.json()["ungrouped_count"] == len(library - filed)
    assert real.json()["all_count"] == len(library | filed)
    # Not on a picture, so the map answer does not list it.
    assert "skip-hat-nowhere" not in real.json()["tags"]


def test_adult_rows_file_under_adult_and_nothing_in_the_import_drops_them(
    client, server, use_defaults
):
    """The filter protects the shipped defaults only; a user's file is filed whole."""
    use_defaults({"People": ["adult-girl"]})
    pic = _upload(client)
    _seed_tags(server, pic, ["adult-chest-tag"])
    rows = [
        ["english", "category", "subcategory"],
        ["adult-chest-tag", "Person", "Chest"],
        ["adult-toy", "Items", "Adult Toys"],
        ["adult-sex", "Expressions & Actions", "Sexual Actions"],
        ["adult-girl", "Person", "Subject"],
        # A keyword the defaults guard would drop: the import keeps it.
        ["nude study", "Person", "Subject"],
    ]

    dry = _import(client, _csv(rows), "adult.csv", dry_run=True).json()["plan"]
    assert dry["skipped_uncategorised"] == 0
    assert "Adult" in dry["groups_created"]
    assert "skipped_adult_or_uncategorised" not in dry

    resp = _import(client, _csv(rows), "adult.csv")
    assert resp.status_code == 200, resp.text
    names = {g["name"]: g["id"] for g in resp.json()["groups"]}
    filed = _map(server)["tags"]
    assert filed["adult-chest-tag"] == names["Adult"]
    assert filed["adult-toy"] == names["Adult"]
    assert filed["adult-sex"] == names["Adult"]
    assert filed["nude study"] == names["People"]
    # Adult is not a default group: a fresh seeding never creates it.
    assert "Adult" not in DEFAULT_GROUP_NAMES


def test_an_unknown_category_creates_its_group_and_a_dry_run_only_names_it(
    client, server, use_defaults
):
    use_defaults({})
    _seed(server)
    pic = _upload(client)
    _seed_tags(server, pic, ["moon-tag"])
    rows = [
        ["english", "category", "subcategory"],
        ["moon-tag", "Weather Lore", "Night"],
    ]

    dry = _import(client, _csv(rows), "lore.csv", dry_run=True).json()
    assert dry["plan"]["groups_created"] == ["Weather Lore"]
    assert "Weather Lore" not in [g["name"] for g in _map(server)["groups"]]

    real = _import(client, _csv(rows), "lore.csv").json()
    assert [g["name"] for g in real["groups"]][-1] == "Weather Lore"
    assert real["groups"][-1]["position"] == len(DEFAULT_GROUP_NAMES)
    assert real["tags"]["moon-tag"] == real["groups"][-1]["id"]


def test_xlsx_and_csv_of_the_same_table_plan_identically(client, server, use_defaults):
    use_defaults({"People": ["fmt-girl"]})
    pic = _upload(client)
    _seed_tags(server, pic, ["fmt-shirt"])
    rows = [
        ["english", "category", "subcategory", "category_en", "subcategory_en"],
        ["fmt-girl", "人物", "主体", "Person", "Subject"],
        ["fmt-shirt", "衣服", "", "", "Clothing Styles"],
    ]

    from_csv = _import(client, _csv(rows), "fmt.csv", dry_run=True).json()
    from_xlsx = _import(client, _xlsx(rows), "fmt.xlsx", dry_run=True).json()

    assert from_xlsx == from_csv
    assert from_xlsx["plan"]["filed"] == 2


def test_a_dtd_bearing_xlsx_is_refused_before_it_is_parsed(
    client, server, use_defaults
):
    use_defaults({"People": ["dtd-girl"]})
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE worksheet [<!ENTITY a "aaaa">]>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&a;</t></is></c>'
        b"</row></sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", bomb)

    resp = _import(client, buf.getvalue(), "bomb.xlsx", dry_run=True)

    assert resp.status_code == 400, resp.text
    assert "DTD" in resp.json()["detail"]


def test_bad_inputs_are_refused_with_a_plain_message(client, server, use_defaults):
    use_defaults({"People": ["bad-girl"]})

    wrong_type = _import(client, b"english,category\nx,y\n", "table.txt", dry_run=True)
    assert wrong_type.status_code == 400
    assert "Upload a .xlsx or .csv" in wrong_type.json()["detail"]

    not_a_zip = _import(client, b"plainly not a zip", "table.xlsx", dry_run=True)
    assert not_a_zip.status_code == 400
    assert "Could not read the .xlsx" in not_a_zip.json()["detail"]

    no_columns = _import(client, _csv([["word", "kind"], ["x", "y"]]), "cols.csv")
    assert no_columns.status_code == 400
    assert "english" in no_columns.json()["detail"]
    assert "category" in no_columns.json()["detail"]


def test_an_upload_over_the_cap_is_refused_with_413(client, server):
    oversize = b"english,category\n" + b"x" * (64 * 1024 * 1024)

    resp = _import(client, oversize, "huge.csv", dry_run=True)

    assert resp.status_code == 413, resp.text
    assert "64 MB" in resp.json()["detail"]


def test_undo_and_redo_restore_groups_and_provenance_exactly(
    client, server, use_defaults
):
    use_defaults({"People": ["undo-girl"], "Hair": ["undo-hair"]})
    _seed(server)
    pic = _upload(client)
    _seed_tags(server, pic, ["undo-shirt"])
    before = _provenance(server)
    rows = [
        ["english", "category", "subcategory"],
        ["undo-girl", "Person", "Hairstyles"],  # default People -> Hair
        ["undo-shirt", "Weather Lore", "Night"],  # Ungrouped -> a new group
    ]

    assert _import(client, _csv(rows), "undo.csv").status_code == 200
    after = _provenance(server)
    assert after["members"]["undo-girl"][1] is False
    assert after["members"]["undo-shirt"][0] is not None

    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    undone = _provenance(server)
    assert undone["members"] == before["members"]
    assert undone["members"]["undo-girl"][1] is True
    # The group the import created stays, empty, after the undo.
    assert "Weather Lore" in undone["groups"].values()
    assert set(before["groups"].items()) <= set(undone["groups"].items())

    assert client.post(f"{API}/operations/redo").status_code == 200
    assert _provenance(server)["members"] == after["members"]


def test_a_user_move_turns_a_default_into_a_user_filing_and_undo_turns_it_back(
    client, server, use_defaults
):
    use_defaults({"People": ["move-girl"]})
    _seed(server)
    people = next(g["id"] for g in _map(server)["groups"] if g["name"] == "People")
    mine = _create(client, "Mine")
    assert _provenance(server)["members"]["move-girl"] == (people, True)

    _assign(client, ["move-girl"], mine)
    assert _provenance(server)["members"]["move-girl"] == (mine, False)

    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    assert _provenance(server)["members"]["move-girl"] == (people, True)


def test_an_undo_written_with_the_bare_group_payload_still_restores(client, server):
    """Operations recorded before provenance was stored hold ``group`` ids only."""

    def _write(session):
        group = TagGroup(name="Legacy", position=0)
        session.add(group)
        session.flush()
        session.add(
            TagGroupMember(tag="legacy-tag", group_id=group.id, is_default=True)
        )
        record_payload_operation_in_session(
            session,
            op_type=tag_group_service.OP_TYPE_MOVE,
            target_type="tag_group",
            before={"tags": {"legacy-tag": None}},
            after={"tags": {"legacy-tag": group.id}},
            summary="Legacy move",
        )
        session.commit()
        return group.id

    group_id = server.vault.db.run_task(_write)
    assert _provenance(server)["members"] == {"legacy-tag": (group_id, True)}

    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    assert _provenance(server)["members"] == {}

    assert client.post(f"{API}/operations/redo").status_code == 200
    # Redo of a bare id is a user filing: the old payload carried no provenance.
    assert _provenance(server)["members"] == {"legacy-tag": (group_id, False)}


def test_a_read_token_is_refused_on_the_import_route(client, server, use_defaults):
    use_defaults({"People": ["tok-girl"]})
    token = mint_read_token(client, "importer")
    reader = bearer_client(server, token)
    rows = _csv([["english", "category"], ["tok-girl", "Person"]])

    # Owner control first: the route exists, so the 403 below is the refusal.
    owner = _import(client, rows, "tok.csv", dry_run=True)
    assert owner.status_code == 200, owner.text
    refused = reader.post(
        f"{API}/tag_groups/import",
        files={"file": ("tok.csv", rows, "application/octet-stream")},
        data={"dry_run": "true"},
    )
    assert refused.status_code == 403


def test_shared_strings_and_rich_text_cells_are_read_by_the_xlsx_parser():
    """Excel writes shared strings, and splits rich text across runs."""
    sst = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>english</t></si>"
        "<si><r><t>cate</t></r><r><t>gory</t></r></si>"
        "<si><t>subcategory</t></si>"
        "<si><t>Person</t></si>"
        "</sst>"
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
        '<c r="C1" t="s"><v>2</v></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>solo</t></is></c>'
        '<c r="B2" t="s"><v>3</v></c></row>'
        "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", sst)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    rows = tag_group_service.parse_tag_file("shared.xlsx", buf.getvalue())

    assert rows == [("solo", "Person", "")]


def test_categories_differing_only_by_case_make_one_group(client, server, use_defaults):
    use_defaults({})
    _seed(server)
    pic = _upload(client)
    _seed_tags(server, pic, ["case-a", "case-b"])
    rows = [
        ["english", "category", "subcategory"],
        ["case-a", "Weather Lore", "Night"],
        ["case-b", "weather lore", "Day"],
    ]

    plan = _import(client, _csv(rows), "case.csv", dry_run=True).json()["plan"]
    assert plan["groups_created"] == ["Weather Lore"]

    real = _import(client, _csv(rows), "case.csv").json()
    lore = [g for g in real["groups"] if g["name"].casefold() == "weather lore"]
    assert len(lore) == 1
    assert real["tags"]["case-a"] == real["tags"]["case-b"] == lore[0]["id"]


_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_SHEET_OPEN = f'<worksheet xmlns="{_NS_MAIN}"><sheetData>'
_SHEET_CLOSE = "</sheetData></worksheet>"


def _book(sheet, shared=None):
    """A workbook holding *sheet* (and *shared* strings, when given), in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        if shared is not None:
            archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_a_utf16_part_is_refused_before_its_dtd_is_scanned(
    client, server, use_defaults
):
    use_defaults({"People": ["u16-girl"]})
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE worksheet [<!ENTITY a "aaaa">]>'
        f'<worksheet xmlns="{_NS_MAIN}"><sheetData><row r="1">'
        '<c r="A1" t="inlineStr"><is><t>&a;</t></is></c>'
        "</row></sheetData></worksheet>"
    )
    with_bom = xml.encode("utf-16")
    # The byte scan alone would miss the DTD: UTF-16 puts a NUL between letters.
    assert b"<!DOCTYPE" not in with_bom

    bom = _import(client, _book(with_bom), "u16.xlsx", dry_run=True)
    assert bom.status_code == 400, bom.text
    assert "UTF-16" in bom.json()["detail"]

    no_bom = _import(client, _book(xml.encode("utf-16-le")), "u16le.xlsx", dry_run=True)
    assert no_bom.status_code == 400, no_bom.text
    assert "NUL byte" in no_bom.json()["detail"]


def test_a_non_numeric_shared_string_index_is_a_400(client, server, use_defaults):
    use_defaults({"People": ["sst-girl"]})
    sheet = (
        _SHEET_OPEN + '<row r="1"><c r="A1" t="s"><v>one</v></c></row>' + _SHEET_CLOSE
    )
    shared = f'<sst xmlns="{_NS_MAIN}"><si><t>english</t></si></sst>'

    resp = _import(
        client, _book(sheet.encode(), shared.encode()), "sst.xlsx", dry_run=True
    )

    assert resp.status_code == 400, resp.text
    assert "not a whole number" in resp.json()["detail"]


def test_a_corrupt_deflate_member_is_a_400(client, server, use_defaults):
    use_defaults({"People": ["zlib-girl"]})
    data = bytearray(_xlsx([["english", "category"], ["zlib-girl", "Person"]]))
    # The sheet is the first member; its deflated bytes follow the local header.
    name_len = int.from_bytes(data[26:28], "little")
    extra_len = int.from_bytes(data[28:30], "little")
    start = 30 + name_len + extra_len
    data[start : start + 16] = bytes(b ^ 0xFF for b in data[start : start + 16])

    resp = _import(client, bytes(data), "zlib.xlsx", dry_run=True)

    assert resp.status_code == 400, resp.text
    assert "Could not read the .xlsx" in resp.json()["detail"]


def test_the_row_cap_refuses_a_longer_sheet_with_a_400(monkeypatch):
    monkeypatch.setattr(xlsx_reader, "MAX_ROWS", 2)
    at_cap = _xlsx([["english", "category"], ["a", "Person"]])
    assert len(tag_group_service.parse_tag_file("rows.xlsx", at_cap)) == 1

    over = _xlsx([["english", "category"], ["a", "Person"], ["b", "Person"]])
    with pytest.raises(tag_group_service.TagGroupError) as caught:
        tag_group_service.parse_tag_file("rows.xlsx", over)
    assert caught.value.status_code == 400
    assert "rows" in caught.value.detail


def test_the_cell_cap_counts_a_row_padded_to_its_last_column(monkeypatch):
    monkeypatch.setattr(xlsx_reader, "MAX_CELLS", 100)
    sheet = (
        _SHEET_OPEN
        + '<row r="1"><c r="A1" t="inlineStr"><is><t>english</t></is></c></row>'
        + '<row r="2"><c r="XFD2" t="inlineStr"><is><t>x</t></is></c></row>'
        + _SHEET_CLOSE
    )

    with pytest.raises(tag_group_service.TagGroupError) as caught:
        tag_group_service.parse_tag_file("wide.xlsx", _book(sheet.encode()))
    assert caught.value.status_code == 400
    assert "cells" in caught.value.detail


def test_one_uncompressed_budget_covers_every_member_read(monkeypatch):
    limit = 1000
    monkeypatch.setattr(xlsx_reader, "MAX_UNCOMPRESSED_BYTES", limit)
    shared = f'<sst xmlns="{_NS_MAIN}"><si><t>{"s" * 300}</t></si></sst>'.encode()
    padding = "<!-- " + "p" * 700 + " -->"
    sheet = (
        _SHEET_OPEN
        + padding
        + '<row r="1"><c r="A1" t="inlineStr"><is><t>english</t></is></c></row>'
        + _SHEET_CLOSE
    ).encode()
    # Each member is under the limit on its own; read together they are over it.
    assert len(shared) + 300 < limit and len(sheet) < limit
    assert len(shared) + 300 + len(sheet) > limit

    with pytest.raises(tag_group_service.TagGroupError) as caught:
        tag_group_service.parse_tag_file("budget.xlsx", _book(sheet, shared))
    assert caught.value.status_code == 400
    assert "larger than the reader allows" in caught.value.detail


def test_an_import_naming_too_many_new_categories_is_a_400(
    client, server, use_defaults
):
    use_defaults({})

    def rows_for(categories):
        return [["english", "category", "subcategory"]] + [
            [f"cap-{n}", f"Lore {n}", "Night"] for n in range(categories)
        ]

    at_cap = _import(client, _csv(rows_for(50)), "cap.csv", dry_run=True)
    assert at_cap.status_code == 200, at_cap.text
    over = _import(client, _csv(rows_for(51)), "cap.csv", dry_run=True)
    assert over.status_code == 400, over.text
    assert "51 categories" in over.json()["detail"]


def test_a_category_longer_than_a_group_name_is_capped_not_refused(
    client, server, use_defaults
):
    use_defaults({})
    _seed(server)
    long_name = "Very Long Category " + "x" * 70
    rows = [["english", "category"], ["long-cat-tag", long_name]]

    resp = _import(client, _csv(rows), "long.csv")
    assert resp.status_code == 200, resp.text
    names = [g["name"] for g in resp.json()["groups"]]
    assert long_name[:64] in names
    assert all(len(n) <= 64 for n in names)


def test_an_ungrouped_or_blank_category_creates_no_group(client, server, use_defaults):
    use_defaults({})
    _seed(server)
    pic = _upload(client)
    _seed_tags(server, pic, ["loose-tag", "blank-tag"])
    rows = [
        ["english", "category", "subcategory"],
        ["loose-tag", "Ungrouped", "Night"],
        ["blank-tag", "", ""],
    ]

    plan = _import(client, _csv(rows), "loose.csv", dry_run=True).json()["plan"]
    assert plan["groups_created"] == []
    assert plan["skipped_uncategorised"] == 2

    real = _import(client, _csv(rows), "loose.csv").json()
    assert "Ungrouped" not in [g["name"] for g in real["groups"]]
    assert real["tags"] == {}


# ---------------------------------------------------------------------------
# The map is library-limited; the Tags listing pages the rest
# ---------------------------------------------------------------------------


def test_the_map_lists_library_tags_and_counts_the_rest(client, server):
    meta = _create(client, "Meta")
    general = _create(client, "General")
    pic = _upload(client)
    _seed_tags(server, pic, ["lib-grain", "lib-rain"])
    _assign(client, ["lib-grain", "filed-only-grain"], meta)
    _assign(client, ["lib-rain"], general)

    body = client.get(f"{API}/tag_groups").json()
    library = _library_tags(server)
    filed = _map(server)["tags"]
    # Only filings of tags a picture carries travel in the map answer.
    assert body["tags"] == {t: g for t, g in filed.items() if t in library}
    assert body["tags"]["lib-grain"] == meta
    assert "filed-only-grain" not in body["tags"]
    # tag_count counts every tag filed in the group, library or not.
    counts = {g["id"]: g["tag_count"] for g in body["groups"]}
    assert counts[meta] == 2 and counts[general] == 1
    assert body["ungrouped_count"] == len(library - set(filed))
    assert body["all_count"] == len(library | set(filed))


def test_the_listing_answers_by_group_none_and_all(client, server):
    meta = _create(client, "Meta")
    first = _upload(client)
    second = _upload(client)
    _seed_tags(server, first, ["ls-grain", "ls-loose"])
    _seed_tags(server, second, ["ls-grain"])
    _assign(client, ["ls-grain", "ls-ghost"], meta)
    library = _library_tags(server)
    filed = _map(server)["tags"]

    by_meta = _listing(client, group=str(meta)).json()
    assert by_meta["total"] == 2
    assert by_meta["tags"] == [
        {"tag": "ls-grain", "count": 2, "group_id": meta},
        {"tag": "ls-ghost", "count": 0, "group_id": meta},
    ]

    unfiled = _listing(client, group="none", sort="name", limit=500).json()
    assert [t["tag"] for t in unfiled["tags"]] == sorted(library - set(filed))
    assert unfiled["total"] == len(library - set(filed))
    assert {"tag": "ls-loose", "count": 1, "group_id": None} in unfiled["tags"]

    everything = _listing(client, group="all", sort="name", limit=500).json()
    assert everything["total"] == len(library | set(filed))
    by_name = {t["tag"]: t for t in everything["tags"]}
    assert by_name["ls-ghost"] == {"tag": "ls-ghost", "count": 0, "group_id": meta}
    assert by_name["ls-loose"]["group_id"] is None
    assert by_name["ls-grain"] == {"tag": "ls-grain", "count": 2, "group_id": meta}


def test_the_listing_pages_sorts_and_matches_a_substring(client, server):
    group = _create(client, "Paging")
    first = _upload(client)
    second = _upload(client)
    _seed_tags(server, first, ["pg-d", "pg-b", "pg-e"])
    _seed_tags(server, second, ["pg-d"])
    _assign(client, ["pg-a", "pg-b", "pg-c", "pg-d", "pg-e"], group)

    def names(**params):
        resp = _listing(client, group=str(group), **params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    # Most pictures first, then name: d (2), then b and e (1), then a and c (0).
    by_pictures = names(sort="pictures")
    assert [t["tag"] for t in by_pictures["tags"]] == [
        "pg-d",
        "pg-b",
        "pg-e",
        "pg-a",
        "pg-c",
    ]
    assert by_pictures["total"] == 5
    assert [t["tag"] for t in names(sort="name")["tags"]] == [
        "pg-a",
        "pg-b",
        "pg-c",
        "pg-d",
        "pg-e",
    ]
    # Pages join up to the whole list, with no repeat and no gap.
    paged = []
    for offset in (0, 2, 4):
        page = names(sort="pictures", limit=2, offset=offset)
        assert page["total"] == 5
        paged += [t["tag"] for t in page["tags"]]
    assert paged == [t["tag"] for t in by_pictures["tags"]]
    # Case-insensitive substring; a miss is an empty page, not an error.
    assert names(q="PG-")["total"] == 5
    assert [t["tag"] for t in names(q="pg-d")["tags"]] == ["pg-d"]
    assert names(q="no-such-tag")["total"] == 0


def test_the_listing_refuses_unknown_groups_and_bad_parameters(client, server):
    assert _listing(client, group="999999").status_code == 404
    # Past SQLite's 64-bit id range: no group can have it, so it is a 404, not a 500.
    assert _listing(client, group="99999999999999999999999").status_code == 404
    assert _listing(client, group="abc").status_code == 400
    assert _listing(client, group="-1").status_code == 400
    assert _listing(client, group="all", limit=0).status_code == 422
    assert _listing(client, group="all", limit=501).status_code == 422
    assert _listing(client, group="all", offset=-1).status_code == 422
    assert _listing(client, group="all", sort="bogus").status_code == 422
    assert _listing(client, group="all").status_code == 200


def test_a_read_token_is_refused_on_the_listing_and_the_owner_is_not(client, server):
    token = mint_read_token(client, "tag-listing-reader")
    reader = bearer_client(server, token)
    meta = _create(client, "Meta")
    _assign(client, ["listed-grain"], meta)

    # Owner control first: the path resolves, so the 403 below is the refusal.
    assert _listing(client, group="all").status_code == 200
    assert (
        reader.get(f"{API}/tag_groups/tags", params={"group": "all"}).status_code == 403
    )


# ---------------------------------------------------------------------------
# Import: every row is filed; a character brings its franchise
# ---------------------------------------------------------------------------


def test_a_character_row_files_its_franchise_under_copyright(
    client, server, use_defaults
):
    use_defaults({"People": ["chr-seed-girl"]})
    rows = [
        ["english", "category", "subcategory"],
        ["raiden shogun (genshin impact)", "Anime Character", ""],
        ["lucy (young)", "Anime Character", ""],
        ["some artist", "Artist", ""],
        # A franchise that a row also lists as a character is Copyright, as in the defaults.
        ["nier:automata", "Anime Character", ""],
        ["2b (nier:automata)", "Anime Character", ""],
    ]

    resp = _import(client, _csv(rows), "chars.csv")

    assert resp.status_code == 200, resp.text
    names = {g["name"]: g["id"] for g in resp.json()["groups"]}
    filed = _map(server)["tags"]
    assert filed["raiden shogun (genshin impact)"] == names["Character"]
    assert filed["genshin impact"] == names["Copyright"]
    assert filed["some artist"] == names["Artist"]
    assert filed["nier:automata"] == names["Copyright"]
    assert filed["2b (nier:automata)"] == names["Character"]
    # A qualifier on the list is a form, not a franchise: lucy is a character only.
    assert filed["lucy (young)"] == names["Character"]
    assert "young" not in filed
    assert {"Character", "Copyright", "Artist"} <= set(
        resp.json()["plan"]["groups_created"]
    )


def test_a_big_import_undoes_and_redoes_exactly(client, server, use_defaults):
    """A few thousand rows, with a default moved, a user filing overwritten and
    a franchise shared by many characters: undo and redo restore exactly."""
    use_defaults({"People": ["big-girl"]})
    _seed(server)
    meta = _create(client, "Meta")
    _assign(client, ["big-user"], meta)
    rows = [
        ["english", "category", "subcategory"],
        ["big-girl", "Clothing", "Clothing Styles"],
        ["big-user", "Clothing", "Clothing Styles"],
    ]
    rows += [
        [f"big-chr-{i} (big-franchise-{i % 40})", "Anime Character", ""]
        for i in range(3000)
    ]
    rows += [[f"big-tag-{i}", "Items", "Other Items"] for i in range(2000)]
    before = _provenance(server)

    resp = _import(client, _csv(rows), "big.csv", overwrite=True)

    assert resp.status_code == 200, resp.text
    plan = resp.json()["plan"]
    assert plan["filed"] == 5000 + 40 + 2
    assert plan["user_overwritten"] == 1 and plan["from_defaults"] == 1
    after = _provenance(server)
    assert after["members"]["big-chr-7 (big-franchise-7)"][0] is not None

    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    assert _provenance(server) == before
    assert client.post(f"{API}/operations/redo").status_code == 200
    assert _provenance(server) == after
    assert client.post(f"{API}/operations/undo", json={}).status_code == 200
    assert _provenance(server) == before


def test_cells_without_a_reference_take_the_next_column():
    # `r` is optional in OOXML; a writer that omits it means "the next column".
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row><c t="inlineStr"><is><t>english</t></is></c>'
        '<c t="inlineStr"><is><t>category</t></is></c></row>'
        '<row><c t="inlineStr"><is><t>long hair</t></is></c>'
        '<c r="C2" t="inlineStr"><is><t>skipped b</t></is></c>'
        '<c t="inlineStr"><is><t>after c</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    rows = list(xlsx_reader.iter_first_sheet_rows(buf.getvalue()))
    assert rows == [
        ["english", "category"],
        ["long hair", "", "skipped b", "after c"],
    ]


@pytest.mark.parametrize(
    "groups", [["a string, not an object"], [None], {"name": "not a list"}]
)
def test_a_defaults_file_with_a_malformed_group_entry_is_a_value_error(
    tmp_path, groups
):
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps({"version": 1, "groups": groups}), encoding="utf-8")
    with pytest.raises(ValueError):
        tag_group_service.load_default_tags(path)


def test_a_phonetic_run_is_not_part_of_the_string():
    # <rPh> carries a reading (furigana), not text: "long hair", not "long hairよみ".
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sst = (
        f'<?xml version="1.0" encoding="UTF-8"?><sst {ns}>'
        "<si><t>english</t></si><si><t>category</t></si>"
        "<si><r><t>long </t></r><r><t>hair</t></r>"
        '<rPh sb="0" eb="1"><t>よみ</t></rPh></si>'
        "</sst>"
    )
    sheet = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet {ns}><sheetData>'
        '<row><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row><c r="A2" t="s"><v>2</v></c>'
        '<c r="B2" t="inlineStr"><is><t>Person</t>'
        '<rPh sb="0" eb="1"><t>ひと</t></rPh></is></c></row>'
        "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", sst)
    rows = list(xlsx_reader.iter_first_sheet_rows(buf.getvalue()))
    assert rows == [["english", "category"], ["long hair", "Person"]]


@pytest.mark.parametrize("tags", ["hair", [1, 2], [None], {"a": 1}])
def test_a_defaults_group_whose_tags_are_not_names_is_a_value_error(tmp_path, tags):
    path = tmp_path / "defaults.json"
    payload = {"version": 1, "groups": [{"name": "Hair", "tags": tags}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        tag_group_service.load_default_tags(path)

"""The v1.11 Phase 3 folder-structure commit.

One module-scoped ``Server`` covers the whole flow: run a real Phase 2 read
over a tiny real folder tree, accept a mapping over it, commit, and check what
came out the other side - a reference folder indexed in place, the accepted
projects/people/sets/tags, every picture linked, and **the release's headline,
asserted rather than eyeballed: not one file on disk moved, was renamed, or
changed a byte.**
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

import pytest

from pixlstash.server import Server
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

API = "/api/v1"
_READ = f"{API}/folder-structure/read"
_READ_STATUS = f"{API}/folder-structure/read/status"
_COMMIT = f"{API}/folder-structure/commit"
_COMMIT_STATUS = f"{API}/folder-structure/commit/status"

pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _make_tree(root: str, spec: dict) -> None:
    """Real (tiny) files on disk - see test_folder_structure_read.py's twin."""
    from PIL import Image

    for rel, files in spec.items():
        folder = os.path.join(root, *rel.split("/")) if rel else root
        os.makedirs(folder, exist_ok=True)
        for name in files:
            path = os.path.join(folder, name)
            if os.path.splitext(name)[1].lower() in (".jpg", ".jpeg", ".png"):
                Image.new("RGB", (16, 16), (32, 64, 96)).save(path)
            else:
                with open(path, "w") as fh:
                    fh.write("a caption")


def _snapshot(root: str) -> dict:
    """Every file under *root*: relative path -> (size, content hash).

    Not mtime - a read-only walk must not perturb it, but asserting on it
    anyway would be asserting on noise the OS itself introduces (atime-linked
    mtime updates on some filesystems). Content is what "not one byte changed"
    actually means.
    """
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            out[rel] = (os.path.getsize(path), digest)
    return out


@pytest.fixture(scope="module")
def owner_env():
    tmp = tempfile.TemporaryDirectory()
    cfg = os.path.join(tmp.name, "server-config.json")
    with open(cfg, "w") as fh:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, fh)
    server = Server(cfg)
    server.__enter__()
    try:
        from starlette.testclient import TestClient

        owner = TestClient(server.api, raise_server_exceptions=True)
        login = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert login.status_code == 200, login.text
        yield {"server": server, "owner": owner, "tmp": tmp.name}
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


def _drain_read(owner, task_id, timeout_s: float = 30.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = owner.get(_READ_STATUS, params={"task_id": task_id})
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    pytest.fail(f"the read never settled: {body}")


def _drain_commit(owner, task_id, timeout_s: float = 30.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = owner.get(_COMMIT_STATUS, params={"task_id": task_id})
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.02)
    pytest.fail(f"the commit never settled: {body}")


def test_every_route_this_file_names_is_a_real_route(owner_env):
    app = owner_env["server"].api
    assert_real_route(app, "POST", _COMMIT)
    assert_real_route(app, "GET", _COMMIT_STATUS)


def test_committing_moves_renames_and_copies_zero_files(owner_env):
    """The release's headline. Real folders, real files, hashed before and after."""
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "zero-move-library")
    _make_tree(
        root,
        {
            "2024 Shoots/mira": ["a.jpg", "b.jpg"],
            "2024 Shoots/jonas": ["c.jpg"],
            "Datasets/mira-lora-v3": ["d.jpg", "d.txt"],
            "final": ["e.jpg"],
        },
    )
    before = _snapshot(root)
    assert len(before) == 6

    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    read_task_id = started.json()["task_id"]
    read_body = _drain_read(owner, read_task_id)
    assert read_body["status"] == "completed", read_body

    commit_started = owner.post(
        _COMMIT,
        json={
            "task_id": read_task_id,
            "assignments": [
                {"relative_path": "2024 Shoots", "kind": "project"},
                {"relative_path": "2024 Shoots/mira", "kind": "person"},
                {"relative_path": "2024 Shoots/jonas", "kind": "person"},
                {"relative_path": "Datasets/mira-lora-v3", "kind": "set"},
                {"relative_path": "final", "kind": "tag"},
            ],
        },
    )
    assert commit_started.status_code == 200, commit_started.text
    commit_task_id = commit_started.json()["task_id"]
    commit_body = _drain_commit(owner, commit_task_id, timeout_s=60.0)
    assert commit_body["status"] == "completed", commit_body
    result = commit_body["result"]
    # 5 images, not the 6 files in `before` - the caption sidecar (`d.txt`)
    # is not a picture and gets no Picture row of its own.
    assert result["pictures_indexed"] == 5
    assert result["projects_created"] == 1
    assert result["people_created"] == 2
    assert result["sets_created"] == 1
    assert result["tags_created"] == 1

    after = _snapshot(root)
    assert after == before, "the folder tree changed - the release's headline broke"


def test_the_accepted_mapping_actually_attaches_the_pictures(owner_env):
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "mapping-effect")
    _make_tree(root, {"2025/ines": ["a.jpg", "b.jpg"], "2025/raw": ["c.jpg"]})

    started = owner.post(_READ, json={"path": root})
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    commit_started = owner.post(
        _COMMIT,
        json={
            "task_id": task_id,
            "assignments": [
                {"relative_path": "2025", "kind": "project"},
                {"relative_path": "2025/ines", "kind": "person"},
                {"relative_path": "2025/raw", "kind": "tag"},
            ],
        },
    )
    commit_task_id = commit_started.json()["task_id"]
    body = _drain_commit(owner, commit_task_id, timeout_s=60.0)
    assert body["status"] == "completed", body

    projects = owner.get(f"{API}/projects").json()
    assert any(p["name"] == "2025" for p in projects)

    characters = owner.get(f"{API}/characters").json()
    names = {c["name"] for c in characters}
    assert "ines" in names


def test_recommitting_a_completed_read_is_refused_and_creates_nothing_twice(owner_env):
    """The one-shot invariant integration_architecture.md §22 documents.

    Not vacuous: without the `committed` guard this would create a second
    "2026" Project and a second "kai" Character, so the count assertions
    below fail if the guard is ever removed or the check-and-set race reopens.
    """
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "recommit-refused")
    _make_tree(root, {"2026/kai": ["a.jpg", "b.jpg"]})

    started = owner.post(_READ, json={"path": root})
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    assignments = [
        {"relative_path": "2026", "kind": "project"},
        {"relative_path": "2026/kai", "kind": "person"},
    ]
    first = owner.post(_COMMIT, json={"task_id": task_id, "assignments": assignments})
    assert first.status_code == 200, first.text
    body = _drain_commit(owner, first.json()["task_id"], timeout_s=60.0)
    assert body["status"] == "completed", body

    second = owner.post(_COMMIT, json={"task_id": task_id, "assignments": assignments})
    assert second.status_code == 409, second.text

    projects = [p for p in owner.get(f"{API}/projects").json() if p["name"] == "2026"]
    assert len(projects) == 1, "recommitting duplicated the Project"
    characters = [
        c for c in owner.get(f"{API}/characters").json() if c["name"] == "kai"
    ]
    assert len(characters) == 1, "recommitting duplicated the Character"


def test_a_malformed_commit_does_not_burn_the_read_s_one_commit(owner_env):
    """A 400 on bad input must not mark the read committed - the owner has
    to be able to fix `assignments` and try again."""
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "malformed-then-retry")
    _make_tree(root, {"": ["a.jpg"]})

    started = owner.post(_READ, json={"path": root})
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    bad = owner.post(
        _COMMIT,
        json={
            "task_id": task_id,
            "assignments": [{"relative_path": "", "kind": "nope"}],
        },
    )
    assert bad.status_code == 400, bad.text

    good = owner.post(_COMMIT, json={"task_id": task_id, "assignments": []})
    assert good.status_code == 200, good.text
    _drain_commit(owner, good.json()["task_id"], timeout_s=60.0)


def test_committing_a_path_already_registered_and_scanned_is_refused(owner_env):
    """§25's reuse-vs-refuse rule: a folder that already completed a scan
    (an unrelated reference folder, or an earlier commit of the same path
    from a since-cancelled read run again) must not be silently reused -
    that would apply the new mapping to whatever is indexed already, not to
    what this read found."""
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "already-a-reference-folder")
    _make_tree(root, {"": ["a.jpg"]})

    add = owner.post(f"{API}/reference-folders", json={"folder": root})
    assert add.status_code == 200, add.text

    def _scanned():
        r = owner.get(f"{API}/reference-folders")
        assert r.status_code == 200, r.text
        row = next(rf for rf in r.json()["folders"] if rf["folder"] == root)
        return row["last_scanned"] is not None

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and not _scanned():
        time.sleep(0.05)
    assert _scanned(), "the plain reference-folder route never finished its own scan"

    started = owner.post(_READ, json={"path": root})
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    commit_started = owner.post(_COMMIT, json={"task_id": task_id, "assignments": []})
    assert commit_started.status_code == 200, commit_started.text
    body = _drain_commit(owner, commit_started.json()["task_id"], timeout_s=60.0)
    assert body["status"] == "failed", body
    assert "already a reference folder" in body["error"]


def test_a_second_commit_while_one_runs_is_a_409(owner_env, monkeypatch):
    import pixlstash.services.folder_structure_commit_service as commit_service

    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "concurrent-commit")
    _make_tree(root, {"": ["a.jpg"]})

    started = owner.post(_READ, json={"path": root})
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    release = {"go": False}
    real_wait = commit_service.wait_for_first_scan

    def blocked_wait(*args, **kwargs):
        deadline = time.monotonic() + 5.0
        while not release["go"] and time.monotonic() < deadline:
            time.sleep(0.01)
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(commit_service, "wait_for_first_scan", blocked_wait)

    first = owner.post(_COMMIT, json={"task_id": task_id, "assignments": []})
    assert first.status_code == 200, first.text
    try:
        second = owner.post(_COMMIT, json={"task_id": task_id, "assignments": []})
        assert second.status_code == 409, second.text
    finally:
        release["go"] = True
        _drain_commit(owner, first.json()["task_id"], timeout_s=60.0)


def test_a_commit_can_be_given_the_read_s_own_result(owner_env):
    """The desktop's first run reads the folder on one server process and
    restarts the backend onto the GPU runtime before the owner answers the
    mapping questions, so the task that produced the answer is gone. The
    result is what survives, and it is enough to commit."""
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "handed-back-result")
    _make_tree(root, {"2024 Shoots/mira": ["a.jpg", "b.jpg"]})

    started = owner.post(_READ, json={"path": root})
    read_task_id = started.json()["task_id"]
    read_body = _drain_read(owner, read_task_id)
    assert read_body["status"] == "completed", read_body
    result = read_body["result"]

    # The task is never named: this is the request a restarted server sees.
    commit_started = owner.post(
        _COMMIT,
        json={
            "read_result": result,
            "assignments": [
                {"relative_path": "2024 Shoots", "kind": "project"},
                {"relative_path": "2024 Shoots/mira", "kind": "person"},
            ],
        },
    )
    assert commit_started.status_code == 200, commit_started.text
    commit_body = _drain_commit(owner, commit_started.json()["task_id"], timeout_s=60.0)
    assert commit_body["status"] == "completed", commit_body
    assert commit_body["result"]["projects_created"] == 1
    assert commit_body["result"]["people_created"] == 1


def test_a_commit_needs_exactly_one_of_the_two(owner_env):
    owner = owner_env["owner"]

    neither = owner.post(_COMMIT, json={"assignments": []})
    assert neither.status_code == 400, neither.text
    assert "either" in neither.json()["detail"].lower()

    both = owner.post(
        _COMMIT,
        json={
            "task_id": "read-1",
            "read_result": {"root": {"path": "/tmp"}},
            "assignments": [],
        },
    )
    assert both.status_code == 400, both.text


def test_a_handed_back_result_without_a_root_is_refused(owner_env):
    """Refused here with a 400, rather than inside the task with a traceback."""
    owner = owner_env["owner"]

    response = owner.post(
        _COMMIT, json={"read_result": {"picture_count": 3}, "assignments": []}
    )

    assert response.status_code == 400, response.text
    assert "root" in response.json()["detail"].lower()


def test_an_unknown_read_task_id_is_a_404(owner_env):
    r = owner_env["owner"].post(_COMMIT, json={"task_id": "nope", "assignments": []})
    assert r.status_code == 404, r.text


def test_an_unsettled_read_is_refused(owner_env):
    """A commit against a read still `running` is refused, not queued.

    The background read has already settled by the time this test forces the
    slot's status back to `running` - nothing else will touch it again - so
    the override is restored afterwards rather than "drained": there is
    nothing left to drain.
    """
    owner = owner_env["owner"]
    server = owner_env["server"]
    root = os.path.join(owner_env["tmp"], "still-scanning")
    _make_tree(root, {"": ["a.jpg"]})

    started = owner.post(_READ, json={"path": root})
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)
    with server.folder_structure_lock:
        server.folder_structure_read["status"] = "running"
    try:
        r = owner.post(_COMMIT, json={"task_id": task_id, "assignments": []})
        assert r.status_code == 409, r.text
    finally:
        with server.folder_structure_lock:
            server.folder_structure_read["status"] = "completed"


def test_an_unknown_commit_status_task_id_is_a_404(owner_env):
    r = owner_env["owner"].get(_COMMIT_STATUS, params={"task_id": "nope"})
    assert r.status_code == 404, r.text


def test_local_import_mode_imports_as_managed_pictures_and_assigns(owner_env):
    """`mode="local_import"`: the "Add a library" bugfix. Pictures already
    inside the library's own `image_root` become ordinary MANAGED pictures -
    no reference folder - with the same entity-assignment semantics as
    `mode="reference"`."""
    server = owner_env["server"]
    owner = owner_env["owner"]
    root = os.path.join(server.vault.image_root, "local-import-library")
    _make_tree(root, {"2027/nova": ["a.jpg", "b.jpg"], "2027/raw": ["c.jpg"]})
    before = _snapshot(root)
    assert len(before) == 3

    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    read_task_id = started.json()["task_id"]
    _drain_read(owner, read_task_id)

    before_refs = owner.get(f"{API}/reference-folders").json()["folders"]

    commit_started = owner.post(
        _COMMIT,
        json={
            "task_id": read_task_id,
            "mode": "local_import",
            "assignments": [
                {"relative_path": "2027", "kind": "project"},
                {"relative_path": "2027/nova", "kind": "person"},
                {"relative_path": "2027/raw", "kind": "tag"},
            ],
        },
    )
    assert commit_started.status_code == 200, commit_started.text
    body = _drain_commit(owner, commit_started.json()["task_id"], timeout_s=60.0)
    assert body["status"] == "completed", body
    result = body["result"]
    assert result["reference_folder_id"] is None, (
        "local_import must not use a ref folder"
    )
    assert result["pictures_indexed"] == 3
    assert result["projects_created"] == 1
    assert result["people_created"] == 1
    assert result["tags_created"] == 1

    after_refs = owner.get(f"{API}/reference-folders").json()["folders"]
    assert [f["id"] for f in after_refs] == [f["id"] for f in before_refs], (
        "local_import must not register a reference folder"
    )

    projects = owner.get(f"{API}/projects").json()
    assert any(p["name"] == "2027" for p in projects)
    characters = owner.get(f"{API}/characters").json()
    assert any(c["name"] == "nova" for c in characters)

    # The originals are untouched - no move, rename or copy, and since #1164
    # not even a thumbnail beside them: those land in the library's own
    # `.pixlstash-thumbnails/`, one per imported image.
    assert _snapshot(root) == before, (
        "local_import must move, rename, copy or write zero files in the folder"
    )
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    for relative in ("2027/nova/a.jpg", "2027/nova/b.jpg", "2027/raw/c.jpg"):
        stored = f"local-import-library/{relative}"
        thumb = ImageUtils.get_thumbnail_path(server.vault.image_root, stored)
        assert os.path.isfile(thumb), f"{relative} should have a thumbnail at {thumb}"


def test_local_import_wakes_the_planner_as_each_chunk_lands(
    owner_env, monkeypatch, caplog
):
    """Workers must start on the first indexed chunk, not on the commit's end.

    The planner sweeps only on a wake or when its backoff expires, and an idle
    library has it parked at `MAX_INTERVAL_S`. The chunk inserts alone never
    woke it, so every finder sat out the whole indexing stage and the AI work
    began on the end-of-commit notify - measured: first task submitted the
    same second the commit reported `done`, for imports up to ten seconds long.
    """
    from pixlstash.services import folder_structure_commit_service as commit_service

    server = owner_env["server"]
    root = os.path.join(server.vault.image_root, "local-import-wakes")
    _make_tree(root, {"": ["a.jpg", "b.jpg"]})

    wakes = []
    monkeypatch.setattr(server.vault, "wake", lambda: wakes.append(1))
    with caplog.at_level("INFO", logger=commit_service.logger.name):
        ids = commit_service.local_import_pictures(server, root, expected_pictures=2)

    assert len(ids) == 2
    assert wakes, "the chunk landed without waking the WorkPlanner"
    # The pass summary that says where an import's time went, read next to
    # the planner's [PIPELINE_PASS] line.
    summary = [
        r.getMessage() for r in caplog.records if "[IMPORT_PASS]" in r.getMessage()
    ]
    assert summary and "pictures=2 reused=0" in summary[-1], summary


def test_local_import_skips_dot_folders(owner_env):
    """A vault's own caches (`.ref_thumbs/`, `.pixlstash` sidecars) can sit
    right inside `image_root`, which `local_import`'s root commonly IS. A
    `.webp` thumbnail in one of those is a supported extension - without the
    same dot-folder prune the Phase 2 read already does, local_import would
    walk straight into it and import PixlStash's own cache files as if they
    were the owner's pictures."""
    server = owner_env["server"]
    owner = owner_env["owner"]
    root = os.path.join(server.vault.image_root, "dot-folder-prune")
    _make_tree(root, {"visible": ["a.jpg"]})
    hidden_dir = os.path.join(root, ".ref_thumbs")
    os.makedirs(hidden_dir, exist_ok=True)
    from PIL import Image

    Image.new("RGB", (16, 16), (1, 2, 3)).save(os.path.join(hidden_dir, "cached.webp"))

    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    commit_started = owner.post(
        _COMMIT, json={"task_id": task_id, "mode": "local_import", "assignments": []}
    )
    assert commit_started.status_code == 200, commit_started.text
    body = _drain_commit(owner, commit_started.json()["task_id"], timeout_s=30.0)
    assert body["status"] == "completed", body
    assert body["result"]["pictures_indexed"] == 1, (
        "the hidden folder's file must not be imported"
    )


def test_local_import_skips_the_librarys_own_folders(owner_env):
    """`tmp/` holds the set and face thumbnail caches; `snapshots/` the vault
    copies. Neither is the owner's pictures. A real import of a library root
    indexed 24 cache thumbnails as pictures before this."""
    owner, server = owner_env["owner"], owner_env["server"]
    root = server.vault.image_root
    _make_tree(
        root,
        {
            "own-folders/Trip": ["a.jpg"],
            "tmp/set_thumbnails": ["picture_set_1.webp"],
            "tmp/face_thumbnails": ["character_1.png"],
            "snapshots/2026-01-01": ["stray.jpg"],
        },
    )

    started = owner.post(_READ, json={"path": root})
    read_task_id = started.json()["task_id"]
    read = _drain_read(owner, read_task_id)
    assert read["status"] == "completed", read
    walked = {
        f["relative_path"]
        for level in read["result"]["levels"]
        for f in level["folders"]
    }
    assert not any(p == "tmp" or p.startswith("tmp/") for p in walked), walked
    assert not any(p == "snapshots" or p.startswith("snapshots/") for p in walked)

    commit_started = owner.post(
        _COMMIT,
        json={"task_id": read_task_id, "assignments": [], "mode": "local_import"},
    )
    assert commit_started.status_code == 200, commit_started.text
    assert (
        _drain_commit(owner, commit_started.json()["task_id"], timeout_s=60.0)["status"]
        == "completed"
    )

    from sqlmodel import select

    from pixlstash.db_models.picture import Picture

    paths = server.vault.db.run_immediate_read_task(
        lambda s: [p for p in s.exec(select(Picture.file_path)).all()]
    )
    assert "own-folders/Trip/a.jpg" in paths
    assert not any(p.startswith(("tmp/", "snapshots/")) for p in paths), [
        p for p in paths if p.startswith(("tmp/", "snapshots/"))
    ]


def test_local_import_mode_refuses_a_root_outside_image_root(owner_env):
    """local_import must never be reachable against an external folder - that
    is exactly what mode="reference" (register_reference_folder) is for, and
    routes.reference_folders already refuses the opposite direction (a
    reference folder that equals or contains image_root)."""
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "outside-image-root-for-local-import")
    _make_tree(root, {"": ["a.jpg"]})

    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    commit_started = owner.post(
        _COMMIT,
        json={"task_id": task_id, "mode": "local_import", "assignments": []},
    )
    assert commit_started.status_code == 200, commit_started.text
    body = _drain_commit(owner, commit_started.json()["task_id"], timeout_s=30.0)
    assert body["status"] == "failed", body
    assert "library's own folder" in body["error"], body


def test_reference_mode_refuses_the_librarys_own_folder(owner_env):
    """The mirror of the test above: the library's own storage is indexed in
    place and never registered as a reference folder. The wizard used to
    default to mode="reference", and pointed at image_root it registered the
    whole library as one reference folder - with absolute paths on every row
    and "remove" on that folder deleting all of them. Refused at the route."""
    server = owner_env["server"]
    owner = owner_env["owner"]
    root = os.path.join(server.vault.image_root, "own-folder-as-reference")
    _make_tree(root, {"2028/shoot": ["a.jpg"]})

    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    task_id = started.json()["task_id"]
    _drain_read(owner, task_id)

    before_refs = owner.get(f"{API}/reference-folders").json()["folders"]
    refused = owner.post(
        _COMMIT, json={"task_id": task_id, "mode": "reference", "assignments": []}
    )
    assert refused.status_code == 400, refused.text
    assert "library's own storage" in refused.json()["detail"]
    after_refs = owner.get(f"{API}/reference-folders").json()["folders"]
    assert [f["id"] for f in after_refs] == [f["id"] for f in before_refs], (
        "the refusal must register nothing"
    )

    # The same read still commits the right way.
    accepted = owner.post(
        _COMMIT, json={"task_id": task_id, "mode": "local_import", "assignments": []}
    )
    assert accepted.status_code == 200, accepted.text
    body = _drain_commit(owner, accepted.json()["task_id"], timeout_s=60.0)
    assert body["status"] == "completed", body


def test_a_picture_frozen_by_a_locked_set_is_skipped_not_filed(owner_env):
    """`local_import` may be handed pictures indexed before the wizard ran, so
    some can already sit in a locked set. Those are skipped; the rest of the
    folder is still filed."""
    from pixlstash.db_models.picture import Picture
    from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
    from pixlstash.db_models.tag import Tag
    from pixlstash.services.folder_structure_commit_service import (
        Assignment,
        CommitResult,
        _link_pictures,
    )
    from sqlmodel import select

    server = owner_env["server"]
    image_root = server.vault.image_root

    def scenario(session):
        frozen = Picture(file_path="locked-set-skip/gallery/frozen.jpg")
        free = Picture(file_path="locked-set-skip/gallery/free.jpg")
        session.add(frozen)
        session.add(free)
        session.flush()
        locked_set = PictureSet(name="frozen-by-a-lock", locked=True)
        session.add(locked_set)
        session.flush()
        session.add(PictureSetMember(set_id=locked_set.id, picture_id=frozen.id))
        session.flush()

        _link_pictures(
            session,
            [frozen, free],
            [Assignment(relative_path="gallery", kind="tag")],
            os.path.join(image_root, "locked-set-skip"),
            image_root,
            CommitResult(),
        )
        session.commit()

        tagged = set(
            session.exec(
                select(Tag.picture_id).where(Tag.picture_id.in_([frozen.id, free.id]))
            ).all()
        )
        return frozen.id, free.id, tagged

    frozen_id, free_id, tagged = server.vault.db.run_task(scenario)
    assert free_id in tagged, "an unlocked picture must still be filed"
    assert frozen_id not in tagged, "a picture frozen by a locked set must be skipped"


def test_a_folder_tag_survives_the_tagger_reaching_the_picture(owner_env):
    """The commit runs while the tagger is still draining the import.

    Every freshly indexed picture carries TAG_PENDING_SENTINEL, and when
    `TagTask` reaches one it deletes the picture's whole Tag set and rewrites
    it from `model_tags | human_POS - human_NEG`. A folder tag the owner just
    accepted is not a model tag, so unless the commit records it in the human
    label ledger the tagger silently deletes it minutes later. Without the
    `record_human_label` call in `_link_pictures` this test fails on the
    "gallery" assertion: only the model's own tag comes back.
    """
    from pixlstash.db_models.picture import Picture
    from pixlstash.db_models.tag import Tag, TAG_PENDING_SENTINEL
    from pixlstash.services.folder_structure_commit_service import (
        Assignment,
        CommitResult,
        _link_pictures,
    )
    from pixlstash.tasks.tag_task import TagTask
    from sqlmodel import select

    server = owner_env["server"]
    image_root = server.vault.image_root

    def scenario(session):
        pic = Picture(file_path="tag-survives-tagger/gallery/kept.jpg")
        session.add(pic)
        session.flush()
        # Exactly what the indexing step leaves behind for the tagger.
        session.add(Tag(picture_id=pic.id, tag=TAG_PENDING_SENTINEL))
        session.flush()

        _link_pictures(
            session,
            [pic],
            [Assignment(relative_path="gallery", kind="tag")],
            os.path.join(image_root, "tag-survives-tagger"),
            image_root,
            CommitResult(),
        )
        session.commit()

        # The tagger reaching this picture, with a model call that knows
        # nothing about the folder the owner filed it under.
        TagTask._add_tags_bulk(session, [{"pic_id": pic.id, "tags": ["outdoor"]}])

        return pic.id, set(
            session.exec(select(Tag.tag).where(Tag.picture_id == pic.id)).all()
        )

    pic_id, tags = server.vault.db.run_task(scenario)
    assert "gallery" in tags, f"the tagger erased the folder tag on {pic_id}: {tags}"
    assert "outdoor" in tags, "the tagger's own tag must still be applied"
    assert TAG_PENDING_SENTINEL not in tags, (
        "the tagger clears the sentinel it acted on"
    )


def test_a_person_lands_on_faces_extracted_before_the_mapping(owner_env):
    """Workers start while the import is still indexing, so a picture's faces
    routinely exist BEFORE the mapping assigns its folder to a person. The
    deferred `pending_character_id` was only ever resolved by the face task's
    own completion hook, which had already run; the mapping must resolve it
    itself for those pictures - and leave a not-yet-extracted picture pending
    rather than let the resolver discard it as "no faces found"."""
    from pixlstash.db_models.character import Character
    from pixlstash.db_models.face import Face
    from pixlstash.db_models.picture import Picture
    from pixlstash.services.folder_structure_commit_service import (
        Assignment,
        apply_local_mapping,
    )
    from sqlmodel import select

    server = owner_env["server"]
    image_root = server.vault.image_root
    root = os.path.join(image_root, "faces-before-mapping")

    def seed(session):
        early = Picture(file_path="faces-before-mapping/pending-person/early.jpg")
        late = Picture(file_path="faces-before-mapping/pending-person/late.jpg")
        session.add(early)
        session.add(late)
        session.flush()
        session.add(Face(picture_id=early.id, face_index=0))
        session.commit()
        return early.id, late.id

    early_id, late_id = server.vault.db.run_task(seed)

    apply_local_mapping(
        server,
        [early_id, late_id],
        [Assignment(relative_path="pending-person", kind="person")],
        root,
    )

    def check(session):
        character = session.exec(
            select(Character).where(Character.name == "pending-person")
        ).one()
        face = session.exec(select(Face).where(Face.picture_id == early_id)).one()
        early = session.get(Picture, early_id)
        late = session.get(Picture, late_id)
        return (
            character.id,
            face.character_id,
            early.pending_character_id,
            late.pending_character_id,
        )

    character_id, face_character, early_pending, late_pending = (
        server.vault.db.run_task(check)
    )
    assert face_character == character_id, "the face that already existed is assigned"
    assert early_pending is None
    assert late_pending == character_id, "a picture not yet extracted stays pending"


# ===========================================================================
# The durable record: a commit that is interrupted is finished, not lost
# ===========================================================================


def test_a_read_survives_a_library_switch_and_commits_into_the_new_library(
    owner_env,
):
    """The Add-library flow: read the folder BEFORE it is a library, then add
    it, switch to it, and commit the pre-switch read as `local_import`. The
    read lives on the Server, not the vault, so the switch must not lose it."""
    from sqlmodel import select

    from pixlstash.db_models.picture import Picture

    server = owner_env["server"]
    owner = owner_env["owner"]
    original = server.library_registry.active_library()
    root = os.path.join(owner_env["tmp"], "future-library")
    _make_tree(root, {"2028/vega": ["a.jpg", "b.jpg"], "2028/raw": ["c.jpg"]})

    started = owner.post(_READ, json={"path": root, "match_existing": False})
    assert started.status_code == 200, started.text
    read_task_id = started.json()["task_id"]
    assert _drain_read(owner, read_task_id)["status"] == "completed"

    try:
        added = owner.post(f"{API}/libraries", json={"path": root, "name": "Future"})
        assert added.status_code == 201, added.text
        switched = owner.post(
            f"{API}/libraries/active", json={"uuid": added.json()["uuid"]}
        )
        assert switched.status_code == 200, switched.text
        assert os.path.realpath(server.vault.image_root) == os.path.realpath(root)

        commit_started = owner.post(
            _COMMIT,
            json={
                "task_id": read_task_id,
                "mode": "local_import",
                "assignments": [
                    {"relative_path": "2028", "kind": "project"},
                    {"relative_path": "2028/vega", "kind": "person"},
                ],
            },
        )
        assert commit_started.status_code == 200, commit_started.text
        body = _drain_commit(owner, commit_started.json()["task_id"], timeout_s=60.0)
        assert body["status"] == "completed", body
        assert body["result"]["pictures_indexed"] == 3
        assert body["result"]["reference_folder_id"] is None

        rows = server.vault.db.run_immediate_read_task(
            lambda session: session.exec(
                select(Picture.file_path, Picture.reference_folder_id)
            ).all()
        )
        assert len(rows) == 3, rows
        assert all(not os.path.isabs(fp) and ref is None for fp, ref in rows), rows
        assert any(c["name"] == "vega" for c in owner.get(f"{API}/characters").json())

        again = owner.post(
            _COMMIT, json={"task_id": read_task_id, "mode": "local_import"}
        )
        assert again.status_code == 409, again.text
        assert "already been committed" in again.json()["detail"]
    finally:
        server.library_switch.switch_to(original.uuid)


def _records(server):
    from sqlmodel import select

    from pixlstash.db_models.folder_mapping_commit import FolderMappingCommit

    return server.vault.db.run_immediate_read_task(
        lambda session: [
            {"task_id": r.task_id, "state": r.state, "stage": r.stage, "mode": r.mode}
            for r in session.exec(
                select(FolderMappingCommit).order_by(FolderMappingCommit.id)
            ).all()
        ]
    )


def _record_for(server, task_id):
    return next((r for r in _records(server) if r["task_id"] == task_id), None)


def test_a_finished_commit_settles_its_record_in_the_same_transaction(owner_env):
    """`done` is written by the assigning transaction, not after it.

    The whole exactly-once argument rests on this: a record still marked
    pending after its entities were created would be resumed at the next
    start-up and create every one of them a second time.
    """
    owner, server = owner_env["owner"], owner_env["server"]
    root = os.path.join(owner_env["tmp"], "settled-record")
    _make_tree(root, {"Anna": ["a.jpg"], "Trips": ["b.jpg"]})

    started = owner.post(_READ, json={"path": root})
    read_task_id = started.json()["task_id"]
    assert _drain_read(owner, read_task_id)["status"] == "completed"

    commit_started = owner.post(
        _COMMIT,
        json={
            "task_id": read_task_id,
            "assignments": [{"relative_path": "Anna", "kind": "person"}],
        },
    )
    assert commit_started.status_code == 200, commit_started.text
    task_id = commit_started.json()["task_id"]
    assert _drain_commit(owner, task_id, timeout_s=60.0)["status"] == "completed"

    record = _record_for(server, task_id)
    assert record is not None, "the commit was never written down"
    assert record["state"] == "done"


def test_an_interrupted_commit_is_recorded_pending_with_what_it_needs(owner_env):
    """The record carries enough to finish the job without the read.

    A restart has no read to go back to - the result only ever lived in server
    memory - so everything the resume needs (root, mode, and the accepted
    assignments themselves) has to be in the row.
    """
    from pixlstash.services import folder_structure_commit_service as svc

    server = owner_env["server"]
    root = os.path.join(owner_env["tmp"], "interrupted-record")
    _make_tree(root, {"Anna": ["a.jpg"]})

    svc.record_pending_commit(
        server,
        task_id="test-interrupted",
        root_path=root,
        mode="reference",
        label="Interrupted",
        expected_pictures=1,
        assignments=svc.parse_assignments(
            [{"relative_path": "Anna", "kind": "person"}]
        ),
    )
    try:
        pending = svc.pending_commit(server)
        assert pending is not None, "an interrupted commit must survive the process"
        assert pending["task_id"] == "test-interrupted"
        assert pending["root_path"] == root
        assert pending["mode"] == "reference"
        assert [a.relative_path for a in pending["assignments"]] == ["Anna"]
        assert [a.kind for a in pending["assignments"]] == ["person"]
    finally:
        svc.settle_pending_commit(server, "test-interrupted", "abandoned")

    # By identity, not by "nothing is pending": a commit that FAILED
    # deliberately leaves its own record pending so the next start-up retries
    # it, and this module's shared server has run several by now.
    assert _record_for(server, "test-interrupted")["state"] == "abandoned"
    resumable = svc.pending_commit(server)
    assert resumable is None or resumable["task_id"] != "test-interrupted", (
        "a settled record is never resumed"
    )


def test_stopping_a_commit_that_is_over_reports_what_it_actually_is(owner_env):
    """Same honesty as the read's cancel: no claim the client cannot check."""
    owner, server = owner_env["owner"], owner_env["server"]
    root = os.path.join(owner_env["tmp"], "stop-after-the-fact")
    _make_tree(root, {"Bea": ["a.jpg"]})

    started = owner.post(_READ, json={"path": root})
    read_task_id = started.json()["task_id"]
    assert _drain_read(owner, read_task_id)["status"] == "completed"
    commit_started = owner.post(
        _COMMIT,
        json={
            "task_id": read_task_id,
            "assignments": [{"relative_path": "Bea", "kind": "person"}],
        },
    )
    task_id = commit_started.json()["task_id"]
    assert _drain_commit(owner, task_id, timeout_s=60.0)["status"] == "completed"

    stopped = owner.request("DELETE", _COMMIT, params={"task_id": task_id})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "completed", "a finished commit is not stoppable"
    assert _record_for(server, task_id)["state"] == "done", "and it stays done"


def test_stopping_an_unknown_commit_is_a_404(owner_env):
    response = owner_env["owner"].request(
        "DELETE", _COMMIT, params={"task_id": "no-such-commit"}
    )
    assert response.status_code == 404


def test_the_stop_route_is_a_real_route(owner_env):
    assert_real_route(owner_env["server"].api, "DELETE", _COMMIT)


def test_a_pending_commit_is_finished_by_the_next_start_up(tmp_path):
    """The point of the whole record: a killed import finishes itself.

    Its own ``Server`` pair rather than the module's shared one, because the
    thing under test *is* the process boundary - the resume runs as the router
    is built, so it cannot be provoked inside a server that is already up.
    """
    from sqlmodel import select

    from pixlstash.db_models.character import Character
    from pixlstash.db_models.picture import Picture
    from pixlstash.services import folder_structure_commit_service as svc

    cfg = str(tmp_path / "server-config.json")
    with open(cfg, "w") as fh:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, fh)

    with Server(cfg) as first:
        image_root = first.vault.image_root
        _make_tree(image_root, {"Anna": ["a.jpg", "b.jpg"]})
        # Exactly the state a kill between the two phases leaves behind: the
        # mapping accepted and written down, nothing indexed, nothing linked.
        svc.record_pending_commit(
            first,
            task_id="killed-mid-import",
            root_path=image_root,
            mode="local_import",
            label=None,
            expected_pictures=2,
            assignments=svc.parse_assignments(
                [{"relative_path": "Anna", "kind": "person"}]
            ),
        )
        assert (
            first.vault.db.run_immediate_read_task(
                lambda s: s.exec(select(Picture.id)).all()
            )
            == []
        ), "nothing may be indexed yet, or this proves nothing"

    with Server(cfg) as second:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            record = _record_for(second, "killed-mid-import")
            if record and record["state"] != "pending":
                break
            time.sleep(0.05)
        assert record is not None and record["state"] == "done", (
            f"start-up did not finish the interrupted commit: {record}"
        )

        def read(session):
            return (
                len(session.exec(select(Picture.id)).all()),
                [c.name for c in session.exec(select(Character)).all()],
            )

        indexed, people = second.vault.db.run_immediate_read_task(read)
        assert indexed == 2, "the pictures the killed import never got to index"
        assert people == ["Anna"], "and the person its assignments named"


def test_a_taken_project_name_does_not_wedge_the_commit(owner_env):
    """`Project.name` is unique case-insensitively across the library. Marking a
    folder as a project and choosing "start a new one" while that name is taken
    raised IntegrityError, which is neither CommitError nor CommitStopped - so
    the route left the durable record *pending*, every start-up resumed it,
    re-walked and re-hashed the whole root, and failed identically for ever.

    The owner asked for a new one, so they get a new one under a free name.
    """
    from pixlstash.db_models.picture import Picture
    from pixlstash.db_models.project import Project
    from pixlstash.services.folder_structure_commit_service import (
        Assignment,
        CommitResult,
        _link_pictures,
    )
    from sqlmodel import select

    server = owner_env["server"]
    image_root = server.vault.image_root

    def scenario(session):
        session.add(Project(name="Taken Name"))
        session.flush()
        pic = Picture(file_path="name-clash/Taken Name/a.jpg")
        session.add(pic)
        session.flush()

        _link_pictures(
            session,
            [pic],
            [Assignment(relative_path="Taken Name", kind="project")],
            os.path.join(image_root, "name-clash"),
            image_root,
            CommitResult(),
        )
        session.commit()
        names = sorted(
            session.exec(
                select(Project.name).where(Project.name.like("Taken Name%"))
            ).all()
        )
        return names, session.get(Picture, pic.id).project_id

    names, project_id = server.vault.db.run_task(scenario)
    assert names == ["Taken Name", "Taken Name (2)"], names
    assert project_id is not None, "the picture must still be filed"


def test_committing_the_same_folder_twice_is_idempotent(owner_env):
    """A resumed or re-run commit re-links pictures that already carry their
    assignments. PictureProjectMember and PictureSetMember have composite
    primary keys and Tag a (picture_id, tag) unique constraint, so the second
    pass raised IntegrityError and wedged the record pending."""
    from pixlstash.db_models.picture import Picture
    from pixlstash.db_models.tag import Tag
    from pixlstash.services.folder_structure_commit_service import (
        Assignment,
        CommitResult,
        _link_pictures,
    )
    from sqlmodel import select

    server = owner_env["server"]
    image_root = server.vault.image_root

    def scenario(session):
        pic = Picture(file_path="twice/gallery/b.jpg")
        session.add(pic)
        session.flush()
        assignments = [Assignment(relative_path="gallery", kind="tag")]
        for _ in range(2):
            _link_pictures(
                session,
                [pic],
                assignments,
                os.path.join(image_root, "twice"),
                image_root,
                CommitResult(),
            )
            session.commit()
        return session.exec(select(Tag).where(Tag.picture_id == pic.id)).all()

    tags = server.vault.db.run_task(scenario)
    assert len(tags) == 1, f"the second commit must not duplicate the tag: {tags}"


def test_the_commit_holds_a_library_lease_for_its_whole_run(owner_env, monkeypatch):
    """The commit thread re-reads `server.vault` at every step and used to hold
    no lease, so `LibraryGenerationCoordinator.begin_switch` saw zero readers
    and let a switch through mid run. A commit against library A that survived
    one created A's projects, people, sets and tags inside B, linked B's rows to
    them, and wrote A's durable record into B - so B resumed A's commit at the
    next start-up. The window is INDEX_TIMEOUT_S: thirty minutes.

    A lease held for the thread's life makes `begin_switch` wait and then fail
    instead of proceeding, which is the honest answer: the library is busy.
    """
    from pixlstash.services import (
        folder_structure_commit_service as commit_service_module,
    )

    owner = owner_env["owner"]
    server = owner_env["server"]
    root = os.path.join(owner_env["tmp"], "lease-library")
    _make_tree(root, {"Shoots/mira": ["a.jpg"]})

    read_task_id = owner.post(_READ, json={"path": root}).json()["task_id"]
    assert _drain_read(owner, read_task_id)["status"] == "completed"

    real_acquire = server.library_coordinator.acquire_read
    real_release = server.library_coordinator.release_read
    held: list = []
    released: list = []
    # Readers seen by the coordinator at the moment the commit does its work.
    # This is the number `begin_switch` waits on, so a zero here is the bug.
    readers_during_work: list = []
    real_stage = commit_service_module.record_commit_stage

    def watched_acquire():
        lease = real_acquire()
        held.append(lease)
        return lease

    def watched_release(lease):
        released.append(lease)
        return real_release(lease)

    def watched_stage(srv, task_id, stage, *args, **kwargs):
        # "assigning" is the step both modes reach and the one that writes the
        # projects, people, sets and tags into whatever vault is active.
        if stage == "assigning":
            coordinator = server.library_coordinator
            readers_during_work.append(
                coordinator._readers.get(coordinator.generation, 0)
            )
        return real_stage(srv, task_id, stage, *args, **kwargs)

    monkeypatch.setattr(server.library_coordinator, "acquire_read", watched_acquire)
    monkeypatch.setattr(server.library_coordinator, "release_read", watched_release)
    monkeypatch.setattr(commit_service_module, "record_commit_stage", watched_stage)

    started = owner.post(
        _COMMIT,
        json={
            "task_id": read_task_id,
            "assignments": [{"relative_path": "Shoots", "kind": "project"}],
        },
    )
    assert started.status_code == 200, started.text
    settled = _drain_commit(owner, started.json()["task_id"], timeout_s=60.0)
    assert settled["status"] == "completed", settled

    assert held and held[0] is not None, "the commit must take a library lease"
    assert released, "and must give it back when it finishes"
    assert readers_during_work, "the commit never reached the assigning step"
    assert readers_during_work[0] > 0, (
        "a switch would have been let through while the commit was working: "
        f"begin_switch saw {readers_during_work[0]} reader(s)"
    )


def test_a_switch_refused_by_a_running_commit_says_why(owner_env):
    """The refusal is the intended behaviour, so it has to read like one.

    `begin_switch` raises "Timed out waiting for active-library readers", which
    is the right mechanism and the wrong sentence to hand an owner who simply
    pressed Switch while a mapping was still being organised.
    """
    server = owner_env["server"]

    with server.folder_structure_commit_lock:
        server.folder_structure_commit = {
            "task_id": "pretend-running",
            "status": "running",
            "stage": "assigning",
            "processed": 0,
            "total": 1,
            "error": None,
            "result": None,
            "stop": None,
        }
    try:
        assert (
            server.library_switch._what_is_holding_the_library()
            == "a folder mapping is still being organised"
        )
    finally:
        with server.folder_structure_commit_lock:
            server.folder_structure_commit = None

    # With nothing running there is nothing to name, and the caller falls back
    # to the coordinator's own words rather than inventing a reason.
    assert server.library_switch._what_is_holding_the_library() is None

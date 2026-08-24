"""The v1.11 Phase 3 folder-structure commit.

One module-scoped ``Server`` covers the whole flow: run a real Phase 2 read
over a tiny real folder tree, accept a mapping over it, commit, and check what
came out the other side — a reference folder indexed in place, the accepted
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
    """Real (tiny) files on disk — see test_folder_structure_read.py's twin."""
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

    Not mtime — a read-only walk must not perturb it, but asserting on it
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
    # 5 images, not the 6 files in `before` — the caption sidecar (`d.txt`)
    # is not a picture and gets no Picture row of its own.
    assert result["pictures_indexed"] == 5
    assert result["projects_created"] == 1
    assert result["people_created"] == 2
    assert result["sets_created"] == 1
    assert result["tags_created"] == 1

    after = _snapshot(root)
    assert after == before, "the folder tree changed — the release's headline broke"


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
    """The one-shot invariant integration_architecture.md §21 documents.

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
    """A 400 on bad input must not mark the read committed — the owner has
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
    from a since-cancelled read run again) must not be silently reused —
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


def test_an_unknown_read_task_id_is_a_404(owner_env):
    r = owner_env["owner"].post(_COMMIT, json={"task_id": "nope", "assignments": []})
    assert r.status_code == 404, r.text


def test_an_unsettled_read_is_refused(owner_env):
    """A commit against a read still `running` is refused, not queued.

    The background read has already settled by the time this test forces the
    slot's status back to `running` — nothing else will touch it again — so
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

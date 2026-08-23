"""The v1.11 Phase 2 folder-structure read.

Two halves, deliberately split by cost. The signal tests run the service
directly over a temporary folder tree with a stubbed detector — no ``Server``,
no inference, milliseconds each. One module-scoped ``Server`` at the bottom
covers the routes and the authz declaration in both directions.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time

import numpy as np
import pytest

from pixlstash.server import Server
from pixlstash.services.folder_structure_service import (
    FolderStructureRead,
    MAX_FOLDERS,
    MIN_FACE_SAMPLE,
    SAME_IDENTITY_COSINE,
    SAMPLED_PER_FOLDER,
    _dominant_identity_count,
    _evenly_spaced,
    load_existing_entities,
    normalise_name,
)
from tests.authz_guard import assert_real_route

API = "/api/v1"


# ===========================================================================
# A folder tree on disk, and a detector that answers from its filenames
# ===========================================================================


def _make_tree(root: str, spec: dict) -> None:
    """Create folders and files. ``spec`` maps a relative folder to its files.

    Picture extensions get a real (tiny) image: the read decodes what it samples,
    and a ``.jpg`` full of ``x`` would be counted as no-face for the wrong
    reason, quietly turning every face assertion green.
    """
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


class _FakeFace:
    def __init__(self, embedding):
        self.bbox = np.array([0.0, 0.0, 10.0, 10.0])
        self.embedding = embedding


#: Wide enough that every seed in these tests is its own identity. An 8-d
#: one-hot would silently wrap at seed 8, so "20 different people" would really
#: be 8 people with three pictures each and the assertion would hold for the
#: wrong reason.
_EMBED_DIM = 64


def _unit(seed: int) -> np.ndarray:
    """A deterministic unit vector. Distinct seeds are orthogonal."""
    assert seed < _EMBED_DIM, "seeds must not wrap, or identities silently merge"
    vector = np.zeros(_EMBED_DIM, dtype=np.float32)
    vector[seed] = 1.0
    return vector


def _near(seed: int, cosine: float) -> np.ndarray:
    """A unit vector at (approximately) ``cosine`` from ``_unit(seed)``.

    Lets a test sit either side of ``SAME_IDENTITY_COSINE`` instead of only at
    the 0.0/1.0 extremes a one-hot fixture can express — the threshold is what
    decides whether a real folder reads as a Person, so it has to be pinned."""
    other = (seed + 1) % _EMBED_DIM
    vector = np.zeros(_EMBED_DIM, dtype=np.float32)
    vector[seed] = cosine
    vector[other] = float(np.sqrt(max(0.0, 1.0 - cosine * cosine)))
    return vector


def _detector_from_identity(identity_by_index):
    """Build a ``detect_faces`` stub.

    ``identity_by_index(i)`` returns an int identity for the i-th image of a
    batch, or ``None`` for "no face in this one".
    """

    def detect(images):
        results = []
        for i, image in enumerate(images):
            identity = None if image is None else identity_by_index(i)
            results.append([] if identity is None else [_FakeFace(_unit(identity))])
        return results

    return detect


@contextlib.contextmanager
def _tree(spec: dict):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "Generations")
        os.makedirs(root)
        _make_tree(root, spec)
        yield root


def _rows(result, depth):
    for level in result["levels"]:
        if level["depth"] == depth:
            return {row["name"]: row for row in level["folders"]}
    raise AssertionError(
        f"no level at depth {depth}: {[lvl['depth'] for lvl in result['levels']]}"
    )


def _level(result, depth):
    for level in result["levels"]:
        if level["depth"] == depth:
            return level
    raise AssertionError(f"no level at depth {depth}")


# ===========================================================================
# The walk
# ===========================================================================


def test_the_walk_numbers_levels_from_the_root_and_counts_pictures_recursively():
    with _tree(
        {
            "": [],
            "2024 Shoots": ["cover.jpg"],
            "2024 Shoots/mira": ["a.jpg", "b.jpg"],
            "2023 Shoots": [],
        }
    ) as root:
        result = FolderStructureRead(root).run()

    assert result["root"]["name"] == "Generations"
    # 3 direct + the root itself
    assert result["folder_count"] == 4
    assert result["picture_count"] == 3, "the root's count is the whole tree"
    assert _level(result, 1)["folder_count"] == 1
    assert _level(result, 2)["folder_count"] == 2
    assert _rows(result, 2)["2024 Shoots"]["picture_count"] == 3, (
        "recursive: its own cover plus mira's two"
    )
    assert _rows(result, 2)["2024 Shoots"]["direct_picture_count"] == 1
    assert _rows(result, 3)["mira"]["relative_path"] == "2024 Shoots/mira"


def test_a_row_never_carries_an_absolute_path():
    """The rows are for a screen. Publishing one must not publish a home dir."""
    with _tree({"": [], "a": ["x.jpg"]}) as root:
        result = FolderStructureRead(root).run()
    blob = json.dumps(result["levels"])
    assert root not in blob, "an absolute host path leaked into the rows"
    assert result["root"]["path"] == root, "the root still names it, once"


def test_the_parent_id_of_a_row_is_the_id_of_its_parent_row():
    with _tree({"": [], "a": [], "a/b": []}) as root:
        result = FolderStructureRead(root).run()
    root_row = _level(result, 1)["folders"][0]
    a = _rows(result, 2)["a"]
    b = _rows(result, 3)["b"]
    assert a["parent_id"] == root_row["id"]
    assert b["parent_id"] == a["id"]
    assert root_row["parent_id"] is None


# ===========================================================================
# Signal: cardinality (level-scoped)
# ===========================================================================


def test_few_names_under_many_parents_reads_as_tag():
    spec = {"": []}
    for parent in ("p1", "p2", "p3", "p4"):
        spec[parent] = []
        for leaf in ("final", "raw", "selects"):
            spec[f"{parent}/{leaf}"] = ["a.jpg"]
    with _tree(spec) as root:
        result = FolderStructureRead(root).run()

    level = _level(result, 3)
    assert level["proposal"]["kind"] == "tag"
    evidence = level["proposal"]["evidence"][0]
    assert evidence["signal"] == "cardinality"
    assert evidence["names"] == 3 and evidence["parents"] == 4
    assert "3 names under 4 parents" in evidence["text"]


def test_names_used_once_each_rule_tag_out_and_rule_nothing_in():
    spec = {"": [], "a": [], "b": [], "c": []}
    with _tree(spec) as root:
        result = FolderStructureRead(root).run()

    proposal = _level(result, 2)["proposal"]
    assert proposal["kind"] is None, "narrowed is not decided"
    assert proposal["candidates"] == ["project", "set", "person"]
    assert "used once each" in proposal["evidence"][0]["text"]


def test_the_root_level_never_carries_a_cardinality_reading():
    with _tree({"": ["a.jpg"]}) as root:
        result = FolderStructureRead(root).run()
    assert _level(result, 1)["proposal"] == {
        "kind": None,
        "candidates": [],
        "match": None,
        "evidence": [],
    }


# ===========================================================================
# Signal: sidecars (folder-scoped)
# ===========================================================================


def test_a_caption_beside_every_picture_reads_as_set():
    with _tree({"": [], "shoot": ["a.jpg", "a.txt", "b.png", "b.txt"]}) as root:
        result = FolderStructureRead(root).run()

    proposal = _rows(result, 2)["shoot"]["proposal"]
    assert proposal["kind"] == "set"
    (evidence,) = proposal["evidence"]
    assert evidence["signal"] == "sidecars"
    assert evidence["pictures"] == 2 and evidence["with_sidecar"] == 2
    assert "all 2 pictures" in evidence["text"]


def test_a_caption_beside_most_pictures_says_nothing_at_all():
    """`every` picture, not `most`. A signal that cannot state its reason does
    not propose — and 'a caption beside 2 of 3' is not the Set fact."""
    with _tree(
        {"": [], "shoot": ["a.jpg", "a.txt", "b.jpg", "b.txt", "c.jpg"]}
    ) as root:
        result = FolderStructureRead(root).run()

    proposal = _rows(result, 2)["shoot"]["proposal"]
    assert proposal["kind"] is None
    assert proposal["evidence"] == []


# ===========================================================================
# Signal: faces (folder-scoped, sampled)
# ===========================================================================


def _faces_spec(count: int) -> dict:
    return {"": [], "mira": [f"{i:03d}.jpg" for i in range(count)]}


def test_one_identity_across_a_folder_reads_as_person_and_says_the_count():
    with _tree(_faces_spec(40)) as root:
        # 19 of the 20 sampled are the same person; the 20th is somebody else.
        result = FolderStructureRead(
            root,
            detect_faces=_detector_from_identity(lambda i: 1 if i < 19 else 2),
        ).run()

    proposal = _rows(result, 2)["mira"]["proposal"]
    assert proposal["kind"] == "person"
    (evidence,) = proposal["evidence"]
    assert evidence["signal"] == "faces"
    assert evidence["sampled"] == SAMPLED_PER_FOLDER
    assert evidence["matched"] == 19
    assert evidence["text"] == "one face, 19 of 20"


def test_a_folder_of_different_people_proposes_nothing():
    with _tree(_faces_spec(40)) as root:
        result = FolderStructureRead(
            root, detect_faces=_detector_from_identity(lambda i: i)
        ).run()

    proposal = _rows(result, 2)["mira"]["proposal"]
    assert proposal["kind"] is None
    assert proposal["evidence"] == []


def test_the_face_signal_stays_silent_below_the_minimum_sample():
    """'One face, 2 of 3' is not evidence anyone should act on."""
    with _tree(_faces_spec(MIN_FACE_SAMPLE - 1)) as root:
        result = FolderStructureRead(
            root, detect_faces=_detector_from_identity(lambda i: 1)
        ).run()

    assert _rows(result, 2)["mira"]["proposal"]["evidence"] == []


def test_no_more_than_the_sample_is_ever_decoded():
    """The whole reason the pass is two minutes rather than an hour."""
    batches = []

    def detect(images):
        batches.append(len(images))
        return [[_FakeFace(_unit(1))] for _ in images]

    with _tree(_faces_spec(500)) as root:
        FolderStructureRead(root, detect_faces=detect).run()

    assert batches == [SAMPLED_PER_FOLDER], batches


def test_a_folder_whose_detection_fails_costs_that_folder_and_not_the_read():
    def detect(images):
        raise RuntimeError("the GPU fell over")

    with _tree(_faces_spec(40)) as root:
        result = FolderStructureRead(root, detect_faces=detect).run()

    assert result["folder_count"] == 2, "the read still completed"
    assert _rows(result, 2)["mira"]["proposal"]["evidence"] == []


def test_without_an_engine_no_folder_is_proposed_as_a_person():
    with _tree(_faces_spec(40)) as root:
        result = FolderStructureRead(root, detect_faces=None).run()
    assert _rows(result, 2)["mira"]["proposal"]["kind"] is None


# ===========================================================================
# Signal: name match
# ===========================================================================


def test_a_name_matching_one_entity_is_a_lookup_not_an_inference():
    with _tree({"": [], "2024_Shoots": []}) as root:
        result = FolderStructureRead(
            root, existing_entities=[("project", 7, "2024 Shoots")]
        ).run()

    proposal = _rows(result, 2)["2024_Shoots"]["proposal"]
    assert proposal["kind"] == "project"
    assert proposal["match"] == {
        "entity_type": "project",
        "id": 7,
        "name": "2024 Shoots",
    }
    assert proposal["evidence"][0]["signal"] == "name_match"


def test_a_tag_match_carries_no_id_because_a_tag_is_not_a_row():
    with _tree({"": [], "final": []}) as root:
        result = FolderStructureRead(
            root, existing_entities=[("tag", None, "final")]
        ).run()

    assert _rows(result, 2)["final"]["proposal"]["match"]["id"] is None


def test_a_name_matching_two_kinds_narrows_and_does_not_pick():
    with _tree({"": [], "mira": []}) as root:
        result = FolderStructureRead(
            root,
            existing_entities=[("project", 3, "Mira"), ("character", 9, "Mira")],
        ).run()

    proposal = _rows(result, 2)["mira"]["proposal"]
    assert proposal["kind"] is None
    assert proposal["match"] is None
    assert sorted(proposal["candidates"]) == ["person", "project"]
    assert (
        "an existing project and an existing person" in proposal["evidence"][0]["text"]
    )


def test_signals_that_disagree_return_both_rather_than_one():
    with _tree(_faces_spec(40)) as root:
        result = FolderStructureRead(
            root,
            detect_faces=_detector_from_identity(lambda i: 1),
            existing_entities=[("project", 3, "mira")],
        ).run()

    proposal = _rows(result, 2)["mira"]["proposal"]
    assert proposal["kind"] is None
    assert sorted(proposal["candidates"]) == ["person", "project"]
    assert {e["signal"] for e in proposal["evidence"]} == {"faces", "name_match"}


def test_signals_that_agree_keep_the_match_and_both_reasons():
    with _tree(_faces_spec(40)) as root:
        result = FolderStructureRead(
            root,
            detect_faces=_detector_from_identity(lambda i: 1),
            existing_entities=[("character", 41, "Mira")],
        ).run()

    proposal = _rows(result, 2)["mira"]["proposal"]
    assert proposal["kind"] == "person"
    assert proposal["match"]["id"] == 41
    assert [e["signal"] for e in proposal["evidence"]] == ["faces", "name_match"]


# ===========================================================================
# The level vote
# ===========================================================================


def test_a_level_whose_rows_agree_is_answered_with_its_own_count():
    spec = {"": []}
    for name in ("alpha", "beta", "gamma", "delta"):
        spec[name] = ["a.jpg", "a.txt", "b.jpg", "b.txt"]
    with _tree(spec) as root:
        result = FolderStructureRead(root).run()

    proposal = _level(result, 2)["proposal"]
    assert proposal["kind"] == "set"
    assert proposal["evidence"][0]["text"] == "4 of 4 folders read as Set"


# ===========================================================================
# Bounds and cancellation
# ===========================================================================


def test_the_walk_is_bounded_and_says_so(monkeypatch):
    monkeypatch.setattr("pixlstash.services.folder_structure_service.MAX_FOLDERS", 3)
    with _tree({"": [], "a": [], "b": [], "c": [], "d": []}) as root:
        result = FolderStructureRead(root).run()

    assert result["truncated"] is True
    assert result["folder_count"] == 3
    assert result["max_folders"] == 3


def test_a_cancelled_read_keeps_what_it_found():
    """Cancel stays live for the whole two minutes, so what it leaves behind has
    to be showable: the folders walked before the cancel, not an empty answer."""
    read_box = {}

    def stop_once_walked(stage, processed, total):
        # Cancel once the walk has two folders: enough has happened to be worth
        # showing, and the read is nowhere near done.
        if stage == "walking" and processed >= 2:
            read_box["read"].cancel()

    with _tree({"": [], "a": ["x.jpg"], "b": ["y.jpg"]}) as root:
        read = FolderStructureRead(root, progress=stop_once_walked)
        read_box["read"] = read
        result = read.run()

    assert read.cancelled is True
    assert result["folder_count"] == 2, "the walk's work survived the cancel"
    assert result["root"]["name"] == "Generations"


def test_a_read_cancelled_before_it_starts_still_returns_a_document():
    with _tree({"": [], "a": ["x.jpg"]}) as root:
        read = FolderStructureRead(root)
        read.cancel()
        result = read.run()

    assert result["levels"] == [], "nothing was walked"
    assert result["root"]["path"] == root
    assert result["folder_count"] == 0


# ===========================================================================
# The small deterministic pieces
# ===========================================================================


def test_the_sample_is_spread_across_the_folder_not_taken_off_the_front():
    items = [f"{i:03d}" for i in range(100)]
    picked = _evenly_spaced(items, 10)
    assert len(picked) == 10
    assert picked[0] == "000" and picked[-1] == "090"
    assert _evenly_spaced(items[:5], 10) == items[:5]


def test_the_dominant_identity_is_the_largest_group_not_the_first():
    embeddings = [_unit(1), _unit(2), _unit(2), _unit(2)]
    assert _dominant_identity_count(embeddings) == 3
    assert _dominant_identity_count([]) == 0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024_Shoots", "2024 shoots"),
        ("2024 shoots", "2024 shoots"),
        ("  Mira-LoRA v3 ", "mira lora v3"),
        ("___", ""),
    ],
)
def test_names_fold_for_comparison(raw, expected):
    assert normalise_name(raw) == expected


# ===========================================================================
# The routes: one real server, both directions on the authz declaration
# ===========================================================================


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
        # The first POST /login on a fresh vault *registers* the owner with
        # whatever it is sent, so this value is invented here rather than known.
        # It carries its marker in the value — the prefix travels with it when
        # the fixture is copied, which a comment beside it would not.
        login = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert login.status_code == 200, login.text
        yield {"server": server, "owner": owner, "tmp": tmp.name}
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


_READ = f"{API}/folder-structure/read"
_STATUS = f"{API}/folder-structure/read/status"


def _drain(owner, task_id, timeout_s: float = 30.0):
    """Poll one read to a settled state and return its status body.

    Sleeps between polls: a tight 200-iteration spin passes on an idle box and
    flakes on a loaded CI shard, which is the worst of both."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = owner.get(_STATUS, params={"task_id": task_id})
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    pytest.fail(f"the read never settled: {body}")


def test_the_owner_reaches_the_read_and_a_missing_folder_is_a_404(owner_env):
    owner = owner_env["owner"]
    r = owner.post(_READ, json={"path": os.path.join(owner_env["tmp"], "nope")})
    assert r.status_code == 404, r.text


def test_a_relative_path_is_refused_before_anything_is_walked(owner_env):
    r = owner_env["owner"].post(_READ, json={"path": "../../etc"})
    assert r.status_code == 400, r.text


def test_an_unknown_task_id_is_a_404_on_status_and_cancel(owner_env):
    owner = owner_env["owner"]
    assert owner.get(_STATUS, params={"task_id": "nope"}).status_code == 404
    assert owner.delete(_READ, params={"task_id": "nope"}).status_code == 404


def test_a_read_completes_and_reports_its_result(owner_env):
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "library")
    _make_tree(root, {"": [], "shoot": ["a.jpg", "a.txt"]})

    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    task_id = started.json()["task_id"]

    body = _drain(owner, task_id)
    assert body["status"] == "completed", body
    assert body["stage"] == "done"
    assert body["result"]["folder_count"] == 2
    assert body["result"]["sampled_per_folder"] == SAMPLED_PER_FOLDER
    # The sidecar signal is a filesystem fact and needs no engine, so it fires
    # in CI where the face one may not.
    shoot = _rows(body["result"], 2)["shoot"]
    assert shoot["proposal"]["kind"] == "set"


def test_the_read_writes_nothing(owner_env):
    """The release's headline, asserted rather than eyeballed."""
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "untouched")
    _make_tree(root, {"": [], "a": ["x.jpg", "x.txt"], "b": ["y.jpg"]})
    before = {
        os.path.join(dirpath, name): os.stat(os.path.join(dirpath, name)).st_mtime_ns
        for dirpath, _dirs, files in os.walk(root)
        for name in files
    }
    counts_before = {
        route: owner.get(f"{API}/{route}").json()
        for route in ("projects", "picture-sets", "characters")
    }

    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    body = _drain(owner, started.json()["task_id"])
    assert body["status"] == "completed", body

    after = {
        os.path.join(dirpath, name): os.stat(os.path.join(dirpath, name)).st_mtime_ns
        for dirpath, _dirs, files in os.walk(root)
        for name in files
    }
    assert after == before, "the read moved, renamed or rewrote a file"
    for route, was in counts_before.items():
        assert owner.get(f"{API}/{route}").json() == was, (
            f"the read created a {route} row"
        )


def test_a_share_token_is_refused_on_all_three_routes(owner_env):
    """Both directions: the owner above reaches them, a READ token does not."""
    from starlette.testclient import TestClient

    server, owner = owner_env["server"], owner_env["owner"]
    minted = owner.post(
        f"{API}/users/me/token",
        json={
            "description": "example-share",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": 1,
        },
    )
    assert minted.status_code == 200, minted.text
    share = {"Authorization": f"Bearer {minted.json()['token']}"}
    anon = TestClient(server.api, raise_server_exceptions=True)
    assert anon.get(f"{API}/pictures", headers=share).status_code == 200, (
        "the share token is dead; the refusals below would prove nothing"
    )

    root = owner_env["tmp"]
    assert anon.post(_READ, json={"path": root}, headers=share).status_code == 403
    assert anon.get(_STATUS, params={"task_id": "x"}, headers=share).status_code == 403
    assert anon.delete(_READ, params={"task_id": "x"}, headers=share).status_code == 403


# ===========================================================================
# The cases the first pass did not cover
# ===========================================================================


def test_a_folder_the_process_cannot_read_is_counted_not_dropped_in_silence():
    """``os.walk`` swallows a permission error by default, and a read that
    quietly omits a subtree while reporting ``truncated: false`` tells the owner
    their library is smaller than it is."""
    with _tree({"": [], "open": ["a.jpg"], "locked": ["b.jpg", "c.jpg"]}) as root:
        locked = os.path.join(root, "locked")
        os.chmod(locked, 0o000)
        try:
            result = FolderStructureRead(root).run()
        finally:
            os.chmod(locked, 0o755)

    assert result["unreadable_folders"] == 1, (
        "the unreadable folder must be counted, not silently dropped"
    )
    assert result["truncated"] is False, "truncation is a different fact"


def test_a_symlink_to_a_restricted_directory_is_refused(owner_env):
    """The blocklist runs on the realpath. A raw-path-only check would let
    ``/home/me/link-to-etc`` walk /etc recursively and decode files out of it."""
    owner = owner_env["owner"]
    link = os.path.join(owner_env["tmp"], "innocent-looking")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink("/etc", link)

    direct = owner.post(_READ, json={"path": "/etc"})
    assert direct.status_code == 400, direct.text
    through_link = owner.post(_READ, json={"path": link})
    assert through_link.status_code == 400, (
        f"a symlink must not get past the blocklist; got "
        f"{through_link.status_code}: {through_link.text}"
    )
    assert "restricted" in through_link.text


def test_a_path_outside_the_configured_roots_is_refused(owner_env):
    """The other direction of the containment: in-root passes, out-of-root 403s."""
    server, owner = owner_env["server"], owner_env["owner"]
    inside = os.path.join(owner_env["tmp"], "inside")
    outside = tempfile.mkdtemp()
    _make_tree(inside, {"": ["a.jpg"]})

    cfg = server._server_config
    previous = cfg.get("filesystem_roots")
    cfg["filesystem_roots"] = [owner_env["tmp"]]
    try:
        refused = owner.post(_READ, json={"path": outside})
        assert refused.status_code == 403, refused.text
        assert "filesystem root" in refused.text
        # In-scope still works — over-blocking is its own regression.
        allowed = owner.post(_READ, json={"path": inside})
        assert allowed.status_code == 200, allowed.text
        _drain(owner, allowed.json()["task_id"])
    finally:
        if previous is None:
            cfg.pop("filesystem_roots", None)
        else:
            cfg["filesystem_roots"] = previous


def test_a_second_read_while_one_runs_is_a_409(owner_env, monkeypatch):
    """The single slot is the whole of the one-read-at-a-time guarantee."""
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "slow")
    _make_tree(root, {"": ["a.jpg"]})

    release = threading.Event()
    original = FolderStructureRead.run

    def slow_run(self):
        release.wait(timeout=10)
        return original(self)

    monkeypatch.setattr(FolderStructureRead, "run", slow_run)
    first = owner.post(_READ, json={"path": root})
    assert first.status_code == 200, first.text
    try:
        second = owner.post(_READ, json={"path": root})
        assert second.status_code == 409, second.text
        assert "already running" in second.text
    finally:
        release.set()
    _drain(owner, first.json()["task_id"])


def test_cancelling_a_finished_read_reports_what_it_is_rather_than_lying(owner_env):
    owner = owner_env["owner"]
    root = os.path.join(owner_env["tmp"], "quick")
    _make_tree(root, {"": ["a.jpg"]})
    started = owner.post(_READ, json={"path": root})
    assert started.status_code == 200, started.text
    task_id = started.json()["task_id"]
    _drain(owner, task_id)

    cancelled = owner.delete(_READ, params={"task_id": task_id})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "completed", (
        "a finished read was not cancelled and must not claim it was"
    )


def test_every_route_this_file_names_is_a_real_route(owner_env):
    """Guards the 403 assertions below: the SPA catch-all answers anything."""
    app = owner_env["server"].api
    assert_real_route(app, "POST", _READ)
    assert_real_route(app, "GET", _STATUS)
    assert_real_route(app, "DELETE", _READ)


def test_load_existing_entities_reads_the_vault(owner_env):
    """The function the whole name-match signal is fed from, exercised."""
    owner = owner_env["owner"]
    created = owner.post(f"{API}/projects", json={"name": "example-project"})
    assert created.status_code in (200, 201), created.text

    rows = load_existing_entities(owner_env["server"].vault.db)
    kinds = {entity_type for entity_type, _id, _name in rows}
    assert "project" in kinds
    assert ("project", created.json()["id"], "example-project") in rows
    for entity_type, entity_id, _name in rows:
        if entity_type == "tag":
            assert entity_id is None, "a tag is a string, not a row"
        else:
            assert entity_id is not None


# ===========================================================================
# Signals: the branches the first pass left unpinned
# ===========================================================================


def test_a_level_of_non_latin_names_is_not_read_as_one_repeated_name():
    """An ASCII-only fold makes every Cyrillic name the *same* empty string, at
    which point fifteen different people read as one label and the level is
    confidently proposed as a Tag."""
    spec = {"": []}
    people = ["Анна", "Ирина", "Мария", "Ольга", "日本", "Пётр", "Ελένη", "김민준"]
    for index, person in enumerate(people):
        parent = f"p{index % 3}"
        spec.setdefault(parent, [])
        spec[f"{parent}/{person}"] = ["a.jpg"]
    with _tree(spec) as root:
        result = FolderStructureRead(root).run()

    proposal = _level(result, 3)["proposal"]
    assert proposal["kind"] != "tag", (
        f"eight distinct names must not read as a tag level: {proposal}"
    )
    assert proposal["evidence"][0]["names"] == len(people), (
        f"each name must count as its own: {proposal['evidence']}"
    )
    assert normalise_name("Анна") != normalise_name("Мария")


def test_a_non_latin_entity_name_still_matches_its_folder():
    with _tree({"": [], "Ольга": []}) as root:
        result = FolderStructureRead(
            root, existing_entities=[("character", 12, "Ольга")]
        ).run()
    assert _rows(result, 2)["Ольга"]["proposal"]["match"]["id"] == 12


def test_accents_fold_so_jose_matches_jose():
    assert normalise_name("José") == normalise_name("Jose")
    with _tree({"": [], "Jose": []}) as root:
        result = FolderStructureRead(
            root, existing_entities=[("character", 5, "José")]
        ).run()
    assert _rows(result, 2)["Jose"]["proposal"]["kind"] == "person"


def test_two_entities_of_one_kind_sharing_a_name_hand_back_no_id():
    """`PictureSet.name` is not unique. §20 promises `id` is a real primary key,
    so an ambiguous name must not be answered with whichever row came first."""
    with _tree({"": [], "reference pictures": []}) as root:
        result = FolderStructureRead(
            root,
            existing_entities=[
                ("set", 1, "reference pictures"),
                ("set", 2, "reference_pictures"),
            ],
        ).run()

    proposal = _rows(result, 2)["reference pictures"]["proposal"]
    assert proposal["kind"] == "set", "the kind is still known"
    assert proposal["match"] is None, "which row is not"
    assert "matches 2 existing sets" in proposal["evidence"][0]["text"]


def test_an_unknown_entity_type_is_skipped_and_does_not_kill_the_read():
    with _tree({"": [], "thing": []}) as root:
        result = FolderStructureRead(
            root, existing_entities=[("aardvark", 1, "thing")]
        ).run()
    assert result["folder_count"] == 2
    assert _rows(result, 2)["thing"]["proposal"]["kind"] is None


def test_a_split_level_is_not_decided_by_what_the_folders_are_called():
    """`Counter.most_common` breaks a tie by insertion order, which here is
    folder sort order. The 60% share is what makes a tie unreachable — at 60%
    two kinds would need 120% of the level — so this pins the property rather
    than the arithmetic: a 2-2 split answers the same either way round, and it
    does not answer with a kind."""

    def build(sidecar_names, face_names):
        spec = {"": []}
        for name in sidecar_names:
            spec[name] = ["a.jpg", "a.txt", "b.jpg", "b.txt"]
        for name in face_names:
            spec[name] = [f"{i:03d}.jpg" for i in range(MIN_FACE_SAMPLE + 1)]
        return spec

    answers = []
    for sidecars, faces in ((("aa", "bb"), ("cc", "dd")), (("yy", "zz"), ("aa", "bb"))):
        with _tree(build(sidecars, faces)) as root:
            result = FolderStructureRead(
                root, detect_faces=_detector_from_identity(lambda i: 1)
            ).run()
        answers.append(_level(result, 2)["proposal"])

    for proposal in answers:
        assert proposal["kind"] is None, f"a 2-2 split must not be decided: {proposal}"
    assert answers[0] == answers[1], "the answer must not depend on folder names"


def test_the_level_vote_share_is_sixty_percent_and_not_a_rounded_half():
    """`round(0.6 * 4)` is 2, so a rule written as 60% would pass at 50%."""
    spec = {"": []}
    for name in ("aa", "bb"):  # 2 of 4 read as Set
        spec[name] = ["a.jpg", "a.txt"]
    for name in ("cc", "dd"):  # …and 2 say nothing at all
        spec[name] = ["a.jpg", "b.jpg"]
    with _tree(spec) as root:
        two_of_four = _level(FolderStructureRead(root).run(), 2)["proposal"]
    assert two_of_four["kind"] is None, f"50% is not 60%: {two_of_four}"

    spec["cc"] = ["a.jpg", "a.txt"]  # now 3 of 4
    with _tree(spec) as root:
        three_of_four = _level(FolderStructureRead(root).run(), 2)["proposal"]
    assert three_of_four["kind"] == "set", three_of_four
    assert three_of_four["evidence"][0]["text"] == "3 of 4 folders read as Set"


def test_the_identity_threshold_is_where_it_says_it_is():
    """One-hot fixtures alone make every value in (0, 1] equivalent. These sit
    either side of the constant, so moving it turns one of them red."""
    just_over = [_unit(1)] + [_near(1, SAME_IDENTITY_COSINE + 0.05) for _ in range(3)]
    just_under = [_unit(1)] + [_near(1, SAME_IDENTITY_COSINE - 0.05) for _ in range(3)]
    assert _dominant_identity_count(just_over) == 4
    assert _dominant_identity_count(just_under) == 3, (
        "faces below the threshold are a different identity"
    )


def test_a_sidecar_in_capitals_still_counts():
    """A dataset exported on Windows is the obvious victim of a case-sensitive
    extension match, and it would fail by the Set signal never firing."""
    with _tree({"": [], "shoot": ["a.jpg", "a.TXT", "b.jpg", "b.Txt"]}) as root:
        result = FolderStructureRead(root).run()
    assert _rows(result, 2)["shoot"]["proposal"]["kind"] == "set"


def test_the_result_says_whether_the_face_signal_ran_at_all():
    """Without it, a library with nobody in it and a library read with no engine
    are the same document — in a module whose docstring claims determinism."""
    with _tree(_faces_spec(MIN_FACE_SAMPLE + 1)) as root:
        without = FolderStructureRead(root, detect_faces=None).run()
        with_engine = FolderStructureRead(
            root, detect_faces=_detector_from_identity(lambda i: 1)
        ).run()
    assert without["face_signal_ran"] is False
    assert with_engine["face_signal_ran"] is True


def test_the_read_stops_at_its_deadline_and_returns_what_it_found():
    with _tree({"": [], "a": ["x.jpg"], "b": ["y.jpg"]}) as root:
        read = FolderStructureRead(root, deadline_s=-1.0)
        result = read.run()
    assert read.cancelled is True, "an out-of-time read stops like a cancelled one"
    assert "root" in result


def test_the_default_bound_is_the_documented_one():
    assert MAX_FOLDERS == 20_000, "docs/integration_architecture.md §20 states this"

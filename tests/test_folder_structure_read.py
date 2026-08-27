"""The v1.11 Phase 2 folder-structure read.

Two halves, deliberately split by cost. The signal tests run the service
directly over a temporary folder tree with a stubbed detector - no ``Server``,
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
    DEFAULT_DEADLINE_S,
    FolderStructureRead,
    JUST_A_FOLDER,
    KINDS,
    MAX_FOLDERS,
    MIN_FACE_SAMPLE,
    FACE_MAJORITY_PCT,
    MIN_SIDECAR_PICTURES,
    SAME_IDENTITY_COSINE,
    SAMPLED_PER_FOLDER,
    _ENTITY_KIND,
    _LEVEL_VOTE_SHARE_PCT,
    _clears_share,
    _NON_TAG_KINDS,
    _TAG_MAX_DISTINCT_NAMES,
    _TAG_MIN_PARENTS,
    _TAG_REPEAT_FACTOR,
    _dominant_identity_count,
    _evenly_spaced,
    load_existing_entities,
    normalise_name,
)
from pixlstash.utils.library_layout import Facet, _match_key
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

API = "/api/v1"

# Fail any test in this file that asserts on the SPA catch-all's answer.
#
# `test_the_read_writes_nothing` shipped naming `/api/v1/picture-sets`, which
# matches no route. It passed here - FastAPI's 404 is a JSON body identical
# before and after, so the comparison held while checking nothing - and it failed
# in CI, where `frontend/dist` exists, the catch-all answers with `index.html`,
# and `.json()` raises. A green local run was the bug's cover.
#
# The guard only bites where the catch-all is mounted, so `assert_real_route`
# stays the net on a checkout with no `frontend/dist` built.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

#: A folder the sidecar signal will speak about: every picture captioned, and
#: enough of them to clear MIN_SIDECAR_PICTURES.
_CAPTIONED = ["a.jpg", "a.txt", "b.jpg", "b.txt", "c.jpg", "c.txt"]


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
    the 0.0/1.0 extremes a one-hot fixture can express - the threshold is what
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


def test_the_count_is_what_the_import_will_actually_index():
    """Videos count. They are imported, and the total is a promise about that.

    The read may only *sample* images - a video frame is not what the face pass
    is built on - but the commit indexes every supported media file, so a
    holiday folder of clips used to finish with more pictures in the library
    than the dialog had said were there.
    """
    with _tree({"": [], "Trip": ["a.jpg", "b.jpg", "clip.mp4"]}) as root:
        result = FolderStructureRead(root).run()

    assert result["root"]["picture_count"] == 3, "the video is going to be imported"
    assert _rows(result, 2)["Trip"]["direct_picture_count"] == 3


def test_our_own_thumbnails_are_not_counted_or_sampled():
    """Re-reading an already-indexed folder used to grow its own total.

    Managed thumbnails sit beside the original as `<name>_thumb.webp`, and
    `.webp` is a supported extension, so every import made the next read of the
    same folder report a larger library than the one before it.
    """
    with _tree(
        {"": [], "Trip": ["a.jpg", "a_thumb.webp", "b.jpg", "b_thumb.webp"]}
    ) as root:
        result = FolderStructureRead(root).run()

    assert result["root"]["picture_count"] == 2, "two pictures and their thumbnails"
    assert _rows(result, 2)["Trip"]["direct_picture_count"] == 2


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
    with _tree(
        {"": [], "shoot": ["a.jpg", "a.txt", "b.png", "b.txt", "c.jpg", "c.jpg.txt"]}
    ) as root:
        result = FolderStructureRead(root).run()

    proposal = _rows(result, 2)["shoot"]["proposal"]
    assert proposal["kind"] == "set"
    (evidence,) = proposal["evidence"]
    assert evidence["signal"] == "sidecars"
    assert evidence["pictures"] == 3 and evidence["with_sidecar"] == 3, (
        "both `a.txt` and `a.jpg.txt` are caption conventions in the wild"
    )
    assert "all 3 pictures" in evidence["text"]


def test_a_caption_beside_most_pictures_says_nothing_at_all():
    """`every` picture, not `most`. A signal that cannot state its reason does
    not propose - and 'a caption beside 2 of 3' is not the Set fact."""
    with _tree(
        {
            "": [],
            "shoot": ["a.jpg", "a.txt", "b.jpg", "b.txt", "c.jpg", "d.jpg", "d.txt"],
        }
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


def test_a_dated_folder_is_never_read_as_a_person():
    """One day of one holiday is mostly one person. It is still a date.

    Reported from a real library: `2009`/`2010` were mapped as Sets and the
    `2006-09-08` level below them came back proposed as People.
    """
    with _tree({"": [], "2006-09-08": [f"{i:03d}.jpg" for i in range(40)]}) as root:
        result = FolderStructureRead(
            root, detect_faces=_detector_from_identity(lambda i: 1)
        ).run()

    proposal = _rows(result, 2)["2006-09-08"]["proposal"]
    assert proposal["kind"] is None, "a date is not a name anybody has"
    # No kind left, so no reason to state - the signal's standing contract.
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
        spec[name] = ["a.jpg", "a.txt", "b.jpg", "b.txt", "c.jpg", "c.txt"]
    with _tree(spec) as root:
        result = FolderStructureRead(root).run()

    proposal = _level(result, 2)["proposal"]
    assert proposal["kind"] == "set"
    assert proposal["evidence"][0]["text"] == "4 of 4 folders read as Set"
    assert proposal["evidence"][0]["signal"] == "level_vote", (
        "a claim about the level must not be labelled with a per-folder signal"
    )


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
        # It carries its marker in the value - the prefix travels with it when
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
    _make_tree(
        root, {"": [], "shoot": ["a.jpg", "a.txt", "b.jpg", "b.txt", "c.jpg", "c.txt"]}
    )

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
    # `picture_sets`, with an underscore. The hyphen spelling matches no route,
    # and naming a dead one here does not fail - it 404s to a JSON body that is
    # identical before and after, so the assertion below passes while checking
    # nothing. In CI the SPA catch-all answers it with index.html instead and
    # `.json()` raises, which is how this was found. Hence the guard: a renamed
    # route must fail loudly rather than dissolve into a vacuous comparison.
    entity_routes = ("projects", "picture_sets", "characters")
    for route in entity_routes:
        assert_real_route(owner_env["server"].api, "GET", f"{API}/{route}")
    counts_before = {}
    for route in entity_routes:
        response = owner.get(f"{API}/{route}")
        assert response.status_code == 200, f"{route}: {response.text}"
        counts_before[route] = response.json()

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
        response = owner.get(f"{API}/{route}")
        assert response.status_code == 200, f"{route}: {response.text}"
        assert response.json() == was, f"the read created a {route} row"


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
        # In-scope still works - over-blocking is its own regression.
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
    folder sort order. The 60% share is what makes a tie unreachable - at 60%
    two kinds would need 120% of the level - so this pins the property rather
    than the arithmetic: a 2-2 split answers the same either way round, and it
    does not answer with a kind."""

    def build(sidecar_names, face_names):
        spec = {"": []}
        for name in sidecar_names:
            spec[name] = _CAPTIONED
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
        spec[name] = _CAPTIONED
    for name in ("cc", "dd"):  # …and 2 say nothing at all
        spec[name] = ["a.jpg", "b.jpg", "c.jpg"]
    with _tree(spec) as root:
        two_of_four = _level(FolderStructureRead(root).run(), 2)["proposal"]
    assert two_of_four["kind"] is None, f"50% is not 60%: {two_of_four}"

    spec["cc"] = _CAPTIONED  # now 3 of 4
    with _tree(spec) as root:
        three_of_four = _level(FolderStructureRead(root).run(), 2)["proposal"]
    assert three_of_four["kind"] == "set", three_of_four
    assert three_of_four["evidence"][0]["text"] == "3 of 4 folders read as Set"


def test_the_identity_threshold_is_where_it_says_it_is():
    """Absolute cosines, not ``SAME_IDENTITY_COSINE ± 0.05``.

    A fixture built from the constant slides with it: the first version of this
    test passed at ``SAME_IDENTITY_COSINE = 0.05`` while its docstring claimed it
    would go red, because both sides of the comparison moved together. These
    numbers are fixed, so moving the constant off 0.35 turns one of them red -
    which is the whole point of pinning the value that decides whether a real
    folder reads as a Person."""
    assert SAME_IDENTITY_COSINE == 0.35, (
        "the vectors below are chosen against 0.35; re-pick them deliberately"
    )
    over = [_unit(1)] + [_near(1, 0.40) for _ in range(3)]
    under = [_unit(1)] + [_near(1, 0.30) for _ in range(3)]
    assert _dominant_identity_count(over) == 4
    assert _dominant_identity_count(under) == 3, (
        "faces below the threshold are a different identity"
    )


def test_the_face_majority_is_where_it_says_it_is():
    """14 of 20 is 70% and reads as one person; 13 of 20 is 65% and does not."""
    assert FACE_MAJORITY_PCT == 70, "the counts below are chosen against 70"
    with _tree(_faces_spec(40)) as root:
        at_the_line = FolderStructureRead(
            root, detect_faces=_detector_from_identity(lambda i: 1 if i < 14 else i + 2)
        ).run()
        below = FolderStructureRead(
            root, detect_faces=_detector_from_identity(lambda i: 1 if i < 13 else i + 2)
        ).run()
    assert _rows(at_the_line, 2)["mira"]["proposal"]["kind"] == "person"
    assert _rows(below, 2)["mira"]["proposal"]["kind"] is None


def test_the_tag_shape_needs_repetition_across_several_parents():
    """All three cardinality constants, each pinned by a tree one step short.

    Without this the heuristic that proposes a whole level as Tag - the one
    signal that speaks for 149 folders at once - has no test that constrains
    when it fires."""
    assert (_TAG_MAX_DISTINCT_NAMES, _TAG_REPEAT_FACTOR, _TAG_MIN_PARENTS) == (
        12,
        3,
        3,
    ), "the trees below are chosen against these"

    def read(parents, leaves):
        spec = {"": []}
        for parent in parents:
            spec[parent] = []
            for leaf in leaves:
                spec[f"{parent}/{leaf}"] = ["a.jpg"]
        with _tree(spec) as root:
            return _level(FolderStructureRead(root).run(), 3)["proposal"]["kind"]

    assert read(("p1", "p2", "p3"), ("final", "raw", "selects")) == "tag"
    assert read(("p1", "p2"), ("final", "raw", "selects")) is None, (
        "two parents is not 'repeating under many parents'"
    )
    # 13 distinct names is past _TAG_MAX_DISTINCT_NAMES, however much they repeat.
    many = tuple(f"n{i}" for i in range(13))
    assert read(("p1", "p2", "p3"), many) is None


def test_a_sidecar_in_capitals_still_counts():
    """A dataset exported on Windows is the obvious victim of a case-sensitive
    extension match, and it would fail by the Set signal never firing."""
    with _tree(
        {"": [], "shoot": ["a.jpg", "a.TXT", "b.jpg", "b.Txt", "c.jpg", "c.Caption"]}
    ) as root:
        result = FolderStructureRead(root).run()
    assert _rows(result, 2)["shoot"]["proposal"]["kind"] == "set"


def test_the_result_says_whether_the_face_signal_ran_at_all():
    """Without it, a library with nobody in it and a library read with no engine
    are the same document - in a module whose docstring claims determinism."""
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
        out_of_time = read.run()
        complete = FolderStructureRead(root).run()
    assert read.cancelled is True, "an out-of-time read stops like a cancelled one"
    # Not `"root" in result` - _build_result emits that key unconditionally, so
    # that assertion could not fail for any implementation of anything.
    assert out_of_time["folder_count"] == 0, "it stopped before the first folder"
    assert complete["folder_count"] == 3, "and the same tree reads fine untimed"


def test_the_default_deadline_is_the_documented_one():
    """Nothing else exercises it: the test above passes its own deadline in, so a
    typo turning 30 minutes into 30 seconds would ship green."""
    assert DEFAULT_DEADLINE_S == 30 * 60.0


def test_the_level_vote_share_is_the_documented_one():
    """Pins the number the tie-unreachability argument rests on. At 50 a 2-2
    split is a tie broken by folder sort order, which is the defect the exact
    integer comparison exists to prevent."""
    assert _LEVEL_VOTE_SHARE_PCT == 60


def test_a_lone_captioned_picture_is_not_a_set():
    """`MIN_SIDECAR_PICTURES`, for the reason `MIN_FACE_SAMPLE` exists: "a caption
    file beside all 1 picture" is not evidence anyone should act on, and a level
    of such folders would clear the 60% vote and be proposed as Set entire."""
    assert MIN_SIDECAR_PICTURES == 3
    with _tree({"": [], "pair": ["a.jpg", "a.txt", "b.jpg", "b.txt"]}) as root:
        result = FolderStructureRead(root).run()
    assert _rows(result, 2)["pair"]["proposal"]["kind"] is None


def test_the_default_bound_is_the_documented_one():
    assert MAX_FOLDERS == 20_000, "docs/integration_architecture.md §20 states this"


def test_the_blocklist_is_re_run_below_the_root_not_only_on_it(monkeypatch):
    """Validating the path the caller named is a check on ONE STRING.

    `/` names no restricted directory and contains every one of them, so a
    root-only check walks `/etc`, `/proc` and `/root` and decodes any
    image-extensioned file it finds there. Measured before this guard: 391 of
    400 folders came from blocklisted subtrees."""
    monkeypatch.setattr("pixlstash.services.folder_structure_service.MAX_FOLDERS", 400)
    result = FolderStructureRead("/").run()

    restricted = [
        row["relative_path"]
        for level in result["levels"]
        for row in level["folders"]
        if validate_reference_folder_path("/" + row["relative_path"])
    ]
    assert restricted == [], (
        f"the walk entered restricted directories below the root: {restricted[:8]}"
    )
    assert result["skipped_folders"]["restricted"] > 0, (
        "…and it must say it refused them rather than skipping in silence"
    )


def test_a_cancelled_read_still_counts_the_pictures_it_found():
    """The counts are summed in _build_result, not at the end of the walk.

    A cancel raises from inside the walk loop, so summing there left every row
    at picture_count 0 beside a real direct_picture_count - a partial map saying
    the library is empty, on the one path whose justification is that the
    partial map is showable."""
    box = {}

    def stop_after_two(stage, processed, total):
        if stage == "walking" and processed >= 2:
            box["read"].cancel()

    with _tree({"": [], "a": ["x.jpg", "y.jpg"], "b": ["z.jpg"]}) as root:
        read = FolderStructureRead(root, progress=stop_after_two)
        box["read"] = read
        result = read.run()

    assert read.cancelled is True
    rows = _rows(result, 2)
    assert rows["a"]["direct_picture_count"] == 2
    assert rows["a"]["picture_count"] == 2, (
        "a row reporting direct pictures and a recursive count of 0 is incoherent"
    )
    assert result["root"]["picture_count"] == 2, "and the root sums what was walked"


def test_hidden_folders_are_counted_rather_than_dropped_in_silence():
    with _tree({"": [], "shown": ["a.jpg"], ".cache": ["b.jpg"]}) as root:
        result = FolderStructureRead(root).run()
    assert sorted(_rows(result, 2)) == ["shown"]
    assert result["skipped_folders"]["hidden"] == 1


def test_the_read_speaks_the_layout_s_facet_vocabulary():
    """Phase 2 proposes what a level is; Phase 4's layout is built out of the
    same words. If they drift, the mapping screen offers a `kind` the layout
    cannot place, and nothing else would notice until a picture failed to move.

    `folder` is the one extra and is deliberately not a Facet: "just a folder"
    is the *absence* of a facet, and no signal ever proposes it."""
    assert set(KINDS) == {f.value for f in Facet} | {JUST_A_FOLDER}
    assert JUST_A_FOLDER not in {f.value for f in Facet}
    assert set(_ENTITY_KIND.values()) <= set(KINDS)
    assert set(_NON_TAG_KINDS) == {f.value for f in Facet} - {Facet.TAG.value}


def test_no_signal_ever_proposes_just_a_folder():
    """Both documents say so, and it is the one `kind` the backend cannot
    justify: no signal can prove a string means nothing."""
    spec = {"": []}
    for name in ("alpha", "beta", "gamma"):
        spec[name] = _CAPTIONED
    spec["delta"] = ["a.jpg", "b.jpg", "c.jpg"]
    with _tree(spec) as root:
        result = FolderStructureRead(
            root,
            detect_faces=_detector_from_identity(lambda i: 1),
            existing_entities=[("project", 1, "alpha"), ("tag", None, "beta")],
        ).run()

    proposed = [
        row["proposal"]["kind"]
        for level in result["levels"]
        for row in level["folders"]
    ] + [level["proposal"]["kind"] for level in result["levels"]]
    assert JUST_A_FOLDER not in proposed, proposed


def test_the_two_name_normalisers_disagree_on_purpose():
    """`normalise_name` proposes; `library_layout._match_key` decides whether a
    picture moves. Folding accents is right for the first and wrong for the
    second, so this pins the difference rather than leaving it looking like a
    duplicated helper somebody should reconcile."""
    assert normalise_name("José") == normalise_name("Jose")
    assert _match_key("José") != _match_key("Jose"), (
        "the layout must keep them apart - it decides moves, not proposals"
    )
    # And the separator half, which is the other direction of the same split.
    assert normalise_name("2024_Shoots") == normalise_name("2024 Shoots")
    assert _match_key("2024_Shoots") != _match_key("2024 Shoots")


def test_a_share_threshold_is_a_floor_and_never_rounds_down_to_below_itself():
    """Review comment on #1110, and the same defect the level vote had.

    `round(0.7 * 6) == 4`, so a float rule spelled `round(share * whole)` lets
    4 of 6 - 66.7% - clear a seventy-percent threshold. `round(0.6 * 4) == 2`
    lets a 50% plurality clear a sixty-percent one. Both go through
    `_clears_share` now, so this pins the property for whichever threshold is
    written next."""
    assert _clears_share(4, 6, 70) is False, "4 of 6 is 66.7%, not 70%"
    assert _clears_share(5, 6, 70) is True
    assert _clears_share(2, 4, 60) is False, "2 of 4 is 50%, not 60%"
    assert _clears_share(3, 4, 60) is True
    # Exactly on the line clears it; a floor includes its own value.
    assert _clears_share(14, 20, 70) is True
    assert _clears_share(13, 20, 70) is False
    assert _clears_share(0, 0, 70) is False, "nothing sampled is not a majority"


def test_a_six_picture_folder_needs_five_faces_not_four():
    """The rounding bug end to end, on the smallest sample that exhibits it."""
    spec = {"": [], "mira": [f"{i:03d}.jpg" for i in range(6)]}
    with _tree(spec) as root:
        four = FolderStructureRead(
            root,
            detect_faces=_detector_from_identity(lambda i: 1 if i < 4 else i + 2),
        ).run()
        five = FolderStructureRead(
            root,
            detect_faces=_detector_from_identity(lambda i: 1 if i < 5 else i + 2),
        ).run()
    assert _rows(four, 2)["mira"]["proposal"]["kind"] is None, (
        "4 of 6 is 66.7% and must not clear a 70% rule"
    )
    assert _rows(five, 2)["mira"]["proposal"]["kind"] == "person"


def test_a_read_rooted_at_a_filesystem_root_still_has_a_name(monkeypatch):
    """Review comment on #1110: `os.path.basename("/")` is the empty string, so
    the root row reached the mapping screen with no name to refer to it by."""
    monkeypatch.setattr("pixlstash.services.folder_structure_service.MAX_FOLDERS", 3)
    result = FolderStructureRead("/").run()
    assert result["root"]["name"] == "/"
    assert _level(result, 1)["folders"][0]["name"] == "/"

    # …and an ordinary folder is unaffected, trailing separator or not.
    with _tree({"": [], "a": []}) as root:
        assert FolderStructureRead(root + os.sep).run()["root"]["name"] == (
            "Generations"
        )


def test_the_librarys_own_snapshots_tree_is_never_offered_for_mapping():
    """A snapshot can land at any time, so excluding beats creating it later."""
    with _tree(
        {
            "": [],
            "Holiday": ["a.jpg"],
            "snapshots": [],
            "snapshots/2026/08/26": [],
        }
    ) as root:
        result = FolderStructureRead(
            root, exclude={os.path.join(root, "snapshots")}
        ).run()

    names = {row["name"] for level in result["levels"] for row in level["folders"]}
    assert "Holiday" in names
    assert "snapshots" not in names
    assert "26" not in names, "the whole subtree, not just its top"

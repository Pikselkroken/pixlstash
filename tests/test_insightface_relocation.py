"""Relocating the InsightFace packs: the setting, the move, and what refuses it.

The packs were listed on the shelf as ``movable='root_only'`` — "relocates as a
whole" — before anything could actually relocate them (#902, then #906). Two
things had to become true for that to stop being a claim:

1. **The root is a setting, and one reader serves every caller.** ``FaceAnalysis``
   loads from ``<root>/models/<pack>``, PixlStash downloads ``auraface`` into the
   same place, and the shelf declares that folder. All three go through
   ``insightface_model_utils.insightface_root()``, so a relocation cannot move
   the shelf's row while the packs keep loading from the old directory — which is
   exactly the failure ``builtin_model_dir()``'s docstring rules out for #905's
   folder, where the path is still computed three times independently.
2. **A pack is a directory**, so the relocation does not go through
   ``ModelMover``: there is no per-file row to repoint and no ``sha256`` to
   verify a copy against. ``move_directory`` keeps the guarantee that matters
   instead — a *complete* pack survives every interruption, at one end or the
   other, because the copy lands under ``.pixlstash-partial`` and is renamed into
   place. A half-populated ``buffalo_l/`` would be a face pipeline that starts
   and then fails on a missing model.

The negative half is as load-bearing as the positive one. Widening the relocate
route from "the managed store only" to "the managed store and the InsightFace
packs" is the kind of change that quietly opens it to everything, so the folders
that must still be refused are asserted here beside the one that must now work —
the HuggingFace cache in particular, which is ``fixed`` because its location is
``HF_HOME``, read at import by a library shared with every other tool on the
machine.

Environment: a real ``Server`` per test, because the route persists to
``server-config.json`` and re-points a process-global that the next test must not
inherit. The InsightFace root is pointed at ``tmp_path`` through that same
setting, which is also the cheapest available proof that the setting works.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from pixlstash.routes import model_moves
from pixlstash.server import Server
from pixlstash.services import builtin_caches
from pixlstash.services.model_mover import PARTIAL_SUFFIX, move_directory
from pixlstash.utils import insightface_model_utils as model_utils

API = "/api/v1"

# Enough to make `declare_insightface_packs` call the directory a pack and
# `_directory_size` report a size for it. Nothing here loads it.
_PACK_FILES = ("det_10g.onnx", "w600k_r50.onnx")


class Crash(BaseException):
    """A process death, as far as the code under test can tell.

    ``BaseException`` rather than ``Exception``: ``move_directory``'s cleanup is
    an unconditional ``except BaseException`` re-raise on purpose, and a plain
    ``Exception`` would let an ``except OSError`` somewhere turn the simulated
    crash into a tidy error — which is not the state a real crash leaves.
    """


def _write_pack(models_dir, name: str) -> None:
    """Put one pack's directory, with files in it, on disk."""
    pack = os.path.join(str(models_dir), name)
    os.makedirs(pack, exist_ok=True)
    for filename in _PACK_FILES:
        with open(os.path.join(pack, filename), "wb") as handle:
            handle.write(f"{name}/{filename}".encode() * 64)


@pytest.fixture
def face_env(tmp_path):
    """A server whose InsightFace root is a temp directory holding one pack.

    Function-scoped, unlike the shared shelf server: a relocation writes the
    config file and re-points ``insightface_root()`` for the whole process, so a
    module-scoped server would hand the next test a root the test did not choose.
    """
    original_root = model_utils.insightface_root()
    insightface_root = tmp_path / "home" / ".insightface"
    models_dir = insightface_root / "models"
    models_dir.mkdir(parents=True)
    _write_pack(models_dir, "buffalo_l")

    model_moves._job = None
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    with open(config_path, "w") as handle:
        json.dump(
            {"port": 8000, "insightface_root": str(insightface_root)},
            handle,
        )
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        r = owner.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        )
        assert r.status_code == 200, r.text
        yield _FaceEnv(
            server=server,
            owner=owner,
            config_path=config_path,
            root=insightface_root,
            models_dir=models_dir,
            target=tmp_path / "big-drive" / ".insightface",
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()
        model_moves._job = None
        model_utils.set_insightface_root(original_root)


class _FaceEnv:
    """What a relocation test needs to name: the server, and both roots."""

    def __init__(self, *, server, owner, config_path, root, models_dir, target):
        self.server = server
        self.owner = owner
        self.config_path = config_path
        self.root = root
        self.models_dir = models_dir
        self.target = target

    @property
    def folder_id(self) -> int:
        """The declared InsightFace folder, which the shelf writes at start-up."""
        row = self.server.hub.fetchone(
            "SELECT id FROM model_folder WHERE path = ?",
            (str(builtin_caches.insightface_models_dir()),),
        )
        assert row is not None, "the InsightFace folder was never declared"
        return int(row["id"])

    def folder_path(self, folder_id: int) -> str:
        row = self.server.hub.fetchone(
            "SELECT path FROM model_folder WHERE id = ?", (folder_id,)
        )
        return None if row is None else row["path"]

    def register_folder(self, path: str, kind: str, movable: str) -> int:
        with self.server.hub.transaction() as conn:
            return int(
                conn.execute(
                    "INSERT INTO model_folder (path, kind, movable, created_at) "
                    "VALUES (?, ?, ?, '2026-08-13T00:00:00Z')",
                    (path, kind, movable),
                ).lastrowid
            )


def _await_move(env, timeout=20.0):
    """Poll the status route until the job stops running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = env.owner.get(f"{API}/model-moves").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("the relocation never finished")


# --------------------------------------------------------------------------- #
# The setting: one reader, three callers
# --------------------------------------------------------------------------- #


def test_one_setting_moves_the_download_dir_and_the_shelf_folder_together(tmp_path):
    """The property the relocation rests on, asserted without a server.

    If these two could disagree the shelf would list the packs on the new drive
    while `ensure_model_pack_available` re-downloaded them to the old one.
    """
    original = model_utils.insightface_root()
    relocated = str(tmp_path / "elsewhere")
    try:
        model_utils.set_insightface_root(relocated)
        assert model_utils.insightface_root() == relocated
        assert model_utils._pack_dir("auraface") == os.path.join(
            relocated, "models", "auraface"
        )
        assert builtin_caches.insightface_models_dir() == os.path.join(
            relocated, "models"
        )

        # Cleared, not just overwritten: an empty setting is "wherever
        # InsightFace itself would look", not an empty path.
        model_utils.set_insightface_root("")
        assert model_utils.insightface_root() == model_utils.DEFAULT_INSIGHTFACE_ROOT
    finally:
        model_utils.set_insightface_root(original)


# --------------------------------------------------------------------------- #
# move_directory: a complete pack survives every interruption
# --------------------------------------------------------------------------- #


def test_move_directory_moves_the_tree_and_removes_the_source(tmp_path):
    source = tmp_path / "from" / "buffalo_l"
    destination = tmp_path / "to" / "buffalo_l"
    _write_pack(tmp_path / "from", "buffalo_l")
    destination.parent.mkdir(parents=True)

    move_directory(str(source), str(destination))

    assert sorted(p.name for p in destination.iterdir()) == sorted(_PACK_FILES)
    assert not source.exists()


def test_a_crash_mid_copy_never_leaves_a_pack_under_its_real_name(
    tmp_path, monkeypatch
):
    """The reason the copy lands under a partial name and is renamed into place.

    A half-populated ``buffalo_l/`` is worse than no ``buffalo_l/`` at all: the
    face pipeline would start, find the directory, and fail on a model that is
    not in it. The source is untouched, so the packs still load from where they
    were and re-running the relocation is the repair.
    """
    _write_pack(tmp_path / "from", "buffalo_l")
    source = tmp_path / "from" / "buffalo_l"
    destination = tmp_path / "to" / "buffalo_l"
    destination.parent.mkdir(parents=True)
    # Force the copy path even though both directories are on one filesystem,
    # which is every machine this suite runs on.
    monkeypatch.setattr(
        "pixlstash.services.model_mover.same_device", lambda *args: False
    )

    def _die(src, dst, **kwargs):
        os.makedirs(dst)
        with open(os.path.join(dst, _PACK_FILES[0]), "wb") as handle:
            handle.write(b"half")
        raise Crash("the process died mid-copy")

    monkeypatch.setattr("pixlstash.services.model_mover.shutil.copytree", _die)

    with pytest.raises(Crash):
        move_directory(str(source), str(destination))

    assert not destination.exists(), "a partial pack must never take the real name"
    assert not (tmp_path / "to" / ("buffalo_l" + PARTIAL_SUFFIX)).exists()
    assert sorted(p.name for p in source.iterdir()) == sorted(_PACK_FILES)


# --------------------------------------------------------------------------- #
# The route: what it now accepts
# --------------------------------------------------------------------------- #


def test_relocating_the_packs_moves_them_and_repoints_every_caller(face_env):
    folder_id = face_env.folder_id
    r = face_env.owner.post(
        f"{API}/model-folders/{folder_id}/relocate",
        json={"path": str(face_env.target)},
    )
    assert r.status_code == 202, r.text
    body = _await_move(face_env)
    assert [item["status"] for item in body["results"]] == ["moved"], body

    # The path names the ROOT; the packs land in the `models` subdirectory,
    # because that name is InsightFace's own layout and not ours to choose.
    moved = face_env.target / "models" / "buffalo_l"
    assert sorted(p.name for p in moved.iterdir()) == sorted(_PACK_FILES)
    assert not face_env.models_dir.exists(), "the vacated directory was tidied"

    # The setting, persisted and applied — no restart.
    with open(face_env.config_path) as handle:
        assert json.load(handle)["insightface_root"] == str(face_env.target)
    assert model_utils.insightface_root() == str(face_env.target)
    assert model_utils._pack_dir("buffalo_l") == str(moved)
    assert builtin_caches.insightface_models_dir() == str(face_env.target / "models")

    # The shelf follows, keeping its rows: the folder moved, it was not replaced.
    assert face_env.folder_path(folder_id) == str(face_env.target / "models")
    rows = face_env.server.hub.fetchall(
        "SELECT relpath, state FROM model_file WHERE model_folder_id = ? "
        "ORDER BY relpath",
        (folder_id,),
    )
    assert {row["relpath"]: row["state"] for row in rows} == {
        # Declared and absent is a normal state for a pack nobody has fetched;
        # it is not moved and it is not dropped.
        "auraface": "missing",
        "buffalo_l": "present",
    }


def test_the_relocated_root_is_what_the_shelf_reports(face_env):
    """The folder list is the surface the Move control reads back."""
    folder_id = face_env.folder_id
    listed = [
        folder
        for folder in face_env.owner.get(f"{API}/model-folders").json()["folders"]
        if folder["id"] == folder_id
    ]
    assert listed and listed[0]["movable"] == "root_only", (
        "the shelf must go on saying this folder relocates as a whole"
    )

    r = face_env.owner.post(
        f"{API}/model-folders/{folder_id}/relocate",
        json={"path": str(face_env.target)},
    )
    assert r.status_code == 202, r.text
    _await_move(face_env)

    listed = [
        folder
        for folder in face_env.owner.get(f"{API}/model-folders").json()["folders"]
        if folder["id"] == folder_id
    ]
    assert listed[0]["path"] == str(face_env.target / "models")
    assert listed[0]["movable"] == "root_only"


# --------------------------------------------------------------------------- #
# The route: what it still refuses
# --------------------------------------------------------------------------- #


def test_a_relocation_onto_the_current_root_is_refused(face_env):
    r = face_env.owner.post(
        f"{API}/model-folders/{face_env.folder_id}/relocate",
        json={"path": str(face_env.root)},
    )
    assert r.status_code == 400, r.text
    assert "already there" in r.json()["detail"]


def test_a_destination_already_holding_a_pack_is_refused_before_anything_moves(
    face_env,
):
    """Never overwritten, and refused in the POST rather than mid-job."""
    _write_pack(face_env.target / "models", "buffalo_l")
    (face_env.target / "models" / "buffalo_l" / "theirs.txt").write_text("not ours")

    r = face_env.owner.post(
        f"{API}/model-folders/{face_env.folder_id}/relocate",
        json={"path": str(face_env.target)},
    )
    assert r.status_code == 409, r.text
    assert (face_env.models_dir / "buffalo_l").is_dir(), "nothing was moved"
    assert (face_env.target / "models" / "buffalo_l" / "theirs.txt").exists()
    assert model_utils.insightface_root() == str(face_env.root)


def test_the_huggingface_cache_still_cannot_be_relocated(face_env, tmp_path):
    """``fixed`` means it cannot move at all — its location is ``HF_HOME``.

    Asserted against the widened route on purpose: "only the managed store" was
    what refused this before, and that sentence is no longer the rule.
    """
    cache_id = face_env.register_folder(
        str(tmp_path / "hf-cache"), kind="foreign", movable="fixed"
    )
    r = face_env.owner.post(
        f"{API}/model-folders/{cache_id}/relocate",
        json={"path": str(tmp_path / "somewhere-else")},
    )
    assert r.status_code == 409, r.text
    assert face_env.folder_path(cache_id) == str(tmp_path / "hf-cache")


def test_a_folder_the_owner_registered_still_cannot_be_relocated(face_env, tmp_path):
    user_dir = tmp_path / "my-loras"
    user_dir.mkdir()
    user_id = face_env.register_folder(str(user_dir), kind="user", movable="per_item")
    r = face_env.owner.post(
        f"{API}/model-folders/{user_id}/relocate",
        json={"path": str(tmp_path / "somewhere-else")},
    )
    assert r.status_code == 409, r.text
    assert "register it again" in r.json()["detail"]


def test_an_unknown_folder_is_still_a_404(face_env, tmp_path):
    r = face_env.owner.post(
        f"{API}/model-folders/999999/relocate",
        json={"path": str(tmp_path / "somewhere-else")},
    )
    assert r.status_code == 404, r.text

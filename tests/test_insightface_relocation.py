"""Relocating the InsightFace packs: the setting, the move, and what refuses it.

The packs were listed on the shelf as ``movable='root_only'`` - "relocates as a
whole" - before anything could actually relocate them (#902, then #906). Two
things had to become true for that to stop being a claim:

1. **The root is a setting, and one reader serves every caller.** ``FaceAnalysis``
   loads from ``<root>/models/<pack>``, PixlStash downloads ``auraface`` into the
   same place, and the shelf declares that folder. All three go through
   ``insightface_model_utils.insightface_root()``, so a relocation cannot move
   the shelf's row while the packs keep loading from the old directory - which is
   exactly the failure ``builtin_model_dir()``'s docstring rules out for #905's
   folder, where the path is still computed three times independently.
2. **A pack is a directory**, so the relocation does not go through
   ``ModelMover``: there is no per-file row to repoint and no ``sha256`` to
   verify a copy against. ``move_directory`` keeps the guarantee that matters
   instead - a *complete* pack survives every interruption, at one end or the
   other, because the copy lands under ``.pixlstash-partial`` and is renamed into
   place. A half-populated ``buffalo_l/`` would be a face pipeline that starts
   and then fails on a missing model.

The negative half is as load-bearing as the positive one. Widening the relocate
route from "the managed store only" to "the managed store and the InsightFace
packs" is the kind of change that quietly opens it to everything, so the folders
that must still be refused are asserted here beside the one that must now work -
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
    crash into a tidy error - which is not the state a real crash leaves.
    """


def _write_pack(models_dir, name: str) -> None:
    """Put one pack's directory, with files in it, on disk."""
    pack = os.path.join(str(models_dir), name)
    os.makedirs(pack, exist_ok=True)
    for filename in _PACK_FILES:
        with open(os.path.join(pack, filename), "wb") as handle:
            handle.write(f"{name}/{filename}".encode() * 64)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Stand in for the platform user data directory, where the pointer lives.

    The same seam ``test_builtin_models.py`` uses, and the same one call:
    ``insightface_model_utils`` resolves its pointer through
    ``builtin_models._pixlstash_data_dir`` rather than rebuilding
    ``user_data_dir("pixlstash")``, so redirecting it here redirects both
    recorded locations at once - which is the property that keeps a test from
    writing into the developer's real home.
    """
    from pixlstash.services import builtin_models

    root = tmp_path / "userdata"
    root.mkdir()
    monkeypatch.setattr(builtin_models, "_pixlstash_data_dir", lambda: str(root))
    return root


@pytest.fixture
def face_env(tmp_path, data_dir, monkeypatch):
    """A server whose InsightFace root is a temp directory holding one pack.

    Function-scoped, unlike the shared shelf server: a relocation writes a
    pointer file that every later call to ``insightface_root()`` reads, so a
    module-scoped server would hand the next test a root it did not choose.

    ``DEFAULT_DECLARE_MODEL_ROOTS`` is back **on** for these tests. The suite
    turns it off (``conftest``) so a Server on a temp config dir cannot describe
    the developer's real home - here the root is a ``tmp_path`` and the pointer
    is redirected with it, so the declaration is both safe and the thing under
    test: without it there is no InsightFace folder row to relocate.
    """
    insightface_root = tmp_path / "home" / ".insightface"
    models_dir = insightface_root / "models"
    models_dir.mkdir(parents=True)
    _write_pack(models_dir, "buffalo_l")
    model_utils.set_insightface_root(str(insightface_root))

    model_moves._job = None
    monkeypatch.setattr(Server, "DEFAULT_DECLARE_MODEL_ROOTS", True)
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    with open(config_path, "w") as handle:
        json.dump({"port": 8000}, handle)
    server = Server(config_path)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        r = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text
        yield _FaceEnv(
            server=server,
            owner=owner,
            pointer=data_dir / model_utils.INSIGHTFACE_ROOT_POINTER,
            root=insightface_root,
            models_dir=models_dir,
            target=tmp_path / "big-drive" / ".insightface",
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()
        # `_job` is deliberately NOT cleared here. Set-up already nulls it, and
        # an autouse fixture tears down after this one, so clearing it here
        # would hide a move this module left running from
        # `no_model_move_outlives_its_test` - in the one module whose route
        # records the InsightFace root.


class _FaceEnv:
    """What a relocation test needs to name: the server, and both roots."""

    def __init__(self, *, server, owner, pointer, root, models_dir, target):
        self.server = server
        self.owner = owner
        self.pointer = pointer
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


def test_the_root_defaults_to_where_insightface_itself_looks(data_dir):
    """A machine that never relocated the packs must not see them move."""
    assert model_utils.insightface_root() == model_utils.DEFAULT_INSIGHTFACE_ROOT


def test_a_recorded_root_survives_the_process_that_wrote_it(data_dir):
    """The relocation is only real if the next start agrees with it - otherwise
    every pack is downloaded again into the directory just emptied.

    A file beside the download folder's own pointer rather than a key in
    `server-config.json`, for the reason #905 gives: this path is machine-global,
    so recording it per deployment would leave a second PixlStash on the same
    machine downloading packs to the old place.
    """
    model_utils.set_insightface_root("/mnt/big/.insightface")
    assert (data_dir / model_utils.INSIGHTFACE_ROOT_POINTER).read_text() == (
        "/mnt/big/.insightface"
    )
    assert model_utils.insightface_root() == "/mnt/big/.insightface"


def test_an_unreadable_record_names_the_default_rather_than_nothing(
    data_dir, monkeypatch, caplog
):
    """Refusing to name a root at all would disable face detection entirely over
    one bad file, so the failure is loud and the default answers."""
    model_utils.set_insightface_root("/mnt/big/.insightface")

    def _boom(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("builtins.open", _boom)
    with caplog.at_level("ERROR"):
        assert model_utils.insightface_root() == model_utils.DEFAULT_INSIGHTFACE_ROOT
    assert "permission denied" in caplog.text


def test_one_recorded_root_moves_the_download_dir_and_the_shelf_folder_together(
    data_dir,
):
    """The property the relocation rests on, asserted without a server.

    If these two could disagree the shelf would list the packs on the new drive
    while `ensure_model_pack_available` re-downloaded them to the old one.
    """
    relocated = str(data_dir / "elsewhere")
    model_utils.set_insightface_root(relocated)
    assert model_utils._pack_dir("auraface") == os.path.join(
        relocated, "models", "auraface"
    )
    assert builtin_caches.insightface_models_dir() == os.path.join(relocated, "models")


def test_nothing_but_the_record_can_name_the_models_directory(data_dir, monkeypatch):
    """No second source for this one path, environment included.

    `builtin_caches` used to carry `PIXLSTASH_INSIGHTFACE_DIR`, a declaration-only
    seam that pointed at the *models* directory - one level below the root that is
    now recorded, so the two could disagree. Harmless while nothing could
    relocate; a bug the moment something could, because the shelf would declare
    the override path while downloads and `FaceAnalysis` used the root, and a
    relocation identified by `insightface_models_dir()` would repoint the row at
    a directory the next start-up would not declare. The download folder's
    override is safe for the opposite reason: it redirects that folder whole.

    Asserted by setting every plausible spelling and requiring the answer not to
    move: a reader that consults the environment fails this, whatever it is
    called.
    """
    relocated = str(data_dir / "recorded")
    for name in (
        "PIXLSTASH_INSIGHTFACE_DIR",
        "PIXLSTASH_INSIGHTFACE_ROOT",
        "INSIGHTFACE_ROOT",
    ):
        monkeypatch.setenv(name, str(data_dir / "from-the-environment"))
    model_utils.set_insightface_root(relocated)
    assert builtin_caches.insightface_models_dir() == os.path.join(relocated, "models")
    assert not hasattr(builtin_caches, "INSIGHTFACE_DIR_ENV"), (
        "the seam is gone; the recorded root reaches all three callers rather "
        "than only the declaration"
    )


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
    r = face_env.owner.post(
        f"{API}/model-folders/{face_env.folder_id}/relocate",
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

    # The root, recorded and in force - no restart.
    assert face_env.pointer.read_text() == str(face_env.target)
    assert model_utils.insightface_root() == str(face_env.target)
    assert model_utils._pack_dir("buffalo_l") == str(moved)
    assert builtin_caches.insightface_models_dir() == str(face_env.target / "models")

    # The shelf follows. `folder_id` re-resolves through the accessor, so this
    # is the row the *relocated* folder now has - the shared ending registers
    # the destination and retires the old row, as it does for the other two.
    relocated_id = face_env.folder_id
    assert face_env.folder_path(relocated_id) == str(face_env.target / "models")
    rows = face_env.server.hub.fetchall(
        "SELECT relpath, state FROM model_file WHERE model_folder_id = ? "
        "ORDER BY relpath",
        (relocated_id,),
    )
    assert {row["relpath"]: row["state"] for row in rows} == {
        # Declared and absent is a normal state for a pack nobody has fetched,
        # which is why it is `not_downloaded` and not `missing` - the shelf draws
        # `missing` as a fault (#926). It carries across rather than being
        # dropped with the old row: it is a tombstone, and the packs moving is
        # not news about whether it came back.
        "auraface": "not_downloaded",
        "buffalo_l": "present",
    }
    assert (
        face_env.server.hub.fetchone(
            "SELECT id FROM model_folder WHERE path = ?", (str(face_env.models_dir),)
        )
        is None
    ), "the old row was retired rather than left beside the new one"


def test_a_record_that_cannot_be_written_still_tells_the_hub_the_truth(
    face_env, monkeypatch, caplog
):
    """What a completed move leaves when only the *memory* of it fails.

    The packs are at the new root and a relocation cannot be undone (the cancel
    ruling), so the hub is still told where they are - the shelf shows them
    correctly. What is lost is durability: the next start resolves the default
    root again and would download there, which is why the failure is loud and
    names the repair. Exactly the residue #905 accepts for the download folder's
    own pointer; giving the two different answers would be the worse outcome.
    """
    folder_id = face_env.folder_id

    def _explode(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(model_moves, "set_insightface_root", _explode)

    with caplog.at_level("ERROR"):
        r = face_env.owner.post(
            f"{API}/model-folders/{folder_id}/relocate",
            json={"path": str(face_env.target)},
        )
        assert r.status_code == 202, r.text
        body = _await_move(face_env)

    assert [item["status"] for item in body["results"]] == ["moved"], body
    moved = face_env.target / "models" / "buffalo_l"
    assert sorted(p.name for p in moved.iterdir()) == sorted(_PACK_FILES)

    # The hub still names where the packs really are.
    row = face_env.server.hub.fetchone(
        "SELECT path FROM model_folder WHERE id = ?", (folder_id,)
    )
    moved_row = face_env.server.hub.fetchone(
        "SELECT id FROM model_folder WHERE path = ?",
        (str(face_env.target / "models"),),
    )
    assert row is None and moved_row is not None

    # The root reverts, because the pointer is the only memory there is.
    assert model_utils.insightface_root() == str(face_env.root)

    # And the failure is loud, naming the new location and the repair.
    assert any(
        "could not record the new root" in record.message
        and "re-run the relocation" in record.message
        for record in caplog.records
    ), [record.message for record in caplog.records]


def test_the_shelf_reports_the_packs_as_relocatable_before_and_after(face_env):
    """The folder list is the surface the Move control reads back.

    ``relocatable``, not ``movable``: the packs, the download folder and the
    HuggingFace cache all read `foreign`, and the first two share `root_only`
    besides, so this boolean is the only thing that tells the client which rows
    to offer Move on. It has to be true here *before* the relocation - that is
    what puts the control on the row - and again afterwards, since a folder that
    moved once can move again.
    """

    def _listed(folder_id):
        return next(
            folder
            for folder in face_env.owner.get(f"{API}/model-folders").json()["folders"]
            if folder["id"] == folder_id
        )

    folder_id = face_env.folder_id
    before = _listed(folder_id)
    assert before["movable"] == "root_only"
    assert before["relocatable"] is True, (
        "without this the dialog shows no Move control at all"
    )

    r = face_env.owner.post(
        f"{API}/model-folders/{folder_id}/relocate",
        json={"path": str(face_env.target)},
    )
    assert r.status_code == 202, r.text
    _await_move(face_env)

    after = _listed(face_env.folder_id)
    assert after["path"] == str(face_env.target / "models")
    assert after["movable"] == "root_only"
    assert after["relocatable"] is True


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
    """``fixed`` means it cannot move at all - its location is ``HF_HOME``.

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

"""Caption-file sync for the library's own picture root.

A reference folder has had two-way sidecar sync since the split-caption work;
pictures imported in place got none of it. The same four fields now live on
``LibrarySettings`` and the same code paths serve them: the root scan's
reconcile pass, ``sync_picture_sidecar``'s write-back, and a settings route
that mirrors ``PATCH /reference-folders/{folder_id}``.
"""

import json
import os
import tempfile
import time

import pytest
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Picture, Tag
from pixlstash.db_models.folder_mapping_commit import (
    STATE_DEFERRED,
    FolderMappingCommit,
)
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.server import Server
from pixlstash.services.library_settings_service import (
    get_caption_sync,
    seed_caption_suffixes,
)
from pixlstash.tasks import TaskType
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask
from pixlstash.utils.service.caption_utils import sync_picture_sidecar
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

API = "/api/v1"
_CAPTIONS = f"{API}/server-config/captions"
_CONFLICTING_FINDERS = (
    TaskType.THUMBNAIL_GENERATION,
    TaskType.REFERENCE_FOLDER_SCAN,
    TaskType.MISSING_FILE_PURGE,
    TaskType.TAGGER,
)

pytestmark = pytest.mark.usefixtures("no_spa_fallback")


@pytest.fixture(scope="module")
def env():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "server-config.json")
        with open(config_path, "w") as fh:
            json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, fh)
        with Server(config_path) as srv:
            for task_type in _CONFLICTING_FINDERS:
                srv.vault._planner_work_finders.pop(task_type)
            srv.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)

            # The root scan waits for the first import offer to be answered;
            # this module runs scans by hand, so answer it once for all.
            def answer(session: Session):
                session.add(
                    FolderMappingCommit(
                        task_id="answered-by-test",
                        root_path=srv.vault.image_root,
                        mode="local_import",
                        state=STATE_DEFERRED,
                    )
                )
                session.commit()

            srv.vault.db.run_task(answer)
            from starlette.testclient import TestClient

            owner = TestClient(srv.api, raise_server_exceptions=True)
            login = owner.post(
                f"{API}/login",
                json={"username": "owner", "password": "example-owner-password"},
            )
            assert login.status_code == 200, login.text
            yield {"server": srv, "owner": owner}


def _settle(path):
    old = time.time() - 600
    os.utime(path, (old, old))


def _make_image(path, color):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (8, 8), color=color).save(path, format="PNG")
    _settle(path)


def _write(path, text, *, when=None):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if when is None:
        _settle(path)
    else:
        os.utime(path, (when, when))


def _run_root_scan(server):
    root = server.vault.image_root
    return ReferenceFolderScanTask(
        database=server.vault.db, folder_id=None, folder_path=root, resolved_path=root
    )._run_task()


def _set_sync(server, **fields):
    def write(session: Session):
        row = session.exec(select(LibrarySettings)).first()
        for name, value in fields.items():
            setattr(row, name, value)
        session.add(row)
        session.commit()

    server.vault.db.run_task(write)


def _picture(server, rel):
    def read(session: Session):
        pic = session.exec(select(Picture).where(Picture.file_path == rel)).first()
        tags = sorted(
            session.exec(select(Tag.tag).where(Tag.picture_id == pic.id)).all()
        )
        return pic.id, pic.description, pic.tags_file, tags

    return server.vault.db.run_immediate_read_task(read)


def _give(server, pic_id, *tags, description=None):
    def write(session: Session):
        session.exec(Tag.__table__.delete().where(Tag.picture_id == pic_id))
        session.add_all(Tag(picture_id=pic_id, tag=t) for t in tags)
        if description is not None:
            session.get(Picture, pic_id).description = description
        session.commit()

    server.vault.db.run_task(write)


def test_every_route_this_file_names_is_a_real_route(env):
    assert_real_route(env["server"].api, "GET", _CAPTIONS)
    assert_real_route(env["server"].api, "PATCH", _CAPTIONS)


def test_the_root_scan_reads_and_exports_sidecars_once_sync_is_on(env):
    """Sync off: a picture's first indexing still reads the file beside it,
    but nothing is exported and a later edit on disk is not read. Sync on: an
    edited sidecar is read in, a picture with tags and no sidecar gets one
    under the confirmed suffix, and a stray empty file imports nothing."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "sync", "edited.png"), (10, 20, 30))
    _make_image(os.path.join(root, "sync", "exported.png"), (40, 50, 60))
    _make_image(os.path.join(root, "sync", "blank.png"), (1, 1, 1))
    _write(os.path.join(root, "sync", "edited.txt"), "cat, calm")
    _set_sync(server, sync_tags=False, sync_descriptions=False, tags_suffix=None)
    _run_root_scan(server)
    edited_id, _, _, edited_tags = _picture(server, "sync/edited.png")
    exported_id, _, _, _ = _picture(server, "sync/exported.png")
    blank_id, _, _, _ = _picture(server, "sync/blank.png")
    assert edited_tags == ["calm", "cat"]

    _give(server, exported_id, "dog", "beach")
    _give(server, blank_id, "kept")
    _write(
        os.path.join(root, "sync", "edited.txt"),
        "cat, calm, sleeping",
        when=time.time() + 5,
    )
    _write(os.path.join(root, "sync", "blank.txt"), "")
    _run_root_scan(server)
    assert _picture(server, "sync/edited.png")[3] == ["calm", "cat"], "sync off"
    assert not os.path.exists(os.path.join(root, "sync", "exported.txt"))

    _set_sync(server, sync_tags=True, tags_suffix=".txt")
    _run_root_scan(server)
    _, _, edited_file, edited_tags = _picture(server, "sync/edited.png")
    assert edited_tags == ["calm", "cat", "sleeping"], "the on-disk edit is read in"
    assert edited_file == os.path.join(root, "sync", "edited.txt")
    with open(os.path.join(root, "sync", "exported.txt"), encoding="utf-8") as fh:
        assert fh.read().strip() == "beach, dog"
    assert not os.path.exists(os.path.join(root, "sync", "exported_tags.txt"))
    assert _picture(server, "sync/blank.png")[3] == ["kept"], (
        "an empty stray file imports nothing"
    )


def test_an_edit_in_pixlstash_is_written_beside_the_managed_picture(env):
    """The write-back returned early for any picture without a reference
    folder. A managed picture follows the library's settings, a pending
    description sentinel is never written out, and a file on disk the picture
    never recorded is left for the scan rather than overwritten."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "writeback", "b.png"), (7, 8, 9))
    _make_image(os.path.join(root, "writeback", "c.png"), (7, 8, 10))
    _set_sync(server, sync_tags=False, sync_descriptions=False, tags_suffix=None)
    _run_root_scan(server)
    b_id, _, _, _ = _picture(server, "writeback/b.png")
    c_id, _, _, _ = _picture(server, "writeback/c.png")
    _give(server, b_id, "square", description="A small blue square.")
    _give(server, c_id, "circle", description="__description::joycaption")
    _write(os.path.join(root, "writeback", "c_tags.txt"), "owner's own, unread")

    sync_picture_sidecar(server, b_id)
    assert not os.path.exists(os.path.join(root, "writeback", "b_tags.txt")), "both off"

    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=True,
        description_suffix="_caption.txt",
    )
    assert [t["tag"] for t in sync_picture_sidecar(server, b_id)] == ["square"]
    with open(os.path.join(root, "writeback", "b_tags.txt"), encoding="utf-8") as fh:
        assert fh.read().strip() == "square"
    with open(os.path.join(root, "writeback", "b_caption.txt"), encoding="utf-8") as fh:
        assert fh.read().strip() == "A small blue square."
    assert _picture(server, "writeback/b.png")[2] == os.path.join(
        root, "writeback", "b_tags.txt"
    )

    sync_picture_sidecar(server, c_id)
    with open(os.path.join(root, "writeback", "c_tags.txt"), encoding="utf-8") as fh:
        assert fh.read() == "owner's own, unread", "an unrecorded file is not replaced"
    assert not os.path.exists(os.path.join(root, "writeback", "c_caption.txt")), (
        "a pending sentinel is not a description"
    )


def test_the_settings_route_reads_patches_and_refuses_bad_suffixes(env, monkeypatch):
    owner = env["owner"]
    server = env["server"]
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=None,
        description_suffix=None,
    )
    body = owner.get(_CAPTIONS).json()
    assert body["sync_tags"] is False and body["tags_suffix"] is None
    assert body["default_tags_suffix"] == "_tags.txt"

    rescans = []
    monkeypatch.setattr(server.vault, "rescan_library_root", lambda: rescans.append(1))
    saved = owner.patch(
        _CAPTIONS, json={"sync_tags": True, "tags_suffix": " .txt "}
    ).json()
    assert saved["sync_tags"] is True and saved["tags_suffix"] == ".txt"
    assert rescans == [1], "a kind coming on asks for the scan that reads files in"
    assert (
        owner.patch(_CAPTIONS, json={"sync_descriptions": None}).json()["sync_tags"]
        is True
    )
    assert rescans == [1], "no change, no scan"

    assert owner.patch(_CAPTIONS, json={"tags_suffix": "../x"}).status_code == 400
    assert owner.patch(_CAPTIONS, json={"tags_suffix": ".png"}).status_code == 400
    # A suffix for a kind that is off is only latent: stored, checked when the
    # kind comes on, when its field is on screen rather than hidden.
    assert (
        owner.patch(_CAPTIONS, json={"description_suffix": ".TXT"}).status_code == 200
    )
    shared = owner.patch(_CAPTIONS, json={"sync_descriptions": True})
    assert shared.status_code == 400 and "share a suffix" in shared.text
    assert owner.get(_CAPTIONS).json()["sync_descriptions"] is False, (
        "nothing stored on a 400"
    )


def test_the_route_is_refused_to_a_remote_owner(env):
    """§16.3 in both directions: a remote owner is refused unless
    allow_remote_host_ops is on; a loopback owner never is."""
    from tests.test_authz_host_capability_16_3 import _enforcing, _remote_host_ops

    owner = env["owner"]
    server = env["server"]
    remote = {"X-Forwarded-For": "8.8.8.8"}
    with _enforcing(server), _remote_host_ops(server, False):
        refused = owner.get(_CAPTIONS, headers=remote)
        assert refused.status_code == 403 and "restricted to local" in refused.text
        refused = owner.patch(_CAPTIONS, json={"sync_tags": False}, headers=remote)
        assert refused.status_code == 403 and "restricted to local" in refused.text
        assert owner.get(_CAPTIONS).status_code == 200
    with _enforcing(server), _remote_host_ops(server, True):
        assert owner.get(_CAPTIONS, headers=remote).status_code == 200


def test_confirming_a_pattern_on_import_turns_that_sync_on(env):
    """The wizard's answer is the opt-in. A suffix already stored wins, a kind
    not confirmed is untouched, a colliding suffix is refused, and a repeat of
    a convention already on asks for no rescan."""
    server = env["server"]
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=".txt",
        description_suffix=None,
    )
    assert seed_caption_suffixes(server.vault.db, "_tags.txt", None) is True
    stored = get_caption_sync(server.vault.db)
    assert stored["sync_tags"] is True and stored["tags_suffix"] == ".txt"
    assert stored["sync_descriptions"] is False and stored["description_suffix"] is None

    assert seed_caption_suffixes(server.vault.db, None, ".txt") is False, (
        "collides with tags"
    )
    assert get_caption_sync(server.vault.db)["sync_descriptions"] is False
    assert seed_caption_suffixes(server.vault.db, None, "_caption.txt") is True
    assert get_caption_sync(server.vault.db)["description_suffix"] == "_caption.txt"
    assert seed_caption_suffixes(server.vault.db, ".txt", "_caption.txt") is False


def test_a_reference_folder_cannot_be_given_one_suffix_for_both(env):
    owner = env["owner"]
    with tempfile.TemporaryDirectory() as folder:
        made = owner.post(
            f"{API}/reference-folders",
            json={
                "folder": folder,
                "tags_suffix": ".txt",
                "description_suffix": ".txt",
            },
        )
        assert made.status_code == 400 and "share a suffix" in made.text

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
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.server import Server
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
    return path


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    _settle(path)


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


def test_every_route_this_file_names_is_a_real_route(env):
    assert_real_route(env["server"].api, "GET", _CAPTIONS)
    assert_real_route(env["server"].api, "PATCH", _CAPTIONS)


def test_the_root_scan_reads_and_exports_sidecars_once_sync_is_on(env):
    """With sync on and a suffix set, the root gets the reference folder's
    reconcile pass: a sidecar edited on disk is read in, a picture with tags
    and no sidecar gets one written, and the write is under the confirmed
    suffix rather than the module default."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "sync", "edited.png"), (10, 20, 30))
    _make_image(os.path.join(root, "sync", "exported.png"), (40, 50, 60))
    _write(os.path.join(root, "sync", "edited.txt"), "cat, calm")

    # Sync off: the scan indexes both and reads the .txt beside `edited` at
    # import (the local-import read), but nothing is exported.
    _set_sync(server, sync_tags=False, sync_descriptions=False, tags_suffix=None)
    _run_root_scan(server)
    edited_id, _, _, edited_tags = _picture(server, "sync/edited.png")
    exported_id, _, _, _ = _picture(server, "sync/exported.png")
    assert edited_tags == ["calm", "cat"]
    assert not os.path.exists(os.path.join(root, "sync", "exported.txt"))

    def give_tags(session: Session):
        session.exec(Tag.__table__.delete().where(Tag.picture_id == exported_id))
        session.add(Tag(picture_id=exported_id, tag="dog"))
        session.add(Tag(picture_id=exported_id, tag="beach"))
        session.commit()

    server.vault.db.run_task(give_tags)
    _write(os.path.join(root, "sync", "edited.txt"), "cat, calm, sleeping")
    # A later mtime than the recorded one is what "edited on disk" means.
    later = time.time() + 5
    os.utime(os.path.join(root, "sync", "edited.txt"), (later, later))

    _set_sync(server, sync_tags=True, tags_suffix=".txt")
    result = _run_root_scan(server)
    assert result["caption_updated_count"] >= 2, result

    _, _, edited_file, edited_tags = _picture(server, "sync/edited.png")
    assert edited_tags == ["calm", "cat", "sleeping"], "the on-disk edit is read in"
    assert edited_file == os.path.join(root, "sync", "edited.txt")
    exported = os.path.join(root, "sync", "exported.txt")
    assert os.path.isfile(exported), "a picture with tags and no sidecar gets one"
    with open(exported, encoding="utf-8") as fh:
        assert fh.read().strip() == "beach, dog"
    assert not os.path.exists(os.path.join(root, "sync", "exported_tags.txt")), (
        "written under the confirmed suffix, not the module default"
    )


def test_the_root_scan_leaves_sidecars_alone_while_sync_is_off(env):
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "quiet", "a.png"), (1, 2, 3))
    _set_sync(server, sync_tags=False, sync_descriptions=False)
    _run_root_scan(server)
    pic_id, _, _, _ = _picture(server, "quiet/a.png")

    def give_tags(session: Session):
        session.exec(Tag.__table__.delete().where(Tag.picture_id == pic_id))
        session.add(Tag(picture_id=pic_id, tag="quiet"))
        session.commit()

    server.vault.db.run_task(give_tags)
    _run_root_scan(server)
    assert not os.path.exists(os.path.join(root, "quiet", "a.txt"))
    assert not os.path.exists(os.path.join(root, "quiet", "a_tags.txt"))


def test_an_edit_in_pixlstash_is_written_beside_the_managed_picture(env):
    """The write-back helper used to return early for any picture without a
    reference folder. A managed picture now follows the library's settings."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "writeback", "b.png"), (7, 8, 9))
    _set_sync(server, sync_tags=False, sync_descriptions=False)
    _run_root_scan(server)
    pic_id, _, _, _ = _picture(server, "writeback/b.png")

    def give_description(session: Session):
        pic = session.get(Picture, pic_id)
        pic.description = "A small blue square."
        session.exec(Tag.__table__.delete().where(Tag.picture_id == pic_id))
        session.add(Tag(picture_id=pic_id, tag="square"))
        session.commit()

    server.vault.db.run_task(give_description)

    sync_picture_sidecar(server, pic_id)
    assert not os.path.exists(os.path.join(root, "writeback", "b_tags.txt")), (
        "both toggles off: nothing is written"
    )

    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=True,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )
    tags = sync_picture_sidecar(server, pic_id)
    assert [t["tag"] for t in tags] == ["square"]
    with open(os.path.join(root, "writeback", "b_tags.txt"), encoding="utf-8") as fh:
        assert fh.read().strip() == "square"
    with open(os.path.join(root, "writeback", "b_caption.txt"), encoding="utf-8") as fh:
        assert fh.read().strip() == "A small blue square."
    _, _, tags_file, _ = _picture(server, "writeback/b.png")
    assert tags_file == os.path.join(root, "writeback", "b_tags.txt")


def test_the_settings_route_reads_patches_and_refuses_an_unsafe_suffix(env):
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

    patched = owner.patch(
        _CAPTIONS, json={"sync_descriptions": True, "description_suffix": ".caption"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["sync_descriptions"] is True
    assert patched.json()["description_suffix"] == ".caption"
    assert patched.json()["sync_tags"] is False, "a field not sent keeps its value"

    nulled = owner.patch(_CAPTIONS, json={"sync_descriptions": None})
    assert nulled.status_code == 200, nulled.text
    assert nulled.json()["sync_descriptions"] is True, (
        "a toggle sent as null is no change, not an accidental off"
    )

    refused = owner.patch(_CAPTIONS, json={"tags_suffix": "../escape.txt"})
    assert refused.status_code == 400, refused.text
    assert owner.get(_CAPTIONS).json()["tags_suffix"] is None, "nothing stored"

    cleared = owner.patch(_CAPTIONS, json={"description_suffix": ""})
    assert cleared.json()["description_suffix"] is None


def test_the_route_is_refused_to_a_remote_owner(env):
    """§16.3: the locality tier, in the negative direction. A remote owner is
    refused both verbs unless allow_remote_host_ops is on, and a local one is
    not - the same two directions the locality suite checks for every route."""
    from tests.test_authz_host_capability_16_3 import _enforcing, _remote_host_ops

    owner = env["owner"]
    server = env["server"]
    remote = {"X-Forwarded-For": "8.8.8.8"}
    with _enforcing(server), _remote_host_ops(server, False):
        refused = owner.get(_CAPTIONS, headers=remote)
        assert refused.status_code == 403 and "restricted to local" in refused.text
        refused = owner.patch(_CAPTIONS, json={"sync_tags": False}, headers=remote)
        assert refused.status_code == 403 and "restricted to local" in refused.text
        assert owner.get(_CAPTIONS).status_code == 200, "loopback owner: allowed"
    with _enforcing(server), _remote_host_ops(server, True):
        assert owner.get(_CAPTIONS, headers=remote).status_code == 200


def test_confirming_a_pattern_on_import_turns_that_sync_on(env):
    """The wizard's answer is the opt-in: a tester who confirmed the patterns
    expected edits to reach the files and did not go looking for a second
    toggle. A suffix already set is kept; a kind not confirmed is untouched."""
    from pixlstash.services.library_settings_service import (
        get_caption_sync,
        seed_caption_suffixes,
    )

    server = env["server"]
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=".txt",
        description_suffix=None,
    )
    seed_caption_suffixes(server.vault.db, "_tags.txt", None)
    stored = get_caption_sync(server.vault.db)
    assert stored["sync_tags"] is True
    assert stored["tags_suffix"] == ".txt", "an earlier convention wins"
    assert stored["sync_descriptions"] is False and stored["description_suffix"] is None

    seed_caption_suffixes(server.vault.db, None, "_caption.txt")
    stored = get_caption_sync(server.vault.db)
    assert stored["sync_descriptions"] is True
    assert stored["description_suffix"] == "_caption.txt"


def test_a_recorded_caption_file_keeps_its_name_under_another_suffix(env):
    """The suffix names files PixlStash creates. A picture imported with a
    `photo.txt` under a `_tags.txt` setting is read and written as
    `photo.txt`; no `_tags.txt` appears beside it, in either direction."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "keep", "photo.png"), (21, 22, 23))
    _write(os.path.join(root, "keep", "photo.txt"), "cat, calm")
    _set_sync(server, sync_tags=False, sync_descriptions=False, tags_suffix=None)
    _run_root_scan(server)
    pic_id, _, tags_file, tags = _picture(server, "keep/photo.png")
    assert tags == ["calm", "cat"] and tags_file.endswith("photo.txt")

    _set_sync(server, sync_tags=True, tags_suffix="_tags.txt")
    _write(os.path.join(root, "keep", "photo.txt"), "cat, calm, asleep")
    later = time.time() + 5
    os.utime(os.path.join(root, "keep", "photo.txt"), (later, later))
    _run_root_scan(server)
    _, _, tags_file, tags = _picture(server, "keep/photo.png")
    assert tags == ["asleep", "calm", "cat"], "read from the recorded file"
    assert tags_file.endswith("photo.txt")
    assert not os.path.exists(os.path.join(root, "keep", "photo_tags.txt"))

    def edit(session: Session):
        session.exec(Tag.__table__.delete().where(Tag.picture_id == pic_id))
        session.add(Tag(picture_id=pic_id, tag="dog"))
        session.commit()

    server.vault.db.run_task(edit)
    sync_picture_sidecar(server, pic_id)
    with open(os.path.join(root, "keep", "photo.txt"), encoding="utf-8") as fh:
        assert fh.read().strip() == "dog", "written to the recorded file"
    assert not os.path.exists(os.path.join(root, "keep", "photo_tags.txt"))


def _set_tags(server, pic_id, *tags):
    def write(session: Session):
        session.exec(Tag.__table__.delete().where(Tag.picture_id == pic_id))
        for tag in tags:
            session.add(Tag(picture_id=pic_id, tag=tag))
        session.commit()

    server.vault.db.run_task(write)


def test_the_first_enable_reads_the_owner_s_file_before_writing_over_it(env):
    """The scenario the seeding used to leave to the write-back: a picture
    indexed long ago, a caption file the owner wrote since, and tags in the
    database. The scan reads the file in; nothing truncates it."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "first", "a.png"), (11, 12, 13))
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=None,
        description_suffix=None,
    )
    _run_root_scan(server)
    pic_id, _, tags_file, _ = _picture(server, "first/a.png")
    assert tags_file is None, "indexed with no caption file beside it"
    _set_tags(server, pic_id, "boat")
    _write(os.path.join(root, "first", "a_tags.txt"), "harbour, dusk")

    _set_sync(server, sync_tags=True, tags_suffix="_tags.txt")
    _run_root_scan(server)
    _, _, tags_file, tags = _picture(server, "first/a.png")
    assert tags == ["dusk", "harbour"], "the owner's file is what the scan reads"
    assert tags_file == os.path.join(root, "first", "a_tags.txt")

    sync_picture_sidecar(server, pic_id)
    with open(os.path.join(root, "first", "a_tags.txt"), encoding="utf-8") as fh:
        assert sorted(fh.read().split(", ")) == ["dusk", "harbour"], (
            "the write-back carries the read-in tags, it does not empty the file"
        )


def test_an_empty_tags_file_leaves_the_picture_s_tags_alone(env):
    """`apply_caption_updates` replaces the whole tag set, so importing the []
    a zero-byte file parses to would clear a picture nobody asked to clear."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "empty", "b.png"), (14, 15, 16))
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=None,
        description_suffix=None,
    )
    _run_root_scan(server)
    pic_id, _, _, _ = _picture(server, "empty/b.png")
    _set_tags(server, pic_id, "boat", "jetty")
    _write(os.path.join(root, "empty", "b_tags.txt"), "")

    _set_sync(server, sync_tags=True, tags_suffix="_tags.txt")
    _run_root_scan(server)
    _, _, _, tags = _picture(server, "empty/b.png")
    assert tags == ["boat", "jetty"]


def test_descriptions_on_with_tags_off_never_reads_a_stray_txt_as_tags(env):
    """A Stable Diffusion prompt `.txt` beside a managed picture is the
    description convention the owner turned on, not the tag set. The read
    direction is gated per type, as the write direction already was."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "stray", "c.png"), (17, 18, 19))
    # `.txt` is the tags convention this library once used and the owner has
    # since turned tags off; descriptions live in `_caption.txt`.
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=".txt",
        description_suffix="_caption.txt",
    )
    _run_root_scan(server)
    pic_id, _, _, _ = _picture(server, "stray/c.png")
    _set_tags(server, pic_id, "boat")
    _write(os.path.join(root, "stray", "c.txt"), "1girl, solo, smile, outdoors")
    _write(os.path.join(root, "stray", "c_caption.txt"), "A lighthouse at dusk.")

    _set_sync(server, sync_descriptions=True)
    _run_root_scan(server)
    _, description, _, tags = _picture(server, "stray/c.png")
    assert tags == ["boat"], "the prompt file is not the tag set"
    assert description == "A lighthouse at dusk."


def test_the_settings_route_refuses_one_suffix_for_both_kinds(env):
    """Both write-backs derive their path from the picture stem plus the
    suffix, so an equal pair is one file: the description wins and the next
    scan reads prose back in as tags. Compared on the effective values, so the
    default counts."""
    owner = env["owner"]
    server = env["server"]
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=None,
        description_suffix=None,
    )
    both = owner.patch(
        _CAPTIONS,
        json={"tags_suffix": "_notes.txt", "description_suffix": "_notes.txt"},
    )
    assert both.status_code == 400, both.text
    assert owner.get(_CAPTIONS).json()["tags_suffix"] is None, "nothing stored"

    against_default = owner.patch(_CAPTIONS, json={"description_suffix": "_tags.txt"})
    assert against_default.status_code == 400, "the unset tags suffix is _tags.txt"

    ok = owner.patch(_CAPTIONS, json={"description_suffix": "_notes.txt"})
    assert ok.status_code == 200, ok.text

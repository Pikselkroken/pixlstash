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


def test_the_route_refuses_a_suffix_that_differs_only_in_case(env):
    """Windows and macOS resolve `_notes.txt` and `_NOTES.TXT` to one file, so
    the pair is as shared as an exact match: both write-backs would land on it
    and the next scan would read the survivor back in as the other kind."""
    owner = env["owner"]
    server = env["server"]
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=True,
        tags_suffix="_notes.txt",
        description_suffix="_caption.txt",
    )
    refused = owner.patch(_CAPTIONS, json={"description_suffix": "_NOTES.TXT"})
    assert refused.status_code == 400, refused.text
    assert "share" in refused.text
    stored = owner.get(_CAPTIONS).json()
    assert stored["description_suffix"] == "_caption.txt", "nothing stored"

    # The other direction, and against the default rather than a stored value.
    _set_sync(server, tags_suffix=None, description_suffix="_caption.txt")
    refused = owner.patch(_CAPTIONS, json={"description_suffix": "_TAGS.TXT"})
    assert refused.status_code == 400, refused.text
    assert owner.get(_CAPTIONS).json()["description_suffix"] == "_caption.txt"


def test_seeding_refuses_a_suffix_the_other_kind_already_uses(env):
    """A second import must not seed one kind with the suffix the other kind
    already uses: both write-backs would point at one file. The kind is
    skipped whole - toggle unchanged, column unset - not half-applied."""
    from pixlstash.services.library_settings_service import (
        get_caption_sync,
        seed_caption_suffixes,
    )

    server = env["server"]
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=False,
        tags_suffix="_caption.txt",
        description_suffix=None,
    )
    assert seed_caption_suffixes(server.vault.db, None, "_CAPTION.TXT") is False
    stored = get_caption_sync(server.vault.db)
    assert stored["sync_descriptions"] is False
    assert stored["description_suffix"] is None
    assert stored["tags_suffix"] == "_caption.txt", "the stored kind is untouched"

    # A kind that does not collide is still seeded in the same call.
    assert seed_caption_suffixes(server.vault.db, "_caption.txt", "_desc.txt") is True
    stored = get_caption_sync(server.vault.db)
    assert stored["sync_descriptions"] is True
    assert stored["description_suffix"] == "_desc.txt"


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


def test_seeding_a_convention_already_stored_asks_for_no_rescan(env):
    """What is returned is *rescan due*, not "a kind was confirmed". A second
    import of a folder whose convention is already stored turns nothing on and
    changes no suffix, and walking the whole library again for files that were
    read the first time costs every later import for nothing."""
    from pixlstash.services.library_settings_service import (
        get_caption_sync,
        seed_caption_suffixes,
    )

    server = env["server"]
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=None,
        description_suffix=None,
    )
    assert seed_caption_suffixes(server.vault.db, "_tags.txt", None) is True
    assert seed_caption_suffixes(server.vault.db, "_tags.txt", None) is False

    # An earlier convention wins, so a kind that is on with a suffix stored
    # takes nothing from a later import - nothing changed, nothing to rescan.
    assert seed_caption_suffixes(server.vault.db, "_labels.txt", None) is False
    stored = get_caption_sync(server.vault.db)
    assert stored["tags_suffix"] == "_tags.txt"


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


def test_the_shared_suffix_rule_runs_inside_the_writer(env):
    """A cross-field rule checked against a separately read snapshot is a
    race: two partial PATCHes can each pass against the old row and serialise
    into the state the rule forbids. The rule runs on the merged row inside
    the writer task, so the second writer sees the first's write."""
    from pixlstash.services.library_settings_service import (
        get_caption_sync,
        set_caption_sync,
    )

    server = env["server"]
    owner = env["owner"]
    _set_sync(server, tags_suffix="_a.txt", description_suffix="_b.txt")
    seen = {}

    def refuse(merged):
        seen.update(merged)
        raise ValueError("refused on the merged row")

    with pytest.raises(ValueError):
        set_caption_sync(server.vault.db, validate=refuse, tags_suffix="_b.txt")
    assert seen["tags_suffix"] == "_b.txt" and seen["description_suffix"] == "_b.txt", (
        "the validator sees the stored row with the patch applied"
    )
    assert get_caption_sync(server.vault.db)["tags_suffix"] == "_a.txt", (
        "nothing stored"
    )

    refused = owner.patch(_CAPTIONS, json={"description_suffix": "_a.txt"})
    assert refused.status_code == 400 and "share" in refused.text
    refused = owner.patch(
        _CAPTIONS, json={"tags_suffix": "", "description_suffix": "_tags.txt"}
    )
    assert refused.status_code == 400, "the default takes part in the comparison"


def test_a_reference_folder_cannot_be_created_with_one_suffix_for_both(env):
    """The rule update enforces applies to create too, through one validator."""
    owner = env["owner"]
    with tempfile.TemporaryDirectory() as folder:
        body = {
            "folder": folder,
            "tags_suffix": "_c.txt",
            "description_suffix": "_c.txt",
        }
        refused = owner.post(f"{API}/reference-folders", json=body)
        assert refused.status_code == 400, refused.text
        assert "share" in refused.text
        body = {"folder": folder, "tags_suffix": "", "description_suffix": "_tags.txt"}
        refused = owner.post(f"{API}/reference-folders", json=body)
        assert refused.status_code == 400, "the default takes part on create too"


def test_a_recorded_path_that_is_the_picture_itself_is_never_a_sidecar(tmp_path):
    """A row from before `sidecar_path` refused image-extension suffixes can
    carry the picture as its own sidecar; a write-back through it would
    overwrite the original. Both helpers refuse it, read and write."""
    from pixlstash.utils.caption_file_utils import (
        SIDECAR_TYPE_TAGS,
        recorded_sidecar,
        writeback_path,
    )

    image = tmp_path / "photo.png"
    image.write_bytes(b"png")
    assert recorded_sidecar(str(image), str(image)) is None
    assert writeback_path(str(image), SIDECAR_TYPE_TAGS, None, str(image)) == str(
        tmp_path / "photo_tags.txt"
    )
    sidecar = tmp_path / "photo.txt"
    sidecar.write_text("cat")
    assert recorded_sidecar(str(image), str(sidecar)) == str(sidecar)


def test_an_edit_before_the_first_scan_leaves_an_unrecorded_sidecar_alone(env):
    """The window both `PATCH /server-config/captions` and
    `seed_caption_suffixes` open: the toggle is stored and the rescan is only
    *queued*, so every caption file on disk is still unrecorded. An edit
    landing in it used to resolve the configured file and truncate it with the
    database value, before the promised read-in."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "unrecorded", "c.png"), (21, 22, 23))
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=None,
        description_suffix=None,
    )
    _run_root_scan(server)
    pic_id, _, tags_file, _ = _picture(server, "unrecorded/c.png")
    assert tags_file is None, "indexed with no caption file beside it"

    tags_path = os.path.join(root, "unrecorded", "c_tags.txt")
    caption_path = os.path.join(root, "unrecorded", "c_caption.txt")
    _write(tags_path, "harbour, dusk")
    _write(caption_path, "A quiet harbour.")
    _set_tags(server, pic_id, "boat")

    def give_description(session: Session):
        session.get(Picture, pic_id).description = "Typed in PixlStash."
        session.commit()

    server.vault.db.run_task(give_description)
    # Sync on, scan not run yet: exactly what the toggle leaves behind.
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=True,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )

    sync_picture_sidecar(server, pic_id)
    with open(tags_path, encoding="utf-8") as fh:
        assert fh.read().strip() == "harbour, dusk", "the owner's file is untouched"
    with open(caption_path, encoding="utf-8") as fh:
        assert fh.read().strip() == "A quiet harbour."
    _, _, tags_file, _ = _picture(server, "unrecorded/c.png")
    assert tags_file is None, "a skipped write records nothing"

    _run_root_scan(server)
    _, description, tags_file, tags = _picture(server, "unrecorded/c.png")
    assert tags == ["dusk", "harbour"], "the scan reads the file in, as promised"
    assert description == "A quiet harbour."
    assert tags_file == tags_path


def test_turning_a_kind_on_makes_the_scan_re_read_files_it_already_recorded(env):
    """A picture indexed while sync was off already carries the file and the
    mtime it was read at, because a first indexing reads the sidecar beside it
    either way. Edit its tags in PixlStash while sync is off and the two have
    diverged; the reconcile then sees the same path and the same mtime, so it
    neither imports nor exports and they stay diverged for good. Turning the
    kind on forgets that mtime, so the scan reads the file in - the file wins,
    the recorded-file rule."""
    from pixlstash.services.library_settings_service import set_caption_sync

    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "reenable", "e.png"), (31, 32, 33))
    _write(os.path.join(root, "reenable", "e.txt"), "harbour, dusk")
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix=None,
        description_suffix=None,
    )
    _run_root_scan(server)
    pic_id, _, tags_file, tags = _picture(server, "reenable/e.png")
    assert (tags_file, tags) == (
        os.path.join(root, "reenable", "e.txt"),
        ["dusk", "harbour"],
    ), "the first indexing recorded the file and its mtime"

    # Edited in PixlStash while sync was off: nothing wrote the file back.
    _set_tags(server, pic_id, "boat")

    set_caption_sync(server.vault.db, sync_tags=True, tags_suffix=".txt")
    _run_root_scan(server)
    _, _, tags_file, tags = _picture(server, "reenable/e.png")
    assert tags == ["dusk", "harbour"], "the file the owner has is read back in"
    assert tags_file == os.path.join(root, "reenable", "e.txt")


def test_a_picture_with_no_caption_file_still_gets_one_written(env):
    """The skip is about replacing somebody else's file, not about writing:
    with nothing on disk the suffix-derived file is still created."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "fresh", "d.png"), (24, 25, 26))
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=False,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )
    _run_root_scan(server)
    pic_id, _, tags_file, _ = _picture(server, "fresh/d.png")
    assert tags_file is None, "no tags and no file: nothing was created"

    _set_tags(server, pic_id, "kite")
    sync_picture_sidecar(server, pic_id)
    written = os.path.join(root, "fresh", "d_tags.txt")
    with open(written, encoding="utf-8") as fh:
        assert fh.read().strip() == "kite"
    _, _, tags_file, _ = _picture(server, "fresh/d.png")
    assert tags_file == written, "and the row records it"


def test_the_rescan_decision_is_made_against_the_row_the_write_replaced(env):
    """Two PATCHes read the same suffix. One writes another and asks for the
    scan; the other writes back exactly the suffix it read. Judged against the
    snapshot the request read, the second one changed nothing and asks for no
    scan - and the files under the restored suffix are then never read in. The
    writer compares with the row it actually replaced, so both end with a
    rescan asked for. An extra idempotent scan is cheap; a dropped one is not.
    """
    from pixlstash.services.library_settings_service import (
        get_caption_sync,
        set_caption_sync,
    )

    server = env["server"]
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=False,
        tags_suffix="_a.txt",
        description_suffix="_b.txt",
    )
    snapshot = get_caption_sync(server.vault.db)

    _, first_due = set_caption_sync(server.vault.db, tags_suffix="_y.txt")
    stored, second_due = set_caption_sync(
        server.vault.db, tags_suffix=snapshot["tags_suffix"]
    )

    assert first_due, "the suffix changed while tag sync is on"
    assert stored["tags_suffix"] == snapshot["tags_suffix"], (
        "the second request wrote back what it read: against its own snapshot "
        "nothing changed at all"
    )
    assert second_due, "but the stored suffix did change, and _a.txt is unread"

    # A kind that is off is never due, whatever its suffix does.
    _, off_due = set_caption_sync(server.vault.db, description_suffix="_c.txt")
    assert off_due is False, "description sync is off"


def test_turning_a_kind_on_through_the_route_asks_for_the_scan(env, monkeypatch):
    """The route acts on what the writer reports: the scan is what reads the
    owner's existing files in before anything is written back over them."""
    server = env["server"]
    owner = env["owner"]
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=False,
        tags_suffix="_a.txt",
        description_suffix="_b.txt",
    )
    rescans = []
    monkeypatch.setattr(server.vault, "rescan_library_root", lambda: rescans.append(1))

    assert owner.get(_CAPTIONS).status_code == 200
    assert not rescans, "a read asks for nothing"

    turned_on = owner.patch(_CAPTIONS, json={"sync_tags": True})
    assert turned_on.status_code == 200, turned_on.text
    assert len(rescans) == 1, "off -> on is due a scan"

    assert owner.patch(_CAPTIONS, json={"sync_tags": True}).status_code == 200
    assert len(rescans) == 1, "already on, same suffix: nothing to re-read"

    assert owner.patch(_CAPTIONS, json={"tags_suffix": "_z.txt"}).status_code == 200
    assert len(rescans) == 2, "a different suffix names files none of which are read"


def test_a_pending_description_is_never_written_into_a_caption_file(env):
    """A queued description is stored as the internal `__description::` marker
    until the engine runs. It is PixlStash's own bookkeeping, not something the
    owner wrote, so neither the scan's export nor the write-back after a tag
    edit may put it in a file beside their picture - the same exclusion the tag
    sentinels already get."""
    from pixlstash.db_models.tag import make_description_sentinel

    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "pending", "f.png"), (61, 62, 63))
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=True,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )
    _run_root_scan(server)
    pic_id, _, _, _ = _picture(server, "pending/f.png")

    def queue_a_description(session: Session):
        pic = session.get(Picture, pic_id)
        pic.description = make_description_sentinel("joycaption")
        session.add(pic)
        session.commit()

    server.vault.db.run_task(queue_a_description)
    caption_file = os.path.join(root, "pending", "f_caption.txt")

    _run_root_scan(server)
    assert not os.path.exists(caption_file), "the reconcile exports no marker"

    _set_tags(server, pic_id, "kite")
    sync_picture_sidecar(server, pic_id)
    with open(os.path.join(root, "pending", "f_tags.txt"), encoding="utf-8") as fh:
        assert fh.read().strip() == "kite", "the tag edit did reach its own file"
    assert not os.path.exists(caption_file), "and still wrote no description file"

    _, description, _, _ = _picture(server, "pending/f.png")
    assert description == make_description_sentinel("joycaption"), "row untouched"


def _recorded(server, rel):
    """``(tags_file_mtime, description_file_mtime, description)`` for a picture."""

    def read(session: Session):
        pic = session.exec(select(Picture).where(Picture.file_path == rel)).first()
        return pic.tags_file_mtime, pic.description_file_mtime, pic.description

    return server.vault.db.run_immediate_read_task(read)


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod 000 does not deny reads to Windows or to root",
)
def test_an_unreadable_tags_file_keeps_the_tags_and_retries_next_scan(env):
    """A read that FAILED is not the owner clearing their sidecar. The read
    used to collapse an OSError into the same `[]` an empty file gives, so a
    permission blip on a recorded tags file deleted every tag AND stored the
    new mtime - which made it permanent, because the next scan compares mtimes
    and never reads the file again."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "denied", "g.png"), (71, 72, 73))
    tags_file = os.path.join(root, "denied", "g_tags.txt")
    _write(tags_file, "harbour, dusk")
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=False,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )
    _run_root_scan(server)
    _, _, recorded_path, tags = _picture(server, "denied/g.png")
    assert tags == ["dusk", "harbour"]
    assert recorded_path == tags_file
    recorded_mtime, _, _ = _recorded(server, "denied/g.png")

    # The owner edits the file and its permissions go with it.
    _write(tags_file, "quay, night")
    os.utime(tags_file, (recorded_mtime + 60, recorded_mtime + 60))
    os.chmod(tags_file, 0o000)
    try:
        _run_root_scan(server)
        _, _, _, tags_after = _picture(server, "denied/g.png")
        assert tags_after == ["dusk", "harbour"], "an unreadable file clears nothing"
        assert _recorded(server, "denied/g.png")[0] == recorded_mtime, (
            "and the recorded mtime is untouched, so the next scan retries"
        )

        os.chmod(tags_file, 0o644)
        _run_root_scan(server)
        _, _, _, tags_retried = _picture(server, "denied/g.png")
        assert tags_retried == ["night", "quay"], "the retry reads the edit in"
    finally:
        os.chmod(tags_file, 0o644)


def test_emptying_a_recorded_description_file_clears_the_description(env):
    """The inverse hole: a description read gave `None` for an empty file and
    for an unreadable one alike, and the apply step skipped `None`, so the
    owner emptying a sidecar they own left the stored description in place."""
    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "cleared", "h.png"), (74, 75, 76))
    caption_file = os.path.join(root, "cleared", "h_caption.txt")
    _write(caption_file, "A lighthouse at dusk.")
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=True,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )
    _run_root_scan(server)
    _, description, _, _ = _picture(server, "cleared/h.png")
    assert description == "A lighthouse at dusk."
    recorded_mtime = _recorded(server, "cleared/h.png")[1]

    _write(caption_file, "")
    os.utime(caption_file, (recorded_mtime + 60, recorded_mtime + 60))
    _run_root_scan(server)
    _, description_after, _, _ = _picture(server, "cleared/h.png")
    assert description_after is None, "the owner emptied their own sidecar"


class _FixedDescriptionWorkflow:
    """Answers every picture in the batch with one machine caption."""

    CAPTION = "A machine-generated caption."

    def generate_batch(self, pictures, engine_override=None, stop_event=None):
        return {pic.id: self.CAPTION for pic in pictures}

    def estimate_vram_mb(self, image_count, plugin_name=None):
        return 0


def test_the_tagger_drops_a_result_for_a_row_the_scan_filled_meanwhile(env):
    """The tagger claims a picture by its pending sentinel and hands the GPU
    back seconds later, and `_add_tags_bulk` deletes every Tag row before
    writing. If the root scan imported the owner's tags file in between, the
    unconditional write replaced what they wrote. The claim has to still be
    there at completion; an empty file puts the sentinel back, so a picture
    with genuinely nothing in its sidecar is still tagged."""
    from pixlstash.db_models.tag import TAG_PENDING_SENTINEL
    from pixlstash.tasks.tag_task import TagTask

    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "claimed", "i.png"), (81, 82, 83))
    _make_image(os.path.join(root, "claimed", "j.png"), (84, 85, 86))
    _set_sync(
        server,
        sync_tags=True,
        sync_descriptions=False,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )
    _run_root_scan(server)
    filled_id, _, _, _ = _picture(server, "claimed/i.png")
    empty_id, _, _, _ = _picture(server, "claimed/j.png")

    # Both pictures are claimed: this is exactly what MissingTagFinder selects.
    _set_tags(server, filled_id, TAG_PENDING_SENTINEL)
    _set_tags(server, empty_id, TAG_PENDING_SENTINEL)

    # The scan lands while the GPU is busy. One owner file has tags, one is empty.
    _write(os.path.join(root, "claimed", "i_tags.txt"), "harbour, dusk")
    _write(os.path.join(root, "claimed", "j_tags.txt"), "")
    _run_root_scan(server)
    assert _picture(server, "claimed/i.png")[3] == ["dusk", "harbour"]

    # The tagger now completes with what it inferred before the scan ran.
    server.vault.db.run_task(
        lambda session: TagTask._add_tags_bulk(
            session,
            [
                {"pic_id": filled_id, "tags": ["cat", "dog"]},
                {"pic_id": empty_id, "tags": ["cat", "dog"]},
            ],
        )
    )

    assert _picture(server, "claimed/i.png")[3] == ["dusk", "harbour"], (
        "the owner's caption file is the authority; the tagger's result is dropped"
    )
    assert _picture(server, "claimed/j.png")[3] == ["cat", "dog"], (
        "an empty sidecar restores the sentinel, so the tagger still writes"
    )


def test_the_describer_drops_a_caption_for_a_row_the_scan_filled_meanwhile(env):
    """The same race on the description side, where the claim is a NULL
    description or a `__description::` sentinel."""
    from pixlstash.db_models.tag import make_description_sentinel
    from pixlstash.tasks.description_task import DescriptionTask
    from types import SimpleNamespace

    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "claimed", "k.png"), (87, 88, 89))
    _make_image(os.path.join(root, "claimed", "l.png"), (90, 91, 92))
    _set_sync(
        server,
        sync_tags=False,
        sync_descriptions=True,
        tags_suffix="_tags.txt",
        description_suffix="_caption.txt",
    )
    _run_root_scan(server)
    filled_id, _, _, _ = _picture(server, "claimed/k.png")
    untouched_id, _, _, _ = _picture(server, "claimed/l.png")

    def claim(session: Session):
        session.get(Picture, filled_id).description = make_description_sentinel(None)
        session.get(Picture, untouched_id).description = None
        session.commit()

    server.vault.db.run_task(claim)

    # The scan lands while the GPU is busy; only one picture has an owner file.
    _write(os.path.join(root, "claimed", "k_caption.txt"), "A lighthouse at dusk.")
    _run_root_scan(server)
    assert _picture(server, "claimed/k.png")[1] == "A lighthouse at dusk."

    DescriptionTask(
        server.vault.db,
        _FixedDescriptionWorkflow(),
        [
            SimpleNamespace(id=filled_id, description=None),
            SimpleNamespace(id=untouched_id, description=None),
        ],
    )._run_task()

    assert _picture(server, "claimed/k.png")[1] == "A lighthouse at dusk.", (
        "the owner's caption file is the authority; the generated one is dropped"
    )
    assert _picture(server, "claimed/l.png")[1] == _FixedDescriptionWorkflow.CAPTION, (
        "a picture still awaiting a description is captioned as before"
    )


def test_a_sidecar_edit_adding_an_anomaly_tag_clears_the_cached_smart_score(env):
    """An applied ``Tag`` row is an input to the scorer's anomaly penalty, and
    ``SmartScoreTask`` only picks up pictures whose score is NULL. The reconcile
    pass replaces tags directly, so without the ``invalidate_on_anomaly_change``
    wrapper an on-disk caption edit leaves the cached score stale."""
    from datetime import datetime
    from pixlstash.db_models.tag_prediction import TagPrediction

    server = env["server"]
    root = server.vault.image_root
    _make_image(os.path.join(root, "score", "m.png"), (93, 94, 95))
    _make_image(os.path.join(root, "score", "n.png"), (96, 97, 98))
    _write(os.path.join(root, "score", "m.txt"), "cat")
    _write(os.path.join(root, "score", "n.txt"), "cat")
    _set_sync(server, sync_tags=True, sync_descriptions=False, tags_suffix=".txt")
    _run_root_scan(server)
    anomaly_id, _, _, _ = _picture(server, "score/m.png")
    content_id, _, _, _ = _picture(server, "score/n.png")

    def seed(session: Session):
        # "watermark" is in the anomaly vocabulary; the scorer charges the
        # prediction only once the defect is visible in the tag list.
        session.add(
            TagPrediction(
                picture_id=anomaly_id,
                tag="watermark",
                confidence=0.9,
                model_version="test-v1",
                status="PENDING",
                predicted_at=datetime.utcnow(),
            )
        )
        session.get(Picture, anomaly_id).smart_score = 0.5
        session.get(Picture, content_id).smart_score = 0.5
        session.commit()

    server.vault.db.run_task(seed)

    later = time.time() + 5
    for name, text in (("m.txt", "cat, watermark"), ("n.txt", "cat, sunset")):
        _write(os.path.join(root, "score", name), text)
        os.utime(os.path.join(root, "score", name), (later, later))
    _run_root_scan(server)

    def scores(session: Session):
        return (
            session.get(Picture, anomaly_id).smart_score,
            session.get(Picture, content_id).smart_score,
        )

    anomaly_score, content_score = server.vault.db.run_immediate_read_task(scores)
    assert _picture(server, "score/m.png")[3] == ["cat", "watermark"]
    assert anomaly_score is None, "the anomaly tag arrived; the cached score is stale"
    assert content_score == 0.5, "a content-only edit keeps the stored score"

"""Tests for the split tags/description sidecar feature on reference folders.

Covers the pure caption-file utilities (classification, resolution, detection)
and the end-to-end scan/export/write-back behaviour through a real Server:

- a scan reads tags and descriptions from separate sidecar files;
- enabling sync exports a picture's tags/descriptions to new sidecar files;
- empty content never creates a sidecar;
- tag write-back updates only the tags sidecar, leaving the description alone.
"""

import os
import tempfile

import pytest
from PIL import Image
from sqlmodel import Session, delete, select

from pixlstash.db_models import Picture, ReferenceFolder, Tag
from pixlstash.server import Server
from pixlstash.utils.caption_file_utils import (
    classify_sidecar,
    detect_folder_suffixes,
    resolve_typed_sidecar,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.caption_utils import sync_picture_sidecar


# ---------------------------------------------------------------------------
# Pure utility tests (no Server needed)
# ---------------------------------------------------------------------------


def _write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def test_classify_sidecar_by_name(tmp_path):
    tags = tmp_path / "a_tags.txt"
    desc = tmp_path / "a_description.txt"
    caption = tmp_path / "a.caption"
    _write(str(tags), "anything at all")
    _write(str(desc), "1girl, solo")  # name wins over content
    _write(str(caption), "free text")
    assert classify_sidecar(str(tags)) == "tags"
    assert classify_sidecar(str(desc)) == "description"
    assert classify_sidecar(str(caption)) == "description"


def test_classify_bare_txt_by_content(tmp_path):
    tags_like = tmp_path / "a.txt"
    prose_like = tmp_path / "b.txt"
    _write(str(tags_like), "1girl, solo, long hair, looking at viewer, smile")
    _write(
        str(prose_like),
        "A young woman with long hair stands in a sunlit field, smiling at the camera.",
    )
    assert classify_sidecar(str(tags_like)) == "tags"
    assert classify_sidecar(str(prose_like)) == "description"


def test_resolve_typed_sidecar_routes_bare_txt(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"x")
    _write(str(tmp_path / "photo.txt"), "cat, dog, tree")
    # The bare .txt classifies as tags, so it resolves for tags and not for descriptions.
    assert resolve_typed_sidecar(str(img), "tags", None) == str(tmp_path / "photo.txt")
    assert resolve_typed_sidecar(str(img), "description", None) is None
    # An explicit suffix is matched exactly (and not found here).
    assert resolve_typed_sidecar(str(img), "tags", "_tags.txt") is None


def test_detect_folder_suffixes(tmp_path):
    for stem in ("one", "two", "three"):
        (tmp_path / f"{stem}.png").write_bytes(b"x")
        _write(str(tmp_path / f"{stem}_tags.txt"), "a, b, c")
        _write(str(tmp_path / f"{stem}_description.txt"), "A sentence about it.")
    result = detect_folder_suffixes(str(tmp_path))
    assert result["tags_suffix"] == "_tags.txt"
    assert result["description_suffix"] == "_description.txt"
    assert result["found_tags"] is True
    assert result["found_descriptions"] is True


def test_detect_folder_suffixes_empty(tmp_path):
    (tmp_path / "lonely.png").write_bytes(b"x")
    result = detect_folder_suffixes(str(tmp_path))
    assert result == {
        "tags_suffix": None,
        "description_suffix": None,
        "found_tags": False,
        "found_descriptions": False,
    }


# ---------------------------------------------------------------------------
# Server-backed scan / export / write-back tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_folders(server):
    """Wipe reference folders / pictures / tags before each test."""

    def _wipe(session: Session):
        session.exec(delete(Tag))
        session.exec(delete(Picture))
        session.exec(delete(ReferenceFolder))
        session.commit()

    server.vault.db.run_task(_wipe)
    yield


def _make_folder(server, folder_dir, **kwargs):
    os.makedirs(folder_dir, exist_ok=True)

    def _insert(session: Session):
        folder = ReferenceFolder(
            folder=folder_dir, label="refs", status="active", **kwargs
        )
        session.add(folder)
        session.commit()
        session.refresh(folder)
        return folder.id

    return server.vault.db.run_task(_insert)


def _make_image(folder_dir, file_name):
    path = os.path.join(folder_dir, file_name)
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(path, format="PNG")
    return path


def _index_picture(server, folder_id, file_path, *, tags=None, description=None):
    pixel_sha = ImageUtils.calculate_hash_from_file_path(file_path)

    def _insert(session: Session):
        pic = Picture(
            file_path=file_path,
            reference_folder_id=folder_id,
            pixel_sha=pixel_sha,
            format="PNG",
            width=8,
            height=8,
            original_file_name=os.path.basename(file_path),
            description=description,
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        for tag in tags or []:
            session.add(Tag(picture_id=pic.id, tag=tag))
        session.commit()
        return pic.id

    return server.vault.db.run_task(_insert)


def _run_scan(server, folder_id, folder_dir):
    from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask

    task = ReferenceFolderScanTask(
        database=server.vault.db,
        folder_id=folder_id,
        folder_path=folder_dir,
        resolved_path=folder_dir,
    )
    return task._run_task()


def _picture_tags(server, pic_id):
    rows = server.vault.db.run_task(
        lambda s: s.exec(select(Tag).where(Tag.picture_id == pic_id)).all()
    )
    return sorted(t.tag for t in rows if not t.tag.startswith("__"))


def _picture(server, pic_id):
    return server.vault.db.run_task(lambda s: s.get(Picture, pic_id))


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_scan_reads_separate_tags_and_description_sidecars(server, tmp_path):
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    img = _make_image(folder_dir, "cat.png")
    _write(os.path.splitext(img)[0] + "_tags.txt", "cat, black cat, whiskers")
    _write(
        os.path.splitext(img)[0] + "_description.txt",
        "A black cat sitting on a windowsill.",
    )

    result = _run_scan(server, folder_id, folder_dir)
    assert result["new_count"] == 1

    pic_id = server.vault.db.run_task(
        lambda s: s.exec(
            select(Picture.id).where(Picture.reference_folder_id == folder_id)
        ).first()
    )
    assert _picture_tags(server, pic_id) == ["black cat", "cat", "whiskers"]
    pic = _picture(server, pic_id)
    assert pic.description == "A black cat sitting on a windowsill."
    assert pic.tags_file.endswith("_tags.txt")
    assert pic.description_file.endswith("_description.txt")


def test_scan_exports_missing_sidecars_when_sync_enabled(server, tmp_path):
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, sync_tags=True, sync_descriptions=True)
    img = _make_image(folder_dir, "dog.png")
    pic_id = _index_picture(
        server,
        folder_id,
        img,
        tags=["dog", "puppy"],
        description="A happy puppy.",
    )

    _run_scan(server, folder_id, folder_dir)

    tags_path = os.path.splitext(img)[0] + "_tags.txt"
    desc_path = os.path.splitext(img)[0] + "_description.txt"
    assert os.path.isfile(tags_path), "tags sidecar should be exported"
    assert os.path.isfile(desc_path), "description sidecar should be exported"
    assert _read(tags_path) == "dog, puppy"
    assert _read(desc_path) == "A happy puppy."

    pic = _picture(server, pic_id)
    assert pic.tags_file == tags_path
    assert pic.description_file == desc_path


def test_scan_does_not_create_empty_sidecars(server, tmp_path):
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, sync_tags=True, sync_descriptions=True)
    img = _make_image(folder_dir, "blank.png")
    _index_picture(server, folder_id, img, tags=[], description=None)

    _run_scan(server, folder_id, folder_dir)

    assert not os.path.isfile(os.path.splitext(img)[0] + "_tags.txt")
    assert not os.path.isfile(os.path.splitext(img)[0] + "_description.txt")


def test_write_back_updates_tags_sidecar_only(server, tmp_path):
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, sync_tags=True, sync_descriptions=True)
    img = _make_image(folder_dir, "bird.png")
    pic_id = _index_picture(
        server, folder_id, img, tags=["bird", "blue"], description="A blue bird."
    )

    # First write-back creates both sidecars.
    sync_picture_sidecar(server, pic_id)
    tags_path = os.path.splitext(img)[0] + "_tags.txt"
    desc_path = os.path.splitext(img)[0] + "_description.txt"
    assert _read(tags_path) == "bird, blue"
    assert _read(desc_path) == "A blue bird."
    desc_mtime = os.stat(desc_path).st_mtime

    # Change only the tags, then write-back again.
    def _retag(session: Session):
        session.exec(delete(Tag).where(Tag.picture_id == pic_id))
        session.add(Tag(picture_id=pic_id, tag="bird"))
        session.commit()

    server.vault.db.run_task(_retag)
    sync_picture_sidecar(server, pic_id)

    assert _read(tags_path) == "bird"
    assert _read(desc_path) == "A blue bird."  # untouched
    assert os.stat(desc_path).st_mtime == desc_mtime

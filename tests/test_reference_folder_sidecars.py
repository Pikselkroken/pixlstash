"""Tests for the split tags/description sidecar feature on reference folders.

Covers the pure caption-file utilities (classification, resolution, detection)
and the end-to-end scan/export/write-back behaviour through a real Server:

- a scan reads tags and descriptions from separate sidecar files;
- enabling sync exports a picture's tags/descriptions to new sidecar files;
- empty content never creates a sidecar;
- tag write-back updates only the tags sidecar, leaving the description alone.
"""

import os
import shutil
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, delete, select

from pixlstash.db_models import Picture, ReferenceFolder, Tag
from pixlstash.routes.reference_folders import _validate_sidecar_suffix
from pixlstash.server import Server
from pixlstash.utils.caption_file_utils import (
    classify_sidecar,
    detect_folder_suffixes,
    resolve_typed_sidecar,
    sidecar_path,
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
# Sidecar suffix is a path component — it must not allow traversal (CWE-22)
# ---------------------------------------------------------------------------


def test_sidecar_path_allows_clean_suffix():
    assert sidecar_path("/refs/f/photo.png", "_tags.txt") == "/refs/f/photo_tags.txt"
    assert sidecar_path("/refs/f/photo.png", ".caption") == "/refs/f/photo.caption"
    assert sidecar_path("/refs/f/photo.png", ".txt") == "/refs/f/photo.txt"


@pytest.mark.parametrize(
    "evil_suffix",
    [
        "_t.txt/../../../../etc/cron.d/evil",
        "_t.txt/../../sibling.txt",
        "/../../etc/passwd",
        "../escape.txt",
    ],
)
def test_sidecar_path_rejects_traversal(evil_suffix):
    """A suffix that would redirect the write outside the image's own directory
    must be rejected at the sink (defence in depth behind the API validator)."""
    with pytest.raises(ValueError):
        sidecar_path("/refs/f/photo.png", evil_suffix)


def test_validate_sidecar_suffix_accepts_known_conventions():
    for good in ("_tags.txt", "_description.txt", "_wd14.txt", ".caption", ".txt"):
        _validate_sidecar_suffix(good)  # must not raise


@pytest.mark.parametrize(
    "evil_suffix",
    [
        "_t.txt/../../../../etc/cron.d/evil",
        "../escape.txt",
        "a/b.txt",
        "a\\b.txt",
        "..",
        "_t..txt",
        "x" * 65,  # over the length cap
        "_t .txt",  # space is not allowed
    ],
)
def test_validate_sidecar_suffix_rejects_dangerous(evil_suffix):
    """The API trust-boundary validator rejects anything that is not a bare
    filename fragment, returning a 400 rather than storing a traversal path."""
    with pytest.raises(HTTPException) as exc:
        _validate_sidecar_suffix(evil_suffix)
    assert exc.value.status_code == 400


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


def _set_picture_sidecars(server, pic_id, *, tags_file=None, description_file=None):
    def _update(session: Session):
        pic = session.get(Picture, pic_id)
        if tags_file is not None:
            pic.tags_file = tags_file
            pic.tags_file_mtime = os.stat(tags_file).st_mtime
        if description_file is not None:
            pic.description_file = description_file
            pic.description_file_mtime = os.stat(description_file).st_mtime
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_update)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _login_client(server):
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return client


def _make_read_token(client, *, resource_type=None, resource_id=None):
    payload = {"description": "read-only", "scope": "READ"}
    if resource_type is not None:
        payload["resource_type"] = resource_type
        payload["resource_id"] = resource_id
    resp = client.post(
        "/users/me/token",
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


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


def test_move_reference_picture_moves_file_sidecars_and_updates_row(server, tmp_path):
    client = _login_client(server)
    source_dir = str(tmp_path / "source")
    dest_dir = str(tmp_path / "dest")
    source_folder_id = _make_folder(
        server, source_dir, sync_tags=True, sync_descriptions=True
    )
    dest_folder_id = _make_folder(server, dest_dir)
    img = _make_image(source_dir, "cat.png")
    tags_path = os.path.splitext(img)[0] + "_tags.txt"
    desc_path = os.path.splitext(img)[0] + "_description.txt"
    _write(tags_path, "cat, calm")
    _write(desc_path, "A calm cat.")
    pic_id = _index_picture(
        server,
        source_folder_id,
        img,
        tags=["cat", "calm"],
        description="A calm cat.",
    )
    _set_picture_sidecars(
        server,
        pic_id,
        tags_file=tags_path,
        description_file=desc_path,
    )

    resp = client.post(
        f"/reference-folders/{dest_folder_id}/move-pictures",
        json={"picture_ids": [pic_id]},
    )
    assert resp.status_code == 200
    assert resp.json()["moved_count"] == 1

    moved_img = os.path.join(dest_dir, "cat.png")
    assert os.path.isfile(moved_img)
    assert os.path.isfile(os.path.join(dest_dir, "cat_tags.txt"))
    assert os.path.isfile(os.path.join(dest_dir, "cat_description.txt"))
    assert not os.path.exists(img)
    pic = _picture(server, pic_id)
    assert pic.reference_folder_id == dest_folder_id
    assert pic.file_path == moved_img
    assert pic.tags_file == os.path.join(dest_dir, "cat_tags.txt")
    assert pic.description_file == os.path.join(dest_dir, "cat_description.txt")


def test_move_reference_picture_auto_renames_on_collision(server, tmp_path):
    client = _login_client(server)
    source_dir = str(tmp_path / "source")
    dest_dir = str(tmp_path / "dest")
    source_folder_id = _make_folder(server, source_dir)
    dest_folder_id = _make_folder(server, dest_dir)
    img = _make_image(source_dir, "same.png")
    _make_image(dest_dir, "same.png")
    pic_id = _index_picture(server, source_folder_id, img)

    resp = client.post(
        f"/reference-folders/{dest_folder_id}/move-pictures",
        json={"picture_ids": [pic_id]},
    )
    assert resp.status_code == 200
    pic = _picture(server, pic_id)
    assert pic.file_path == os.path.join(dest_dir, "same_1.png")
    assert os.path.isfile(pic.file_path)


def test_move_reference_picture_rejects_non_reference_picture(server, tmp_path):
    client = _login_client(server)
    dest_dir = str(tmp_path / "dest")
    dest_folder_id = _make_folder(server, dest_dir)
    standalone = _make_image(str(tmp_path), "loose.png")
    pic_id = _index_picture(server, None, standalone)

    resp = client.post(
        f"/reference-folders/{dest_folder_id}/move-pictures",
        json={"picture_ids": [pic_id]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["failures"][0]["reason"] == "not_reference_picture"


def test_relocate_reference_folder_moves_contents_and_updates_rows(
    server, tmp_path, monkeypatch
):
    monkeypatch.setattr(server, "running_in_docker", lambda: False)
    client = _login_client(server)
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    sub = old_root / "sub"
    sub.mkdir(parents=True)
    folder_id = _make_folder(server, str(old_root))
    img = _make_image(str(sub), "bird.png")
    tags_path = sub / "bird_tags.txt"
    _write(str(tags_path), "bird, blue")
    (old_root / "notes.md").write_text("keep me", encoding="utf-8")
    pic_id = _index_picture(server, folder_id, img, tags=["bird", "blue"])
    _set_picture_sidecars(server, pic_id, tags_file=str(tags_path))

    resp = client.post(
        f"/reference-folders/{folder_id}/relocate",
        json={"destination_folder": str(new_root)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["old_folder"] == str(old_root)
    assert body["new_folder"] == str(new_root)
    assert body["rewritten_count"] == 1
    assert body["moved_picture_ids"] == [pic_id]

    assert not os.path.exists(img)
    assert (new_root / "sub" / "bird.png").is_file()
    assert (new_root / "sub" / "bird_tags.txt").is_file()
    assert (new_root / "notes.md").read_text(encoding="utf-8") == "keep me"

    pic = _picture(server, pic_id)
    assert pic.file_path == str(new_root / "sub" / "bird.png")
    assert pic.tags_file == str(new_root / "sub" / "bird_tags.txt")
    folder = server.vault.db.run_task(lambda s: s.get(ReferenceFolder, folder_id))
    assert folder.folder == str(new_root)


def test_relocate_reference_folder_rejects_non_empty_destination(
    server, tmp_path, monkeypatch
):
    monkeypatch.setattr(server, "running_in_docker", lambda: False)
    client = _login_client(server)
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    folder_id = _make_folder(server, str(old_root))
    _make_image(str(old_root), "cat.png")
    new_root.mkdir()
    (new_root / "existing.txt").write_text("occupied", encoding="utf-8")

    resp = client.post(
        f"/reference-folders/{folder_id}/relocate",
        json={"destination_folder": str(new_root)},
    )
    assert resp.status_code == 409
    assert os.path.isfile(old_root / "cat.png")


def test_reference_folder_file_operations_reject_read_tokens(server, tmp_path):
    owner_client = _login_client(server)
    token_client = TestClient(server.api)
    source_dir = str(tmp_path / "source")
    dest_dir = str(tmp_path / "dest")
    folder_id = _make_folder(server, source_dir)
    dest_id = _make_folder(server, dest_dir)
    img = _make_image(source_dir, "cat.png")
    pic_id = _index_picture(server, folder_id, img)
    read_token = _make_read_token(
        owner_client,
        resource_type="picture",
        resource_id=pic_id,
    )
    headers = {"Authorization": f"Bearer {read_token}"}

    move_resp = token_client.post(
        f"/reference-folders/{dest_id}/move-pictures",
        json={"picture_ids": [pic_id]},
        headers=headers,
    )
    assert move_resp.status_code == 403

    relocate_resp = token_client.post(
        f"/reference-folders/{folder_id}/relocate",
        json={"destination_folder": str(tmp_path / "new-root")},
        headers=headers,
    )
    assert relocate_resp.status_code == 403

    mkdir_resp = token_client.post(
        "/filesystem/folders",
        json={"path": str(tmp_path / "new-folder")},
        headers=headers,
    )
    assert mkdir_resp.status_code == 403


def test_relocate_reference_folder_preserves_relative_paths_and_hash(server, tmp_path):
    client = _login_client(server)
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    sub = old_root / "sub"
    sub.mkdir(parents=True)
    (new_root / "sub").mkdir(parents=True)
    folder_id = _make_folder(server, str(old_root))
    img = _make_image(str(sub), "bird.png")
    _write(str(sub / "bird_tags.txt"), "bird, blue")
    pic_id = _index_picture(server, folder_id, img, tags=["bird", "blue"])
    _set_picture_sidecars(server, pic_id, tags_file=str(sub / "bird_tags.txt"))
    shutil.move(img, str(new_root / "sub" / "bird.png"))
    shutil.move(str(sub / "bird_tags.txt"), str(new_root / "sub" / "bird_tags.txt"))

    resp = client.patch(
        f"/reference-folders/{folder_id}",
        json={"folder": str(new_root), "label": "moved"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["folder"] == str(new_root)
    assert body["relocation"]["rewritten_count"] == 1
    assert body["relocation"]["missing_count"] == 0
    assert body["relocation"]["unmatched_count"] == 0
    pic = _picture(server, pic_id)
    assert pic.file_path == str(new_root / "sub" / "bird.png")
    assert pic.tags_file == str(new_root / "sub" / "bird_tags.txt")


def test_metadata_export_and_import_tags_and_descriptions_by_subfolder(
    server, tmp_path
):
    client = _login_client(server)
    folder_dir = str(tmp_path / "refs")
    sub_dir = os.path.join(folder_dir, "sub")
    os.makedirs(sub_dir, exist_ok=True)
    folder_id = _make_folder(server, folder_dir)
    in_scope_img = _make_image(sub_dir, "in.png")
    out_scope_img = _make_image(folder_dir, "out.png")
    in_pic_id = _index_picture(
        server,
        folder_id,
        in_scope_img,
        tags=["first", "tag"],
        description="First description.",
    )
    out_pic_id = _index_picture(
        server,
        folder_id,
        out_scope_img,
        tags=["outside"],
        description="Outside description.",
    )

    export_resp = client.post(
        f"/reference-folders/{folder_id}/metadata/export",
        json={"scope_path": sub_dir, "types": ["tags", "descriptions"]},
    )
    assert export_resp.status_code == 200
    assert export_resp.json()["tags_count"] == 1
    assert export_resp.json()["descriptions_count"] == 1
    assert _read(os.path.join(sub_dir, "in_tags.txt")) == "first, tag"
    assert _read(os.path.join(sub_dir, "in_description.txt")) == "First description."
    assert not os.path.exists(os.path.join(folder_dir, "out_tags.txt"))

    _write(os.path.join(sub_dir, "in_tags.txt"), "changed, tags")
    _write(os.path.join(sub_dir, "in_description.txt"), "Changed description.")
    import_resp = client.post(
        f"/reference-folders/{folder_id}/metadata/import",
        json={"scope_path": sub_dir, "types": ["tags", "descriptions"]},
    )
    assert import_resp.status_code == 200
    assert _picture_tags(server, in_pic_id) == ["changed", "tags"]
    assert _picture(server, in_pic_id).description == "Changed description."
    assert _picture_tags(server, out_pic_id) == ["outside"]

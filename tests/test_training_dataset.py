"""Tests for the ai-toolkit training dataset builder (ExportUtils.build_training_dataset).

These mirror the export-API test fixtures (real server + real uploads) but call
the dataset builder directly, since it is a trusted server-side helper with no
HTTP route of its own.
"""

import gc
import glob
import hashlib
import json
import os
import shutil
import tempfile

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.db_models import Face, Picture, Tag
from pixlstash.server import Server
from pixlstash.utils.service.export_utils import ExportUtils
from tests.utils import quiesce_background_work, upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _upload(client, filename):
    img_path = os.path.join(PICTURES_DIR, filename)
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(
            client, [("file", (filename, f, "image/png"))]
        )
    assert result["status"] == "completed", result
    return result["results"][0]["picture_id"]


def _create_set(client, name):
    resp = client.post("/picture_sets", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["picture_set"]["id"]


def _add_to_set(client, set_id, pic_id):
    resp = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
    assert resp.status_code == 200, resp.text


def _assert_pairs(dest_dir, expected_count, trigger_word=None):
    """Assert one .txt per image, contiguous image_NNNNN naming, trigger leading."""
    files = sorted(os.listdir(dest_dir))
    images = [f for f in files if not f.endswith(".txt")]
    captions = [f for f in files if f.endswith(".txt")]

    assert len(images) == expected_count, (
        f"Expected {expected_count} images, got {images}"
    )
    assert len(captions) == expected_count, (
        f"Expected {expected_count} captions, got {captions}"
    )

    for img in images:
        stem = os.path.splitext(img)[0]
        assert f"{stem}.txt" in captions, f"Missing sidecar for {img}"
        # Naming is image_00001, image_00002, ... (the folder export writer)
        assert stem.startswith("image_"), f"Unexpected stem {stem}"

    if trigger_word is not None:
        for cap in captions:
            with open(os.path.join(dest_dir, cap), encoding="utf-8") as fh:
                text = fh.read().strip()
            assert text.split(",")[0].strip() == trigger_word, (
                f"Caption {cap!r} did not lead with trigger word: {text!r}"
            )


@pytest.fixture(scope="module")
def env():
    """One server for the module: sets and characters are per test, so they never clash."""
    temp_dir, client, server = _setup()
    yield client, server
    server.close()
    temp_dir.cleanup()
    gc.collect()


def test_build_training_dataset_from_set(env):
    client, server = env
    set_id = _create_set(client, "TrainSet")
    pic_ids = [
        _upload(client, "Bad1.png"),
        _upload(client, "Bad2.png"),
    ]
    for pid in pic_ids:
        _add_to_set(client, set_id, pid)

    with tempfile.TemporaryDirectory() as dest_dir:
        manifest = ExportUtils.build_training_dataset(
            server,
            dest_dir=dest_dir,
            set_id=set_id,
            trigger_word="mychar",
        )

        assert manifest["image_count"] == 2
        assert manifest["caption_mode"] == "tags"
        assert manifest["trigger_word"] == "mychar"
        assert manifest["dest_dir"] == dest_dir
        assert set(manifest["picture_ids"]) == set(pic_ids)

        _assert_pairs(dest_dir, expected_count=2, trigger_word="mychar")

        # The snapshot items (#2123) are the files written, read back: each
        # picture's own file, the sha256 of its bytes and its caption.
        items = manifest["items"]
        assert [item["picture_id"] for item in items] == manifest["picture_ids"]
        for item in items:
            path = os.path.join(dest_dir, item["file_name"])
            with open(path, "rb") as fh:
                assert item["sha256"] == hashlib.sha256(fh.read()).hexdigest()
            stem = os.path.splitext(item["file_name"])[0]
            with open(os.path.join(dest_dir, f"{stem}.txt"), encoding="utf-8") as fh:
                assert item["caption"] == fh.read().rstrip("\n")
            assert item["caption"].startswith("mychar")


def test_build_training_dataset_from_character(env):
    client, server = env
    resp = client.post("/characters", json={"name": "Alice"})
    assert resp.status_code == 200, resp.text
    char_id = int(resp.json()["character"]["id"])

    pic_id = _upload(client, "Reference1.png")
    other_id = _upload(client, "Reference2.png")  # NOT assigned to the character

    # Assign a face on pic_id to the character.
    server.vault.db.run_task(
        lambda session: (
            session.add(
                Face(
                    picture_id=pic_id,
                    # Background face extraction may already hold index 0.
                    face_index=99,
                    bbox=[1, 2, 3, 4],
                    character_id=char_id,
                )
            ),
            session.commit(),
        )
    )

    with tempfile.TemporaryDirectory() as dest_dir:
        manifest = ExportUtils.build_training_dataset(
            server,
            dest_dir=dest_dir,
            character_id=char_id,
            trigger_word="alicelora",
        )

        assert manifest["image_count"] == 1
        assert manifest["picture_ids"] == [pic_id]
        assert other_id not in manifest["picture_ids"]

        _assert_pairs(dest_dir, expected_count=1, trigger_word="alicelora")


def test_build_training_dataset_skips_videos(env):
    client, server = env
    set_id = _create_set(client, "VideoSet")

    # One real image in the set.
    img_id = _upload(client, "Bad1.png")
    _add_to_set(client, set_id, img_id)

    # One on-disk video file + a Picture row pointing at it, added to the set.
    image_root = server.vault.image_root
    video_rel = "fake_clip.mp4"
    video_abs = os.path.join(image_root, video_rel)
    with open(video_abs, "wb") as fh:
        fh.write(b"\x00\x00\x00\x18ftypmp42")  # minimal bytes; ext drives skip

    def _insert_video(session):
        pic = Picture(
            file_path=video_rel,
            imported_at=datetime.now(timezone.utc),
        )
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic.id

    video_pic_id = server.vault.db.run_task(_insert_video)
    _add_to_set(client, set_id, video_pic_id)

    with tempfile.TemporaryDirectory() as dest_dir:
        manifest = ExportUtils.build_training_dataset(
            server,
            dest_dir=dest_dir,
            set_id=set_id,
            trigger_word="mychar",
        )

        # Video is skipped: only the single image is written, and the count
        # a job is resolved against at enqueue skips it the same way.
        assert manifest["image_count"] == 1
        assert ExportUtils.count_training_pictures(server, set_id=set_id) == 1
        assert manifest["picture_ids"] == [img_id]
        assert video_pic_id not in manifest["picture_ids"]

        _assert_pairs(dest_dir, expected_count=1, trigger_word="mychar")
        # No .mp4 leaked into the dataset.
        assert not glob.glob(os.path.join(dest_dir, "*.mp4"))


def test_build_training_dataset_no_trigger_word(env):
    client, server = env
    set_id = _create_set(client, "NoTriggerSet")
    pic_id = _upload(client, "Bad1.png")
    _add_to_set(client, set_id, pic_id)

    with tempfile.TemporaryDirectory() as dest_dir:
        manifest = ExportUtils.build_training_dataset(
            server,
            dest_dir=dest_dir,
            set_id=set_id,
        )

        assert manifest["image_count"] == 1
        assert manifest["trigger_word"] is None
        # One pair, and no trigger assertion (no leading token required).
        _assert_pairs(dest_dir, expected_count=1)


def test_training_captions_reads_each_pictures_caption_and_tags(env):
    """The sample-prompt generator reads what the dataset holds: one caption
    and one tag list per picture, in id order, "" for a picture with none.

    Last in the module: it takes the finders out so the captioner and the
    tagger cannot rewrite the rows it places.
    """
    client, server = env
    set_id = _create_set(client, "CaptionSet")
    pic_ids = sorted([_upload(client, "Bad1.png"), _upload(client, "Bad2.png")])
    for pid in pic_ids:
        _add_to_set(client, set_id, pid)
    quiesce_background_work(server)

    def place(session):
        first, second = (session.get(Picture, pid) for pid in pic_ids)
        first.description = "An elderly man reading in a garden."
        second.description = None
        for tag in session.exec(select(Tag).where(Tag.picture_id.in_(pic_ids))):
            session.delete(tag)
        session.add(Tag(picture_id=second.id, tag="old man"))
        session.add(Tag(picture_id=second.id, tag="beard"))
        session.commit()

    server.vault.db.run_task(place)

    captions, tags = ExportUtils.training_captions(server, set_id=set_id)
    assert captions == ["An elderly man reading in a garden.", ""]
    assert tags[0] == []
    assert sorted(tags[1]) == ["beard", "old man"]


def test_training_facts_count_untagged_near_copies_and_other_people(env):
    """The Train dialog's checks: a picture with no tags trains on the trigger
    word alone, two pictures with the same perceptual hash are near copies,
    and a face named as somebody else is another person in the dataset.

    It takes the finders out itself, so nothing rewrites the rows it places,
    and it places its pictures as rows rather than uploading them, so it works
    whichever test ran before it on this module's server.
    """
    client, server = env
    quiesce_background_work(server)
    set_id = _create_set(client, "FactsSet")

    def insert(session):
        made = []
        for n, source in enumerate(("Bad1.png", "Bad2.png", "Reference1.png")):
            rel = f"facts_{n}.png"
            shutil.copy(
                os.path.join(PICTURES_DIR, source),
                os.path.join(server.vault.image_root, rel),
            )
            pic = Picture(file_path=rel, imported_at=datetime.now(timezone.utc))
            session.add(pic)
            session.commit()
            session.refresh(pic)
            made.append(pic.id)
        return made

    *pic_ids, third = server.vault.db.run_task(insert)
    for pid in [*pic_ids, third]:
        _add_to_set(client, set_id, pid)
    ids = []
    for name in ("FactsGrandad", "FactsNan"):
        resp = client.post("/characters", json={"name": name})
        assert resp.status_code == 200, resp.text
        ids.append(int(resp.json()["character"]["id"]))
    grandad, nan = ids

    def place(session):
        for tag in session.exec(
            select(Tag).where(Tag.picture_id.in_([*pic_ids, third]))
        ):
            session.delete(tag)
        session.add(Tag(picture_id=pic_ids[1], tag="beard"))
        session.add(Tag(picture_id=third, tag="garden"))
        for pid in pic_ids:
            session.get(Picture, pid).perceptual_hash = "00ff00ff00ff00ff"
        session.get(Picture, third).perceptual_hash = "ff00ff00ff00ff00"
        for face in session.exec(select(Face).where(Face.picture_id == third)):
            face.character_id = None
        session.add(
            Face(picture_id=third, face_index=98, bbox=[1, 2, 3, 4], character_id=nan)
        )
        session.commit()

    server.vault.db.run_task(place)

    facts = ExportUtils.training_facts(server, set_id=set_id, character_id=grandad)
    assert facts == {
        "count": 3,
        "untagged": 1,
        "near_duplicates": 2,
        "other_people": 1,
    }
    # Without a character nobody is "somebody else".
    assert ExportUtils.training_facts(server, set_id=set_id)["other_people"] == 0

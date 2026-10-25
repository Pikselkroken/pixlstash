"""Tests for the ai-toolkit training dataset builder (ExportUtils.build_training_dataset).

These mirror the export-API test fixtures (real server + real uploads) but call
the dataset builder directly, since it is a trusted server-side helper with no
HTTP route of its own.
"""

import gc
import glob
import json
import os
import tempfile

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from pixlstash.db_models import Face, Picture
from pixlstash.server import Server
from pixlstash.utils.service.export_utils import ExportUtils
from tests.utils import upload_pictures_and_wait

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

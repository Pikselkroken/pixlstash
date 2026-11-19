"""Files derived from a video and cached beside its thumbnail (#2147).

The storyboard: eleven evenly spaced frames in one WebP, made on demand by
``GET /pictures/storyboards/{id}.webp``. It is keyed on the picture's stored
path like the thumbnail, so deleting a picture removes it and every move
carries it - including the two reference-folder routes that carried nothing,
not even the thumbnail, before this.
"""

import io
import json
import os
import struct
import tempfile
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from pixlstash.db_models import Picture, ReferenceFolder
from pixlstash.server import Server
from pixlstash.services import layout_move_service
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask
from pixlstash.utils.image_processing import video_utils
from pixlstash.utils.image_processing.image_utils import (
    STORYBOARD_FRAMES,
    STORYBOARD_TILE_HEIGHT,
    THUMBNAIL_DIR_NAME,
    ImageUtils,
)
from pixlstash.utils.image_processing.video_utils import VideoUtils
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401
from tests.utils import reset_owner_auth

API = "/api/v1"
STORYBOARD = API + "/pictures/storyboards/{id}.webp"

pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _write_clip(path, frames, size=(32, 16)) -> str:
    """An mp4v clip whose frame ``n`` is flat grey ``shade(n)``."""
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, size)
    if not writer.isOpened():
        pytest.skip("no OpenCV video encoder available in this environment")
    for n in range(frames):
        writer.write(np.full((size[1], size[0], 3), _shade(n), dtype=np.uint8))
    writer.release()
    return path


def _shade(n: int) -> int:
    return 8 * n


def _rotate_90(path: str) -> str:
    """Write a 90-degree display matrix into the clip's track header."""
    data = bytearray(open(path, "rb").read())
    at = data.find(b"tkhd")
    version = data[at + 4]
    header = (8 + 8 + 4 + 4 + 8) if version == 1 else (4 + 4 + 4 + 4 + 4)
    matrix_at = at + 4 + 4 + header + 8 + 2 + 2 + 2 + 2
    matrix = [0, 0x10000, 0, -0x10000 & 0xFFFFFFFF, 0, 0, 0, 0, 0x40000000]
    data[matrix_at : matrix_at + 36] = struct.pack(">9I", *matrix)
    open(path, "wb").write(data)
    return path


def _mean(img: Image.Image) -> float:
    return float(np.asarray(img.convert("L")).mean())


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------


def test_tile_i_is_frame_round_i_times_last_over_ten(tmp_path):
    clip = _write_clip(str(tmp_path / "thirty.mp4"), 30)
    frames = VideoUtils.extract_storyboard_frames(clip, STORYBOARD_FRAMES)
    assert len(frames) == STORYBOARD_FRAMES
    for i, frame in enumerate(frames):
        expected = round(i * 29 / 10)
        assert abs(_mean(frame) - _shade(expected)) < 3, (i, _mean(frame))


def test_a_clip_shorter_than_the_strip_repeats_frames(tmp_path):
    clip = _write_clip(str(tmp_path / "five.mp4"), 5)
    frames = VideoUtils.extract_storyboard_frames(clip, STORYBOARD_FRAMES)
    assert len(frames) == STORYBOARD_FRAMES
    assert abs(_mean(frames[0]) - _shade(0)) < 3
    assert abs(_mean(frames[-1]) - _shade(4)) < 3


def test_one_unreadable_frame_refuses_the_whole_strip(tmp_path, monkeypatch, caplog):
    clip = _write_clip(str(tmp_path / "broken.mp4"), 30)
    real = cv2.VideoCapture

    class OneBadFrame:
        def __init__(self, *args):
            self._cap = real(*args)
            self._at = None

        def set(self, prop, value):
            self._at = value
            return self._cap.set(prop, value)

        def read(self):
            if self._at == 9:
                return False, None
            return self._cap.read()

        def __getattr__(self, name):
            return getattr(self._cap, name)

    monkeypatch.setattr(video_utils.cv2, "VideoCapture", OneBadFrame)
    assert VideoUtils.extract_storyboard_frames(clip, STORYBOARD_FRAMES) == []
    assert "could not read frame 9" in caplog.text


def test_a_rotated_clip_tiles_like_its_thumbnail(tmp_path):
    clip = _rotate_90(_write_clip(str(tmp_path / "rotated.mp4"), 6))
    first = VideoUtils.read_first_video_frame_bgr(clip)
    # Proves the clip really is rotated, so the comparison below is not vacuous.
    assert first.shape[:2] == (32, 16)
    frames = VideoUtils.extract_storyboard_frames(clip, STORYBOARD_FRAMES)
    assert {f.size for f in frames} == {(16, 32)}


def test_render_storyboard_lays_equal_tiles_left_to_right(tmp_path):
    frames = VideoUtils.extract_storyboard_frames(
        _write_clip(str(tmp_path / "c.mp4"), 30), STORYBOARD_FRAMES
    )
    sheet = Image.open(io.BytesIO(ImageUtils.render_storyboard(frames)))
    assert sheet.format == "WEBP"
    assert sheet.size == (STORYBOARD_FRAMES * 2 * STORYBOARD_TILE_HEIGHT, 160)


@pytest.mark.parametrize("size", [(4000, 10), (20000, 2)])
def test_a_very_wide_clip_still_fits_one_webp(size):
    frames = [Image.new("RGB", size)] * STORYBOARD_FRAMES
    sheet = Image.open(io.BytesIO(ImageUtils.render_storyboard(frames)))
    assert sheet.width <= 16383


# ---------------------------------------------------------------------------
# Where derived files live, and how they follow a picture
# ---------------------------------------------------------------------------


def test_derived_path_is_keyed_like_the_thumbnail(tmp_path):
    root = str(tmp_path)
    for stored in ("2026/clip.mp4", "/home/me/refs/clip.mp4"):
        thumb = ImageUtils.get_thumbnail_path(root, stored)
        board = ImageUtils.derived_media_path(root, stored, "storyboard")
        assert board == thumb.replace("_thumb.webp", "_storyboard.webp")
        assert os.path.dirname(board) == os.path.join(root, THUMBNAIL_DIR_NAME)
    assert ImageUtils.derived_media_path(None, "clip.mp4", "storyboard") is None


def _place_cached(root, stored):
    thumb = ImageUtils.write_thumbnail_bytes(root, stored, b"thumb")
    board = ImageUtils.write_derived_media_bytes(root, stored, "storyboard", b"sb")
    assert thumb and board
    return thumb, board


def test_remove_thumbnail_removes_the_storyboard_too(tmp_path):
    root = str(tmp_path)
    thumb, board = _place_cached(root, "clip.mp4")
    assert ImageUtils.remove_thumbnail(root, "clip.mp4") == 2
    assert not os.path.exists(thumb) and not os.path.exists(board)


@pytest.mark.parametrize("site", ["layout_move", "reference_scan"])
def test_each_move_site_carries_the_storyboard(tmp_path, site):
    root = str(tmp_path)
    _place_cached(root, "a/clip.mp4")
    if site == "layout_move":
        carried = layout_move_service._carry_thumbnail(root, "a/clip.mp4", "b/clip.mp4")
    else:
        task = SimpleNamespace(_db=SimpleNamespace(image_root=root))
        carried = ReferenceFolderScanTask._carry_thumbnail(
            task, "a/clip.mp4", "b/clip.mp4"
        )
    assert carried is True
    assert os.path.isfile(
        ImageUtils.derived_media_path(root, "b/clip.mp4", "storyboard")
    )
    assert not os.path.exists(
        ImageUtils.derived_media_path(root, "a/clip.mp4", "storyboard")
    )


def test_a_storyboard_is_carried_even_without_a_thumbnail(tmp_path):
    root = str(tmp_path)
    ImageUtils.write_derived_media_bytes(root, "a.mp4", "storyboard", b"sb")
    assert ImageUtils.carry_thumbnail(root, "a.mp4", "b.mp4") is False
    assert os.path.isfile(ImageUtils.derived_media_path(root, "b.mp4", "storyboard"))


# ---------------------------------------------------------------------------
# The route, on one shared server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "server-config.json")
        with open(config_path, "w") as handle:
            json.dump({"disable_background_workers": True}, handle)
        with Server(config_path) as srv:
            yield srv


@pytest.fixture
def owner(server):
    reset_owner_auth(server)
    client = TestClient(server.api)
    r = client.post(
        f"{API}/login",
        json={"username": "owner", "password": "example-owner-password"},
    )
    assert r.status_code == 200, r.text
    return client


def _insert(server, picture: Picture) -> int:
    def write(session: Session):
        session.add(picture)
        session.commit()
        session.refresh(picture)
        return picture.id

    return server.vault.db.run_task(write)


def _library_clip(server, tmp_path, frames=30) -> int:
    path = _write_clip(str(tmp_path / "src.mp4"), frames)
    data = open(path, "rb").read()
    picture = ImageUtils.create_picture_from_bytes(server.vault.image_root, data)
    assert picture.is_video
    return _insert(server, picture)


def _still(server) -> int:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buffer, format="PNG")
    picture = ImageUtils.create_picture_from_bytes(
        server.vault.image_root, buffer.getvalue()
    )
    return _insert(server, picture)


def _file_path(server, picture_id) -> str:
    return server.vault.db.run_immediate_read_task(
        lambda s: s.get(Picture, picture_id).file_path
    )


def test_storyboard_route_serves_and_caches_a_webp(
    server, owner, tmp_path, monkeypatch
):
    pic = _library_clip(server, tmp_path)
    path = STORYBOARD.format(id=pic)
    assert_real_route(server.api, "GET", path, template=STORYBOARD)
    r = owner.get(path)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/webp"
    assert "must-revalidate" in r.headers["cache-control"]
    sheet = Image.open(io.BytesIO(r.content))
    assert sheet.size == (STORYBOARD_FRAMES * 2 * STORYBOARD_TILE_HEIGHT, 160)
    cached = ImageUtils.derived_media_path(
        server.vault.image_root, _file_path(server, pic), "storyboard"
    )
    assert os.path.isfile(cached)

    def no_more_decoding(*args):
        raise AssertionError("the cached storyboard was not used")

    monkeypatch.setattr(VideoUtils, "extract_storyboard_frames", no_more_decoding)
    again = owner.get(path)
    assert again.status_code == 200 and again.content == r.content


def test_storyboard_route_refuses_a_still(server, owner):
    r = owner.get(STORYBOARD.format(id=_still(server)))
    assert r.status_code == 404
    assert r.json()["detail"] == "Picture is not a video"


def test_library_switch_clears_the_storyboard_locks(server, owner, tmp_path):
    owner.get(STORYBOARD.format(id=_library_clip(server, tmp_path, frames=8)))
    clear = server._clear_thumbnail_runtime_cache
    cells = dict(
        zip(clear.__code__.co_freevars, (c.cell_contents for c in clear.__closure__))
    )
    locks = cells["storyboard_generation_locks"]
    assert locks
    clear()
    assert not locks


def _mint(owner, **body) -> str:
    r = owner.post(f"{API}/users/me/token", json={"description": "t", **body})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_storyboard_is_picture_scoped_both_ways(server, owner, tmp_path):
    pic = _library_clip(server, tmp_path)
    other = _still(server)
    path = STORYBOARD.format(id=pic)
    assert_real_route(server.api, "GET", path, template=STORYBOARD)
    anon = TestClient(server.api)

    def get(token):
        return anon.get(path, headers={"Authorization": f"Bearer {token}"})

    in_scope = _mint(owner, scope="READ", resource_type="picture", resource_id=pic)
    assert get(in_scope).status_code == 200
    out_of_scope = _mint(
        owner, scope="READ", resource_type="picture", resource_id=other
    )
    assert get(out_of_scope).status_code == 403
    assert get(_mint(owner, scope="COMFYUI")).status_code == 403


# ---------------------------------------------------------------------------
# The reference-folder routes carry the cached files with the rows
# ---------------------------------------------------------------------------


def _reference_clip(server, folder_dir, name="clip.mp4"):
    os.makedirs(folder_dir, exist_ok=True)

    def make_folder(session: Session):
        folder = ReferenceFolder(folder=folder_dir, label="refs", status="active")
        session.add(folder)
        session.commit()
        session.refresh(folder)
        return folder.id

    folder_id = server.vault.db.run_task(make_folder)
    path = _write_clip(os.path.join(folder_dir, name), 12)
    pic = _insert(
        server,
        Picture(
            file_path=path,
            reference_folder_id=folder_id,
            pixel_sha=ImageUtils.calculate_hash_from_file_path(path),
            format="MP4",
            original_file_name=name,
        ),
    )
    _place_cached(server.vault.image_root, path)
    return folder_id, pic, path


def _assert_carried(server, old, new):
    root = server.vault.image_root
    assert os.path.isfile(ImageUtils.get_thumbnail_path(root, new))
    assert os.path.isfile(ImageUtils.derived_media_path(root, new, "storyboard"))
    assert not os.path.exists(ImageUtils.get_thumbnail_path(root, old))
    assert not os.path.exists(ImageUtils.derived_media_path(root, old, "storyboard"))


def test_relocating_a_reference_folder_carries_its_cached_files(
    server, owner, tmp_path, monkeypatch
):
    monkeypatch.setattr(server, "running_in_docker", lambda: False)
    old_root = os.path.realpath(str(tmp_path / "old"))
    folder_id, pic, old = _reference_clip(server, old_root)
    new_root = os.path.realpath(str(tmp_path / "new"))
    r = owner.post(
        f"{API}/reference-folders/{folder_id}/relocate",
        json={"destination_folder": new_root},
    )
    assert r.status_code == 200, r.text
    new = _file_path(server, pic)
    assert new == os.path.join(new_root, "clip.mp4")
    _assert_carried(server, old, new)


def test_moving_reference_pictures_carries_their_cached_files(server, owner, tmp_path):
    _, pic, old = _reference_clip(server, os.path.realpath(str(tmp_path / "from")))
    dest_dir = os.path.realpath(str(tmp_path / "to"))
    os.makedirs(dest_dir)

    def make_dest(session: Session):
        folder = ReferenceFolder(folder=dest_dir, label="to", status="active")
        session.add(folder)
        session.commit()
        session.refresh(folder)
        return folder.id

    dest_id = server.vault.db.run_task(make_dest)
    r = owner.post(
        f"{API}/reference-folders/{dest_id}/move-pictures",
        json={"picture_ids": [pic]},
    )
    assert r.status_code == 200, r.text
    new = _file_path(server, pic)
    assert new == os.path.join(dest_dir, "clip.mp4")
    _assert_carried(server, old, new)


def test_repointing_a_moved_reference_folder_carries_its_cached_files(
    server, owner, tmp_path
):
    old_root = os.path.realpath(str(tmp_path / "before"))
    folder_id, pic, old = _reference_clip(server, old_root)
    new_root = os.path.realpath(str(tmp_path / "after"))
    os.rename(old_root, new_root)  # The owner moved it outside PixlStash.
    r = owner.patch(f"{API}/reference-folders/{folder_id}", json={"folder": new_root})
    assert r.status_code == 200, r.text
    new = _file_path(server, pic)
    assert new == os.path.join(new_root, "clip.mp4")
    _assert_carried(server, old, new)


def test_repointing_onto_a_missing_file_drops_the_old_cached_files(
    server, owner, tmp_path
):
    folder_id, pic, old = _reference_clip(server, os.path.realpath(str(tmp_path / "x")))
    elsewhere = os.path.realpath(str(tmp_path / "empty"))
    os.makedirs(elsewhere)
    r = owner.patch(f"{API}/reference-folders/{folder_id}", json={"folder": elsewhere})
    assert r.status_code == 200, r.text
    root = server.vault.image_root
    assert not os.path.exists(ImageUtils.get_thumbnail_path(root, old))
    assert not os.path.exists(ImageUtils.derived_media_path(root, old, "storyboard"))


def test_repointing_a_picture_with_no_hash_drops_its_cached_files(
    server, owner, tmp_path
):
    old_root = os.path.realpath(str(tmp_path / "unhashed"))
    folder_id, pic, old = _reference_clip(server, old_root)

    def forget_hash(session: Session):
        session.get(Picture, pic).pixel_sha = None
        session.commit()

    server.vault.db.run_task(forget_hash)
    new_root = os.path.realpath(str(tmp_path / "unhashed-moved"))
    os.rename(old_root, new_root)
    r = owner.patch(f"{API}/reference-folders/{folder_id}", json={"folder": new_root})
    assert r.status_code == 200, r.text
    root = server.vault.image_root
    new = _file_path(server, pic)
    for path in (old, new):
        assert not os.path.exists(ImageUtils.get_thumbnail_path(root, path))
        assert not os.path.exists(
            ImageUtils.derived_media_path(root, path, "storyboard")
        )

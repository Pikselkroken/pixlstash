"""Files derived from a video and cached beside its thumbnail (#2147).

The storyboard: eleven evenly spaced frames in one WebP, made on demand by
``GET /pictures/storyboards/{id}.webp``. It is keyed on the picture's stored
path like the thumbnail, so deleting a picture removes it and every move
carries it - including the two reference-folder routes that carried nothing,
not even the thumbnail, before this.
"""

import io
import json
import logging
import os
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, text
from sqlmodel import Session

from pixlstash.db_models import Picture, ReferenceFolder, VideoCopy
from pixlstash.server import Server
from pixlstash.routes.pictures import _thumbnails
from pixlstash.services import layout_move_service
from pixlstash.services.library_backup_service import _library_files
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask
from pixlstash.utils.image_processing import ffmpeg_utils, video_utils
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


# ---------------------------------------------------------------------------
# Playable copies and audio waveforms (#2807). ffmpeg runs as a separate
# process; these tests drive the bundled binary through its command line.
# ---------------------------------------------------------------------------

PROXY_STATUS = API + "/pictures/proxies/{id}/status"
PROXY_START = API + "/pictures/proxies/{id}/start"
PROXY_MP4 = API + "/pictures/proxies/{id}.mp4"
WAVEFORM = API + "/pictures/waveforms/{id}.json"
VIDEO_COPIES = API + "/video-copies"


def _sound_clip(path, seconds=2, audio="sine") -> str:
    """An mp4 with a sine track, an all-zero track, or (None) no audio at all."""
    ffmpeg = ffmpeg_utils.ffmpeg_executable()
    if ffmpeg is None:
        pytest.skip("no ffmpeg binary in this environment")
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=d={seconds}:s=64x48:r=10",
    ]
    if audio == "sine":
        args += ["-f", "lavfi", "-i", f"sine=d={seconds}", "-c:a", "aac"]
    elif audio == "zero":
        args += ["-f", "lavfi", "-i", f"anullsrc=d={seconds}", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    subprocess.run(args, check=True)
    return path


def _library_file(server, path) -> int:
    picture = ImageUtils.create_picture_from_bytes(
        server.vault.image_root, open(path, "rb").read()
    )
    assert picture.is_video
    return _insert(server, picture)


def _row_exists(server, picture_id) -> bool:
    return (
        server.vault.db.run_immediate_read_task(lambda s: s.get(VideoCopy, picture_id))
        is not None
    )


def _forget_copies(server) -> None:
    """Start from no copies: no rows and no files in this library."""

    def clear(session: Session):
        session.execute(delete(VideoCopy))
        session.commit()

    server.vault.db.run_task(clear)
    for path, _size in _thumbnails._list_copy_files(server.vault.image_root):
        os.remove(path)


def _settled(owner, path, timeout=60.0) -> dict:
    """Poll a status route until it stops saying ``preparing``."""
    deadline = time.monotonic() + timeout
    while True:
        body = owner.get(path).json()
        if body["state"] != "preparing":
            return body
        assert time.monotonic() < deadline, f"still preparing after {timeout}s"
        time.sleep(0.05)


def _bearer(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── The ffmpeg wrapper, without a server ─────────────────────────────────────


def test_a_playable_copy_reads_back_as_h264(tmp_path):
    clip = _write_clip(str(tmp_path / "src.mp4"), 12)
    dest = str(tmp_path / "copy.mp4")
    ffmpeg_utils.make_playable_copy(clip, dest)
    cap = cv2.VideoCapture(dest)
    code = int(cap.get(cv2.CAP_PROP_FOURCC))
    cap.release()
    assert struct.pack("<I", code & 0xFFFFFFFF) == b"h264"


def test_a_failed_copy_leaves_no_temp_file(tmp_path, monkeypatch, caplog):
    clip = _write_clip(str(tmp_path / "src.mp4"), 12)
    dest = tmp_path / "copy.mp4"

    def refuse_the_rename(src, dst):
        # The encode has finished and the temp file exists; the rename fails.
        assert os.path.exists(src)
        raise OSError("forced rename failure")

    monkeypatch.setattr(ffmpeg_utils.os, "replace", refuse_the_rename)
    with pytest.raises(OSError, match="forced rename failure"):
        ffmpeg_utils.make_playable_copy(clip, str(dest))
    assert not dest.exists()
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".proxy-tmp-")]
    assert "Playable copy failed" in caplog.text


def test_audio_peaks_are_512_values_in_unit_range(tmp_path):
    clip = _sound_clip(str(tmp_path / "sound.mp4"), seconds=2)
    duration, peaks = ffmpeg_utils.audio_peaks(clip)
    assert len(peaks) == 512
    assert all(0.0 <= value <= 1.0 for value in peaks)
    assert max(peaks) == 1.0
    assert duration == pytest.approx(2.0, abs=0.1)


def test_an_all_silent_track_gives_zero_peaks(tmp_path):
    clip = _sound_clip(str(tmp_path / "zero.mp4"), audio="zero")
    _duration, peaks = ffmpeg_utils.audio_peaks(clip)
    assert peaks == [0.0] * 512


def test_a_clip_without_audio_has_no_peaks_and_no_warning(tmp_path, caplog):
    clip = _sound_clip(str(tmp_path / "mute.mp4"), audio=None)
    with caplog.at_level(logging.WARNING):
        assert ffmpeg_utils.audio_peaks(clip) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_file_ffmpeg_cannot_open_is_an_error_not_silence(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video" * 100)
    with pytest.raises(RuntimeError, match="could not open"):
        ffmpeg_utils.audio_peaks(str(bad))


def test_backup_walk_leaves_out_the_playable_copies(tmp_path):
    root = str(tmp_path)
    ImageUtils.write_thumbnail_bytes(root, "a/clip.mp4", b"thumb")
    folder = ImageUtils.video_copy_folder(root)
    os.makedirs(folder)
    with open(os.path.join(folder, "7.mp4"), "wb") as handle:
        handle.write(b"copy")
    collected = [archive for _, archive in _library_files(root)]
    assert any(name.endswith("_thumb.webp") for name in collected)
    assert not [name for name in collected if "proxies" in name]


# ── The routes, on the shared server ────────────────────────────────────────


def test_a_short_copy_starts_at_once_and_then_serves_with_ranges(
    server, owner, tmp_path
):
    pic = _library_clip(server, tmp_path)
    path = PROXY_STATUS.format(id=pic)
    assert_real_route(server.api, "GET", path, template=PROXY_STATUS)
    first = owner.get(path).json()
    assert first["state"] == "preparing"
    assert first["estimate_seconds"] < 10
    assert first["format"] == "MP4"
    done = _settled(owner, path)
    assert done["state"] == "ready"
    # 30 frames at 10 fps.
    assert done["duration_seconds"] == pytest.approx(3.0, abs=0.1)
    r = owner.get(PROXY_MP4.format(id=pic), headers={"Range": "bytes=0-99"})
    assert r.status_code == 206, r.text
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["accept-ranges"] == "bytes"
    cap = cv2.VideoCapture(ImageUtils.video_copy_path(server.vault.image_root, pic))
    code = int(cap.get(cv2.CAP_PROP_FOURCC))
    cap.release()
    assert struct.pack("<I", code & 0xFFFFFFFF) == b"h264"


def test_a_long_copy_is_offered_until_started(server, owner, tmp_path, monkeypatch):
    _forget_copies(server)
    # No copy yet, so the rate falls back to the default; a 1 px-frame/s rate
    # makes this clip's estimate far over the 10 s threshold.
    monkeypatch.setattr(_thumbnails, "_DEFAULT_COPY_RATE", 1.0)
    pic = _library_clip(server, tmp_path)
    path = PROXY_STATUS.format(id=pic)
    offered = owner.get(path).json()
    assert offered["state"] == "offered"
    assert offered["estimate_seconds"] >= 10
    copy = ImageUtils.video_copy_path(server.vault.image_root, pic)
    assert not os.path.exists(copy)
    started = owner.post(PROXY_START.format(id=pic)).json()
    assert started["state"] == "preparing"
    assert _settled(owner, path)["state"] == "ready"


def test_a_clip_with_no_frame_count_starts_and_does_not_poison_the_rate(
    server, owner, tmp_path, monkeypatch
):
    # Some containers report no frame count. Such a clip's copy starts (its
    # length is unknown), and the row it leaves, pixel_frames 0, must never
    # become the rate the next estimate divides by.
    _forget_copies(server)
    still = _still(server)
    server.vault.db.run_task(
        lambda s: (
            s.add(
                VideoCopy(
                    picture_id=still,
                    copy_bytes=1,
                    source_bytes=1,
                    source_mtime=0.0,
                    pixel_frames=0,
                    build_seconds=1.0,
                )
            ),
            s.commit(),
        )
    )
    pic = _library_clip(server, tmp_path)
    path = PROXY_STATUS.format(id=pic)
    known = owner.get(path)
    assert known.status_code == 200, known.text
    assert known.json()["estimate_seconds"] > 0
    assert _settled(owner, path)["state"] == "ready"

    real_header = VideoUtils.video_header
    monkeypatch.setattr(
        VideoUtils,
        "video_header",
        lambda source: (0, *real_header(source)[1:]),
    )
    unknown_pic = _library_clip(server, tmp_path)
    body = owner.get(PROXY_STATUS.format(id=unknown_pic)).json()
    assert body["state"] == "preparing"
    assert body["estimate_seconds"] is None
    _settled(owner, PROXY_STATUS.format(id=unknown_pic))


def test_a_still_has_no_copy_to_make(server, owner):
    pic = _still(server)
    body = owner.get(PROXY_STATUS.format(id=pic)).json()
    assert body["state"] == "unavailable"


def test_a_failed_copy_is_reported_once_then_retried(
    server, owner, tmp_path, monkeypatch
):
    _forget_copies(server)
    release = threading.Event()
    real_copy = ffmpeg_utils.make_playable_copy
    calls = []

    def flaky(source, dest, on_start=None):
        calls.append(source)
        on_start(None)
        if len(calls) == 1:
            raise RuntimeError("forced failure")
        release.wait(30)
        real_copy(source, dest)

    monkeypatch.setattr(ffmpeg_utils, "make_playable_copy", flaky)
    pic = _library_clip(server, tmp_path)
    path = PROXY_STATUS.format(id=pic)
    assert _settled(owner, path)["state"] == "failed"
    # Dropped once reported, so the next call starts again instead of failing
    # forever. The retry is held on the event, so this answer is deterministic.
    assert owner.get(path).json()["state"] == "preparing"
    release.set()
    assert _settled(owner, path)["state"] == "ready"


def test_start_retries_a_failed_copy_at_once(server, owner, tmp_path, monkeypatch):
    # The POST starts the copy unless one is ready or running; a failed one is
    # neither, so it retries rather than answering "failed".
    _forget_copies(server)
    calls = []

    def failing(source, dest, on_start=None):
        calls.append(source)
        raise RuntimeError("forced failure")

    monkeypatch.setattr(ffmpeg_utils, "make_playable_copy", failing)
    pic = _library_clip(server, tmp_path)
    start = PROXY_START.format(id=pic)
    assert owner.post(start).json()["state"] == "preparing"
    _thumbnails._copy_executor.submit(lambda: None).result(timeout=30)
    assert owner.post(start).json()["state"] == "preparing"
    _thumbnails._copy_executor.submit(lambda: None).result(timeout=30)
    assert len(calls) == 2


def test_a_library_switch_does_not_report_the_old_job(
    server, owner, tmp_path, monkeypatch
):
    _forget_copies(server)
    gate = threading.Event()
    started = threading.Event()
    real_copy = ffmpeg_utils.make_playable_copy

    def gated(source, dest, on_start=None):
        on_start(None)
        started.set()
        gate.wait(30)
        real_copy(source, dest)

    monkeypatch.setattr(ffmpeg_utils, "make_playable_copy", gated)
    pic = _library_clip(server, tmp_path)
    path = PROXY_STATUS.format(id=pic)
    assert owner.get(path).json()["state"] == "preparing"
    assert started.wait(10)
    assert owner.get(path).json()["elapsed_seconds"] > 0

    clear = server._clear_thumbnail_runtime_cache
    cells = dict(
        zip(clear.__code__.co_freevars, (c.cell_contents for c in clear.__closure__))
    )
    drop = cells["drop_copy_jobs"]
    jobs = dict(
        zip(drop.__code__.co_freevars, (c.cell_contents for c in drop.__closure__))
    )["copy_jobs"]
    assert jobs
    clear()
    assert not jobs
    # A fresh job, queued behind the old one: it has not started, so its
    # elapsed time is zero. The old job would show a running clock.
    after = owner.get(path).json()
    assert after["state"] == "preparing"
    assert after["elapsed_seconds"] == 0.0
    gate.set()
    assert _settled(owner, path)["state"] == "ready"


def test_a_copy_goes_stale_when_the_original_changes(server, owner, tmp_path):
    pic = _library_clip(server, tmp_path)
    path = PROXY_STATUS.format(id=pic)
    owner.get(path)
    assert _settled(owner, path)["state"] == "ready"
    rel = server.vault.db.run_immediate_read_task(
        lambda s: s.get(Picture, pic).file_path
    )
    source = ImageUtils.resolve_picture_path(server.vault.image_root, rel)
    later = time.time() + 60
    os.utime(source, (later, later))
    assert owner.get(PROXY_MP4.format(id=pic)).status_code == 404
    # The stale status starts a rebuild at once rather than serving the old copy.
    assert owner.get(path).json()["state"] == "preparing"
    assert _settled(owner, path)["state"] == "ready"


def test_purge_removes_the_copy_while_the_scrapheap_keeps_it(server, owner, tmp_path):
    pic = _library_clip(server, tmp_path)
    _settled(owner, PROXY_STATUS.format(id=pic))
    copy = ImageUtils.video_copy_path(server.vault.image_root, pic)
    assert os.path.isfile(copy) and _row_exists(server, pic)
    # The foreign-key cascade must be live on this connection for the row to
    # go with the picture, since the purge never deletes it by hand.
    assert (
        server.vault.db.run_immediate_read_task(
            lambda s: s.execute(text("PRAGMA foreign_keys")).scalar()
        )
        == 1
    )

    assert owner.delete(f"{API}/pictures/{pic}").status_code == 200
    assert os.path.isfile(copy) and _row_exists(server, pic)

    token = owner.post(
        f"{API}/pictures/scrapheap/delete-preview", json={"ids": [pic]}
    ).json()["confirm_token"]
    r = owner.request(
        "DELETE",
        f"{API}/pictures/scrapheap",
        json={"ids": [pic], "confirm_token": token, "include_protected": True},
    )
    assert r.status_code == 200, r.text
    assert not os.path.exists(copy)
    assert not _row_exists(server, pic)


def test_video_copies_counts_and_deletes_this_library_copies(server, owner, tmp_path):
    _forget_copies(server)
    ids = []
    for index, frames in enumerate((30, 12)):
        folder = tmp_path / f"c{index}"
        folder.mkdir()
        ids.append(_library_clip(server, folder, frames=frames))
    for pic in ids:
        assert _settled(owner, PROXY_STATUS.format(id=pic))["state"] == "ready"
    root = server.vault.image_root
    sizes = [os.path.getsize(ImageUtils.video_copy_path(root, pic)) for pic in ids]
    assert owner.get(VIDEO_COPIES).json() == {"count": 2, "bytes": sum(sizes)}

    assert owner.delete(VIDEO_COPIES).json() == {"removed": 2, "bytes": sum(sizes)}
    assert owner.get(VIDEO_COPIES).json() == {"count": 0, "bytes": 0}
    assert not any(_row_exists(server, pic) for pic in ids)


def test_delete_copies_drops_the_jobs_in_flight(server, owner, tmp_path, monkeypatch):
    # A build running or queued when the owner deletes the copies must not
    # write its copy back afterwards: the queued one is cancelled and the
    # running ffmpeg is killed.
    _forget_copies(server)
    gate = threading.Event()
    built = []

    def gated(source, dest, on_start=None):
        on_start(None)
        built.append(source)
        gate.wait(30)
        raise RuntimeError("killed")

    kills = []
    monkeypatch.setattr(ffmpeg_utils, "make_playable_copy", gated)
    monkeypatch.setattr(
        ffmpeg_utils, "terminate_running_processes", lambda: kills.append(1)
    )
    ids = []
    for index in range(2):
        folder = tmp_path / f"d{index}"
        folder.mkdir()
        ids.append(_library_clip(server, folder))
    for pic in ids:
        assert owner.get(PROXY_STATUS.format(id=pic)).json()["state"] == "preparing"
    assert owner.delete(VIDEO_COPIES).status_code == 200
    assert kills, "the running ffmpeg was not stopped"
    gate.set()
    _thumbnails._copy_executor.submit(lambda: None).result(timeout=30)
    # Only the first build ever ran; the queued second one was cancelled.
    assert len(built) == 1


def test_a_startup_sweep_removes_temps_and_copies_with_no_row(server, tmp_path):
    _forget_copies(server)
    root = server.vault.image_root
    folder = ImageUtils.video_copy_folder(root)
    os.makedirs(folder, exist_ok=True)
    pic = _library_clip(server, tmp_path)
    kept = ImageUtils.video_copy_path(root, pic)
    temp = os.path.join(folder, ".proxy-tmp-deadbeef.mp4")
    orphan = os.path.join(folder, "999999.mp4")
    for path in (temp, orphan, kept):
        with open(path, "wb") as handle:
            handle.write(b"x")

    def give_row(session: Session):
        session.merge(
            VideoCopy(
                picture_id=pic,
                copy_bytes=1,
                source_bytes=1,
                source_mtime=1.0,
                pixel_frames=1,
                build_seconds=1.0,
            )
        )
        session.commit()

    server.vault.db.run_task(give_row)
    server._sweep_video_copies()
    assert not os.path.exists(temp)
    assert not os.path.exists(orphan)
    assert os.path.exists(kept)
    # The row above has a made-up rate; drop it so later tests estimate honestly.
    _forget_copies(server)


def test_waveform_gives_512_peaks_and_is_cached(server, owner, tmp_path, monkeypatch):
    clip = _sound_clip(str(tmp_path / "sound.mp4"), seconds=2)
    pic = _library_file(server, clip)
    path = WAVEFORM.format(id=pic)
    assert_real_route(server.api, "GET", path, template=WAVEFORM)
    r = owner.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duration"] == pytest.approx(2.0, abs=0.1)
    assert len(body["peaks"]) == 512
    assert all(0.0 <= value <= 1.0 for value in body["peaks"])

    def no_decoding(*args, **kwargs):
        raise AssertionError("the cached waveform was not used")

    monkeypatch.setattr(ffmpeg_utils, "audio_peaks", no_decoding)
    assert owner.get(path).json() == body


def test_a_silent_clip_answers_nulls_and_caches_them_without_a_warning(
    server, owner, tmp_path, caplog
):
    root = server.vault.image_root
    for name, audio in (("mute.mp4", None), ("zero.mp4", "zero")):
        clip = _sound_clip(str(tmp_path / name), audio=audio)
        pic = _library_file(server, clip)
        with caplog.at_level(logging.WARNING):
            r = owner.get(WAVEFORM.format(id=pic))
        assert r.status_code == 200, r.text
        assert r.json() == {"duration": None, "peaks": None}
        rel = server.vault.db.run_immediate_read_task(
            lambda s, p=pic: s.get(Picture, p).file_path
        )
        assert os.path.isfile(ImageUtils.derived_media_path(root, rel, "waveform"))
    assert not [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "aveform" in r.getMessage()
    ]


def test_no_ffmpeg_means_503_for_the_waveform_and_unavailable_for_the_copy(
    server, owner, tmp_path, monkeypatch
):
    pic = _library_clip(server, tmp_path)
    monkeypatch.setattr(ffmpeg_utils, "ffmpeg_executable", lambda: None)
    assert owner.get(WAVEFORM.format(id=pic)).status_code == 503
    assert owner.get(PROXY_STATUS.format(id=pic)).json()["state"] == "unavailable"


def test_proxy_and_waveform_routes_are_picture_scoped_both_ways(
    server, owner, tmp_path
):
    folder = tmp_path / "in"
    folder.mkdir()
    pic = _library_clip(server, folder)
    other = _still(server)
    assert _settled(owner, PROXY_STATUS.format(id=pic))["state"] == "ready"
    anon = TestClient(server.api)
    in_scope = _mint(owner, scope="READ", resource_type="picture", resource_id=pic)
    out_of_scope = _mint(
        owner, scope="READ", resource_type="picture", resource_id=other
    )
    comfy = _mint(owner, scope="COMFYUI")
    for template in (PROXY_STATUS, PROXY_MP4, WAVEFORM):
        path = template.format(id=pic)
        assert_real_route(server.api, "GET", path, template=template)
        assert anon.get(path, headers=_bearer(in_scope)).status_code == 200
        assert anon.get(path, headers=_bearer(out_of_scope)).status_code == 403
        assert anon.get(path, headers=_bearer(comfy)).status_code == 403

    start = PROXY_START.format(id=pic)
    assert_real_route(server.api, "POST", start, template=PROXY_START)
    # Starting a job is a write: a READ token is refused by the middleware.
    assert anon.post(start, headers=_bearer(in_scope)).status_code == 403
    assert owner.post(start).status_code == 200


def test_video_copies_routes_are_owner_only(server, owner, tmp_path):
    pic = _library_clip(server, tmp_path)
    anon = TestClient(server.api)
    read = _mint(owner, scope="READ", resource_type="picture", resource_id=pic)
    comfy = _mint(owner, scope="COMFYUI")
    assert_real_route(server.api, "GET", VIDEO_COPIES)
    assert owner.get(VIDEO_COPIES).status_code == 200
    assert anon.get(VIDEO_COPIES, headers=_bearer(read)).status_code == 403
    assert anon.get(VIDEO_COPIES, headers=_bearer(comfy)).status_code == 403
    assert_real_route(server.api, "DELETE", VIDEO_COPIES)
    assert anon.delete(VIDEO_COPIES, headers=_bearer(read)).status_code == 403
    assert anon.delete(VIDEO_COPIES, headers=_bearer(comfy)).status_code == 403


def test_a_row_whose_file_is_gone_counts_as_no_copy(server, owner, tmp_path):
    pic = _library_clip(server, tmp_path)
    path = PROXY_STATUS.format(id=pic)
    assert _settled(owner, path)["state"] == "ready"
    os.remove(ImageUtils.video_copy_path(server.vault.image_root, pic))
    assert _row_exists(server, pic)
    assert owner.get(PROXY_MP4.format(id=pic)).status_code == 404
    assert owner.get(path).json()["state"] == "preparing"
    assert _settled(owner, path)["state"] == "ready"


def test_shutdown_kills_a_running_copy_and_its_job_fails_cleanly(tmp_path, monkeypatch):
    # A stand-in for ffmpeg that just sleeps: `exec` so the process we kill is
    # the one holding the stderr pipe, not a shell wrapped around it.
    fake = tmp_path / "fake-ffmpeg"
    fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr(ffmpeg_utils, "ffmpeg_executable", lambda: str(fake))
    started = threading.Event()
    outcome = {}

    def run():
        try:
            ffmpeg_utils.make_playable_copy(
                str(tmp_path / "src.mov"),
                str(tmp_path / "copy.mp4"),
                on_start=lambda _proc: started.set(),
            )
        except RuntimeError as exc:
            outcome["error"] = str(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(10)
    ffmpeg_utils.terminate_running_processes()
    worker.join(10)
    assert not worker.is_alive(), "the running copy was not killed"
    assert "exited with status" in outcome["error"]
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".proxy-tmp-")]

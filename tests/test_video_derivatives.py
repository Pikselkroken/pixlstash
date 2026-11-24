"""Files derived from a video and cached beside its thumbnail (#2147, #2807).

The storyboard: eleven evenly spaced frames in one WebP, made on demand by
``GET /pictures/storyboards/{id}.webp``. It is keyed on the picture's stored
path like the thumbnail, so deleting a picture removes it and every move
carries it - including the two reference-folder routes that carried nothing,
not even the thumbnail, before this. The waveform is a second such kind.

The playable copy is not: one per picture, named by its id, with a
``video_copy`` row saying whether it is still fresh (#2807).
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
from sqlmodel import Session

from pixlstash.db_models import Picture, ReferenceFolder, VideoCopy
from pixlstash.routes.pictures import _thumbnails
from pixlstash import auth, maintenance
from pixlstash.server import Server
from pixlstash.services import layout_move_service, library_backup_service
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask
from pixlstash.utils.image_processing import ffmpeg_utils, video_utils
from pixlstash.utils.image_processing.image_utils import (
    STORYBOARD_FRAMES,
    STORYBOARD_TILE_HEIGHT,
    THUMBNAIL_DIR_NAME,
    VIDEO_COPY_DIR,
    ImageUtils,
)
from pixlstash.utils.image_processing.video_utils import VIDEO_EXTENSIONS, VideoUtils
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401
from tests.utils import reset_owner_auth

API = "/api/v1"
STORYBOARD = API + "/pictures/storyboards/{id}.webp"
WAVEFORM = API + "/pictures/waveforms/{id}.json"
COPY = API + "/pictures/video-copies/{id}"
COPY_STATUS = COPY + "/status"
COPY_FILE = COPY + ".mp4"
COPIES = API + "/library/video-copies"

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
# ffmpeg: playable copies and the waveform (#2807)
# ---------------------------------------------------------------------------


def _ffmpeg_clip(path, sound=True) -> str:
    """A 1 s 64x32 MPEG-4 clip made by the bundled ffmpeg, with a tone or not."""
    exe = ffmpeg_utils.ffmpeg_executable()
    assert exe, (
        "No ffmpeg found: these tests need one with an H.264 encoder. Put ffmpeg "
        "on PATH or set PIXLSTASH_FFMPEG to its absolute path."
    )
    assert ffmpeg_utils.h264_encoder(), (
        f"{exe} has none of {', '.join(ffmpeg_utils.H264_ENCODERS)}; these "
        "tests need an ffmpeg with an H.264 encoder."
    )
    cmd = [exe, "-y", "-loglevel", "error", "-f", "lavfi"]
    cmd += ["-i", "testsrc=d=1:s=64x32:r=10"]
    if sound:
        cmd += ["-f", "lavfi", "-i", "sine=d=1", "-c:a", "aac", "-shortest"]
    subprocess.run([*cmd, "-c:v", "mpeg4", path], check=True)
    return path


def _fourcc(path) -> str:
    code = int(cv2.VideoCapture(path).get(cv2.CAP_PROP_FOURCC))
    return "".join(chr((code >> 8 * i) & 0xFF) for i in range(4))


def test_a_copy_is_h264_and_leaves_no_temp_file(tmp_path):
    source = _ffmpeg_clip(str(tmp_path / "in.mp4"))
    dest = str(tmp_path / "copies" / "7.mp4")
    ffmpeg_utils.make_playable_copy(source, dest)
    assert _fourcc(dest) in ("h264", "avc1")
    assert os.listdir(tmp_path / "copies") == ["7.mp4"]


def test_a_failed_copy_raises_and_leaves_no_temp_file(tmp_path):
    source = tmp_path / "broken.mov"
    source.write_bytes(b"not a video at all")
    with pytest.raises(RuntimeError):
        ffmpeg_utils.make_playable_copy(str(source), str(tmp_path / "c" / "1.mp4"))
    assert os.listdir(tmp_path / "c") == []


def test_audio_peaks_reads_the_tone_and_answers_none_for_silence(tmp_path):
    duration, peaks = ffmpeg_utils.audio_peaks(_ffmpeg_clip(str(tmp_path / "s.mp4")))
    assert abs(duration - 1.0) < 0.1
    assert len(peaks) == ffmpeg_utils.WAVEFORM_BUCKETS
    assert min(peaks) >= 0 and max(peaks) == 1.0
    silent = _ffmpeg_clip(str(tmp_path / "q.mp4"), sound=False)
    assert ffmpeg_utils.audio_peaks(silent) is None


def test_describe_names_the_codec_or_the_container(tmp_path):
    info = VideoUtils.describe(_ffmpeg_clip(str(tmp_path / "a.mp4"), sound=False))
    assert info == {"pixel_frames": 10 * 64 * 32, "duration": 1.0, "format": "MPEG-4"}
    exe = ffmpeg_utils.ffmpeg_executable()
    mkv = str(tmp_path / "b.mkv")
    subprocess.run(
        [exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1"]
        + ["-c:v", ffmpeg_utils.h264_encoder(), mkv],
        check=True,
    )
    assert VideoUtils.describe(mkv)["format"] == "MKV"


def test_backups_leave_the_video_copies_out(tmp_path):
    assert library_backup_service._VIDEO_COPY_DIR == VIDEO_COPY_DIR
    root = str(tmp_path)
    copy = ImageUtils.video_copy_path(root, 3)
    os.makedirs(os.path.dirname(copy))
    open(copy, "wb").write(b"copy")
    thumb = ImageUtils.write_thumbnail_bytes(root, "clip.mp4", b"thumb")
    payload = [path for path, _ in library_backup_service._library_files(root)]
    assert thumb in payload
    assert copy not in payload


def _sound_clip(server, tmp_path, name="s.mp4", sound=True) -> int:
    data = open(_ffmpeg_clip(str(tmp_path / name), sound=sound), "rb").read()
    picture = ImageUtils.create_picture_from_bytes(server.vault.image_root, data)
    assert picture.is_video
    return _insert(server, picture)


def _status(client, pic) -> dict:
    r = client.get(COPY_STATUS.format(id=pic))
    assert r.status_code == 200, r.text
    return r.json()


def _until_settled(client, pic) -> dict:
    deadline = time.monotonic() + 60
    status = _status(client, pic)
    while status["state"] == "preparing" and time.monotonic() < deadline:
        time.sleep(0.1)
        status = _status(client, pic)
    return status


def _make_copy(client, pic) -> str:
    r = client.post(COPY.format(id=pic), json={"confirm": True})
    assert r.status_code == 200, r.text
    assert _until_settled(client, pic)["state"] == "ready"
    return ImageUtils.video_copy_path(client.server.vault.image_root, pic)


@pytest.fixture
def copier(server, owner):
    """The owner's client, with its server, and no copy left running after."""
    owner.server = server
    yield owner
    _thumbnails._copy_executor.submit(lambda: None).result(timeout=60)


def _row(server, pic):
    return server.vault.db.run_immediate_read_task(lambda s: s.get(VideoCopy, pic))


def test_a_copy_goes_none_preparing_ready_and_serves_ranges(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    for method, path, template in (
        ("GET", COPY_STATUS.format(id=pic), COPY_STATUS),
        ("POST", COPY.format(id=pic), COPY),
        ("GET", COPY_FILE.format(id=pic), COPY_FILE),
    ):
        assert_real_route(server.api, method, path, template=template)
    before = _status(copier, pic)
    assert before["state"] == "none"
    assert before["format"] == "MPEG-4"
    assert before["duration_seconds"] == 1.0
    assert 0 <= before["estimate_seconds"] <= _thumbnails.COPY_OFFER_SECONDS
    assert copier.get(COPY_FILE.format(id=pic)).status_code == 404

    started = copier.post(COPY.format(id=pic), json={"confirm": False}).json()
    assert started["state"] == "preparing"
    assert started["remaining_seconds"] is not None
    assert _until_settled(copier, pic)["state"] == "ready"

    r = copier.get(COPY_FILE.format(id=pic), headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"
    assert len(r.content) == 100
    row = _row(server, pic)
    path = ImageUtils.video_copy_path(server.vault.image_root, pic)
    assert row.bytes == os.path.getsize(path) and row.pixel_frames == 10 * 64 * 32
    # Already made: answered, not made again.
    again = copier.post(COPY.format(id=pic), json={"confirm": False}).json()
    assert again["state"] == "ready"


def test_a_long_copy_is_offered_until_confirmed(copier, server, tmp_path, monkeypatch):
    pic = _sound_clip(server, tmp_path)
    # With no copy made yet, the estimate divides by this.
    assert copier.delete(COPIES).status_code == 200
    monkeypatch.setattr(_thumbnails, "DEFAULT_COPY_RATE", 1000)
    offer = copier.post(COPY.format(id=pic), json={"confirm": False}).json()
    assert offer["state"] == "offered"
    assert offer["estimate_seconds"] == round(10 * 64 * 32 / 1000, 1)
    assert _status(copier, pic)["state"] == "none"  # Nothing started.
    started = copier.post(COPY.format(id=pic), json={"confirm": True}).json()
    assert started["state"] == "preparing"
    assert _until_settled(copier, pic)["state"] == "ready"


def test_a_changed_original_makes_the_copy_count_as_none(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    path = _make_copy(copier, pic)
    source = ImageUtils.resolve_picture_path(
        server.vault.image_root, _file_path(server, pic)
    )
    stat = os.stat(source)
    os.utime(source, (stat.st_atime, stat.st_mtime + 5))
    assert _status(copier, pic)["state"] == "none"
    assert _row(server, pic) is None
    assert not os.path.exists(path)


def test_a_missing_copy_file_counts_as_none(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    os.remove(_make_copy(copier, pic))
    assert copier.get(COPY_FILE.format(id=pic)).status_code == 404
    assert _row(server, pic) is None


def test_a_failed_copy_is_reported_once(copier, server, tmp_path, monkeypatch):
    pic = _sound_clip(server, tmp_path)

    def fail(source, dest, on_start=None):
        raise RuntimeError("forced")

    monkeypatch.setattr(ffmpeg_utils, "make_playable_copy", fail)
    assert copier.post(COPY.format(id=pic), json={}).json()["state"] == "preparing"
    failed = _until_settled(copier, pic)
    assert failed["state"] == "failed" and failed["detail"]
    assert _status(copier, pic)["state"] == "none"


def test_a_library_switch_forgets_the_old_job(copier, server, tmp_path, monkeypatch):
    pic = _sound_clip(server, tmp_path)
    release = threading.Event()

    def hang(source, dest, on_start=None):
        release.wait(30)
        raise RuntimeError("stopped")

    monkeypatch.setattr(ffmpeg_utils, "make_playable_copy", hang)
    try:
        assert copier.post(COPY.format(id=pic), json={}).json()["state"] == "preparing"
        assert _status(copier, pic)["state"] == "preparing"
        server._clear_thumbnail_runtime_cache()
        # The same id in the next library is not this library's job.
        assert _status(copier, pic)["state"] == "none"
    finally:
        release.set()
    _thumbnails._copy_executor.submit(lambda: None).result(timeout=30)
    assert _status(copier, pic)["state"] == "none"  # Its failure is never reported.


def test_the_scrapheap_keeps_a_copy_and_a_purge_removes_it(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    path = _make_copy(copier, pic)
    assert copier.delete(f"{API}/pictures/{pic}").status_code == 200
    assert os.path.isfile(path) and _row(server, pic) is not None
    assert _status(copier, pic)["state"] == "ready"
    preview = copier.post(
        f"{API}/pictures/scrapheap/delete-preview", json={"ids": [pic]}
    )
    token = preview.json()["confirm_token"]
    r = copier.request(
        "DELETE",
        f"{API}/pictures/scrapheap",
        json={"picture_ids": [pic], "include_protected": True, "confirm_token": token},
    )
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10
    while os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not os.path.exists(path)
    assert _row(server, pic) is None  # By the cascade.


def test_settings_count_and_delete_the_copies(copier, server, tmp_path):
    assert copier.delete(COPIES).status_code == 200
    assert copier.get(COPIES).json() == {"count": 0, "bytes": 0}
    pic = _sound_clip(server, tmp_path)
    size = os.path.getsize(_make_copy(copier, pic))
    stranger = os.path.join(server.vault.image_root, VIDEO_COPY_DIR, "abc.mp4")
    open(stranger, "wb").write(b"not ours")
    try:
        assert copier.get(COPIES).json() == {"count": 1, "bytes": size}
        assert copier.delete(COPIES).json() == {"deleted": 1, "bytes": size}
        assert os.path.isfile(stranger)
    finally:
        os.remove(stranger)
    assert _row(server, pic) is None
    assert _status(copier, pic)["state"] == "none"


def test_startup_sweeps_temp_and_orphaned_copy_files(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    kept = _make_copy(copier, pic)
    folder = os.path.dirname(kept)
    leftovers = [
        os.path.join(folder, ffmpeg_utils.COPY_TMP_PREFIX + "abc.mp4"),
        os.path.join(folder, "987654.mp4"),
    ]
    strangers = [os.path.join(folder, name) for name in ("notes.txt", "mine.mp4")]
    for path in leftovers + strangers:
        open(path, "wb").write(b"x")
    server._sweep_video_copies()
    assert os.path.isfile(kept)
    assert not any(os.path.exists(path) for path in leftovers)
    # Only a copy's or a temp file's name shape is ever removed.
    assert all(os.path.isfile(path) for path in strangers)
    for path in strangers:
        os.remove(path)


def test_an_unreadable_copies_folder_does_not_stop_startup(
    copier, server, tmp_path, monkeypatch, caplog
):
    _make_copy(copier, _sound_clip(server, tmp_path))

    def unreadable(path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(maintenance.os, "listdir", unreadable)
    with caplog.at_level(logging.WARNING):
        server._sweep_video_copies()
    assert "Could not sweep the video copies" in caplog.text


def test_no_ffmpeg_means_unavailable_and_503(copier, server, tmp_path, monkeypatch):
    pic = _sound_clip(server, tmp_path)
    monkeypatch.setattr(_thumbnails, "ffmpeg_executable", lambda: None)
    assert _status(copier, pic)["state"] == "unavailable"
    assert copier.get(WAVEFORM.format(id=pic)).status_code == 503


def test_a_still_has_no_copy_and_no_waveform(copier, server):
    still = _still(server)
    assert _status(copier, still)["state"] == "unavailable"
    assert copier.get(WAVEFORM.format(id=still)).status_code == 404


def test_waveform_route_reads_and_caches_the_peaks(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    assert_real_route(server.api, "GET", WAVEFORM.format(id=pic), template=WAVEFORM)
    body = copier.get(WAVEFORM.format(id=pic)).json()
    assert len(body["peaks"]) == 512
    assert all(0 <= p <= 1 for p in body["peaks"]) and max(body["peaks"]) == 1
    assert abs(body["duration"] - 1.0) < 0.1
    assert os.path.isfile(
        ImageUtils.derived_media_path(
            server.vault.image_root, _file_path(server, pic), "waveform"
        )
    )


def test_a_silent_clip_answers_null_cached_without_a_warning(
    copier, server, tmp_path, monkeypatch, caplog
):
    pic = _sound_clip(server, tmp_path, name="q.mp4", sound=False)
    with caplog.at_level(logging.WARNING):
        body = copier.get(WAVEFORM.format(id=pic)).json()
    assert body == {"duration": None, "peaks": None}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def probe_again(*args, **kwargs):
        raise AssertionError("the silent answer was not cached")

    monkeypatch.setattr(ffmpeg_utils, "audio_peaks", probe_again)
    assert copier.get(WAVEFORM.format(id=pic)).json() == body


def test_copy_and_waveform_routes_are_picture_scoped_both_ways(
    copier, server, tmp_path
):
    pic = _sound_clip(server, tmp_path)
    _make_copy(copier, pic)
    other = _still(server)
    anon = TestClient(server.api)
    in_scope = _mint(copier, scope="READ", resource_type="picture", resource_id=pic)
    out_of_scope = _mint(
        copier, scope="READ", resource_type="picture", resource_id=other
    )
    comfyui = _mint(copier, scope="COMFYUI")

    def call(method, path, token):
        return anon.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            json={} if method == "POST" else None,
        )

    for path, template in (
        (COPY_STATUS.format(id=pic), COPY_STATUS),
        (COPY_FILE.format(id=pic), COPY_FILE),
        (WAVEFORM.format(id=pic), WAVEFORM),
    ):
        assert_real_route(server.api, "GET", path, template=template)
        assert call("GET", path, in_scope).status_code == 200, path
        assert call("GET", path, out_of_scope).status_code == 403, path
        assert call("GET", path, comfyui).status_code == 403, path
    post = COPY.format(id=pic)
    assert_real_route(server.api, "POST", post, template=COPY)
    assert copier.post(post, json={}).status_code == 200
    # A share token is read-only: it may watch a copy, not start one.
    for token in (in_scope, out_of_scope, comfyui):
        assert call("POST", post, token).status_code == 403


def test_the_copies_settings_are_owner_only(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    assert_real_route(server.api, "GET", COPIES)
    assert_real_route(server.api, "DELETE", COPIES)
    assert copier.get(COPIES).status_code == 200
    anon = TestClient(server.api)
    scoped = _mint(copier, scope="READ", resource_type="picture", resource_id=pic)
    for token in (scoped, _mint(copier, scope="READ"), _mint(copier, scope="COMFYUI")):
        headers = {"Authorization": f"Bearer {token}"}
        assert anon.get(COPIES, headers=headers).status_code == 403
        assert anon.delete(COPIES, headers=headers).status_code == 403


def test_a_finished_copy_serves_before_its_status_is_read(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    assert copier.post(COPY.format(id=pic), json={"confirm": True}).status_code == 200
    _thumbnails._copy_executor.submit(lambda: None).result(timeout=60)
    r = copier.get(COPY_FILE.format(id=pic))
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"


def _closure(fn) -> dict:
    return dict(zip(fn.__code__.co_freevars, (c.cell_contents for c in fn.__closure__)))


def test_a_queued_copy_never_starts_after_a_clear(
    copier, server, tmp_path, monkeypatch
):
    first = _sound_clip(server, tmp_path, name="a.mp4")
    second = _sound_clip(server, tmp_path, name="b.mp4")
    release = threading.Event()
    started = []

    def hang(source, dest, on_start=None):
        started.append(dest)
        release.wait(30)
        raise RuntimeError("stopped")

    monkeypatch.setattr(ffmpeg_utils, "make_playable_copy", hang)
    try:
        for pic in (first, second):
            assert copier.post(COPY.format(id=pic), json={}).json()["state"] == (
                "preparing"
            )
        deadline = time.monotonic() + 10
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        server._clear_thumbnail_runtime_cache()
    finally:
        release.set()
    _thumbnails._copy_executor.submit(lambda: None).result(timeout=30)
    root = server.vault.image_root
    assert started == [ImageUtils.video_copy_path(root, first)]


def test_shutdown_cancels_queued_copies_too(server, monkeypatch):
    called = []
    monkeypatch.setattr(
        server, "_clear_thumbnail_runtime_cache", lambda: called.append(True)
    )
    # Leave the shared server as it was: no close, no generation clean-up.
    monkeypatch.setattr(server.vault, "close", lambda: None)
    monkeypatch.setattr(server, "library_switch", None)
    server._close_active_vault()
    assert called == [True]


def test_the_status_reads_disk_and_db_outside_the_jobs_lock(
    copier, server, tmp_path, monkeypatch
):
    pic = _sound_clip(server, tmp_path)
    lock = _closure(server._clear_thumbnail_runtime_cache)["copy_jobs_lock"]
    held = []
    real = VideoUtils.describe

    def describe(path):
        held.append(lock.locked())
        return real(path)

    monkeypatch.setattr(VideoUtils, "describe", describe)
    _status(copier, pic)
    copier.post(COPY.format(id=pic), json={"confirm": False})
    _until_settled(copier, pic)
    assert held and not any(held)


def _clip_for(ext, path):
    exe = ffmpeg_utils.ffmpeg_executable()
    codecs = ["-c:v", "libvpx-vp9", "-c:a", "libopus"] if ext == ".webm" else []
    subprocess.run(
        [exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
        + ["testsrc=d=1:s=64x32:r=10", "-f", "lavfi", "-i", "sine=d=1"]
        + (codecs or ["-c:v", "mpeg4", "-c:a", "aac"])
        + ["-shortest", path],
        check=True,
    )
    return path


@pytest.mark.parametrize("ext", VIDEO_EXTENSIONS)
def test_every_accepted_container_passes_the_input_whitelist(tmp_path, ext):
    source = _clip_for(ext, str(tmp_path / f"clip{ext}"))
    dest = str(tmp_path / "copy.mp4")
    ffmpeg_utils.make_playable_copy(source, dest)
    assert _fourcc(dest) in ("h264", "avc1")
    duration, peaks = ffmpeg_utils.audio_peaks(source)
    assert len(peaks) == 512 and duration > 0.9


def test_ffmpeg_follows_no_playlist_and_no_url(tmp_path):
    _ffmpeg_clip(str(tmp_path / "real.mp4"))
    # A concat playlist named like a clip would make ffmpeg read another file.
    playlist = tmp_path / "playlist.mp4"
    playlist.write_text("ffconcat version 1.0\nfile 'real.mp4'\n")
    assert ffmpeg_utils.audio_peaks(str(playlist)) is None
    with pytest.raises(RuntimeError):
        ffmpeg_utils.make_playable_copy(str(playlist), str(tmp_path / "c.mp4"))
    probe = subprocess.run(
        [ffmpeg_utils.ffmpeg_executable(), "-nostdin"]
        + ffmpeg_utils._input("tcp://127.0.0.1:9"),
        capture_output=True,
        text=True,
    )
    assert "not on whitelist" in probe.stderr


def test_a_failed_waveform_is_cached_until_the_original_changes(
    copier, server, tmp_path, monkeypatch
):
    pic = _sound_clip(server, tmp_path)
    calls = []

    def broken(source):
        calls.append(source)
        raise RuntimeError("forced")

    monkeypatch.setattr(ffmpeg_utils, "audio_peaks", broken)
    for _ in range(2):
        r = copier.get(WAVEFORM.format(id=pic))
        assert r.status_code == 200
        assert r.json() == {"duration": None, "peaks": None}
    assert len(calls) == 1
    source = ImageUtils.resolve_picture_path(
        server.vault.image_root, _file_path(server, pic)
    )
    os.utime(source, (time.time() + 60, time.time() + 60))
    copier.get(WAVEFORM.format(id=pic))
    assert len(calls) == 2


def test_a_corrupt_waveform_cache_is_read_again(copier, server, tmp_path):
    pic = _sound_clip(server, tmp_path)
    cached = ImageUtils.derived_media_path(
        server.vault.image_root, _file_path(server, pic), "waveform"
    )
    os.makedirs(os.path.dirname(cached), exist_ok=True)
    open(cached, "w").write('{"duration": 1.0, "pea')
    r = copier.get(WAVEFORM.format(id=pic))
    assert r.status_code == 200 and len(r.json()["peaks"]) == 512


def test_the_owner_only_copies_routes_hold_at_the_gate(
    copier, server, tmp_path, monkeypatch
):
    """Same negatives with the READ-token belts emptied: the gate alone refuses."""
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PATHS", frozenset())
    monkeypatch.setattr(auth, "READ_BLOCKED_GET_PREFIXES", ())
    pic = _sound_clip(server, tmp_path)
    anon = TestClient(server.api)
    scoped = _mint(copier, scope="READ", resource_type="picture", resource_id=pic)
    for token in (scoped, _mint(copier, scope="READ"), _mint(copier, scope="COMFYUI")):
        r = anon.get(COPIES, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403, r.text
    # The positive control on the same emptied belts.
    assert copier.get(COPIES).status_code == 200


def test_the_waveform_decodes_at_most_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg_utils, "_PEAK_MAX_SECONDS", 0.5)
    duration, _ = ffmpeg_utils.audio_peaks(_ffmpeg_clip(str(tmp_path / "s.mp4")))
    assert abs(duration - 0.5) < 0.05


# ---------------------------------------------------------------------------
# Which ffmpeg, and which H.264 encoder (no GPL ffmpeg is shipped)
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_resolver():
    """Forget the cached ffmpeg and encoder, before and after."""
    for fn in (ffmpeg_utils.ffmpeg_executable, ffmpeg_utils.h264_encoder):
        fn.cache_clear()
    yield
    for fn in (ffmpeg_utils.ffmpeg_executable, ffmpeg_utils.h264_encoder):
        fn.cache_clear()


def test_the_env_var_wins_over_path(fresh_resolver, tmp_path, monkeypatch):
    own = tmp_path / "ffmpeg"
    own.write_bytes(b"")
    monkeypatch.setenv(ffmpeg_utils.FFMPEG_ENV, str(own))
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert ffmpeg_utils.ffmpeg_executable() == str(own)


def test_a_missing_env_path_warns_and_falls_back_to_path(
    fresh_resolver, tmp_path, monkeypatch, caplog
):
    gone = str(tmp_path / "nowhere" / "ffmpeg")
    monkeypatch.setenv(ffmpeg_utils.FFMPEG_ENV, gone)
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    with caplog.at_level(logging.WARNING):
        assert ffmpeg_utils.ffmpeg_executable() == "/usr/bin/ffmpeg"
    assert gone in caplog.text


def test_no_env_and_nothing_on_path_is_none(fresh_resolver, monkeypatch):
    monkeypatch.delenv(ffmpeg_utils.FFMPEG_ENV, raising=False)
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: None)
    assert ffmpeg_utils.ffmpeg_executable() is None
    assert ffmpeg_utils.h264_encoder() is None


def _encoders(*names) -> str:
    rows = "".join(f" V....D {name:<20} some encoder\n" for name in names)
    return "Encoders:\n V..... = Video\n ------\n" + rows + " A....D aac  AAC\n"


@pytest.mark.parametrize(
    "names, chosen",
    [
        (("libx264", "libopenh264"), "libopenh264"),
        (("h264_videotoolbox", "libx264", "libopenh264"), "libopenh264"),
        (("libx264", "h264_videotoolbox"), "h264_videotoolbox"),
        (("h264_videotoolbox",), "h264_videotoolbox"),
        (("libx264",), "libx264"),
        (("mpeg4", "libvpx-vp9"), None),
    ],
)
def test_the_h264_encoder_preference(names, chosen):
    assert ffmpeg_utils.pick_h264_encoder(_encoders(*names)) == chosen


def test_no_h264_encoder_means_no_copy_but_a_waveform(
    copier, server, tmp_path, monkeypatch
):
    pic = _sound_clip(server, tmp_path)
    monkeypatch.setattr(ffmpeg_utils, "h264_encoder", lambda: None)
    status = _status(copier, pic)
    assert status["state"] == "unavailable" and "H.264" in status["detail"]
    assert (
        copier.post(COPY.format(id=pic), json={"confirm": True}).json()["state"]
        == "unavailable"
    )
    assert len(copier.get(WAVEFORM.format(id=pic)).json()["peaks"]) == 512


def test_ffmpeg_never_inherits_pixlstash_variables(
    fresh_resolver, tmp_path, monkeypatch
):
    monkeypatch.setenv("PIXLSTASH_DESKTOP_SESSION", "example-session-token")
    envs = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        envs.append(kwargs.get("env"))
        return real_popen(*args, **kwargs)

    source = _ffmpeg_clip(str(tmp_path / "s.mp4"))
    # subprocess.run starts its process through Popen too.
    monkeypatch.setattr(ffmpeg_utils.subprocess, "Popen", popen)
    ffmpeg_utils.h264_encoder.cache_clear()
    ffmpeg_utils.make_playable_copy(source, str(tmp_path / "c" / "1.mp4"))
    ffmpeg_utils.audio_peaks(source)
    # -encoders, the copy, the probe and the decode.
    assert len(envs) == 4
    for env in envs:
        assert env is not None and "PATH" in env
        assert not [key for key in env if key.startswith("PIXLSTASH_")]

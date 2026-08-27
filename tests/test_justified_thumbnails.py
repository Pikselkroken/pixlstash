"""Tests for the single whole-frame AR-bitmap thumbnail + stored square crop.

Covers the sizing function (short-edge target + long-edge cap + no-upscale), the
mode-agnostic whole-frame ``render_thumbnail``, the face-weighted square-crop
rectangle, the ``ThumbnailGenerationTask`` regeneration, the endpoint's
bitmap-space bbox mapping, the config-mode enum validation, and the removal of
the per-switch regeneration machinery.
"""

import os
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from pixlstash.utils.image_processing.face_utils import FaceUtils
from pixlstash.utils.image_processing.image_utils import (
    ImageUtils,
    THUMBNAIL_SHORT_EDGE,
    THUMBNAIL_LONG_EDGE_CAP,
)
from pixlstash.utils.service.user_settings_utils import apply_user_config_patch
from pixlstash.tasks.thumbnail_generation_task import ThumbnailGenerationTask


def _solid_image(w, h, colour=(120, 60, 200)):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = colour
    return Image.fromarray(arr, "RGB")


def _thumb_dims(thumbnail_bytes):
    with Image.open(BytesIO(thumbnail_bytes)) as im:
        return im.size  # (w, h)


# ── thumbnail_bitmap_size ───────────────────────────────────────────────────


def test_bitmap_size_landscape_short_edge_is_target():
    w, h = ImageUtils.thumbnail_bitmap_size(1920, 1080)
    assert min(w, h) == THUMBNAIL_SHORT_EDGE
    assert abs((w / h) - (1920 / 1080)) < 0.01
    assert max(w, h) <= THUMBNAIL_LONG_EDGE_CAP


def test_bitmap_size_portrait_short_edge_is_target():
    w, h = ImageUtils.thumbnail_bitmap_size(1080, 1920)
    assert min(w, h) == THUMBNAIL_SHORT_EDGE
    assert w < h


def test_bitmap_size_square():
    assert ImageUtils.thumbnail_bitmap_size(512, 512) == (384, 384)


def test_bitmap_size_long_edge_cap_shrinks_short_edge():
    # 4:1 exceeds cap/short (1024/384 ≈ 2.67): long edge pinned to the cap and
    # the short edge falls below the 384 target.
    w, h = ImageUtils.thumbnail_bitmap_size(4000, 1000)
    assert max(w, h) == THUMBNAIL_LONG_EDGE_CAP
    assert min(w, h) < THUMBNAIL_SHORT_EDGE
    assert w == 1024 and h == 256


def test_bitmap_size_extreme_panorama():
    # 12:1 - long edge hits the cap, short edge is a thin strip; square side then
    # equals that short edge (the accepted extreme-panorama edge case).
    w, h = ImageUtils.thumbnail_bitmap_size(6000, 500)
    assert w == THUMBNAIL_LONG_EDGE_CAP
    assert h == min(w, h)
    assert h < THUMBNAIL_SHORT_EDGE


def test_bitmap_size_never_upscales_small_source():
    assert ImageUtils.thumbnail_bitmap_size(200, 150) == (200, 150)


# ── render_thumbnail (whole-frame AR bitmap + square crop) ──────────────────


def test_render_whole_frame_preserves_aspect_ratio():
    img = _solid_image(1000, 500)
    thumbnail_bytes, bmp_w, bmp_h, crop = ImageUtils.render_thumbnail(img)
    assert min(bmp_w, bmp_h) == THUMBNAIL_SHORT_EDGE
    assert abs((bmp_w / bmp_h) - 2.0) < 0.02  # whole frame, AR preserved
    assert (bmp_w, bmp_h) == _thumb_dims(thumbnail_bytes)
    # Faceless landscape → square crop is horizontally centred, side == short edge.
    assert crop["side"] == min(bmp_w, bmp_h)
    assert crop["y"] == 0
    assert crop["x"] == round((bmp_w - crop["side"]) / 2.0)


def test_render_portrait_square_crop_is_top_anchored():
    img = _solid_image(500, 1000)
    _b, bmp_w, bmp_h, crop = ImageUtils.render_thumbnail(img)
    assert bmp_h > bmp_w
    assert crop["x"] == 0
    assert crop["y"] == 0  # top-anchored when no faces
    assert crop["side"] == min(bmp_w, bmp_h)


def test_render_face_weighted_square_crop_follows_face():
    # Landscape source, face on the right → the square crop shifts right of centre.
    img = _solid_image(1200, 600)
    faces = [[900, 250, 1050, 400]]  # source-space, right side
    _b, bmp_w, bmp_h, crop = ImageUtils.render_thumbnail(img, face_bboxes=faces)
    centred_x = round((bmp_w - crop["side"]) / 2.0)
    assert crop["y"] == 0
    assert crop["side"] == min(bmp_w, bmp_h)
    assert crop["x"] > centred_x  # weighted toward the face
    assert crop["x"] + crop["side"] <= bmp_w


# ── square_crop_rect (pure geometry, bitmap space) ──────────────────────────


def test_square_crop_rect_landscape_centred_no_faces():
    assert FaceUtils.square_crop_rect(800, 400, None) == (200, 0, 400)


def test_square_crop_rect_portrait_top_anchored_no_faces():
    assert FaceUtils.square_crop_rect(400, 800, None) == (0, 0, 400)


def test_square_crop_rect_landscape_moves_x_only():
    x, y, side = FaceUtils.square_crop_rect(800, 400, [[600, 150, 700, 250]])
    assert y == 0 and side == 400
    assert 0 <= x <= 400
    assert x + side <= 800
    # The face (600–700) is contained in the crop window.
    assert x <= 600 and x + side >= 700


def test_square_crop_rect_portrait_moves_y_only():
    x, y, side = FaceUtils.square_crop_rect(400, 800, [[150, 600, 250, 700]])
    assert x == 0 and side == 400
    assert y <= 600 and y + side >= 700


# ── config enum validation (display-only preference) ────────────────────────


@pytest.mark.parametrize("value", ["square", "justified", "JUSTIFIED", " Square "])
def test_thumbnail_mode_accepts_valid(value):
    user = SimpleNamespace(thumbnail_mode="square")
    changed = apply_user_config_patch(user, {"thumbnail_mode": value})
    assert user.thumbnail_mode in ("square", "justified")
    if value.strip().lower() != "square":
        assert changed


@pytest.mark.parametrize("value", ["grid", "tall", "1", "wide"])
def test_thumbnail_mode_rejects_invalid(value):
    user = SimpleNamespace(thumbnail_mode="square")
    with pytest.raises(ValueError):
        apply_user_config_patch(user, {"thumbnail_mode": value})


def test_thumbnail_mode_blank_resets_to_square():
    user = SimpleNamespace(thumbnail_mode="justified")
    apply_user_config_patch(user, {"thumbnail_mode": ""})
    assert user.thumbnail_mode == "square"


# ── ThumbnailGenerationTask (mode-agnostic regeneration) ────────────────────


class _FakeDB:
    def __init__(self, image_root):
        self.image_root = image_root


def _write_source(tmp_path, name, w, h):
    path = tmp_path / name
    _solid_image(w, h).save(path)
    return path


def test_task_generates_whole_frame_bitmap_and_square_crop(tmp_path):
    _write_source(tmp_path, "a.png", 1000, 500)
    pic = SimpleNamespace(id=1, file_path="a.png", width=1000, height=500, faces=[])
    task = ThumbnailGenerationTask(_FakeDB(str(tmp_path)), [pic])
    columns = task._resolve_columns(pic)
    assert columns is not None
    assert min(columns["thumbnail_width"], columns["thumbnail_height"]) == 384
    assert columns["thumbnail_width"] != columns["thumbnail_height"]  # AR preserved
    assert columns["square_crop_side"] == min(
        columns["thumbnail_width"], columns["thumbnail_height"]
    )
    assert columns["square_crop_y"] == 0  # landscape
    # File written and its dims match the recorded bitmap dims.
    thumb = ImageUtils.get_thumbnail_path(str(tmp_path), "a.png")
    assert os.path.exists(thumb)
    with Image.open(thumb) as im:
        assert im.size == (columns["thumbnail_width"], columns["thumbnail_height"])


def test_task_square_crop_follows_faces(tmp_path):
    _write_source(tmp_path, "b.png", 1200, 600)
    face = SimpleNamespace(bbox=[900, 250, 1050, 400])  # right side
    pic = SimpleNamespace(id=2, file_path="b.png", width=1200, height=600, faces=[face])
    task = ThumbnailGenerationTask(_FakeDB(str(tmp_path)), [pic])
    columns = task._resolve_columns(pic)
    centred = round((columns["thumbnail_width"] - columns["square_crop_side"]) / 2.0)
    assert columns["square_crop_x"] > centred


def test_task_returns_none_for_missing_source(tmp_path):
    pic = SimpleNamespace(id=3, file_path="gone.png", width=100, height=100, faces=[])
    task = ThumbnailGenerationTask(_FakeDB(str(tmp_path)), [pic])
    assert task._resolve_columns(pic) is None


# ── endpoint bbox mapping (source → AR-bitmap space) ────────────────────────


def _map_bbox(bbox, pic):
    """Replicate ``_thumbnails.map_bbox_to_thumbnail`` (the shared contract)."""
    out_w, out_h = pic.thumbnail_width, pic.thumbnail_height
    pic_w, pic_h = pic.width, pic.height
    target_ar = out_w / float(out_h)
    if abs((pic_w / float(pic_h)) - target_ar) <= abs(
        (pic_h / float(pic_w)) - target_ar
    ):
        src_w, src_h = float(pic_w), float(pic_h)
    else:
        src_w, src_h = float(pic_h), float(pic_w)
    sx, sy = out_w / src_w, out_h / src_h
    x1, y1, x2, y2 = bbox
    return [
        int(round(max(0.0, min(float(out_w), x1 * sx)))),
        int(round(max(0.0, min(float(out_h), y1 * sy)))),
        int(round(max(0.0, min(float(out_w), x2 * sx)))),
        int(round(max(0.0, min(float(out_h), y2 * sy)))),
    ]


def test_bbox_maps_into_bitmap_space():
    pic = SimpleNamespace(
        width=1200, height=600, thumbnail_width=768, thumbnail_height=384
    )
    face = [560, 270, 640, 330]  # centred at (600, 300)
    x1, y1, x2, y2 = _map_bbox(face, pic)
    assert 0 <= x1 < x2 <= pic.thumbnail_width
    assert 0 <= y1 < y2 <= pic.thumbnail_height
    # Centre maps to the bitmap centre (uniform 0.64 scale).
    assert abs(((x1 + x2) / 2) - pic.thumbnail_width / 2) < 2
    assert abs(((y1 + y2) / 2) - pic.thumbnail_height / 2) < 2


def test_bbox_mapping_handles_exif_rotation_swap():
    # Stored dims un-rotated (landscape) but the bitmap is portrait (EXIF 90°):
    # the mapper must scale by the swapped source dimension.
    pic = SimpleNamespace(
        width=4000, height=3000, thumbnail_width=384, thumbnail_height=512
    )
    x1, y1, x2, y2 = _map_bbox([1500, 2000, 1600, 2100], pic)
    assert 0 <= x1 < x2 <= pic.thumbnail_width
    assert 0 <= y1 < y2 <= pic.thumbnail_height


# ── per-switch regeneration machinery is gone ───────────────────────────────


def test_no_thumbnail_mode_regen_machinery():
    import pixlstash.routes.config as config_module
    import pixlstash.vault as vault_module

    # The daemon-thread requeue helper was removed entirely.
    assert not hasattr(config_module, "_requeue_thumbnails_for_mode_change")
    # The vault no longer caches or exposes a generation-time thumbnail mode.
    assert not hasattr(vault_module.Vault, "set_thumbnail_mode")
    assert not hasattr(vault_module.Vault, "thumbnail_mode")


def test_mode_patch_only_touches_the_user_preference():
    # Switching mode mutates the user's preference and nothing thumbnail-related
    # on any picture - generation never reads it.
    user = SimpleNamespace(thumbnail_mode="square", thumbnail_width=999)
    apply_user_config_patch(user, {"thumbnail_mode": "justified"})
    assert user.thumbnail_mode == "justified"
    assert user.thumbnail_width == 999  # untouched


# ---------------------------------------------------------------------------
# Regeneration announces itself
# ---------------------------------------------------------------------------


class _RecordingDB(_FakeDB):
    """A fake DB whose ``run_task`` just runs the persist callable's contract."""

    def run_task(self, fn, *args, **kwargs):
        # The real persist writes columns and returns a count; the announcement
        # is driven off the update dict, not off this value.
        return len(args[0]) if args else 0


def test_regeneration_announces_the_pictures_it_repaired(tmp_path):
    """Silence here is why a rotate's undo needed a full page reload.

    While a picture sits at ``thumbnail_width IS NULL`` it has no stored aspect
    ratio, so its card lays out with the wrong shape, and its cache token is
    ``"0"``. This task fixes both - and used to tell nobody, so an open grid kept
    painting the pre-rotate tile until the whole view was reloaded by hand.
    """
    _write_source(tmp_path, "announced.png", 800, 400)
    pic = SimpleNamespace(
        id=77, file_path="announced.png", width=800, height=400, faces=[]
    )
    announcements = []
    task = ThumbnailGenerationTask(
        _RecordingDB(str(tmp_path)),
        [pic],
        notifier=lambda event, data: announcements.append((event, data)),
    )

    task._run_task()

    assert len(announcements) == 1, "one batch, one announcement"
    _event, data = announcements[0]
    assert data["picture_ids"] == [77]
    assert data["change_kind"] == "updated"
    assert data["fields"] == ["pixels"], (
        "the client repairs a changed thumbnail off `pixels`; any other field "
        "name means it re-reads metadata and keeps the stale bitmap"
    )


def test_regeneration_announces_nothing_when_it_changed_nothing(tmp_path):
    """A batch whose sources are all missing must not raise a phantom repaint."""
    pic = SimpleNamespace(id=78, file_path="gone.png", width=10, height=10, faces=[])
    announcements = []
    task = ThumbnailGenerationTask(
        _RecordingDB(str(tmp_path)),
        [pic],
        notifier=lambda event, data: announcements.append((event, data)),
    )

    task._run_task()

    assert announcements == []


def test_a_broken_notifier_cannot_fail_a_persisted_batch(tmp_path):
    """The bitmap is already on disk by then; losing the announcement is the
    lesser failure and must not be escalated into a failed task."""
    _write_source(tmp_path, "noisy.png", 800, 400)
    pic = SimpleNamespace(id=79, file_path="noisy.png", width=800, height=400, faces=[])

    def _explode(event, data):
        raise RuntimeError("socket is gone")

    task = ThumbnailGenerationTask(
        _RecordingDB(str(tmp_path)), [pic], notifier=_explode
    )

    assert task._run_task() == {"changed_count": 1}

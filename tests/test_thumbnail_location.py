"""Thumbnails live in ``image_root/.pixlstash-thumbnails/`` (#1164).

Before this a managed picture's thumbnail sat beside it as ``<stem>_thumb.webp``
and a reference-folder picture's under ``.ref_thumbs/``. Both are still found,
moved home on first read, and deleted with their picture.
"""

import os

from pixlstash.utils.image_processing.image_utils import (
    THUMBNAIL_DIR_NAME,
    ImageUtils,
)


def _touch(path: str, payload: bytes = b"webp") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


def test_every_thumbnail_is_under_the_hidden_folder(tmp_path):
    root = str(tmp_path)
    managed = ImageUtils.get_thumbnail_path(root, "Mira/shoot_01.png")
    reference = ImageUtils.get_thumbnail_path(root, "/elsewhere/refs/shoot_01.png")

    for thumb in (managed, reference):
        assert os.path.dirname(thumb) == os.path.join(root, THUMBNAIL_DIR_NAME)
        assert os.path.basename(thumb).startswith("shoot_01_")
        assert thumb.endswith("_thumb.webp")
    # Same stem, different path: different bitmap. The name is a hash of the
    # STORED path, so two pictures cannot share one thumbnail by accident.
    assert managed != reference
    assert managed != ImageUtils.get_thumbnail_path(root, "Jonas/shoot_01.png")


def test_a_sibling_thumbnail_is_moved_home_on_first_read(tmp_path):
    root = str(tmp_path)
    _touch(os.path.join(root, "Mira", "a.png"))
    legacy = _touch(os.path.join(root, "Mira", "a_thumb.webp"), b"legacy")
    home = ImageUtils.get_thumbnail_path(root, "Mira/a.png")
    assert not os.path.exists(home)

    found = ImageUtils.find_thumbnail(root, "Mira/a.png")

    assert found == home
    assert not os.path.exists(legacy), "the owner's folder must be left clean"
    with open(home, "rb") as fh:
        assert fh.read() == b"legacy", "moved, not re-rendered"
    # Second read: nothing left to move, same answer.
    assert ImageUtils.find_thumbnail(root, "Mira/a.png") == home


def test_a_ref_thumbs_thumbnail_is_moved_home_on_first_read(tmp_path):
    root = str(tmp_path)
    stored = os.path.join(str(tmp_path / "refs"), "b.png")
    home = ImageUtils.get_thumbnail_path(root, stored)
    legacy = _touch(
        os.path.join(root, ".ref_thumbs", os.path.basename(home)), b"legacy"
    )

    assert ImageUtils.find_thumbnail(root, stored) == home
    assert not os.path.exists(legacy)
    assert os.path.isfile(home)


def test_nothing_anywhere_is_none(tmp_path):
    assert ImageUtils.find_thumbnail(str(tmp_path), "Mira/none.png") is None
    assert ImageUtils.find_thumbnail(str(tmp_path), None) is None


def test_remove_thumbnail_clears_the_legacy_home_too(tmp_path):
    root = str(tmp_path)
    _touch(os.path.join(root, "Mira", "c.png"))
    legacy = _touch(os.path.join(root, "Mira", "c_thumb.webp"))
    home = _touch(ImageUtils.get_thumbnail_path(root, "Mira/c.png"))

    assert ImageUtils.remove_thumbnail(root, "Mira/c.png") == 2
    assert not os.path.exists(legacy)
    assert not os.path.exists(home)
    assert ImageUtils.remove_thumbnail(root, "Mira/c.png") == 0


def test_write_lands_in_the_hidden_folder(tmp_path):
    root = str(tmp_path)
    written = ImageUtils.write_thumbnail_bytes(root, "Mira/d.png", b"bytes")

    assert written == ImageUtils.get_thumbnail_path(root, "Mira/d.png")
    assert os.path.isfile(written)
    assert not os.path.exists(os.path.join(root, "Mira", "d_thumb.webp"))

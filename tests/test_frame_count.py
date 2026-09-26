"""``ImageUtils.frame_count``: the Frames row in the lightbox's info panel."""

import cv2
import numpy as np
from PIL import Image

from pixlstash.utils.image_processing.image_utils import ImageUtils


def test_a_still_image_is_one_frame(tmp_path):
    path = tmp_path / "still.png"
    Image.new("RGB", (8, 8), "red").save(path)
    assert ImageUtils.frame_count(str(path)) == 1


def test_an_animated_gif_counts_its_frames(tmp_path):
    path = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (8, 8), c) for c in ("red", "green", "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=50)
    assert ImageUtils.frame_count(str(path)) == 3


def test_a_video_counts_its_frames(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (16, 16))
    for i in range(5):
        writer.write(np.full((16, 16, 3), i * 40, dtype=np.uint8))
    writer.release()
    assert ImageUtils.frame_count(str(path)) == 5


def test_a_missing_or_unreadable_file_is_none(tmp_path):
    assert ImageUtils.frame_count(str(tmp_path / "gone.png")) is None
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image")
    assert ImageUtils.frame_count(str(junk)) is None

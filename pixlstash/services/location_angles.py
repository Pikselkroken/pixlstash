"""More angles of one location from one picture of it (#2819).

The sheets (#2817) draw a *new* place four ways. This answers "give me more
angles of exactly this place": one image-to-video run films four static
tripod shots of it joined by hard cuts, the first being the picture itself;
the sharpest frame of each new shot is taken; and an image model restores
each frame's detail from the frame (img2img), with the picture as its second
reference. The two runs are the built-in workflows *MiniMax H3 Location Shots*
and *MiniMax H3 Restore a Frame*; this module writes the shots prompt, finds
the frames and checks the restored views.

A static shot's frames are sharp, and a restore from the frame keeps its
framing. A restore from noise can flip back to the original, which
:func:`restore_check` catches.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from pixlstash.image_plugins.service import import_derived_outputs
from pixlstash.pixl_logging import get_logger
from pixlstash.services.character_sheet import (
    SheetNotFound,
    SheetUnreadable,
    pictures_in_session,
    unlink_from_sheet_in_session,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils

logger = get_logger(__name__)

# What each new shot looks at, from where. `{words}` is the owner's, and says
# what the shot shows; a preset alone still names a camera move.
SHOT_PRESETS = {
    "closer": "cut to a static shot from closer in, looking at {words}",
    "opposite": "cut to a static shot facing the opposite side straight on: {words}",
    "reverse": (
        "cut to a static shot from the far end, looking back towards where "
        "the first shot's camera stands: {words}"
    ),
    "left": "cut to a static shot turned to the left: {words}",
    "right": "cut to a static shot turned to the right: {words}",
}
DEFAULT_WORDS = {
    "closer": "the place's main feature",
    "opposite": "what faces the first shot's camera",
    "reverse": "the first shot's left on the right, its right on the left",
    "left": "what is to the left of the first shot",
    "right": "what is to the right of the first shot",
}
SHOT_SECONDS = 2
# Frames right after a cut still carry the cut's blend and the settle.
SETTLE_FRAMES = 6
# A cut is a frame difference far above the static shots' (~0.3 against 20+
# while a camera turns, on 0-255 grey).
CUT_FLOOR = 8.0
CUT_OVER_MEDIAN = 6.0
# A restored view must still be its frame: grey correlation at 128 px.
RESTORE_KEEPS = 0.98


class ShotsNotFound(ValueError):
    """The video does not hold the cuts it was asked for."""


def location_shots_prompt(
    description: str,
    layout: str,
    shots: list[dict],
    light: str = "plain daylight",
) -> str:
    """The shots video's prompt: shot 1 is the picture, then one per row.

    *layout* is where each unique feature is as seen from the picture, said
    once (the model keeps it; "exactly one" for each keeps it from repeating
    one). Each shot is ``{preset, words}``.
    """
    place = description.strip().rstrip(".") or "the place in <Picture 1>"
    lines = [
        f"Gimbal footage inside {place}, {light.strip() or 'plain daylight'}, no people."
    ]
    if layout.strip():
        lines.append(layout.strip().rstrip(".") + ".")
    lines.append("The place and everything in it stay exactly the same throughout.")
    lines.append(
        f"A sequence of {len(shots) + 1} static shots of the same place, each held "
        "perfectly still on a tripod, joined by hard cuts."
    )
    lines.append("SHOT 1: exactly <Picture 1>, held still.")
    for number, shot in enumerate(shots, start=2):
        preset = shot.get("preset") if shot.get("preset") in SHOT_PRESETS else "closer"
        words = (shot.get("words") or "").strip() or DEFAULT_WORDS[preset]
        lines.append(f"SHOT {number}: {SHOT_PRESETS[preset].format(words=words)}.")
    return " ".join(lines)


def shots_seconds(shot_count: int) -> int:
    """The video's length: two seconds a shot, the picture's included."""
    return SHOT_SECONDS * (shot_count + 1)


def find_cuts(diffs: list[float], cuts: int) -> list[int]:
    """The frame index each of *cuts* hard cuts starts at.

    *diffs[i]* is the mean grey difference between frames *i* and *i+1*. A
    run of high differences is one cut. Raises :class:`ShotsNotFound` when
    the video holds another number of cuts: a guess would hand back frames of
    the wrong shot.
    """
    values = np.asarray(diffs, dtype=float)
    if values.size == 0:
        raise ShotsNotFound("The video has no frames to read.")
    threshold = max(CUT_FLOOR, CUT_OVER_MEDIAN * float(np.median(values)))
    found: list[int] = []
    previous = False
    for index, value in enumerate(values):
        high = value > threshold
        if high and not previous:
            found.append(index + 1)
        previous = high
    if len(found) != cuts:
        raise ShotsNotFound(
            f"The video has {len(found)} hard cuts where {cuts} were asked for, "
            "so its shots can't be told apart. Draw it again."
        )
    return found


def sharpest_frames(path: str, shot_count: int) -> list[int]:
    """The sharpest frame index of each new shot (the picture's shot left out).

    Sharpness is the Laplacian variance, with motion weighted against, past
    the first frames after each cut.
    """
    capture = cv2.VideoCapture(path)
    diffs: list[float] = []
    sharpness: list[float] = []
    previous = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(grey, (64, 36), interpolation=cv2.INTER_AREA).astype(
                np.float32
            )
            if previous is not None:
                diffs.append(float(np.mean(np.abs(small - previous))))
            previous = small
            sharpness.append(float(cv2.Laplacian(grey, cv2.CV_64F).var()))
    finally:
        capture.release()
    if len(sharpness) < 2:
        raise ShotsNotFound("The video could not be read frame by frame.")
    starts = find_cuts(diffs, shot_count)
    bounds = [*starts, len(sharpness)]
    picked: list[int] = []
    for start, end in zip(bounds, bounds[1:]):
        first = min(start + SETTLE_FRAMES, end - 1)
        motion = [
            diffs[i - 1] if 0 < i <= len(diffs) else 0.0 for i in range(first, end)
        ]
        scores = [sharpness[i] / (1.0 + motion[i - first]) for i in range(first, end)]
        picked.append(first + int(np.argmax(scores)))
    return picked


def frames_of_video(server, video_id: int, shot_count: int) -> list[int]:
    """Import the sharpest frame of each new shot as a picture made from the
    video, in shot order. Raises :class:`SheetNotFound`,
    :class:`SheetUnreadable` or :class:`ShotsNotFound`."""
    found = server.vault.db.run_immediate_read_task(pictures_in_session, [video_id])
    if found is None:
        raise SheetNotFound(f"No picture {video_id}.")
    video = found[0]
    path = ImageUtils.resolve_picture_path(server.vault.image_root, video.file_path)
    if not path or not VideoUtils.is_video_file(path):
        raise SheetUnreadable(
            f"Picture {video_id} is not a video this server can read."
        )
    indices = sharpest_frames(path, shot_count)
    frames = VideoUtils.extract_video_frames(path, indices)
    if len(frames) != len(indices):
        raise SheetUnreadable(f"Couldn't read frames {indices} of video {video_id}.")
    entries = []
    for frame in frames:
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG")
        entries.append((buffer.getvalue(), ".png"))
    _new, _duplicates, made = import_derived_outputs(
        server, [video] * len(entries), entries, stack=False
    )
    server.vault.db.run_task(unlink_from_sheet_in_session, made)
    logger.info(
        "Location shots video %s: frames %s became pictures %s", video_id, indices, made
    )
    return made


def _grey(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L").resize((128, 72)), dtype=np.float64).ravel()


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def restore_check(
    frame: Image.Image, restored: Image.Image, original: Image.Image
) -> dict:
    """Whether a restored view is still its frame and not the original again.

    ``{ok, reason, frame_match, original_match}``: a restore that drifted off
    its frame, or that matches the picture it was restored with better than
    its frame, has flipped back to that picture.
    """
    restored_grey = _grey(restored)
    frame_match = _correlation(restored_grey, _grey(frame))
    original_match = _correlation(restored_grey, _grey(original))
    if original_match > frame_match:
        reason = "It turned back into the original picture."
    elif frame_match < RESTORE_KEEPS:
        reason = "It drifted from the view it was restored from."
    else:
        reason = ""
    return {
        "ok": not reason,
        "reason": reason,
        "frame_match": round(frame_match, 4),
        "original_match": round(original_match, 4),
    }


def check_restored(
    server, original_id: int, pairs: list[tuple[int, int]]
) -> list[dict]:
    """:func:`restore_check` for each ``(frame_id, restored_id)``."""
    ids = [original_id, *[pid for pair in pairs for pid in pair]]
    pictures = server.vault.db.run_immediate_read_task(pictures_in_session, ids)
    if pictures is None:
        raise SheetNotFound(f"Not every picture among {ids} exists.")
    by_id = {p.id: p for p in pictures}

    def load(pid: int) -> Image.Image:
        path = ImageUtils.resolve_picture_path(
            server.vault.image_root, by_id[pid].file_path
        )
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except (OSError, TypeError) as exc:
            logger.warning(
                "Couldn't read picture %s at %s to check it: %s", pid, path, exc
            )
            raise SheetUnreadable(f"Picture {pid} can't be read as an image.") from exc

    original = load(original_id)
    results = []
    for frame_id, restored_id in pairs:
        results.append(
            {
                "frame_id": frame_id,
                "restored_id": restored_id,
                **restore_check(load(frame_id), load(restored_id), original),
            }
        )
    return results

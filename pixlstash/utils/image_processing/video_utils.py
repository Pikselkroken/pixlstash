"""Video frame extraction and metadata utilities."""

import cv2
import json
import os
import struct
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np
from PIL import Image

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# MP4/MOV timestamps count seconds since midnight, 1 January 1904 (UTC).
_MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)

# Supported video file extensions (lowercase).
VIDEO_EXTENSIONS = (".mp4", ".webm", ".avi", ".mov", ".mkv")

# The most of a file `extract_embedded_metadata` will read: the `moov` box or
# the `Tags` element. Either is kilobytes, a few megabytes with a large
# workflow in it. It is read once and walked through views, never copied.
_MAX_TAG_BYTES = 64 * 1024 * 1024

_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
# Matroska element ids, as written (the length marker is part of an id).
_MKV_SEGMENT = 0x18538067
_MKV_TAGS = 0x1254C367
_MKV_CLUSTER = 0x1F43B675
_MKV_TAG = 0x7373
_MKV_SIMPLE_TAG = 0x67C8
_MKV_TAG_NAME = 0x45A3
_MKV_TAG_STRING = 0x4487


def _text(data) -> str:
    return str(data, "utf-8", "replace")


def _boxes(data):
    """Yield ``(type, payload)`` for each ISO-BMFF box laid end to end in *data*."""
    pos = 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if pos + 16 > len(data):
                return
            size, header = struct.unpack_from(">Q", data, pos + 8)[0], 16
        elif size == 0:
            size = len(data) - pos
        if size < header or pos + size > len(data):
            return
        yield kind, data[pos + header : pos + size]
        pos += size


def _mp4_tags(handle, file_size: int) -> dict:
    """The text tags of an MP4 or MOV file, read off its ``moov`` box.

    Top-level boxes are stepped over by their size, so the media data is never
    read whichever side of it ``moov`` is on.
    """
    moov = b""
    pos = 0
    while pos + 8 <= file_size:
        handle.seek(pos)
        header = handle.read(16)
        size, kind = struct.unpack_from(">I4s", header)
        skip = 8
        if size == 1:
            size, skip = struct.unpack_from(">Q", header, 8)[0], 16
        elif size == 0:
            size = file_size - pos
        if size < skip:
            break
        if kind == b"moov":
            if size > _MAX_TAG_BYTES:
                raise ValueError(f"its moov box is {size} bytes")
            handle.seek(pos + skip)
            moov = memoryview(handle.read(size - skip))
            break
        pos += size

    tags: dict = {}
    top = dict(_boxes(moov))
    user = dict(_boxes(top.get(b"udta", b"")))
    # QuickTime's own comment atom: [length:2][language:2][text]. The length
    # is 16 bits and wraps on a longer text, which a workflow usually is, so
    # the box's own size is what bounds it.
    comment = user.get(b"\xa9cmt", b"")
    if len(comment) > 4:
        tags["comment"] = _text(comment[4:])
    for meta in (user.get(b"meta"), top.get(b"meta")):
        if not meta:
            continue
        # A full box in MP4 (version and flags first), a plain one in QuickTime.
        inner = dict(_boxes(meta if meta[4:8] == b"hdlr" else meta[4:]))
        # `keys` names the items `ilst` then numbers from 1; without it the
        # items are iTunes atoms, of which the comment is the one read.
        names = [name for _, name in _boxes(inner.get(b"keys", b"")[8:])]
        for kind, item in _boxes(inner.get(b"ilst", b"")):
            data = dict(_boxes(item)).get(b"data")
            if data is None or data[:4] != b"\x00\x00\x00\x01":
                continue  # Not UTF-8 text: cover art, a number.
            index = int.from_bytes(kind, "big")
            if kind == b"\xa9cmt":
                name = b"comment"
            elif 1 <= index <= len(names):
                name = names[index - 1]
            else:
                continue
            tags.setdefault(_text(name).lower(), _text(data[8:]))
    return tags


def _ebml_header(data, pos: int) -> tuple:
    """Read an EBML element's id and size at *pos*.

    Returns ``(id, size, payload position)``; the size is ``None`` when the
    element was written without one (a live stream's).
    """
    values = []
    for _ in range(2):
        if pos >= len(data) or not data[pos]:
            raise ValueError("a truncated or malformed EBML element")
        length = 9 - data[pos].bit_length()
        if pos + length > len(data):
            raise ValueError("a truncated EBML element")
        values.append((int.from_bytes(data[pos : pos + length], "big"), length))
        pos += length
    (ident, _), (size, length) = values
    mask = (1 << (7 * length)) - 1
    size &= mask
    return ident, None if size == mask else size, pos


def _ebml(data):
    """Yield ``(id, payload)`` for each EBML element laid end to end in *data*."""
    pos = 0
    while pos < len(data):
        ident, size, pos = _ebml_header(data, pos)
        end = len(data) if size is None else pos + size
        yield ident, data[pos:end]
        pos = end


def _matroska_tags(handle, file_size: int) -> dict:
    """The text tags of a WebM or Matroska file, read off its ``Tags`` elements.

    The segment's children are stepped over by their size, so no cluster is
    read. ffmpeg upper-cases the names; they come back lower-cased.
    """
    tags: dict = {}
    pos = 0
    while pos < file_size:
        handle.seek(pos)
        ident, size, used = _ebml_header(handle.read(12), 0)
        pos += used
        if ident == _MKV_SEGMENT:
            continue  # Its children are the elements this loop is after.
        if size is None:
            break  # Unsized: nothing past it can be stepped to.
        if ident == _MKV_CLUSTER and tags:
            break  # ffmpeg writes its tags ahead of the media; they are read.
        if ident == _MKV_TAGS:
            if size > _MAX_TAG_BYTES:
                raise ValueError(f"its Tags element is {size} bytes")
            handle.seek(pos)
            for tag_id, tag in _ebml(memoryview(handle.read(size))):
                if tag_id != _MKV_TAG:
                    continue
                for simple_id, simple in _ebml(tag):
                    if simple_id != _MKV_SIMPLE_TAG:
                        continue
                    fields = dict(_ebml(simple))
                    if _MKV_TAG_NAME in fields and _MKV_TAG_STRING in fields:
                        # An EBML string may be padded with NULs.
                        name, value = (
                            _text(fields[key]).rstrip("\x00")
                            for key in (_MKV_TAG_NAME, _MKV_TAG_STRING)
                        )
                        tags.setdefault(name.lower(), value)
        pos += size
    return tags


class VideoUtils:
    """Utility methods for video file handling."""

    @staticmethod
    def is_video_file(file_path: str) -> bool:
        """Return True if the file is a supported video format."""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in VIDEO_EXTENSIONS

    @staticmethod
    def is_animated_gif(file_path: str) -> bool:
        """Return True for a .gif with more than one frame.

        Only the frame-sampling sites consult this; a GIF stays a picture
        everywhere else (thumbnails, export, ``is_video``).
        """
        if os.path.splitext(file_path)[1].lower() != ".gif":
            return False
        try:
            with Image.open(file_path) as img:
                # is_animated only looks for a second frame; n_frames walks
                # the whole file.
                return bool(getattr(img, "is_animated", False))
        except Exception as exc:
            logger.debug(
                "Could not open %s to check for animation, treating it as a still: %s",
                file_path,
                exc,
            )
            return False

    @staticmethod
    def is_multiframe_file(file_path: str) -> bool:
        """Return True for a video or an animated GIF."""
        return VideoUtils.is_video_file(file_path) or VideoUtils.is_animated_gif(
            file_path
        )

    @staticmethod
    def extract_embedded_metadata(file_path: str) -> dict:
        """The text tags a video's container carries, keyed by lower-cased name.

        This is where ComfyUI's video savers write the graph they ran, as its
        image savers write PNG text chunks: a ``prompt`` and a ``workflow`` tag
        each (SaveVideo, SaveWEBM), or both inside one JSON ``comment``
        (VideoHelperSuite), which is unpacked into its keys so every writer
        reads the same. MP4/MOV and WebM/Matroska are read; any other
        container, and a file that cannot be parsed, answers ``{}``.
        """
        try:
            with open(file_path, "rb") as handle:
                file_size = os.fstat(handle.fileno()).st_size
                if handle.read(4) == _EBML_MAGIC:
                    tags = _matroska_tags(handle, file_size)
                else:
                    tags = _mp4_tags(handle, file_size)
        except (OSError, struct.error, ValueError) as exc:
            logger.warning(
                "Could not read the container tags of %s, so it is read as "
                "carrying none: %s",
                file_path,
                exc,
            )
            return {}
        comment = tags.get("comment", "")
        if comment.lstrip().startswith("{"):
            try:
                envelope = json.loads(comment)
            except (ValueError, RecursionError):
                # Somebody's own comment that happens to open with a brace.
                envelope = None
            if isinstance(envelope, dict):
                del tags["comment"]
                for key, value in envelope.items():
                    tags.setdefault(str(key).lower(), value)
        return tags

    @staticmethod
    def extract_created_at_from_bytes(data: bytes) -> Optional[datetime]:
        """Extract the recording creation time from MP4/MOV container bytes.

        Scans for the ``mvhd`` (movie header) ISOM box and reads its
        ``creation_time`` field, converting from the MP4 epoch (1904-01-01
        UTC) to a timezone-aware UTC datetime.

        Args:
            data: Raw video file bytes.

        Returns:
            UTC datetime if found, or None if no valid timestamp is present.
        """
        pos = 0
        while pos < len(data) - 8:
            idx = data.find(b"mvhd", pos)
            if idx < 4:
                # Need at least 4 bytes before 'mvhd' for the box size field.
                break
            version_pos = idx + 4  # byte immediately after the 4-byte box type
            if version_pos >= len(data):
                break
            version = data[version_pos]
            # Layout after box type: [version:1][flags:3][creation_time:4 or 8]
            ct_pos = version_pos + 4  # skip version (1) + flags (3)
            try:
                if version == 0:
                    if ct_pos + 4 > len(data):
                        break
                    ct_val = struct.unpack(">I", data[ct_pos : ct_pos + 4])[0]
                elif version == 1:
                    if ct_pos + 8 > len(data):
                        break
                    ct_val = struct.unpack(">Q", data[ct_pos : ct_pos + 8])[0]
                else:
                    pos = idx + 4
                    continue
            except struct.error:
                pos = idx + 4
                continue
            if ct_val == 0:
                # Unset / epoch-0 means not recorded.
                pos = idx + 4
                continue
            return _MP4_EPOCH + timedelta(seconds=ct_val)
        return None

    @staticmethod
    def read_first_video_frame_bgr(file_path: str) -> Optional[np.ndarray]:
        """Read the first frame of a video file and return it as a BGR numpy array."""
        cap = cv2.VideoCapture(file_path)
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            return frame
        return None

    @staticmethod
    def extract_video_frames(file_path: str, frame_indices=None) -> List[Image.Image]:
        """
        Extract frames from a video file and return them as PIL Images.

        Args:
            file_path: Path to video file.
            frame_indices: List of specific frame indices to extract (0-based).
                           If None, all frames are extracted.

        Returns:
            List of PIL.Image objects.
        """
        frames = []
        cap = cv2.VideoCapture(file_path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if frame_indices is not None:
            sorted_indices = sorted(list(set(frame_indices)))
            for idx in sorted_indices:
                if 0 <= idx < frame_count:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        pil_img = Image.fromarray(frame_rgb)
                        frames.append(pil_img)
            cap.release()
            return frames

        for idx in range(frame_count):
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            frames.append(pil_img)
        cap.release()
        return frames

    @staticmethod
    def extract_representative_video_frames(
        file_path: str, count: int = 3
    ) -> List[Image.Image]:
        """
        Extract ``count`` evenly spaced frames from a video (e.g. start, middle, end).

        Args:
            file_path: Path to video file.
            count: Number of frames to extract (evenly spaced).

        Returns:
            List of PIL.Image objects.
        """
        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        if total_frames <= 0:
            total_frames = 1

        if count == 1:
            indices = [0]
        else:
            step = (total_frames - 1) / (count - 1)
            indices = sorted(list(set([int(i * step) for i in range(count)])))

        return VideoUtils.extract_video_frames(file_path, frame_indices=indices)

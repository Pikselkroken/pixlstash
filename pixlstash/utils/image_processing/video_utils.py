"""Video frame extraction and metadata utilities."""

import cv2
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


class VideoUtils:
    """Utility methods for video file handling."""

    @staticmethod
    def is_video_file(file_path: str) -> bool:
        """Return True if the file is a supported video format."""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in [".mp4", ".webm", ".avi", ".mov", ".mkv"]

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
    def _read_first_video_frame_bgr(file_path: str) -> Optional[np.ndarray]:
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

"""Video frame extraction and metadata utilities."""

import cv2
import os
from typing import List, Optional

import numpy as np
from PIL import Image

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class VideoUtils:
    """Utility methods for video file handling."""

    @staticmethod
    def is_video_file(file_path: str) -> bool:
        """Return True if the file is a supported video format."""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in [".mp4", ".webm", ".avi", ".mov", ".mkv"]

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

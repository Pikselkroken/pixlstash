"""Built-in rotate plugin."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import cv2
import numpy as np
from PIL import Image

from pixlstash.image_plugins.base import ImagePlugin


class RotatePlugin(ImagePlugin):
    """Rotate images or videos by 90° left, 90° right or 180°."""

    name = "rotate"
    display_name = "Rotate"
    description = "Rotate images or videos by 90° left, 90° right, or 180°."
    supports_images = True
    supports_videos = True

    MODES = {
        "90_left": "90° Left (counter-clockwise)",
        "90_right": "90° Right (clockwise)",
        "180": "180°",
    }

    def parameter_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "direction",
                "label": "Rotation",
                "type": "string",
                "default": "90_right",
                "enum": list(self.MODES.keys()),
                "enumLabels": self.MODES,
                "description": "Direction and amount of rotation.",
            }
        ]

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    def get_bbox_transform(self, parameters, source_size, output_size):
        """Transform bounding boxes according to the rotation direction.

        All four corners of the source bbox are rotated as points, then the
        axis-aligned bounding box of the rotated corners is returned.
        """
        params = parameters or {}
        direction = str(params.get("direction") or "90_right").strip().lower()
        if direction not in self.MODES:
            direction = "90_right"

        src_w, src_h = source_size

        def transform(bbox: list[int]) -> list[int]:
            x1, y1, x2, y2 = bbox
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            if direction == "90_left":  # CCW: (x, y) -> (y, src_w - x)
                rotated = [(y, src_w - x) for x, y in corners]
            elif direction == "90_right":  # CW: (x, y) -> (src_h - y, x)
                rotated = [(src_h - y, x) for x, y in corners]
            else:  # 180°: (x, y) -> (src_w - x, src_h - y)
                rotated = [(src_w - x, src_h - y) for x, y in corners]
            xs = [p[0] for p in rotated]
            ys = [p[1] for p in rotated]
            return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]

        return transform

    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
        captions: list[str] | None = None,
    ) -> list[Image.Image]:
        params = parameters or {}
        direction = str(params.get("direction") or "90_right").strip().lower()
        if direction not in self.MODES:
            direction = "90_right"

        out: list[Image.Image] = []
        total = len(images)
        for idx, image in enumerate(images):
            try:
                rotated = self._rotate_image(image, direction)
                out.append(rotated)
                self.report_progress(
                    progress_callback,
                    current=idx + 1,
                    total=total,
                    message=f"Rotated image {idx + 1}/{total}",
                )
            except Exception as exc:
                self.report_error(
                    error_callback,
                    index=idx,
                    message="Failed to rotate image",
                    details={"error": str(exc)},
                )
                out.append(image.copy())
        return out

    # ------------------------------------------------------------------
    # Videos
    # ------------------------------------------------------------------

    def run_video(
        self,
        source_path: str,
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
        error_callback=None,
    ) -> tuple[bytes, str]:
        params = parameters or {}
        direction = str(params.get("direction") or "90_right").strip().lower()
        if direction not in self.MODES:
            direction = "90_right"

        cap = cv2.VideoCapture(source_path)
        if not cap.isOpened():
            raise ValueError(f"Failed to open video file: {source_path}")

        frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0:
            fps = 24.0
        src_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        src_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if src_width <= 0 or src_height <= 0:
            cap.release()
            raise ValueError(f"Invalid video dimensions for {source_path}")

        # 90° rotations swap width and height.
        if direction in ("90_left", "90_right"):
            out_width, out_height = src_height, src_width
        else:
            out_width, out_height = src_width, src_height

        temp_path = ""
        writer = None
        output_ext = ".mp4"
        processed = 0
        try:
            source_ext = os.path.splitext(source_path)[1].lower()
            writer_candidates = [
                (".mp4", "avc1"),
                (".mp4", "H264"),
                (".webm", "VP80"),
                (".webm", "VP90"),
                (".mp4", "mp4v"),
            ]
            if source_ext == ".webm":
                writer_candidates = [
                    (".webm", "VP80"),
                    (".webm", "VP90"),
                    (".mp4", "avc1"),
                    (".mp4", "H264"),
                    (".mp4", "mp4v"),
                ]

            for candidate_ext, codec in writer_candidates:
                with tempfile.NamedTemporaryFile(
                    suffix=candidate_ext,
                    delete=False,
                ) as temp_file:
                    temp_path = temp_file.name
                candidate_writer = cv2.VideoWriter(
                    temp_path,
                    cv2.VideoWriter_fourcc(*codec),
                    fps,
                    (out_width, out_height),
                )
                if candidate_writer.isOpened():
                    writer = candidate_writer
                    output_ext = candidate_ext
                    break
                candidate_writer.release()
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                temp_path = ""

            if writer is None:
                raise ValueError("Failed to open output video writer")

            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rotated = self._rotate_image(Image.fromarray(rgb_frame), direction)
                rotated_bgr = cv2.cvtColor(
                    np.array(rotated.convert("RGB")),
                    cv2.COLOR_RGB2BGR,
                )
                writer.write(rotated_bgr)

                processed += 1
                self.report_progress(
                    progress_callback,
                    current=processed,
                    total=frame_total if frame_total > 0 else processed,
                    message=f"Rotated video frame {processed}",
                )

            if processed == 0:
                raise ValueError("No frames processed from video")

            writer.release()
            writer = None
            cap.release()

            with open(temp_path, "rb") as handle:
                return handle.read(), output_ext
        except Exception as exc:
            self.report_error(
                error_callback,
                index=0,
                message="Failed to rotate video",
                details={"error": str(exc), "source_path": source_path},
            )
            raise
        finally:
            if writer is not None:
                writer.release()
            cap.release()
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rotate_image(image: Image.Image, direction: str) -> Image.Image:
        rgb = image.convert("RGB")
        if direction == "90_left":
            # ROTATE_90 in PIL is counter-clockwise.
            return rgb.transpose(Image.Transpose.ROTATE_90)
        if direction == "90_right":
            # ROTATE_270 in PIL is clockwise.
            return rgb.transpose(Image.Transpose.ROTATE_270)
        # 180°
        return rgb.transpose(Image.Transpose.ROTATE_180)

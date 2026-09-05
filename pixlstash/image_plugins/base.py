# Copyright 2026 Gaute Lindkvist
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

from __future__ import annotations

import contextlib
import os
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image

ProgressCallback = Callable[[dict[str, Any]], None]
ErrorCallback = Callable[[dict[str, Any]], None]

# Container/codec pairs tried in order when opening the output writer. Which of
# these OpenCV can actually open depends on how the local build was compiled, so
# the writer is opened by trial rather than by assumption.
_WRITER_CANDIDATES = [
    (".mp4", "avc1"),
    (".mp4", "H264"),
    (".webm", "VP80"),
    (".webm", "VP90"),
    (".mp4", "mp4v"),
]

# A .webm source keeps its container when possible, so a VP8/VP9 input does not
# silently become H.264 on output.
_WEBM_WRITER_CANDIDATES = [
    (".webm", "VP80"),
    (".webm", "VP90"),
    (".mp4", "avc1"),
    (".mp4", "H264"),
    (".mp4", "mp4v"),
]


class ImagePlugin(ABC):
    """Base class for image transformation plugins.

    Licensing note:
        This file is MIT-licensed so third-party plugin authors can depend on
        this API without requiring GPL licensing for their plugin scripts.

    Plugins receive a list of PIL images and JSON-compatible parameters,
    and return a list of PIL images in the same order. Subclasses must
    implement ``parameter_schema`` and ``run``. Optionally override
    ``run_video`` to support video inputs.

    Attributes:
        name: Unique snake_case identifier used to look up the plugin by name.
        display_name: Human-readable label shown in the UI.
        description: Short description of what the plugin does.
        author: ``Name <contact>``, where the contact is an email address or a
            URL. Empty when the plugin does not say.
        license: SPDX identifier for the plugin's *own code*, where there is
            one. Empty when the plugin does not say.
        models: One entry per model or remote service the plugin uses, each a
            dict with ``name`` (the identifier it is fetched by, e.g. a
            HuggingFace repo id) and ``license`` (an SPDX identifier where the
            model declares one, otherwise the terms it actually ships, said
            plainly); empty when it loads none. This is the field a user
            needs, because the plugin's own license says nothing about the
            weights it downloads.
        supports_images: Whether the plugin handles still images via ``run``.
        supports_videos: Whether the plugin handles video files via ``run_video``.
    """

    name: str = ""
    display_name: str = ""
    description: str = ""
    # Declared as literals on purpose: a tool reads this header off the source
    # with ``ast`` rather than importing the plugin to find out what it is.
    author: str = ""
    license: str = ""
    models: list[dict[str, str]] = []
    supports_images: bool = True
    supports_videos: bool = False

    def plugin_schema(self) -> dict[str, Any]:
        """Return the JSON-serialisable metadata dict for this plugin.

        Used by the plugin registry to expose plugin capabilities to the
        frontend. Calls ``parameter_schema`` internally.

        Returns:
            A dict with keys ``name``, ``display_name``, ``description``,
            ``author``, ``license``, ``models``, ``supports_images``,
            ``supports_videos``, and ``parameters``.

        Raises:
            TypeError: If ``models`` is not a list of dicts. The registry
                probes this at load, so the plugin is refused with the message
                below rather than the shape reaching a caller.
        """
        models = self.models or []
        if not isinstance(models, list) or not all(
            isinstance(model, dict) for model in models
        ):
            raise TypeError(
                f"{type(self).__name__}.models must be a list of "
                "{'name': ..., 'license': ...} dicts - one entry per model, "
                "a list even when there is only one"
            )
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description or "",
            "author": self.author or "",
            "license": self.license or "",
            # Copied so a caller cannot mutate the class attribute it came from.
            "models": [dict(model) for model in models],
            "supports_images": bool(self.supports_images),
            "supports_videos": bool(self.supports_videos),
            "parameters": self.parameter_schema(),
        }

    @abstractmethod
    def parameter_schema(self) -> list[dict[str, Any]]:
        """Return the parameter definitions for this plugin.

        Each entry in the list is a dict describing one user-facing parameter.
        Required keys: ``name`` (str, snake_case identifier), ``label`` (str,
        display label), ``type`` (str, one of ``"number"``, ``"integer"``,
        ``"boolean"``, ``"string"``), ``default`` (Any, value used when the
        parameter is omitted). Optional keys: ``description`` (str, help text).

        **A dropdown is ``"string"`` plus an ``enum`` list**, optionally with an
        ``enumLabels`` map from value to display text. This docstring used to
        describe a ``"select"`` type with an ``"options"`` key; no such branch
        exists in ``PluginParametersUI.vue``, so a field declared that way
        renders as a free-text input. Every built-in uses ``string`` + ``enum``.
        (Tagger plugins are a different schema and *do* have a real ``select``.)

        Returns:
            List of parameter definition dicts, one per parameter.

        See ``docs/writing-image-filter-plugins.md`` for the full contract.
        """

    @abstractmethod
    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        error_callback: ErrorCallback | None = None,
        captions: list[str] | None = None,
    ) -> list[Image.Image]:
        """Apply the plugin transform to a batch of images.

        The returned list must be the same length as ``images``. On a
        per-image failure, append a fallback (e.g. a copy of the original)
        and call ``self.report_error`` so the caller can surface the problem
        without aborting the whole batch.

        Args:
            images: Input images to process.
            parameters: Parameter values keyed by the ``name`` field from
                ``parameter_schema``. Missing keys should fall back to defaults.
            progress_callback: Optional callable invoked after each image via
                ``self.report_progress``.
            error_callback: Optional callable invoked on per-image failures via
                ``self.report_error``.
            captions: Optional list of caption strings, one per image in the
                same order as ``images``. Each entry is the stored description
                for that picture (or an empty string if none). Use these to
                drive caption-conditioned transforms.

        Returns:
            Transformed images in the same order as ``images``.
        """

    def get_bbox_transform(
        self,
        parameters: dict[str, Any] | None,
        source_size: tuple[int, int],
        output_size: tuple[int, int],
    ) -> Callable[[list[int]], list[int]] | None:
        """Return a function that maps a bbox from source to output image space.

        **Currently unused. Overriding it has no effect.** Its only caller was
        the face-copy step in ``service.py``, which copied a source picture's
        face rows onto plugin outputs. That was removed: outputs now get
        ``source_picture_id`` and nothing else, so ``MissingFaceExtractionFinder``
        detects their real faces and ``SourceFaceLikenessTask`` inherits a
        character only where the faces actually match. Copying a box through a
        transform assumed the output still contains the face, and a wrong
        assumption there wrote boxes that captured nothing.

        Kept because the contract is still the right one if bbox mapping is ever
        needed again (a crop preview, say), and because plugins outside this repo
        may already implement it. ``scaling`` and ``rotate`` still do.

        The callable receives ``[x1, y1, x2, y2]`` in source pixel coordinates
        and must return a new ``[x1, y1, x2, y2]`` in output pixel coordinates.
        Return ``None`` to indicate no mapping is available.

        Args:
            parameters: The same parameter dict passed to ``run``/``run_video``.
            source_size: ``(width, height)`` of each input image.
            output_size: ``(width, height)`` of the corresponding output image.

        Returns:
            A callable ``transform(bbox) -> bbox``, or ``None``.
        """
        return None

    def run_video(
        self,
        source_path: str,
        parameters: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        error_callback: ErrorCallback | None = None,
    ) -> bytes | tuple[bytes, str]:
        """Apply the plugin transform to a video file.

        Override this method when ``supports_videos = True``. The default
        implementation raises ``NotImplementedError``.

        Args:
            source_path: Absolute path to the input video file.
            parameters: Parameter values keyed by the ``name`` field from
                ``parameter_schema``. Missing keys should fall back to defaults.
            progress_callback: Optional callable for reporting progress.
            error_callback: Optional callable for reporting errors.

        Returns:
            Encoded video bytes, or a ``(bytes, extension)`` tuple where
            ``extension`` is the output file extension (e.g. ``".mp4"``).
        """
        raise NotImplementedError(
            f"Plugin '{self.name or self.__class__.__name__}' does not support video processing"
        )

    def report_progress(
        self,
        progress_callback: ProgressCallback | None,
        *,
        current: int,
        total: int,
        message: str,
    ) -> None:
        """Invoke the progress callback with structured progress data.

        Does nothing if ``progress_callback`` is ``None``.

        Args:
            progress_callback: Callback to invoke, or ``None``.
            current: Number of images processed so far (1-based).
            total: Total number of images in the batch.
            message: Human-readable status message.
        """
        if progress_callback is None:
            return
        progress_callback(
            {
                "plugin": self.name,
                "current": current,
                "total": total,
                "progress": (float(current) / float(total) * 100.0) if total else 0.0,
                "message": message,
            }
        )

    def report_error(
        self,
        error_callback: ErrorCallback | None,
        *,
        index: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Invoke the error callback with structured error data.

        Does nothing if ``error_callback`` is ``None``.

        Args:
            error_callback: Callback to invoke, or ``None``.
            index: Zero-based index of the image that failed.
            message: Short description of the failure.
            details: Optional dict with additional context (e.g. exception
                message, crop shape, file path).
        """
        if error_callback is None:
            return
        payload: dict[str, Any] = {
            "plugin": self.name,
            "index": index,
            "message": message,
        }
        if details:
            payload["details"] = details
        error_callback(payload)

    def transform_video(
        self,
        source_path: str,
        transform: Callable[[Image.Image], Image.Image],
        *,
        progress_callback: ProgressCallback | None = None,
        error_callback: ErrorCallback | None = None,
        error_message: str,
        progress_verb: str = "Processed",
    ) -> tuple[bytes, str]:
        """Run *transform* over every frame of a video and return the re-encoded result.

        Implements the decode → transform → encode loop shared by every
        video-capable plugin, so a subclass' ``run_video`` only has to parse its
        parameters and hand over a per-frame function.

        The output writer is sized from the *first transformed frame* rather than
        from the source dimensions. Transforms that change the frame size (a 90°
        rotation, say) therefore need no separate size arithmetic, and the writer
        can never disagree with the frames it is given, and a mismatch makes OpenCV
        drop every write silently.

        Args:
            source_path: Absolute path to the input video file.
            transform: Callable applied to each frame as an RGB PIL image; it
                must return a PIL image, and must return the same size for every
                frame.
            progress_callback: Optional callable invoked per frame.
            error_callback: Optional callable invoked once if the run fails.
            error_message: Message passed to ``report_error`` on failure.
            progress_verb: Leading word of the per-frame progress message.

        Returns:
            A ``(bytes, extension)`` tuple of the encoded video.

        Raises:
            ValueError: The source cannot be opened, reports invalid dimensions,
                yields no frames, or no output writer could be opened.
        """
        cap = cv2.VideoCapture(source_path)
        if not cap.isOpened():
            raise ValueError(f"Failed to open video file: {source_path}")

        frame_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0:
            fps = 24.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            cap.release()
            raise ValueError(f"Invalid video dimensions for {source_path}")

        temp_path = ""
        writer = None
        output_ext = ".mp4"
        processed = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                transformed = transform(Image.fromarray(rgb_frame))
                transformed_bgr = cv2.cvtColor(
                    np.array(transformed.convert("RGB")),
                    cv2.COLOR_RGB2BGR,
                )

                if writer is None:
                    out_height, out_width = transformed_bgr.shape[:2]
                    writer, temp_path, output_ext = self._open_video_writer(
                        source_path, fps, out_width, out_height
                    )

                writer.write(transformed_bgr)

                processed += 1
                self.report_progress(
                    progress_callback,
                    current=processed,
                    total=frame_total if frame_total > 0 else processed,
                    message=f"{progress_verb} video frame {processed}",
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
                message=error_message,
                details={"error": str(exc), "source_path": source_path},
            )
            raise
        finally:
            if writer is not None:
                writer.release()
            cap.release()
            if temp_path and os.path.exists(temp_path):
                with contextlib.suppress(OSError):
                    os.remove(temp_path)

    @staticmethod
    def _open_video_writer(
        source_path: str, fps: float, width: int, height: int
    ) -> tuple[Any, str, str]:
        """Open an output writer, trying each container/codec pair in turn.

        Args:
            source_path: Input path, used only to prefer the source container.
            fps: Frame rate for the output stream.
            width: Output frame width in pixels.
            height: Output frame height in pixels.

        Returns:
            A ``(writer, temp_path, extension)`` tuple. The caller owns the
            writer and the temporary file.

        Raises:
            ValueError: None of the candidate encoders could be opened.
        """
        source_ext = os.path.splitext(source_path)[1].lower()
        candidates = (
            _WEBM_WRITER_CANDIDATES if source_ext == ".webm" else _WRITER_CANDIDATES
        )

        for candidate_ext, codec in candidates:
            with tempfile.NamedTemporaryFile(
                suffix=candidate_ext, delete=False
            ) as temp_file:
                temp_path = temp_file.name
            candidate_writer = cv2.VideoWriter(
                temp_path,
                cv2.VideoWriter_fourcc(*codec),
                fps,
                (width, height),
            )
            if candidate_writer.isOpened():
                return candidate_writer, temp_path, candidate_ext
            candidate_writer.release()
            with contextlib.suppress(OSError):
                os.remove(temp_path)

        raise ValueError("Failed to open output video writer")

    @staticmethod
    def _coerce_number(value: Any, default: float) -> float:
        """Return *value* as a float, or *default* if it is not numeric.

        Args:
            value: Raw parameter value straight off the JSON payload.
            default: Value returned when *value* cannot be parsed.

        Returns:
            The parsed float, or *default*.
        """
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_positive_number(value: Any, default: float) -> float:
        """Return *value* as a strictly positive float, or *default*.

        Non-numeric input, zero, negatives and NaN all fall back to *default*
        (the ``> 0`` test is false for NaN, which is why it is written this way
        round rather than as ``<= 0``).

        Args:
            value: Raw parameter value straight off the JSON payload.
            default: Value returned when *value* is not a positive number.

        Returns:
            The parsed positive float, or *default*.
        """
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

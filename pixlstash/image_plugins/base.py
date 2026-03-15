from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from PIL import Image

ProgressCallback = Callable[[dict[str, Any]], None]
ErrorCallback = Callable[[dict[str, Any]], None]


class ImagePlugin(ABC):
    """Base class for image transformation plugins.

    Plugins receive a list of PIL images and JSON-compatible parameters,
    and return a list of PIL images in the same order. Subclasses must
    implement ``parameter_schema`` and ``run``. Optionally override
    ``run_video`` to support video inputs.

    Attributes:
        name: Unique snake_case identifier used to look up the plugin by name.
        display_name: Human-readable label shown in the UI.
        description: Short description of what the plugin does.
        supports_images: Whether the plugin handles still images via ``run``.
        supports_videos: Whether the plugin handles video files via ``run_video``.
    """

    name: str = ""
    display_name: str = ""
    description: str = ""
    supports_images: bool = True
    supports_videos: bool = False

    def plugin_schema(self) -> dict[str, Any]:
        """Return the JSON-serialisable metadata dict for this plugin.

        Used by the plugin registry to expose plugin capabilities to the
        frontend. Calls ``parameter_schema`` internally.

        Returns:
            A dict with keys ``name``, ``display_name``, ``description``,
            ``supports_images``, ``supports_videos``, and ``parameters``.
        """
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description or "",
            "supports_images": bool(self.supports_images),
            "supports_videos": bool(self.supports_videos),
            "parameters": self.parameter_schema(),
        }

    @abstractmethod
    def parameter_schema(self) -> list[dict[str, Any]]:
        """Return the parameter definitions for this plugin.

        Each entry in the list is a dict describing one user-facing parameter.
        Required keys: ``name`` (str, snake_case identifier), ``label`` (str,
        display label), ``type`` (str, one of ``"number"``, ``"string"``,
        ``"boolean"``, ``"select"``), ``default`` (Any, value used when the
        parameter is omitted). Optional keys: ``description`` (str),
        ``options`` (list[str], required for ``"select"`` type).

        Returns:
            List of parameter definition dicts, one per parameter.
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

        The callable receives ``[x1, y1, x2, y2]`` in source pixel coordinates
        and must return a new ``[x1, y1, x2, y2]`` in output pixel coordinates.
        Return ``None`` to fall back to the default proportional scaling used
        by the face-copy logic in ``service.py``.

        Override this in plugins that apply a geometric transformation (rotation,
        scaling, cropping, etc.) so that face bounding boxes are correctly
        repositioned on the output image.

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

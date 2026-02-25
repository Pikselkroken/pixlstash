from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from PIL import Image

ProgressCallback = Callable[[dict[str, Any]], None]
ErrorCallback = Callable[[dict[str, Any]], None]


class ImagePlugin(ABC):
    """Base class for image transformation plugins.

    Plugins receive a list of PIL images and JSON-compatible parameters,
    and return a list of PIL images in the same order.
    """

    name: str = ""
    display_name: str = ""
    description: str = ""
    supports_images: bool = True
    supports_videos: bool = False

    def plugin_schema(self) -> dict[str, Any]:
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
        """Return a JSON schema-like parameter definition list."""

    @abstractmethod
    def run(
        self,
        images: list[Image.Image],
        parameters: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        error_callback: ErrorCallback | None = None,
    ) -> list[Image.Image]:
        """Run plugin on input images and return output images."""

    def run_video(
        self,
        source_path: str,
        parameters: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        error_callback: ErrorCallback | None = None,
    ) -> bytes | tuple[bytes, str]:
        """Run plugin on a video source and return encoded bytes (optionally with extension)."""
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

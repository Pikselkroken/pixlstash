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

    def plugin_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description or "",
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

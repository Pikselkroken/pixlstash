# Copyright 2026 Gaute Lindkvist
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Base classes for the tagger/captioner plugin system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class TagResult:
    """A single tag emitted by a tagger plugin.

    Attributes:
        tag: Normalised tag string.
        confidence: Model confidence in [0, 1], or ``None`` when the model
            does not produce per-tag probabilities (e.g. LLM-based taggers).
    """

    tag: str
    confidence: float | None = None


class TaggerPlugin(ABC):
    """Abstract base for tagger and captioner plugins.

    Licensing note:
        This file is MIT-licensed so third-party plugin authors can depend on
        this API without pulling in the GPL.

    Subclasses declare their capabilities via class-level attributes and
    implement ``tag_images`` and/or ``generate_descriptions`` depending on
    what they support.

    Attributes:
        name: Unique snake_case identifier used to look up the plugin.
        display_name: Human-readable label shown in the UI.
        description: Short description of what the plugin does.
        supports_tags: Whether this plugin can produce tags via ``tag_images``.
        supports_descriptions: Whether this plugin can generate captions via
            ``generate_descriptions``.
        requires_download: Whether the plugin requires an explicit download step
            before first use (i.e. the model is not bundled or pre-installed).
    """

    name: str = ""
    display_name: str = ""
    description: str = ""
    supports_tags: bool = False
    supports_descriptions: bool = False
    requires_download: bool = True
    default_enabled: bool = False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @abstractmethod
    def parameter_schema(self) -> list[dict[str, Any]]:
        """Return the parameter definitions for this plugin.

        Each entry is a dict describing one user-facing parameter.

        Required keys:
            name (str): snake_case identifier.
            label (str): Display label.
            type (str): One of ``"number"``, ``"integer"``, ``"boolean"``,
                ``"select"``, ``"string"``, ``"textarea"``, ``"csv-int"``.
            default (Any): Value used when the parameter is omitted.

        Optional keys:
            description (str): Tooltip text.
            min (float|int): Minimum value for numeric types.
            max (float|int): Maximum value for numeric types.
            step (float|int): Step size for numeric types.
            options (list[dict]): Required for ``"select"`` — each entry is
                ``{"value": ..., "label": ...}``.

        Returns:
            List of parameter definition dicts, one per parameter.
        """

    def default_params(self) -> dict[str, Any]:
        """Return a dict of ``{name: default}`` from ``parameter_schema``."""
        return {field["name"]: field["default"] for field in self.parameter_schema()}

    def plugin_schema(self) -> dict[str, Any]:
        """Return the JSON-serialisable metadata dict for this plugin.

        Used by the registry to expose capabilities to the frontend.

        Returns:
            Dict with plugin metadata and the parameter schema.
        """
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description or "",
            "supports_tags": bool(self.supports_tags),
            "supports_descriptions": bool(self.supports_descriptions),
            "requires_download": bool(self.requires_download),
            "parameters": self.parameter_schema(),
            "downloaded_artifacts": self.list_downloaded_artifacts(),
            "is_loaded": self.is_loaded(),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def needs_download(self, parameters: dict[str, Any] | None = None) -> bool:
        """Return ``True`` if required model files are absent."""

    def download(
        self,
        parameters: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Download required model files.

        Default implementation is a no-op for plugins that bundle their
        models.  Override to add download logic.

        Args:
            parameters: Current plugin parameters (e.g. ``precision``).
            progress_callback: Optional callable for progress reporting.
        """

    @abstractmethod
    def init(self, parameters: dict[str, Any]) -> None:
        """Load the model into memory.

        Should be idempotent (a second call while already loaded is a no-op).

        Args:
            parameters: Current plugin parameters.
        """

    @abstractmethod
    def unload(self) -> None:
        """Release model from memory."""

    @abstractmethod
    def is_loaded(self) -> bool:
        """Return ``True`` if the model is ready for inference."""

    def list_downloaded_artifacts(self) -> list[dict[str, Any]]:
        """Return metadata for locally-present model artifacts.

        Used by the UI to populate the "Downloaded models" panel and offer
        delete buttons.  Plugins that bundle or do not cache artifacts should
        return an empty list (the default).

        Returns:
            List of artifact dicts.  Each dict must have at least ``"name"``
            (str) and ``"size_bytes"`` (int).
        """
        return []

    def delete_artifact(self, name: str) -> None:
        """Delete a downloaded artifact by name.

        Args:
            name: Artifact name as returned by ``list_downloaded_artifacts``.

        Raises:
            NotImplementedError: If the plugin has no deletable artifacts.
            ValueError: If *name* is not a known artifact.
        """
        raise NotImplementedError(f"{self.name} has no deletable artifacts")

    # ------------------------------------------------------------------
    # VRAM hints
    # ------------------------------------------------------------------

    def estimated_vram_mb(
        self, image_count: int, parameters: dict[str, Any] | None = None
    ) -> int:
        """Estimate VRAM (MB) required for *image_count* images.

        Used by workflows for sequential VRAM budgeting (max over plugins).
        Return 0 for CPU-only models.

        Args:
            image_count: Number of images to be processed.
            parameters: Current plugin parameters.

        Returns:
            Estimated VRAM in MB.
        """
        return 0

    def effective_batch_size(self, parameters: dict[str, Any] | None = None) -> int:
        """Return the effective inference batch size for the given parameters.

        Args:
            parameters: Current plugin parameters.

        Returns:
            Batch size (≥1).
        """
        return 1

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def tag_images(
        self,
        image_paths: list[str],
        parameters: dict[str, Any],
        preloaded: dict | None = None,
        stop_event=None,
    ) -> dict[str, list[TagResult]]:
        """Tag a batch of images.

        Only called when ``supports_tags`` is ``True``.

        Args:
            image_paths: Ordered list of absolute image/video paths.
            parameters: Current plugin parameters.
            preloaded: Optional ``{path: preprocessed_data}`` map to skip
                re-loading images from disk.
            stop_event: Optional :class:`threading.Event` to interrupt
                inference mid-batch.

        Returns:
            ``{path: [TagResult, ...]}`` for each processed image.
        """
        raise NotImplementedError(f"{self.name} does not support tags")

    def generate_descriptions(
        self,
        image_paths: list[str],
        parameters: dict[str, Any],
        stop_event=None,
    ) -> dict[str, str | None]:
        """Generate captions for a batch of images.

        Only called when ``supports_descriptions`` is ``True``.

        Args:
            image_paths: Ordered list of absolute image/video paths.
            parameters: Current plugin parameters.
            stop_event: Optional :class:`threading.Event` to interrupt
                inference mid-batch.

        Returns:
            ``{path: caption_str}`` — value is ``None`` on per-image failure.
        """
        raise NotImplementedError(f"{self.name} does not support descriptions")

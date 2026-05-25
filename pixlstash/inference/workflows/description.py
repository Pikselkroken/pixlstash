"""Description workflow: batch caption generation via Florence-2."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.florence2 import (
    FLORENCE_BASE_VRAM_MB,
    FLORENCE_PER_IMAGE_VRAM_MB,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine

logger = get_logger(__name__)

_VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"})


class DescriptionWorkflow:
    """Generates textual captions for images and video thumbnails.

    Uses Florence-2 for both single-image and batched captioning.  The
    lifecycle (loading / unloading the model) is delegated to the engine's
    :class:`~pixlstash.inference.model_lifecycle.ModelLifecycleManager`.

    Args:
        engine: The :class:`~pixlstash.inference.engine.InferenceEngine` that
            holds the already-constructed service instances.
        image_root: Absolute path prefix used to resolve relative picture
            ``file_path`` values.  May be ``None`` when pictures store
            absolute paths already.
    """

    def __init__(self, engine: "InferenceEngine", image_root: str | None) -> None:
        self._engine = engine
        self._image_root = image_root

    def generate_batch(
        self, pictures: list, engine_override: str | None = None
    ) -> dict[int, str]:
        """Generate captions for a batch of picture-like objects.

        Dispatches to the active description plugin (from tagger_settings).
        Defaults to Florence-2 when no other plugin is configured.

        Args:
            pictures: Sequence of ORM ``Picture`` objects (or any object that
                exposes ``id`` and ``file_path``).
            engine_override: If supplied, use this plugin instead of
                ``active_description_plugin`` for this batch.

        Returns:
            A ``{picture_id: caption_str}`` mapping.  Missing or failed
            captions are stored as ``None``.  An empty dict is returned when
            *pictures* is empty.
        """
        if not pictures:
            return {}

        active = (
            engine_override
            if engine_override is not None
            else self._engine.tagger_settings.get(
                "active_description_plugin", "florence2"
            )
        )
        logger.info(
            "[DescriptionWorkflow] active_description_plugin=%r; known plugins: %s",
            active,
            list(self._engine.tagger_settings.get("plugins", {}).keys()),
        )

        if active and active != "florence2":
            explicit = engine_override is not None
            return self._generate_batch_plugin(pictures, active, explicit=explicit)

        return self._generate_batch_florence(pictures)

    def _generate_batch_plugin(
        self, pictures: list, plugin_name: str, explicit: bool = False
    ) -> dict[int, str]:
        """Dispatch description generation to a named TaggerPlugin."""
        from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

        mgr = get_tagger_plugin_manager()
        plugin = mgr.get_plugin(plugin_name)
        logger.info(
            "[DescriptionWorkflow] dispatching to plugin %r (found=%s, supports_descriptions=%s)",
            plugin_name,
            plugin is not None,
            plugin.supports_descriptions if plugin else "n/a",
        )
        if plugin is None or not plugin.supports_descriptions:
            logger.warning(
                "Active description plugin %r not found or does not support descriptions; "
                "falling back to Florence-2.",
                plugin_name,
            )
            return self._generate_batch_florence(pictures)

        plugins_cfg = self._engine.tagger_settings.get("plugins", {})
        cfg = plugins_cfg.get(plugin_name, {})
        params = {**plugin.default_params(), **cfg.get("params", {})}

        try:
            if hasattr(plugin, "setup"):
                plugin.setup(self._engine.device)
            plugin.init(params)
        except Exception:
            logger.exception(
                "Failed to initialise description plugin %r; %s.",
                plugin_name,
                "description will be cleared (not falling back to Florence-2 because plugin was explicitly requested)"
                if explicit
                else "falling back to Florence-2",
            )
            if explicit:
                return {}
            return self._generate_batch_florence(pictures)

        image_paths = []
        path_to_id: dict[str, int] = {}
        results: dict[int, str | None] = {}

        for picture in pictures:
            picture_path = ImageUtils.resolve_picture_path(
                self._image_root, getattr(picture, "file_path", None)
            )
            if not picture_path:
                results[picture.id] = None
                continue
            image_paths.append(picture_path)
            path_to_id[picture_path] = picture.id

        try:
            captions = plugin.generate_descriptions(image_paths, parameters=params)
            for path, caption in captions.items():
                pic_id = path_to_id.get(str(path))
                if pic_id is not None:
                    results[pic_id] = caption
        except Exception:
            logger.exception(
                "Description plugin %r raised during generation; results may be partial.",
                plugin_name,
            )

        return results

    def _generate_batch_florence(self, pictures: list) -> dict[int, str]:
        """Caption a batch using Florence-2 (original implementation)."""
        if not pictures:
            return {}

        self._engine.lifecycle.ensure_captioning_ready(self._engine.florence_service)

        results: dict[int, str | None] = {}
        batch_items: list[tuple[int, str]] = []

        for picture in pictures:
            picture_path = ImageUtils.resolve_picture_path(
                self._image_root, getattr(picture, "file_path", None)
            )
            if not picture_path:
                results[picture.id] = None
                continue
            ext = os.path.splitext(picture_path)[1].lower()
            if ext in _VIDEO_EXTS:
                results[picture.id] = self._engine.florence_service.generate_caption(
                    picture_path, _retry_on_cpu=False
                )
            else:
                batch_items.append((picture.id, picture_path))

        batch_size = self._engine.florence_service.description_batch_size()
        for idx in range(0, len(batch_items), batch_size):
            chunk = batch_items[idx : idx + batch_size]
            chunk_paths = [picture_path for _, picture_path in chunk]
            captions = self._engine.florence_service.generate_captions_batch(
                chunk_paths
            )
            for picture_id, picture_path in chunk:
                results[picture_id] = captions.get(picture_path)

        return results

    def estimate_vram_mb(self, image_count: int) -> int:
        """Estimate incremental VRAM (in MB) required to caption *image_count* images.

        When Florence is already resident in GPU memory only the per-image
        activation scratch is charged, avoiding a false-positive VRAM gate
        stall on warm runs.

        Args:
            image_count: Number of images to be captioned.

        Returns:
            Estimated VRAM in MB, or ``0`` on non-CUDA devices.
        """
        if self._engine.device != "cuda":
            return 0
        florence_batch = max(
            1, int(self._engine.florence_service.description_batch_size())
        )
        batch = min(max(1, int(image_count or 1)), florence_batch)
        if self._engine.florence_service.is_loaded():
            return int(FLORENCE_PER_IMAGE_VRAM_MB * batch)
        return int(FLORENCE_BASE_VRAM_MB + FLORENCE_PER_IMAGE_VRAM_MB * batch)

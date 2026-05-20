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

    def generate_batch(self, pictures: list) -> dict[int, str]:
        """Generate captions for a batch of picture-like objects.

        Videos are captioned one at a time (from their first frame); images
        are sent to Florence-2 in GPU mini-batches.

        Args:
            pictures: Sequence of ORM ``Picture`` objects (or any object that
                exposes ``id`` and ``file_path``).

        Returns:
            A ``{picture_id: caption_str}`` mapping.  Missing or failed
            captions are stored as ``None``.  An empty dict is returned when
            *pictures* is empty.
        """
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

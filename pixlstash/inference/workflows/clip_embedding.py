"""CLIP image-embedding inference workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine

logger = get_logger(__name__)


class ClipEmbeddingWorkflow:
    """CLIP image-embedding workflow with VRAM-budget-aware batch sizing.

    Args:
        engine: The :class:`~pixlstash.inference.engine.InferenceEngine` that
            owns the :class:`~pixlstash.tagger_plugins.clip_service.ClipService`
            and :class:`~pixlstash.inference.vram_budget.VramBudget`.
    """

    # CLIP ViT-B-32: ~350 MB model (fp16), ~8 MB per image activation.
    _CLIP_BASE_VRAM_MB = 350
    _CLIP_PER_IMAGE_VRAM_MB = 8

    def __init__(self, engine: "InferenceEngine") -> None:
        self._engine = engine

    @property
    def device(self) -> str:
        """Current inference device (``"cuda"`` or ``"cpu"``)."""
        return self._engine.device

    def is_ready(self) -> bool:
        """Return ``True`` when the CLIP model is loaded and ready."""
        return self._engine.clip_service.is_loaded()

    def ensure_ready(self) -> None:
        """Load the CLIP model if not already loaded."""
        self._engine.clip_service.ensure_ready()

    def encode_images(self, images: list) -> Optional[np.ndarray]:
        """Encode a batch of PIL images into normalised CLIP visual embeddings.

        Args:
            images: List of ``PIL.Image`` objects.

        Returns:
            Float32 numpy array of shape ``(N, D)`` or ``None`` on failure.
        """
        return self._engine.clip_service.encode_image_batch(images)

    def suggested_batch_size(self) -> int:
        """Return the VRAM-budget-constrained batch size for a CLIP inference pass.

        For GPU devices the size is capped by the VRAM budget; on CPU the maximum
        of 128 images is returned unchanged.
        """
        max_batch = 128
        if self._engine.device == "cuda":
            max_batch = min(
                max_batch,
                self._engine.vram_budget.limited_batch_cap(
                    base_mb=self._CLIP_BASE_VRAM_MB,
                    per_item_mb=self._CLIP_PER_IMAGE_VRAM_MB,
                ),
            )
        return max(1, max_batch)

    def estimated_vram_mb(self, image_count: int) -> int:
        """Return the incremental VRAM estimate for a batch of *image_count* images.

        Returns 0 on CPU; on CUDA returns at least 64 MB.
        """
        if self._engine.device != "cuda":
            return 0
        batch = min(max(1, int(image_count or 1)), 512)
        return int(max(64, self._CLIP_PER_IMAGE_VRAM_MB * batch))

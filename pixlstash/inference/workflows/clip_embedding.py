"""CLIP image-embedding inference workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.accelerator import is_accelerated

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
    #
    # These are **CUDA** measurements and are reused unchanged on Metal, where
    # they are unverified - the same caveat WD14's figures carry in
    # ``inference/vram_budget.py``. They are applied there anyway because an
    # unverified conservative cap is strictly better than the flat 128 with no
    # cap at all that a unified-memory machine got while these gates read
    # ``== "cuda"``: on Apple Silicon the budget is the only thing between a
    # batch estimate and the machine's own RAM. Measure before tightening.
    _CLIP_BASE_VRAM_MB = 350
    _CLIP_PER_IMAGE_VRAM_MB = 8

    def __init__(self, engine: "InferenceEngine") -> None:
        self._engine = engine

    @property
    def device(self) -> str:
        """Current inference device (``"cuda"``, ``"mps"`` or ``"cpu"``)."""
        return self._engine.device

    def is_ready(self) -> bool:
        """Return ``True`` when the CLIP model is loaded and ready."""
        return self._engine.clip_service.is_loaded()

    def ensure_ready(self) -> None:
        """Load the CLIP model if not already loaded."""
        self._engine.clip_service.ensure_ready()

    def preprocess_images(self, images: list) -> Optional[list]:
        """CPU preprocessing for :meth:`encode_images`; ``None`` if not loaded."""
        return self._engine.clip_service.preprocess_images(images)

    def encode_images(
        self, images: list, tensors: Optional[list] = None
    ) -> Optional[np.ndarray]:
        """Encode a batch of PIL images into normalised CLIP visual embeddings.

        Args:
            images: List of ``PIL.Image`` objects.
            tensors: Their :meth:`preprocess_images` output, if already done.

        Returns:
            Float32 numpy array of shape ``(N, D)`` or ``None`` on failure.
        """
        return self._engine.clip_service.encode_image_batch(images, tensors=tensors)

    def encode_query_image(self, image) -> Optional[np.ndarray]:
        """Encode one uploaded likeness-search image, off the inference device.

        Separate from :meth:`encode_images` because the caller, not the
        workflow, knows which side of the comparison an image is on. The GPU
        worker writes the stored picture embeddings and must keep the
        accelerator; the *query* arrives on a request thread while the worker
        is running, and on Metal that second thread is what crashes the
        process. Where the engine has no CPU copies this is
        :meth:`encode_images` with a batch of one.

        Args:
            image: The uploaded ``PIL.Image`` to search with.

        Returns:
            Float32 numpy array of shape ``(1, D)`` or ``None`` on failure.
        """
        encoders = self._engine.query_encoders
        if encoders is not None and encoders.ensure_serving():
            return encoders.encode_query_image(image)
        # No copies on this host, or no GPU worker running - see
        # ``TextEmbeddingWorkflow.encode_query`` for why the latter is safe.
        return self.encode_images([image])

    def suggested_batch_size(self) -> int:
        """Return the VRAM-budget-constrained batch size for a CLIP inference pass.

        On any accelerator the size is capped by the device-memory budget; on
        CPU the maximum of 128 images is returned unchanged.
        """
        max_batch = 128
        if is_accelerated(self._engine.device):
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

        Returns 0 on CPU; on any accelerator returns at least 64 MB. The task
        runner reserves against this figure, so answering 0 on a device that is
        really running CLIP books the work as free and lets the scheduler
        overcommit the one pool a unified-memory machine has.
        """
        if not is_accelerated(self._engine.device):
            return 0
        batch = min(max(1, int(image_count or 1)), 512)
        return int(max(64, self._CLIP_PER_IMAGE_VRAM_MB * batch))

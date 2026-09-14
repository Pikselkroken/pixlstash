"""CPU copies of the query encoders, for search on Apple Metal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pixlstash.inference.workflows.clip_embedding import ClipEmbeddingWorkflow
from pixlstash.inference.workflows.text_embedding import TextEmbeddingWorkflow

if TYPE_CHECKING:
    from pixlstash.tagger_plugins.clip_service import ClipService
    from pixlstash.tagger_plugins.sbert import SBertService


class CpuQueryEncoders:
    """CPU copies of the SBERT and CLIP services, for encoding search queries on Metal.

    torch crashes when two threads use Apple Metal at once, so only the task
    runner's GPU worker may (``docs/apple-metal-thread-safety.md``). A search
    runs on a request thread, so on Metal it encodes its query with these
    copies instead of the engine's own services and never touches Metal. The
    engine's services still compute the stored vectors, on Metal, in GPU
    tasks.

    The copies are instances of the engine's own service classes on the
    ``cpu`` device: the same model names, weights, preprocessing, normalisation
    and float32 dtype, so a query vector is comparable with the stored ones.
    Stands in for the engine in :class:`TextEmbeddingWorkflow` and
    :class:`ClipEmbeddingWorkflow`, which read only :attr:`device` and the two
    services.

    Nothing here loads a model on its own. :meth:`load` runs on the GPU
    worker, queued by the Vault, and a caller hands the copies to a search only
    once :meth:`is_loaded` is true. The idle unload does not reach them, so
    once loaded they stay loaded until the engine is released.

    Args:
        clip_service: A :class:`ClipService` on the ``cpu`` device.
        sbert_service: An :class:`SBertService` on the ``cpu`` device.
    """

    device = "cpu"

    def __init__(
        self, clip_service: "ClipService", sbert_service: "SBertService"
    ) -> None:
        self.clip_service = clip_service
        self.sbert_service = sbert_service

    @property
    def text_embedding_workflow(self) -> TextEmbeddingWorkflow:
        """A :class:`TextEmbeddingWorkflow` on the CPU copies."""
        return TextEmbeddingWorkflow(engine=self)

    @property
    def clip_embedding_workflow(self) -> ClipEmbeddingWorkflow:
        """A :class:`ClipEmbeddingWorkflow` on the CPU copy of CLIP."""
        return ClipEmbeddingWorkflow(engine=self)

    def is_loaded(self) -> bool:
        """Whether both models are loaded and a search may use them."""
        return self.sbert_service.is_loaded() and self.clip_service.is_loaded()

    def load(self) -> None:
        """Load both models on the CPU. Idempotent.

        Runs on the GPU worker, where every other model loads: transformers and
        accelerate fail when two threads import or load models at once, and no
        lock held here would keep the worker's own loads out.
        """
        self.sbert_service.ensure_ready()
        self.clip_service.ensure_ready()

"""SBERT sentence-embedding service for PixlStash.

Wraps the SentenceTransformer model used to generate semantic text embeddings
for pictures and search queries.
"""

from __future__ import annotations

import logging

import numpy as np

from pixlstash.utils.model_utils import load_sentence_transformer

logger = logging.getLogger(__name__)

SBERT_MODEL_NAME = "all-MiniLM-L6-v2"
SBERT_MODEL_REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"


class SBertService:
    """Manages the SentenceTransformer model for semantic text embedding.

    Lazy-loads the model on first use and falls back to CPU on CUDA errors.

    Args:
        device: Initial inference device (``"cuda"`` or ``"cpu"``).
    """

    def __init__(self, device: str) -> None:
        self._device = device
        self._model = None

    def is_loaded(self) -> bool:
        """Return True when the model is ready for inference."""
        return self._model is not None

    def ensure_ready(self) -> None:
        """Load the model if not already loaded."""
        if self._model is not None:
            return
        try:
            self._model = load_sentence_transformer(
                SBERT_MODEL_NAME,
                device=self._device,
                local_files_only=True,
                revision=SBERT_MODEL_REVISION,
            )
        except OSError:
            logger.info("Downloading %s for the first time...", SBERT_MODEL_NAME)
            self._model = load_sentence_transformer(
                SBERT_MODEL_NAME,
                device=self._device,
                revision=SBERT_MODEL_REVISION,
            )

    def unload(self) -> None:
        """Release model memory."""
        self._model = None

    def encode(self, texts: list[str]) -> list[np.ndarray]:
        """Encode a list of texts into SBERT embeddings.

        Args:
            texts: Pre-processed lowercase text strings to encode.

        Returns:
            List of numpy arrays, one per input text.
        """
        self.ensure_ready()
        logger.debug(
            "Generating SBERT embeddings for %d texts on device: %s",
            len(texts),
            self._model.device,
        )
        try:
            raw = self._model.encode(texts, show_progress_bar=False)
            logger.debug("Done generating SBERT embeddings.")
        except RuntimeError as exc:
            if "CUDA" not in str(exc):
                logger.error("Failed to generate text embedding: %s", exc)
                raise
            logger.warning(
                "SBERT embedding failed on CUDA: %s. Falling back to CPU.", exc
            )
            try:
                self._model = load_sentence_transformer(
                    SBERT_MODEL_NAME,
                    device="cpu",
                    local_files_only=True,
                    revision=SBERT_MODEL_REVISION,
                )
            except OSError:
                self._model = load_sentence_transformer(
                    SBERT_MODEL_NAME,
                    device="cpu",
                    revision=SBERT_MODEL_REVISION,
                )
            self._device = "cpu"
            logger.info("Falling back to CPU for SBERT embeddings.")
            raw = self._model.encode(texts, show_progress_bar=False)

        arr = np.asarray(raw)
        return [arr[i] for i in range(len(texts))]

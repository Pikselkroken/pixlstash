"""CLIP service for PixlStash.

Manages the OpenCLIP model used for both text-query embeddings and facial
feature extraction from image crops.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np
import open_clip
import torch

logger = logging.getLogger(__name__)

CLIP_MODEL_NAME = "ViT-B-32"
CLIP_MODEL_WEIGHTS = "laion2b_s34b_b79k"


class ClipService:
    """Manages the OpenCLIP model for text and image embeddings.

    Lazy-loads on first use and falls back to CPU on CUDA errors.

    Args:
        device: Initial inference device (``"cuda"`` or ``"cpu"``).
    """

    def __init__(self, device: str) -> None:
        self._device = device
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def is_loaded(self) -> bool:
        """Return True when the model is ready for inference."""
        return (
            self._model is not None
            and self._preprocess is not None
            and self._tokenizer is not None
        )

    def ensure_ready(self) -> None:
        """Load the model if not already loaded."""
        if self.is_loaded():
            return
        with self._lock:
            if not self.is_loaded():
                self._load()

    def unload(self) -> None:
        """Release model memory."""
        self._model = None
        self._preprocess = None
        self._tokenizer = None

    def _load(self) -> None:
        model, _, preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL_NAME, pretrained=CLIP_MODEL_WEIGHTS
        )
        model = model.to(self._device)
        if self._device == "cuda":
            model = model.half()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(CLIP_MODEL_NAME)

    def encode_text(self, query: str) -> Optional[np.ndarray]:
        """Encode a text query into a normalised CLIP embedding.

        Args:
            query: Raw query string.

        Returns:
            1-D numpy array or ``None`` on failure.
        """
        if not query:
            return None
        self.ensure_ready()
        try:
            tokens = self._tokenizer([query]).to(self._device)
            with torch.no_grad():
                features = self._model.encode_text(tokens)
                features = features / features.norm(dim=-1, keepdim=True)
            return features.cpu().float().numpy()[0]
        except Exception as exc:
            logger.error("ClipService: failed to encode text: %s", exc)
            return None

    def encode_image_crops(
        self, crops: list, pic_desc: str = ""
    ) -> list[Optional[np.ndarray]]:
        """Encode a list of PIL image crops into CLIP visual embeddings.

        Handles CUDA OOM by falling back to CPU for the remainder of the batch.

        Args:
            crops: List of PIL.Image objects (or ``None`` for failed crops).
            pic_desc: Human-readable description used in log messages.

        Returns:
            List of 1-D numpy arrays (or ``None`` for failed crops), same
            length as ``crops``.
        """
        self.ensure_ready()
        results: list[Optional[np.ndarray]] = []
        for i, crop in enumerate(crops):
            if crop is None:
                logger.warning("Face crop is None for '%s' (index %d)", pic_desc, i)
                results.append(None)
                continue
            try:
                img_input = self._preprocess(crop).unsqueeze(0).to(self._device)
                with torch.no_grad():
                    features = self._model.encode_image(img_input).cpu().numpy()[0]
                results.append(features)
            except RuntimeError as exc:
                if any(
                    kw in str(exc)
                    for kw in ("CUDA out of memory", "not compatible", "CUDA error")
                ):
                    logger.warning(
                        "ClipService CUDA error for '%s' index %d; retrying on CPU: %s",
                        pic_desc,
                        i,
                        exc,
                    )
                    self._device = "cuda"  # will be overridden below
                    self._model = self._model.float().to("cpu")
                    self._device = "cpu"
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    try:
                        img_input = self._preprocess(crop).unsqueeze(0).to("cpu")
                        with torch.no_grad():
                            features = (
                                self._model.encode_image(img_input).cpu().numpy()[0]
                            )
                        results.append(features)
                    except Exception as cpu_exc:
                        logger.error(
                            "ClipService CPU fallback failed for '%s' index %d: %s",
                            pic_desc,
                            i,
                            cpu_exc,
                        )
                        results.append(None)
                else:
                    logger.error(
                        "ClipService RuntimeError for '%s' index %d: %s",
                        pic_desc,
                        i,
                        exc,
                    )
                    results.append(None)
            except Exception as exc:
                logger.error(
                    "ClipService exception for '%s' index %d: %s", pic_desc, i, exc
                )
                results.append(None)
        return results

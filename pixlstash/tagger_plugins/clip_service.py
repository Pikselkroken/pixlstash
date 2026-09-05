"""CLIP service for PixlStash.

Manages the OpenCLIP model used for both text-query embeddings and facial
feature extraction from image crops.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

# ML imports (torch / open_clip, which itself pulls torch, torchvision and
# transformers) are deliberately FUNCTION-LOCAL throughout
# this module. They cost seconds to import, and this module sits on the API
# server's import path - so importing them at module scope would make server
# startup and every single test pay that cost before doing any work.

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
        # Reentrant, and held by ``unload`` as well as ``ensure_ready``: the
        # load side was already serialised, but ``aggressive_unload`` could
        # still drop the model out from under an in-flight load, freeing device
        # memory the loader was writing into. See test_model_unload_race.py.
        self._lock = threading.RLock()

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
        """Release model memory, waiting for any in-flight load to finish."""
        with self._lock:
            self._model = None
            self._preprocess = None
            self._tokenizer = None

    def _load(self) -> None:
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL_NAME, pretrained=CLIP_MODEL_WEIGHTS
        )
        model = model.to(self._device)
        if self._device == "cuda":
            model = model.half()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(CLIP_MODEL_NAME)

    @property
    def device(self) -> str:
        """Current inference device (``"cuda"`` or ``"cpu"``)."""
        return self._device

    @property
    def model(self):
        """The loaded OpenCLIP model, or ``None`` if not yet loaded."""
        return self._model

    @property
    def tokenizer(self):
        """The CLIP tokenizer, or ``None`` if not yet loaded."""
        return self._tokenizer

    def preprocess_images(self, images: list) -> "Optional[list]":
        """Run CLIP's preprocessing (resize, crop, normalise) on the CPU.

        The half of :meth:`encode_image_batch` that costs the time: ~30 ms per
        full-resolution image, against ~1 ms of GPU forward pass each. Split
        out so a task can run it in its preload threads instead of on the
        single GPU worker, where it was 4 s of every 4.2 s batch. Returns
        ``None`` when the model is not loaded - preloading must never be what
        loads it.

        Args:
            images: List of ``PIL.Image`` objects.

        Returns:
            One CPU tensor per image, or ``None`` if the model is not loaded.
        """
        if not self.is_loaded():
            return None
        return [self._preprocess(img) for img in images]

    def encode_image_batch(
        self, images: list, tensors: "Optional[list]" = None
    ) -> "Optional[np.ndarray]":
        """Encode a batch of PIL images into normalised CLIP visual embeddings.

        Preprocesses the images (unless *tensors* carries that work already
        done), runs a single batched forward pass, and returns row-normalised
        float32 embeddings.  Falls back to CPU on CUDA OOM.

        Args:
            images: List of ``PIL.Image`` objects.
            tensors: Their :meth:`preprocess_images` output, when the caller
                did that on another thread; same order as *images*.

        Returns:
            Float32 numpy array of shape ``(N, D)`` or ``None`` on failure.
        """
        import torch

        if not images:
            return None
        self.ensure_ready()
        try:
            if tensors is None or len(tensors) != len(images):
                tensors = [self._preprocess(img) for img in images]
            tensors = torch.stack(tensors).to(self._device)
            if self._device == "cuda":
                tensors = tensors.half()
            with torch.no_grad():
                features = self._model.encode_image(tensors)
                features = features / features.norm(dim=-1, keepdim=True)
            return features.cpu().float().numpy()
        except RuntimeError as exc:
            if any(
                kw in str(exc)
                for kw in ("CUDA out of memory", "not compatible", "CUDA error")
            ):
                logger.warning(
                    "ClipService.encode_image_batch: CUDA error, retrying on CPU: %s",
                    exc,
                )
                self._model = self._model.float().to("cpu")
                self._device = "cpu"
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                try:
                    tensors = torch.stack([self._preprocess(img) for img in images]).to(
                        "cpu"
                    )
                    with torch.no_grad():
                        features = self._model.encode_image(tensors)
                        features = features / features.norm(dim=-1, keepdim=True)
                    return features.cpu().float().numpy()
                except Exception as cpu_exc:
                    logger.error(
                        "ClipService.encode_image_batch: CPU fallback failed: %s",
                        cpu_exc,
                    )
                    return None
            logger.error("ClipService.encode_image_batch: RuntimeError: %s", exc)
            return None
        except Exception as exc:
            logger.error("ClipService.encode_image_batch: %s", exc)
            return None

    def encode_text(self, query: str) -> Optional[np.ndarray]:
        """Encode a text query into a normalised CLIP embedding.

        Args:
            query: Raw query string.

        Returns:
            1-D numpy array or ``None`` on failure.
        """
        import torch

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
        import torch

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

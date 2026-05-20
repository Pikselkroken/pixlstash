"""Text embedding workflow: SBERT sentence embeddings and CLIP text embeddings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.model_utils import clean_asset_name
from pixlstash.utils.service.caption_utils import filter_texts

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine

logger = get_logger(__name__)


class TextEmbeddingWorkflow:
    """Encodes pictures and free-form queries into dense vector embeddings.

    Two embedding models are exposed:

    * **SBERT** (``encode``, ``encode_query``): an all-MiniLM-L6-v2 style
      sentence-transformer that embeds the structured text metadata
      (description, character names/descriptions, ComfyUI prompts, …)
      associated with a picture.  Used for semantic search and deduplication.
    * **CLIP** (``encode_clip_query``): the OpenCLIP ViT-B-32 text encoder.
      Used to find visually similar images via natural-language queries.

    Args:
        engine: The :class:`~pixlstash.inference.engine.InferenceEngine` that
            holds the already-constructed service instances.
    """

    def __init__(self, engine: "InferenceEngine") -> None:
        self._engine = engine

    def encode(self, pictures: list) -> list:
        """Generate SBERT embeddings for a list of picture-like objects.

        Each element must implement ``text_embedding_data() -> dict``.  The
        output list preserves the input order.

        Args:
            pictures: Sequence of ORM ``Picture`` objects (or any object that
                exposes ``text_embedding_data()``).

        Returns:
            A list of ``np.ndarray`` embeddings, one per input picture.  An
            empty list is returned when *pictures* is empty or all text
            representations are blank.
        """
        texts = []
        for picture in pictures:
            text = picture.text_embedding_data()
            flat_text = self._flatten_texts(text)
            filtered_text = filter_texts(flat_text)
            full_text = ". ".join(filtered_text).lower()
            texts.append(full_text)
        if not texts:
            return []
        return self._engine.sbert_service.encode(texts)

    def encode_query(self, query: str) -> list:
        """Generate a SBERT embedding for a free-form query string.

        Args:
            query: Search query.  Lower-cased before encoding.

        Returns:
            A one-element list containing the query embedding, or an empty
            list if *query* is blank.
        """
        if not query:
            return []
        return self._engine.sbert_service.encode([query.lower()])

    def encode_clip_query(self, query: str) -> Optional[np.ndarray]:
        """Generate a CLIP text embedding for a free-form query string.

        Args:
            query: Search query.

        Returns:
            A normalised ``np.ndarray`` (shape ``[D]``) or ``None`` on failure.
        """
        return self._engine.clip_service.encode_text(query)

    @staticmethod
    def _flatten_texts(texts: dict) -> list[str]:
        """Flatten a ``text_embedding_data()`` dict into a list of strings.

        The returned list is later joined with ``". "`` and lower-cased before
        encoding.  Order matters: prominent identifiers (character names,
        caption) come first so the sentence-transformer can weight them
        appropriately.

        Args:
            texts: Mapping returned by ``Picture.text_embedding_data()``.

        Returns:
            Ordered list of non-empty text fragments.
        """
        flat: list[str] = []

        characters = texts.get("characters") or []

        if characters:
            if len(characters) == 1:
                flat.append(f"A picture of {characters[0]['name']}. ")
            else:
                prefix = "A picture of "
                prefix += ", ".join([char["name"] for char in characters[:-1]])
                prefix += f" and {characters[-1]['name']}. "
                flat.append(prefix)

        if texts.get("description"):
            flat.append(str(texts["description"]))

        for char in characters:
            if char.get("description"):
                flat.append(str(char["description"]))

        comfyui = texts.get("comfyui") or {}
        if comfyui.get("positive_prompt"):
            flat.append(str(comfyui["positive_prompt"]))
        models = [clean_asset_name(m) for m in (comfyui.get("models") or []) if m]
        if models:
            flat.append(", ".join(models))
        loras = [clean_asset_name(lf) for lf in (comfyui.get("loras") or []) if lf]
        if loras:
            flat.append(", ".join(loras))

        return flat

"""Task that scores whether pictures look like their prompts at all."""

import time

import numpy as np
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring.prompt_match import (
    DISTRACTOR_PROMPTS,
    PROMPT_MATCH_FAILED,
    clean_prompt,
    prompt_match_score,
)
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

logger = get_logger(__name__)


class PromptMatchTask(BaseTask):
    """Fill ``picture.prompt_match`` for a batch of pictures with a prompt.

    Reuses the stored CLIP image embedding; the only inference is the text
    side, one forward pass for the batch's distinct prompts. Text embeddings
    are cached per cleaned prompt for the life of the process, because a
    library is mostly re-rolls of a few prompts.
    """

    BATCH_SIZE = 128
    # ponytail: cleared wholesale when full; an LRU only if re-encoding shows up.
    _TEXT_CACHE_LIMIT = 4096
    _text_cache: dict[str, np.ndarray] = {}
    _bank: np.ndarray | None = None

    def __init__(self, database, clip_service, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="PromptMatchTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._clip = clip_service
        self._picture_ids = picture_ids

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def estimated_vram_mb(self) -> int:
        # The CLIP ViT-B-32 the image embeddings already loaded (~350 MB);
        # 77-token text activations for one batch are small beside it.
        return 400

    def _run_task(self):
        start = time.time()
        rows = self._db.run_immediate_read_task(_fetch_inputs, self._picture_ids)
        if not rows:
            return {"changed_count": 0, "changed": []}

        cleaned = {pid: clean_prompt(prompt) for pid, prompt, _ in rows}
        bank = self._distractor_bank()
        wanted = {text for text in cleaned.values() if text}
        texts = self._text_embeddings(wanted)
        if bank is None or len(texts) < len(wanted):
            # The encoder failed (it logged why). It may well work later, so
            # nothing is stored: failing defers the batch for the session.
            raise RuntimeError(
                f"CLIP could not encode the "
                f"{'distractor bank' if bank is None else 'prompts'} for "
                f"{len(rows)} picture(s)"
            )

        # Each score carries the inputs it was computed from, so _persist can
        # refuse it if they were replaced (and the score reset) meanwhile.
        updates: list[tuple[int, str, bytes, float]] = []
        for pid, prompt, image_blob in rows:
            text = cleaned[pid]
            text_embedding = texts.get(text)
            image = np.frombuffer(image_blob, dtype=np.float32)
            if text_embedding is None or image.shape[0] != text_embedding.shape[0]:
                score = PROMPT_MATCH_FAILED
            else:
                score = prompt_match_score(image, text_embedding, bank)
            if score == PROMPT_MATCH_FAILED:
                logger.warning(
                    "prompt_match: cannot score picture %s (cleaned prompt %r from "
                    "%r, text embedding %s, image embedding %d dims); storing %s",
                    pid,
                    text,
                    prompt[:120],
                    "none" if text_embedding is None else text_embedding.shape,
                    image.shape[0],
                    PROMPT_MATCH_FAILED,
                )
            updates.append((pid, prompt, image_blob, score))

        changed = self._db.run_task(_persist, updates, priority=DBPriority.LOW)
        logger.debug(
            "PromptMatchTask scored %d pictures (%d distinct prompts) in %.2fs",
            len(updates),
            len(texts),
            time.time() - start,
        )
        return {"changed_count": len(changed), "changed": changed}

    def _distractor_bank(self) -> np.ndarray | None:
        if PromptMatchTask._bank is None:
            PromptMatchTask._bank = self._clip.encode_texts(list(DISTRACTOR_PROMPTS))
        return PromptMatchTask._bank

    def _text_embeddings(self, texts: set[str]) -> dict[str, np.ndarray]:
        cache = PromptMatchTask._text_cache
        missing = sorted(text for text in texts if text not in cache)
        if missing:
            encoded = self._clip.encode_texts(missing)
            if encoded is not None:
                if len(cache) + len(missing) > self._TEXT_CACHE_LIMIT:
                    cache.clear()
                cache.update(zip(missing, encoded))
        return {text: cache[text] for text in texts if text in cache}


def _fetch_inputs(session: Session, picture_ids: list[int]) -> list[tuple]:
    """``(id, prompt, image embedding bytes)`` for those still to be scored."""
    pictures = session.exec(
        select(Picture)
        .options(
            load_only(
                Picture.id, Picture.comfyui_positive_prompt, Picture.image_embedding
            )
        )
        .where(Picture.id.in_(picture_ids))
        .where(Picture.comfyui_positive_prompt.is_not(None))
        .where(Picture.image_embedding.is_not(None))
    ).all()
    return [
        (pic.id, pic.comfyui_positive_prompt, bytes(pic.image_embedding))
        for pic in pictures
    ]


def _persist(session: Session, updates: list[tuple[int, str, bytes, float]]) -> list:
    """Store each score only if its prompt and image embedding are still current.

    Extraction and re-embedding reset ``prompt_match`` to NULL when they replace
    an input; writing a score computed from the old one would undo that reset
    and the new inputs would never be scored.
    """
    changed = []
    for picture_id, prompt, image_blob, score in updates:
        pic = session.get(Picture, picture_id)
        if pic is None:
            continue
        if pic.comfyui_positive_prompt != prompt or (
            pic.image_embedding is None or bytes(pic.image_embedding) != image_blob
        ):
            logger.debug(
                "prompt_match: picture %s changed its prompt or image embedding "
                "while it was scored; dropping the stale score, the finder "
                "picks it up again",
                picture_id,
            )
            continue
        pic.prompt_match = score
        session.add(pic)
        changed.append((Picture, picture_id, "prompt_match", score))
    session.commit()
    return changed

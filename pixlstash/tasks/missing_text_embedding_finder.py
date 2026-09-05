from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import load_only, selectinload

from pixlstash.db_models import (
    Character,
    DESCRIPTION_SENTINEL_ESCAPE_CHAR,
    DESCRIPTION_SENTINEL_LIKE_PATTERN,
    Picture,
    TAG_SENTINEL_ESCAPE_CHAR,
    TAG_SENTINEL_LIKE_PATTERN,
    Tag,
)

from .base_task_finder import SimpleMissingFinder
from .text_embedding_task import TextEmbeddingTask


class MissingTextEmbeddingFinder(SimpleMissingFinder):
    """Find a batch of pictures missing text embeddings and create a TextEmbeddingTask."""

    EMBEDDING_BATCH_SIZE = 32

    def __init__(
        self,
        database,
        engine_getter: Callable,
    ):
        super().__init__(database)
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingTextEmbeddingFinder"

    def _guard(self) -> bool:
        return self._engine_getter() is not None

    def _batch_size(self) -> int:
        return self.EMBEDDING_BATCH_SIZE

    def _fetch_candidates(self, session: Session, limit: int):
        query = select(Picture)
        query = query.options(
            load_only(Picture.id, Picture.description, Picture.text_embedding),
            selectinload(Picture.tags),
            selectinload(Picture.characters).load_only(
                Character.id,
                Character.name,
                Character.description,
            ),
        )
        # Served by ix_picture_text_embedding_missing; the rest are filters.
        query = query.where(Picture.text_embedding.is_(None))
        # Per-row stage dependencies, in place of a barrier on the whole tag
        # and description stages: wait for THIS picture's tags and description.
        # A stage that is switched off never delivers, so it is not waited for
        # -- decided exactly the way MissingTagFinder / MissingDescriptionFinder
        # decide whether to run at all (``tagger_settings`` absent means the
        # tagger is off and Florence-2 captioning is on).
        settings = getattr(self._engine_getter(), "tagger_settings", None)
        if (settings or {}).get("active_tag_plugin"):
            pending_tags = Tag.tag.like(
                TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
            )
            query = query.where(~Picture.tags.any(pending_tags))
        if settings is None or settings.get("active_description_plugin"):
            query = query.where(
                Picture.description.is_not(None),
                ~Picture.description.like(
                    DESCRIPTION_SENTINEL_LIKE_PATTERN,
                    escape=DESCRIPTION_SENTINEL_ESCAPE_CHAR,
                ),
            )
        query = query.order_by(Picture.id)
        query = query.limit(limit)
        return session.exec(query).all()

    def _create_task(self, pictures: list):
        return TextEmbeddingTask(
            database=self._db,
            workflow=self._engine_getter().text_embedding_workflow,
            pictures=pictures,
        )

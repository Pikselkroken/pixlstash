from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import load_only, selectinload

from pixlstash.db_models import Character, Picture

from .base_task_finder import SimpleMissingFinder
from .text_embedding_task import TextEmbeddingTask


class MissingTextEmbeddingFinder(SimpleMissingFinder):
    """Find a batch of pictures missing text embeddings and create a TextEmbeddingTask."""

    EMBEDDING_BATCH_SIZE = 32

    def __init__(
        self,
        database,
        picture_tagger_getter: Callable,
    ):
        super().__init__(database)
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingTextEmbeddingFinder"

    def depends_on(self) -> list[str]:
        # Defer text embeddings until face extraction, tagging and description
        # generation have all drained.  Face extraction has GPU priority;
        # tags and descriptions feed into the embedded text so must complete first.
        return [
            "MissingFaceExtractionFinder",
            "MissingTagFinder",
            "MissingDescriptionFinder",
        ]

    def _guard(self) -> bool:
        return self._picture_tagger_getter() is not None

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
        query = query.where(Picture.text_embedding.is_(None))
        query = query.where(Picture.description.is_not(None))
        query = query.order_by(Picture.id)
        query = query.limit(limit)
        return session.exec(query).all()

    def _create_task(self, pictures: list):
        return TextEmbeddingTask(
            database=self._db,
            workflow=self._picture_tagger_getter().text_embedding_workflow,
            pictures=pictures,
        )

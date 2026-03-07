from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import load_only, selectinload

from pixlvault.db_models import Character, Picture

from .base_task_finder import BaseTaskFinder
from .text_embedding_task import TextEmbeddingTask


class MissingTextEmbeddingFinder(BaseTaskFinder):
    """Find a batch of pictures missing text embeddings and create a TextEmbeddingTask."""

    EMBEDDING_BATCH_SIZE = 32

    def __init__(
        self,
        database,
        picture_tagger_getter: Callable,
    ):
        super().__init__()
        self._db = database
        self._picture_tagger_getter = picture_tagger_getter

    def finder_name(self) -> str:
        return "MissingTextEmbeddingFinder"

    def find_task(self):
        picture_tagger = self._picture_tagger_getter()
        if picture_tagger is None:
            return None

        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_text_embeddings(
                session,
                self.EMBEDDING_BATCH_SIZE * 3,
            )
        )
        if not pictures:
            return None

        selected = self._filter_and_claim(pictures, self.EMBEDDING_BATCH_SIZE)
        if not selected:
            return None

        return TextEmbeddingTask(
            database=self._db,
            picture_tagger=picture_tagger,
            pictures=selected,
        )

    @staticmethod
    def _fetch_missing_text_embeddings(session: Session, limit: int):
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

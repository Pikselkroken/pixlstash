import time

from sqlmodel import select, Session
from sqlalchemy.orm import load_only, selectinload
from sqlalchemy import func

from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.database import VaultDatabase
from pixlvault.worker_registry import BaseWorker, WorkerType

from pixlvault.db_models import Character, Picture

logger = get_logger(__name__)

class EmbeddingWorker(BaseWorker):
    """
    Worker for generating text embeddings for pictures with descriptions.
    """

    EMBEDDING_BATCH_SIZE = 32

    def worker_type(self) -> WorkerType:
        return WorkerType.TEXT_EMBEDDING

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("[EMBEDDING WORKER]  Starting iteration...")
                embeddings_updated = 0
                total_described = self._db.run_immediate_read_task(
                    self._count_total_described
                )
                total_missing = self._db.run_immediate_read_task(
                    self._count_missing_text_embeddings
                )
                total = max(int(total_described or 0), 0)
                missing = max(int(total_missing or 0), 0)
                self._set_progress(
                    label="text_embeddings",
                    current=max(total - missing, 0),
                    total=total,
                )
                pictures_to_embed = self._fetch_missing_text_embeddings()
                logger.debug(
                    f"[EMBEDDING WORKER]  Got {len(pictures_to_embed)} pictures needing embeddings."
                )
                if not pictures_to_embed:
                    timing = time.time() - start
                    logger.debug(
                        f"[EMBEDDING WORKER]  Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
                    continue
                if self._stop.is_set():
                    break
                embeddings_generated = self._generate_text_embeddings(pictures_to_embed)
                logger.debug(
                    f"[EMBEDDING WORKER]  Generated {len(embeddings_generated)} embeddings."
                )
                if self._stop.is_set():
                    break
                if embeddings_generated:
                    changed = self._update_text_embeddings(embeddings_generated)
                    embeddings_updated = len(changed)
                timing = time.time() - start
                if embeddings_updated > 0:
                    logger.debug(
                        f"[EMBEDDING WORKER]  Done after {timing:.2f} seconds. Having updated {embeddings_updated} pictures."
                    )
            except Exception as e:
                logger.debug(
                    f"EmbeddingWorker thread exiting due to DB error (likely shutdown): {e}"
                )
                break
        logger.info("Exiting EmbeddingWorker loop.")

    def _fetch_missing_text_embeddings(self):
        """Return Pictures needing text embeddings."""

        def find_pictures_without_embeddings(session: Session):
            # Only load fields needed for text embedding
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
            query = query.limit(self.EMBEDDING_BATCH_SIZE)
            results = session.exec(query)
            return results.all()

        return VaultDatabase.result_or_throw(
            self._db.submit_task(find_pictures_without_embeddings)
        )

    @staticmethod
    def _count_total_described(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.description.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_text_embeddings(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.description.is_not(None))
            .where(Picture.text_embedding.is_(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    def _generate_text_embeddings(self, pictures_to_embed):
        """
        Generate text embeddings for a batch of PictureModel objects using PictureTagger.
        Returns the number of pictures updated.
        """
        embeddings = self._picture_tagger.generate_text_embedding(
            pictures=pictures_to_embed
        )
        if not embeddings:
            return []

        if len(embeddings) != len(pictures_to_embed):
            logger.warning(
                "[EMBEDDING WORKER] Embedding count mismatch: embeddings=%s pictures=%s",
                len(embeddings),
                len(pictures_to_embed),
            )

        limit = min(len(embeddings), len(pictures_to_embed))
        for pic, embedding in zip(pictures_to_embed[:limit], embeddings[:limit]):
            pic.text_embedding = embedding

        return pictures_to_embed[:limit]

    def _update_text_embeddings(self, pictures: list[Picture]):
        """
        Update the text embeddings for a picture in the database, with detailed logging.
        """

        def update_pictures(session: Session, pics):
            changed = []
            for pic in pics:
                db_pic = session.get(Picture, pic.id)
                if db_pic:
                    db_pic.text_embedding = pic.text_embedding
                    session.add(db_pic)
                    changed.append(
                        (Picture, pic.id, "text_embedding", pic.text_embedding)
                    )
            session.commit()
            logger.debug(
                f"[EMBEDDING WORKER] Committed {len(changed)} embedding updates to DB."
            )
            return changed

        changed = self._db.run_task(update_pictures, pictures, priority=DBPriority.LOW)
        self._notify_ids_processed(changed)
        return changed

from sqlmodel import Session

from pixlvault.database import DBPriority
from pixlvault.db_models import Picture
from pixlvault.picture_tagger import PictureTagger
from pixlvault.pixl_logging import get_logger
from pixlvault.task_runner import BaseTask


logger = get_logger(__name__)


class TextEmbeddingTask(BaseTask):
    """Task for generating and persisting text embedding batches."""

    def __init__(
        self,
        database,
        picture_tagger: PictureTagger,
        pictures: list[Picture],
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="TextEmbeddingTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._picture_tagger = picture_tagger
        self._pictures = pictures or []

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": []}

        embeddings_generated = self._generate_text_embeddings(self._pictures)
        if not embeddings_generated:
            return {"changed_count": 0, "changed": []}

        def update_pictures(session: Session, pics):
            changed = []
            for pic in pics:
                db_pic = session.get(Picture, pic.id)
                if db_pic is not None:
                    db_pic.text_embedding = pic.text_embedding
                    session.add(db_pic)
                    changed.append(
                        (Picture, pic.id, "text_embedding", pic.text_embedding)
                    )
            session.commit()
            logger.debug(
                "TextEmbeddingTask: Committed %s embedding updates to DB.",
                len(changed),
            )
            return changed

        changed = self._db.run_task(
            update_pictures,
            embeddings_generated,
            priority=DBPriority.LOW,
        )

        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    def _generate_text_embeddings(
        self, pictures_to_embed: list[Picture]
    ) -> list[Picture]:
        embeddings = self._picture_tagger.generate_text_embedding(
            pictures=pictures_to_embed
        )
        if not embeddings:
            return []

        if len(embeddings) != len(pictures_to_embed):
            logger.warning(
                "TextEmbeddingTask: Embedding count mismatch: embeddings=%s pictures=%s",
                len(embeddings),
                len(pictures_to_embed),
            )

        limit = min(len(embeddings), len(pictures_to_embed))
        for pic, embedding in zip(pictures_to_embed[:limit], embeddings[:limit]):
            pic.text_embedding = embedding

        return pictures_to_embed[:limit]

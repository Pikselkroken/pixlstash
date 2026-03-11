from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Character, Picture
from pixlstash.picture_tagger import PictureTagger
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask


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

        # Re-fetch pictures from DB with current character associations so that
        # any association that happened after the finder picked these pictures up
        # is reflected in the embedding text.
        picture_ids = [pic.id for pic in self._pictures]

        def fetch_fresh(session: Session, ids: list[int]) -> list[Picture]:
            return session.exec(
                select(Picture)
                .where(Picture.id.in_(ids))
                .options(
                    selectinload(Picture.characters).load_only(
                        Character.id,
                        Character.name,
                        Character.description,
                    ),
                    selectinload(Picture.tags),
                )
            ).all()

        fresh_pictures = self._db.run_immediate_read_task(fetch_fresh, picture_ids)
        # Preserve original order and description from the finder's snapshot.
        description_map = {pic.id: pic.description for pic in self._pictures}
        fresh_by_id = {pic.id: pic for pic in fresh_pictures}
        pictures_to_embed = []
        for pid in picture_ids:
            pic = fresh_by_id.get(pid)
            if pic is None:
                continue
            # Description may not be loaded in the fresh fetch; carry it over.
            if pic.description is None:
                pic.description = description_map.get(pid)
            pictures_to_embed.append(pic)

        if not pictures_to_embed:
            return {"changed_count": 0, "changed": []}

        embeddings_generated = self._generate_text_embeddings(pictures_to_embed)
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

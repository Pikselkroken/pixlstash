import time

from sqlmodel import select, Session
from sqlalchemy.orm import load_only, selectinload

from pixlvault.event_types import EventType
from pixlvault.picture_tagger import PictureTagger
from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.database import VaultDatabase
from pixlvault.worker_registry import BaseWorker, WorkerType

from pixlvault.db_models import Character, Picture, Tag

logger = get_logger(__name__)


class DescriptionWorker(BaseWorker):
    """
    Worker for generating picture descriptions only.
    """

    def worker_type(self) -> WorkerType:
        return WorkerType.DESCRIPTION

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.info("DescriptionWorker: Starting iteration...")
                data_updated = False
                missing_descriptions = self._fetch_missing_descriptions()

                logger.debug(
                    f"DescriptionWorker: Got {len(missing_descriptions)} pictures needing descriptions."
                )
                if self._stop.is_set():
                    break
                descriptions_generated = self._generate_descriptions(
                    self._picture_tagger, missing_descriptions
                )
                logger.debug(
                    f"DescriptionWorker: Generated {len(descriptions_generated)} descriptions."
                )
                if self._stop.is_set():
                    break
                if descriptions_generated:

                    def update_descriptions(session: Session, pics):
                        changed = []
                        for pic in pics:
                            db_pic = session.get(Picture, pic.id)
                            if db_pic is not None:
                                db_pic.description = pic.description
                                session.add(db_pic)
                                changed.append(
                                    (Picture, pic.id, "description", pic.description)
                                )
                        session.commit()
                        return changed

                    changed = self._db.run_task(
                        update_descriptions,
                        descriptions_generated,
                        priority=DBPriority.LOW,
                    )
                    data_updated = len(changed) > 0
                    self._notify_ids_processed(changed)
                    self._notify_others(EventType.CHANGED_DESCRIPTIONS)
                timing = time.time() - start
                if data_updated:
                    logger.debug(f"DescriptionWorker: Done after {timing:.2f} seconds.")
                else:
                    logger.debug(
                        f"DescriptionWorker: Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except Exception as e:
                import traceback

                logger.error(
                    "DescriptionWorker thread exiting due to error: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                break
        logger.info("Exiting DescriptionWorker loop.")

    def _fetch_missing_descriptions(self):
        logger.debug("Starting the database fetch for missing descriptions")

        return VaultDatabase.result_or_throw(
            self._db.submit_task(
                lambda session: session.exec(
                    select(Picture)
                    .where(Picture.description.is_(None))
                    .options(selectinload(Picture.characters))
                ).all()
            )
        )

    def _generate_descriptions(
        self, picture_tagger: PictureTagger, missing_descriptions: list[Picture]
    ) -> int:
        """Generate descriptions for pictures using PictureTagger."""
        assert missing_descriptions is not None
        batch = missing_descriptions[: picture_tagger.max_concurrent_images()]

        descriptions_generated = []
        for pic in batch:
            try:
                description = picture_tagger.generate_description(picture=pic)
                logger.debug("[DESCRIPTION WORKER] Got description: " + description)

                def set_description(session: Session, pic_id, description):
                    pic = session.exec(
                        select(Picture)
                        .where(Picture.id == pic_id)
                        .options(selectinload(Picture.characters))
                    ).one()
                    pic.description = description
                    session.add(pic)
                    session.commit()
                    session.refresh(pic)
                    return pic

                pic = self._db.run_task(
                    set_description, pic.id, description, priority=DBPriority.LOW
                )
                assert pic.description == description

                descriptions_generated.append(pic)

            except Exception as e:
                logger.error(
                    f"Failed to generate/store description for picture {pic.id}: {e}"
                )
        return descriptions_generated


class TagWorker(BaseWorker):
    """
    Worker for generating tags for pictures with descriptions.
    """

    def worker_type(self) -> WorkerType:
        return WorkerType.TAGGER  # Or define a new WorkerType if desired

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("TaggingWorker: Starting iteration...")
                missing_tags = self._fetch_missing_tags()
                logger.debug(
                    f"TaggingWorker: Got {len(missing_tags)} pictures needing tags."
                )
                if self._stop.is_set():
                    break
                tagged_pictures = self._tag_pictures(missing_tags)
                self._notify_ids_processed(tagged_pictures)
                logger.debug(f"TaggingWorker: Tagged {len(tagged_pictures)} pictures.")
                timing = time.time() - start
                if tagged_pictures:
                    self._notify_others(EventType.CHANGED_TAGS)
                    logger.debug(
                        f"TaggingWorker: Done after {timing:.2f} seconds. Having updated {len(tagged_pictures)} pictures."
                    )
                else:
                    logger.debug(
                        f"TaggingWorker: Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except Exception as e:
                import traceback

                logger.error(
                    "TaggingWorker thread exiting due to error: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                break
        logger.info("Exiting TaggingWorker loop.")

    def _fetch_missing_tags(self):
        logger.debug("Starting the database fetch for missing tags")

        def fetch_tags(session: Session):
            statement = (
                select(Picture)
                .where(~Picture.tags.any())
                .options(selectinload(Picture.tags))
            )
            result = session.exec(statement)
            return result.all()

        return VaultDatabase.result_or_throw(self._db.submit_task(fetch_tags))

    def _tag_pictures(self, missing_tags) -> int:
        """Tag all pictures missing tags."""
        assert missing_tags is not None
        batch = missing_tags[: self._picture_tagger.max_concurrent_images()]
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            image_paths.append(pic.file_path)
            pic_by_path[pic.file_path] = pic

        tagged_pictures = []
        if image_paths:
            logger.debug(f"Tagging {len(image_paths)} images: {image_paths}")
            tag_results = self._picture_tagger.tag_images(image_paths)
            logger.debug(f"Got tag results for {len(tag_results)} images.")
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                logger.debug(f"Processing tags for image at path: {path}: {tags}")
                if tags:

                    def add_tags(session: Session, pic_id, tags):
                        pic = Picture.find(session, id=pic_id)
                        session.add_all([Tag(picture_id=pic_id, tag=t) for t in tags])
                        session.commit()
                        if pic:
                            session.refresh(pic[0])
                            return pic[0]
                        return None

                    pic = self._db.run_task(
                        add_tags,
                        pic.id,
                        tags,
                        priority=DBPriority.LOW,
                    )
                    tagged_pictures.append((Picture, pic.id, "tags", tags))

        return tagged_pictures


class EmbeddingWorker(BaseWorker):
    """
    Worker for generating text embeddings for pictures with descriptions.
    """

    def worker_type(self) -> WorkerType:
        return WorkerType.TEXT_EMBEDDING

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("[EMBEDDING WORKER]  Starting iteration...")
                embeddings_updated = 0
                pictures_to_embed = self._fetch_missing_text_embeddings()
                logger.debug(
                    f"[EMBEDDING WORKER]  Got {len(pictures_to_embed)} pictures needing embeddings."
                )
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
                else:
                    logger.debug(
                        f"[EMBEDDING WORKER]  Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
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
                    Character.original_prompt,
                ),
            )
            query = query.where(Picture.text_embedding.is_(None))
            query = query.where(Picture.description.is_not(None))
            results = session.exec(query)
            return results.all()

        return VaultDatabase.result_or_throw(
            self._db.submit_task(find_pictures_without_embeddings)
        )

    def _generate_text_embeddings(self, pictures_to_embed):
        """
        Generate text embeddings for a batch of PictureModel objects using PictureTagger.
        Returns the number of pictures updated.
        """
        updated = []
        for pic in pictures_to_embed:
            try:
                logger.debug(
                    f"[EMBEDDING WORKER]  Generating embedding for picture {pic.id} of type {type(pic)}"
                )
                embedding, _ = self._picture_tagger.generate_text_embedding(picture=pic)
                if embedding is not None:
                    pic.text_embedding = embedding.tobytes()
                    updated.append(pic)
            except Exception as e:
                logger.error(f"Failed to generate text embedding for {pic.id}: {e}")
        return updated

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

import sqlite3
import time

from pixlvault.picture_tagger import PictureTagger
import pixlvault.picture_db_tools as db_tools

from .characters import Characters
from .database import DBPriority
from .logging import get_logger
from .worker_registry import BaseWorker, WorkerType

logger = get_logger(__name__)


class DescriptionWorker(BaseWorker):
    """
    Worker for generating picture descriptions only.
    """

    def __init__(
        self,
        db_connection,
        picture_tagger: PictureTagger,
        characters: Characters,
        position: int = 0,
    ):
        super().__init__(db_connection, picture_tagger, characters)
        self._progress_position = position

    def worker_type(self) -> WorkerType:
        return WorkerType.DESCRIPTION

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("DescriptionWorker: Starting iteration...")
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
                    self._update_attributes(descriptions_generated, ["description"])
                    data_updated = True
                timing = time.time() - start
                if timing > 0.5:
                    logger.info(f"DescriptionWorker: Done after {timing:.2f} seconds.")
                if not data_updated:
                    logger.info(
                        f"DescriptionWorker: Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except (sqlite3.OperationalError, OSError) as e:
                logger.debug(
                    f"DescriptionWorker thread exiting due to DB error (likely shutdown): {e}"
                )
                break
        logger.info("Exiting DescriptionWorker loop.")

    def _fetch_missing_descriptions(self):
        logger.debug("Starting the database fetch for missing descriptions")

        rows_missing_descriptions = self._db.submit_task(
            lambda conn: conn.execute(
                """
            SELECT p.*
            FROM pictures p
            WHERE p.description IS NULL
            """
            ).fetchall()
        ).result()

        return db_tools.from_batch_of_db_dicts(rows_missing_descriptions, [])

    def _generate_descriptions(self, picture_tagger, missing_descriptions) -> int:
        """Generate descriptions for pictures using PictureTagger."""
        assert missing_descriptions is not None
        batch = missing_descriptions[: picture_tagger.max_concurrent_images()]

        descriptions_generated = []
        for pic in batch:
            try:
                # Look up full Character object if available
                character_obj = None
                char_id = getattr(pic, "primary_character_id", None)
                assert self._characters is not None, "Characters manager is not set."
                if char_id is not None and self._characters is not None:
                    try:
                        character_obj = self._characters[int(char_id)]
                        if hasattr(character_obj, "name"):
                            logger.debug(f"Character name value: {character_obj.name}")
                    except Exception as e:
                        logger.error(
                            f"Failed to fetch character {char_id}: {e}", exc_info=True
                        )
                        character_obj = None
                logger.debug(
                    f"Generating embedding for picture {pic.id} with character {char_id} and character name {getattr(character_obj, 'name', None)}"
                )
                pic.description = picture_tagger.generate_description(
                    picture=pic, character=character_obj
                )
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

    def __init__(
        self,
        db_connection,
        picture_tagger: PictureTagger,
        characters: Characters,
        position: int = 0,
    ):
        super().__init__(db_connection, picture_tagger, characters)
        self._progress_position = position

    def worker_type(self) -> WorkerType:
        return WorkerType.TAGGER  # Or define a new WorkerType if desired

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("TaggingWorker: Starting iteration...")
                data_updated = False
                missing_tags = self._fetch_pictures_missing_tags()
                logger.debug(
                    f"TaggingWorker: Got {len(missing_tags)} pictures needing tags."
                )
                if self._stop.is_set():
                    break
                tagged_pictures = self._tag_pictures(missing_tags)
                logger.debug(f"TaggingWorker: Tagged {len(tagged_pictures)} pictures.")
                if self._stop.is_set():
                    break
                if tagged_pictures:
                    self._update_picture_tags(tagged_pictures)
                    data_updated = True
                timing = time.time() - start
                if timing > 0.5:
                    logger.info(f"TaggingWorker: Done after {timing:.2f} seconds.")
                if not data_updated:
                    logger.info(
                        f"TaggingWorker: Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except (sqlite3.OperationalError, OSError) as e:
                logger.debug(
                    f"TaggingWorker thread exiting due to DB error (likely shutdown): {e}"
                )
                break
        logger.info("Exiting TaggingWorker loop.")

    def _fetch_pictures_missing_tags(self):
        """Return PictureModels needing tags using the provided connection."""

        logger.debug("Starting the optimized database fetch for missing tags.")
        pictures_missing_tags = self._db.submit_task(
            lambda conn: conn.execute(
                """
            SELECT p.*
            FROM pictures p
            LEFT JOIN picture_tags pt ON pt.picture_id = p.id
            WHERE p.description IS NOT NULL
            GROUP BY p.id
            HAVING COUNT(pt.tag) = 0
            """
            ).fetchall()
        ).result()
        return db_tools.from_batch_of_db_dicts(pictures_missing_tags)

    def _update_picture_tags(self, pictures):
        """
        Update the tags for a picture in the database using the picture_tags table.
        """

        def bulk_update_tags(conn, pictures):
            cursor = conn.cursor()
            cursor.executemany(
                "DELETE FROM picture_tags WHERE picture_id = ?",
                [(picture.id,) for picture in pictures],
            )
            cursor.executemany(
                "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                [(picture.id, tag) for picture in pictures for tag in picture.tags],
            )
            conn.commit()

        self._db.submit_task(bulk_update_tags, pictures, priority=DBPriority.LOW)

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
                if pic is not None:
                    # Remove character tag from tags if present
                    char_tag = getattr(pic, "primary_character_id", None)
                    if char_tag and char_tag in tags:
                        tags = [t for t in tags if t != char_tag]
                    # Use Florence description to correct tags
                    try:
                        corrected_tags = (
                            self._picture_tagger.correct_tags_with_florence(
                                pic.description, tags
                            )
                        )
                        if corrected_tags:
                            tags = corrected_tags
                    except Exception as e:
                        logger.error(
                            f"Florence tag correction failed for {pic.file_path}: {e}"
                        )
                    if tags:
                        pic.tags = tags
                        tagged_pictures.append(pic)

        return tagged_pictures


class EmbeddingWorker(BaseWorker):
    """
    Worker for generating text embeddings for pictures with descriptions.
    """

    def __init__(
        self,
        db_connection,
        picture_tagger: PictureTagger,
        characters: Characters,
        position: int = 0,
    ):
        super().__init__(db_connection, picture_tagger, characters)
        self._progress_position = position

    def worker_type(self) -> WorkerType:
        return WorkerType.TEXT_EMBEDDING

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("EmbeddingWorker: Starting iteration...")
                data_updated = False
                pictures_to_embed = self._fetch_missing_text_embeddings()
                logger.debug(
                    f"EmbeddingWorker: Got {len(pictures_to_embed)} pictures needing embeddings."
                )
                if self._stop.is_set():
                    break
                embeddings_generated = self._generate_text_embeddings(pictures_to_embed)
                logger.debug(
                    f"EmbeddingWorker: Generated {len(embeddings_generated)} embeddings."
                )
                if self._stop.is_set():
                    break
                if embeddings_generated:
                    self._update_attributes(embeddings_generated, ["text_embedding"])
                    data_updated = True
                timing = time.time() - start
                if timing > 0.5:
                    logger.info(f"EmbeddingWorker: Done after {timing:.2f} seconds.")
                if not data_updated:
                    logger.info(
                        f"EmbeddingWorker: Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except (sqlite3.OperationalError, OSError) as e:
                logger.debug(
                    f"EmbeddingWorker thread exiting due to DB error (likely shutdown): {e}"
                )
                break
        logger.info("Exiting EmbeddingWorker loop.")

    def _fetch_missing_text_embeddings(self):
        """Return PictureModels needing text embeddings."""

        rows_missing_embeddings = self._db.submit_task(
            lambda conn: conn.execute(
                """
                SELECT *
                FROM pictures WHERE description IS NOT NULL AND text_embedding IS NULL
                """
            ).fetchall()
        ).result()
        return db_tools.from_batch_of_db_dicts(rows_missing_embeddings, [])

    def _generate_text_embeddings(self, pictures_to_embed):
        """
        Generate text embeddings for a batch of PictureModel objects using PictureTagger.
        Returns the number of pictures updated.
        """
        updated = []
        for pic in pictures_to_embed:
            try:
                embedding, _ = self._picture_tagger.generate_text_embedding(pic)
                if embedding is not None:
                    pic.text_embedding = embedding
                    updated.append(pic)
            except Exception as e:
                logger.error(f"Failed to generate text embedding for {pic.id}: {e}")
        return updated

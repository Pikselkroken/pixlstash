import json

from pixelurgy_vault.logging import get_logger

from pixelurgy_vault.picture import Picture
from pixelurgy_vault.picture_tagger import PictureTagger, MAX_CONCURRENT_IMAGES

logger = get_logger(__name__)

class Pictures:
    def __init__(self, connection, picture_iterations):
        self.connection = connection
        self.picture_iterations = picture_iterations
        self.picture_tagger = PictureTagger()

    def __getitem__(self, picture_id):
        # Return master Picture by picture_uuid
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT id, character_id, description, tags, created_at, embedding FROM pictures WHERE id = ?",
            (picture_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Picture with id {picture_id} not found.")
        tags = []
        if row['tags']:
            try:
                tags = json.loads(row['tags'])
            except Exception:
                tags = []
        has_embedding = bool(row['embedding']) if 'embedding' in row.keys() else False
        pic = Picture(
            id=row['id'],
            character_id=row['character_id'],
            description=row['description'],
            tags=tags,
            created_at=row['created_at'],
            has_embedding=has_embedding,
        )
        return pic

    def __setitem__(self, picture_id, picture):
        picture.id = picture_id
        self.import_picture(picture)

    def __delitem__(self, picture_id):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM pictures WHERE id = ?", (picture_id,))
        self.connection.commit()

    def __iter__(self):
        cursor = self.connection.cursor()
        cursor.execute("SELECT id FROM pictures")
        for row in cursor.fetchall():
            yield row['id']

    def update_picture_tags(self, picture_id, tags):
        """
        Update the tags for a picture in the database.
        Uses a context manager for atomic update to avoid thread transaction issues.
        """
        tags_json = json.dumps(tags)
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                "UPDATE pictures SET tags = ? WHERE id = ?",
                (tags_json, picture_id)
            )

    def start_tag_worker(self, interval=0.01):
        import threading
        if hasattr(self, '_tag_worker') and self._tag_worker.is_alive():
            return
        self._tag_worker_stop = threading.Event()
        self._tag_worker = threading.Thread(target=self._tag_worker_loop, args=(interval,), daemon=True)
        self._tag_worker.start()

    def stop_tag_worker(self):
        if hasattr(self, '_tag_worker_stop'):
            self._tag_worker_stop.set()
        if hasattr(self, '_tag_worker'):
            self._tag_worker.join(timeout=5)

    def _tag_worker_loop(self, interval):
        while not self._tag_worker_stop.is_set():
            # Find all Pictures with no tags
            untagged = [pic for pic in self.find() if not pic.tags]
            if not untagged:
                self._tag_worker_stop.wait(interval)
                continue
            # Select up to MAX_CONCURRENT_IMAGES
            batch = untagged[:MAX_CONCURRENT_IMAGES]
            # Get master iteration image paths using self.picture_iterations
            image_paths = []
            pic_by_path = {}
            for pic in batch:
                # Find master iteration for this picture
                master_iters = [it for it in self.picture_iterations.find(picture_id=pic.id, is_master=1)]
                if master_iters:
                    master_iter = master_iters[0]
                    image_paths.append(master_iter.file_path)
                    pic_by_path[master_iter.file_path] = pic
            if not image_paths:
                self._tag_worker_stop.wait(interval)
                continue
            # Tag images
            logger.info(f"Tagging {len(image_paths)} images")
            tag_results = self.picture_tagger.tag_images(image_paths)
            # Assign tags and embeddings to each Picture
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                if pic is not None:
                    pic.tags = tags
                    self.update_picture_tags(pic.id, tags)
                    # Generate and store embedding
                    try:
                        print("Generating embedding for picture", pic.id, " after tagging: " , tags)
                        embedding = self.picture_tagger.generate_embedding(picture=pic)
                        # Store as BLOB in DB
                        with self.connection:
                            cursor = self.connection.cursor()
                            cursor.execute(
                                "UPDATE pictures SET embedding = ? WHERE id = ?",
                                (embedding.astype('float32').tobytes(), pic.id)
                            )
                    except Exception as e:
                        logger.error(f"Failed to generate/store embedding for picture {pic.id}: {e}")
            self._tag_worker_stop.wait(interval)

    def import_pictures(self, pictures):
        """Import a list of Picture instances into the database using executemany for efficiency."""
        cursor = self.connection.cursor()
        values = []
        for picture in pictures:
            tags_json = json.dumps(picture.tags) if hasattr(picture, "tags") else None
            values.append(
                (
                    picture.id,
                    getattr(picture, "character_id", None),
                    getattr(picture, "description", None),
                    tags_json,
                    getattr(picture, "created_at", None),
                    getattr(picture, "is_reference", 0),
                )
            )
        cursor.executemany(
            """
            INSERT INTO pictures (
                id, character_id, description, tags, created_at, is_reference
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        self.connection.commit()

    def contains(self, picture):
        """
        Check if a Picture with the same id exists in the database.
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT 1 FROM pictures WHERE id = ?", (picture.id,))
        return cursor.fetchone() is not None

    def find(self, **kwargs):
        """
        Find and return a list of Picture objects matching all provided attribute=value pairs.
        Example: pictures.find(character_id="hero")
        """
        cursor = self.connection.cursor()
        if not kwargs:
            cursor.execute("SELECT * FROM pictures")
        else:
            query = "SELECT * FROM pictures WHERE " + " AND ".join(
                [f"{k}=?" for k in kwargs.keys()]
            )
            cursor.execute(query, tuple(kwargs.values()))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            has_embedding = bool(row['embedding']) if 'embedding' in row.keys() else False
            pic = Picture(
                id=row['id'],
                character_id=row['character_id'],
                description=row['description'],
                tags=json.loads(row['tags']) if row['tags'] else [],
                created_at=row['created_at'],
                is_reference=row['is_reference'] if 'is_reference' in row.keys() else 0,
                has_embedding=has_embedding,
            )
            result.append(pic)
        return result

    def find_by_text(self, text, top_n=5, include_scores=False, threshold=0.5):
        """
        Find the top N pictures whose embeddings best match the input text.
        Returns a list of Picture objects (and optionally similarity scores).
        If the input text is empty, returns an empty list.
        Adds debug logging for diagnosis.
        """
        if not text or not str(text).strip():
            logger.warning("find_by_text called with empty text; returning empty result.")
            return []
        # Generate query embedding
        query_emb = self.picture_tagger.generate_embedding(picture={"description": text})
        logger.info(f"Semantic search: query embedding shape: {getattr(query_emb, 'shape', None)}")
        # Load all picture embeddings and ids
        cursor = self.connection.cursor()
        cursor.execute("SELECT id, embedding FROM pictures WHERE embedding IS NOT NULL")
        rows = cursor.fetchall()
        logger.info(f"Semantic search: found {len(rows)} candidate images with embeddings.")
        if not rows:
            return []
        # Compute similarities
        import numpy as np
        sims = []
        for row in rows:
            pic_id = row[0]
            emb_blob = row[1]
            if emb_blob is None:
                continue
            emb = np.frombuffer(emb_blob, dtype=np.float32)
            sim = float(np.dot(query_emb, emb) / (np.linalg.norm(query_emb) * np.linalg.norm(emb) + 1e-8))
            logger.info(f"Semantic search: similarity for {pic_id}: {sim}")
            if sim >= threshold:
                sims.append((pic_id, sim))
        # Sort by similarity, descending
        sims.sort(key=lambda x: x[1], reverse=True)
        top = sims[:top_n]
        logger.info(f"Semantic search: top {top_n} results above threshold {threshold}: {top}")
        # Fetch Picture objects
        results = []
        for pic_id, sim in top:
            pic = self[pic_id]
            if include_scores:
                results.append((pic, sim))
            else:
                results.append(pic)
        return results

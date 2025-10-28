import numpy as np
import json

from pixelurgy_vault.logging import get_logger

from pixelurgy_vault.picture import Picture
from pixelurgy_vault.picture_tagger import PictureTagger, MAX_CONCURRENT_IMAGES

logger = get_logger(__name__)


class Pictures:
    def _extract_face_embeddings(self, thread_conn):
        """Extract face embeddings for pictures that have no face_embedding, using the master iteration image."""
        if not hasattr(self, "_skip_pictures"):
            self._skip_pictures = set()
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            logger.error(
                "InsightFace is not installed. Skipping face embedding extraction."
            )
            return False

        # Initialize InsightFace only once
        if not hasattr(self, "_insightface_app"):
            self._insightface_app = FaceAnalysis()
            self._insightface_app.prepare(ctx_id=0)

        # Find pictures missing face_embedding
        cursor = thread_conn.cursor()
        cursor.execute(
            "SELECT id FROM pictures WHERE face_embedding IS NULL OR face_embedding = ''"
        )
        pic_ids = [row[0] for row in cursor.fetchall()]
        if not pic_ids:
            return False
        batch = [pic_id for pic_id in pic_ids if pic_id not in self._skip_pictures][
            :MAX_CONCURRENT_IMAGES
        ]
        for pic_id in batch:
            self._skip_pictures.add(pic_id)
            # Find master iteration for this picture
            master_iters = [
                it
                for it in self._picture_iterations.find(picture_id=pic_id, is_master=1)
            ]
            if not master_iters:
                continue
            master_iter = master_iters[0]
            try:
                import cv2

                img = cv2.imread(master_iter.file_path)
                if img is None:
                    logger.warning(
                        f"Could not read image {master_iter.file_path} for face embedding."
                    )
                    continue
                faces = self._insightface_app.get(img)
                if not faces:
                    logger.info(
                        f"No face found in {master_iter.file_path} for picture {pic_id}."
                    )
                    continue
                else:
                    logger.info(
                        f"Found {len(faces)} face(s) in {master_iter.file_path} for picture {pic_id}."
                    )
                # Use the largest face (by area)
                face = max(faces, key=lambda f: f.bbox[2] * f.bbox[3])
                embedding = face.embedding
                embedding_json = json.dumps(embedding.tolist())
                bbox_json = json.dumps([float(v) for v in face.bbox])
                with thread_conn:
                    cursor2 = thread_conn.cursor()
                    cursor2.execute(
                        "UPDATE pictures SET face_embedding = ?, face_bbox = ? WHERE id = ?",
                        (embedding_json, bbox_json, pic_id),
                    )
                logger.debug(f"Stored face embedding and bbox for picture {pic_id}.")
            except Exception as e:
                logger.error(
                    f"Failed to extract/store face embedding for picture {pic_id}: {e}"
                )
        return self._skip_pictures

    def __init__(self, connection, picture_iterations, db_path):
        self._connection = connection
        self._picture_iterations = picture_iterations
        self._db_path = db_path
        self._picture_tagger = PictureTagger()
        self._skip_pictures = set()

    def __getitem__(self, picture_id):
        # Return master Picture by picture_uuid
        import sqlite3
        import time

        logger.info(f"Fetching picture with id={picture_id} (type={type(picture_id)})")
        retries = 5
        delay = 0.2
        for attempt in range(retries):
            try:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL;")
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, character_id, description, tags, created_at, embedding FROM pictures WHERE id = ?",
                    (picture_id,),
                )
                row = cursor.fetchone()
                conn.close()
                if not row:
                    raise KeyError(f"Picture with id {picture_id} not found.")
                tags = []
                if row["tags"]:
                    try:
                        tags = json.loads(row["tags"])
                    except Exception:
                        tags = []
                has_embedding = (
                    bool(row["embedding"]) if "embedding" in row.keys() else False
                )
                pic = Picture(
                    id=row["id"],
                    character_id=row["character_id"],
                    description=row["description"],
                    tags=tags,
                    created_at=row["created_at"],
                    has_embedding=has_embedding,
                )
                return pic
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < retries - 1:
                    logger.warning(
                        f"Database is locked, retrying ({attempt + 1}/{retries})..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    raise

    def __setitem__(self, picture_id, picture):
        picture.id = picture_id
        self.import_picture(picture)

    def __delitem__(self, picture_id):
        cursor = self._connection.cursor()
        cursor.execute("DELETE FROM pictures WHERE id = ?", (picture_id,))
        self._connection.commit()

    def __iter__(self):
        cursor = self._connection.cursor()
        cursor.execute("SELECT id FROM pictures")
        for row in cursor.fetchall():
            yield row["id"]

    def update_picture_tags(self, picture_id, tags):
        """
        Update the tags for a picture in the database.
        Uses a context manager for atomic update to avoid thread transaction issues.
        """
        tags_json = json.dumps(tags)
        with self._connection:
            cursor = self._connection.cursor()
            cursor.execute(
                "UPDATE pictures SET tags = ? WHERE id = ?", (tags_json, picture_id)
            )

    def start_embeddings_worker(self, interval=0.01):
        import threading

        if hasattr(self, "_tag_worker") and self._tag_worker.is_alive():
            return
        self._tag_worker_stop = threading.Event()
        self._tag_worker = threading.Thread(
            target=self._tag_embeddings_loop, args=(interval,), daemon=True
        )
        self._tag_worker.start()

    def stop_embeddings_worker(self):
        if hasattr(self, "_tag_worker_stop"):
            self._tag_worker_stop.set()
        if hasattr(self, "_tag_worker"):
            self._tag_worker.join(timeout=5)

    def _tag_embeddings_loop(self, interval):
        import sqlite3

        # Create a new connection for this thread
        thread_conn = sqlite3.connect(self._db_path, check_same_thread=False)
        thread_conn.row_factory = sqlite3.Row

        while not self._tag_worker_stop.is_set():
            self._tag_missing_pictures(thread_conn)
            self._embed_tagged_pictures(thread_conn)
            self._extract_face_embeddings(thread_conn)
            self._tag_worker_stop.wait(interval)

    def _tag_missing_pictures(self, thread_conn):
        """Tag all pictures missing tags."""
        missing_tags = [pic for pic in self.find() if not pic.tags]
        if not missing_tags:
            return False
        batch = missing_tags[:MAX_CONCURRENT_IMAGES]
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            master_iters = [
                it
                for it in self._picture_iterations.find(picture_id=pic.id, is_master=1)
            ]
            if master_iters:
                master_iter = master_iters[0]
                image_paths.append(master_iter.file_path)
                pic_by_path[master_iter.file_path] = pic
        if image_paths:
            logger.debug(f"Tagging {len(image_paths)} images")
            tag_results = self._picture_tagger.tag_images(image_paths)
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                if pic is not None:
                    # Remove character tag from tags if present
                    char_tag = getattr(pic, "character_id", None)
                    if char_tag and char_tag in tags:
                        tags = [t for t in tags if t != char_tag]
                    pic.tags = tags
                    tags_json = json.dumps(tags)
                    with thread_conn:
                        cursor = thread_conn.cursor()
                        cursor.execute(
                            "UPDATE pictures SET tags = ? WHERE id = ?",
                            (tags_json, pic.id),
                        )
        return True

    def _embed_tagged_pictures(self, thread_conn):
        """Generate embeddings for pictures that have tags but no embedding."""
        missing_embeddings = [
            pic
            for pic in self.find()
            if not getattr(pic, "has_embedding", False) and pic.tags
        ]
        if not missing_embeddings:
            return False
        batch = missing_embeddings[:MAX_CONCURRENT_IMAGES]
        for pic in batch:
            try:
                print(
                    "Generating embedding for picture",
                    pic.id,
                    " (tags: ",
                    pic.tags,
                    ")",
                )
                embedding = self._picture_tagger.generate_embedding(
                    picture=pic, character=pic.character_id
                )
                with thread_conn:
                    cursor = thread_conn.cursor()
                    cursor.execute(
                        "UPDATE pictures SET embedding = ? WHERE id = ?",
                        (embedding.astype("float32").tobytes(), pic.id),
                    )
                pic.has_embedding = True
            except Exception as e:
                logger.error(
                    f"Failed to generate/store embedding for picture {pic.id}: {e}"
                )
        return True

    def import_pictures(self, pictures):
        """Import a list of Picture instances into the database using executemany for efficiency."""
        import os

        cursor = self._connection.cursor()
        values = []
        for picture in pictures:
            # Remove character tag from tags if present
            tags = (
                list(picture.tags) if hasattr(picture, "tags") and picture.tags else []
            )
            char_tag = getattr(picture, "character_id", None)
            if char_tag and char_tag in tags:
                tags = [t for t in tags if t != char_tag]
            tags_json = json.dumps(tags)
            logger.debug(f"Preparing to insert Picture {picture.id} into database.")
            file_path = getattr(picture, "file_path", None)
            if file_path:
                if os.path.exists(file_path):
                    logger.debug(
                        f"File {file_path} for Picture {picture.id} exists before DB insert."
                    )
                else:
                    logger.warning(
                        f"File {file_path} for Picture {picture.id} does NOT exist before DB insert."
                    )
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
        self._connection.commit()
        for picture in pictures:
            file_path = getattr(picture, "file_path", None)
            if file_path:
                if os.path.exists(file_path):
                    logger.debug(
                        f"File {file_path} for Picture {picture.id} exists after DB insert."
                    )
                else:
                    logger.warning(
                        f"File {file_path} for Picture {picture.id} does NOT exist after DB insert."
                    )

    def update_pictures(self, pictures):
        """Update a list of Picture instances in the database using executemany for efficiency."""
        cursor = self._connection.cursor()
        values = []
        for picture in pictures:
            tags = (
                list(picture.tags) if hasattr(picture, "tags") and picture.tags else []
            )
            char_tag = getattr(picture, "character_id", None)
            if char_tag and char_tag in tags:
                tags = [t for t in tags if t != char_tag]
            tags_json = json.dumps(tags)
            values.append(
                (
                    getattr(picture, "character_id", None),
                    getattr(picture, "description", None),
                    tags_json,
                    getattr(picture, "created_at", None),
                    getattr(picture, "is_reference", 0),
                    picture.id,
                )
            )
        cursor.executemany(
            """
            UPDATE pictures SET character_id=?, description=?, tags=?, created_at=?, is_reference=? WHERE id=?
            """,
            values,
        )
        self._connection.commit()

    def contains(self, picture):
        """
        Check if a Picture with the same id exists in the database.
        """
        cursor = self._connection.cursor()
        cursor.execute("SELECT 1 FROM pictures WHERE id = ?", (picture.id,))
        return cursor.fetchone() is not None

    def find(self, **kwargs):
        """
        Find and return a list of Picture objects matching all provided attribute=value pairs.
        Example: pictures.find(character_id="hero")
        Special case: if a value is an empty string, search for IS NULL.
        """
        cursor = self._connection.cursor()
        if not kwargs:
            cursor.execute("SELECT * FROM pictures")
        else:
            clauses = []
            values = []
            for k, v in kwargs.items():
                if v == "":
                    clauses.append(f"{k} IS NULL")
                else:
                    clauses.append(f"{k}=?")
                    values.append(v)
            query = "SELECT * FROM pictures WHERE " + " AND ".join(clauses)
            cursor.execute(query, tuple(values))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            has_embedding = (
                bool(row["embedding"]) if "embedding" in row.keys() else False
            )
            pic = Picture(
                id=row["id"],
                character_id=row["character_id"],
                description=row["description"],
                tags=json.loads(row["tags"]) if row["tags"] else [],
                created_at=row["created_at"],
                is_reference=row["is_reference"] if "is_reference" in row.keys() else 0,
                has_embedding=has_embedding,
            )
            result.append(pic)
        return result

    def find_by_tag_or_description(self, query):
        """
        Find pictures where the query matches any tag or appears in the description (case-insensitive, partial match).
        """
        cursor = self._connection.cursor()
        q = f"%{query.lower()}%"
        # Search tags (as JSON string) and description
        cursor.execute(
            "SELECT * FROM pictures WHERE LOWER(description) LIKE ? OR LOWER(tags) LIKE ?",
            (q, q),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            has_embedding = (
                bool(row["embedding"]) if "embedding" in row.keys() else False
            )
            pic = Picture(
                id=row["id"],
                character_id=row["character_id"],
                description=row["description"],
                tags=json.loads(row["tags"]) if row["tags"] else [],
                created_at=row["created_at"],
                is_reference=row["is_reference"] if "is_reference" in row.keys() else 0,
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
            logger.warning(
                "find_by_text called with empty text; returning empty result."
            )
            return []
        # Generate query embedding
        query_emb = self._picture_tagger.generate_embedding(
            picture={"description": text}
        )
        logger.debug(
            f"Semantic search: query embedding shape: {getattr(query_emb, 'shape', None)}"
        )
        # Load all picture embeddings and ids
        cursor = self._connection.cursor()
        cursor.execute("SELECT id, embedding FROM pictures WHERE embedding IS NOT NULL")
        rows = cursor.fetchall()
        logger.debug(
            f"Semantic search: found {len(rows)} candidate images with embeddings."
        )
        if not rows:
            return []
        # Compute similarities

        sims = []
        for row in rows:
            pic_id = row[0]
            emb_blob = row[1]
            if emb_blob is None:
                continue
            emb = np.frombuffer(emb_blob, dtype=np.float32)
            sim = float(
                np.dot(query_emb, emb)
                / (np.linalg.norm(query_emb) * np.linalg.norm(emb) + 1e-8)
            )
            logger.debug(f"Semantic search: similarity for {pic_id}: {sim}")
            if sim >= threshold:
                sims.append((pic_id, sim))
        # Sort by similarity, descending
        sims.sort(key=lambda x: x[1], reverse=True)
        top = sims[:top_n]
        logger.debug(
            f"Semantic search: top {top_n} results above threshold {threshold}: {top}"
        )
        # Fetch Picture objects
        results = []
        for pic_id, sim in top:
            pic = self[pic_id]
            if include_scores:
                results.append((pic, sim))
            else:
                results.append(pic)
        return results

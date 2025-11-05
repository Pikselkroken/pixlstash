from enum import Enum

import gc
import numpy as np
import json
import time

from pixelurgy_vault.logging import get_logger

from pixelurgy_vault.picture import Picture
from pixelurgy_vault.picture_tagger import PictureTagger, MAX_CONCURRENT_IMAGES

logger = get_logger(__name__)


# Enum for sorting mechanisms
class SortMechanism(str, Enum):
    DATE_DESC = "date_desc"
    DATE_ASC = "date_asc"
    SCORE_DESC = "score_desc"
    SCORE_ASC = "score_asc"
    SEARCH_LIKENESS = "search_likeness"


# List of available sorting mechanisms for API
def get_sort_mechanisms():
    """Return a list of available sort mechanisms as dicts for API consumption."""
    return [
        {"id": sm.value, "label": label}
        for sm, label in [
            (SortMechanism.DATE_DESC, "Date (latest first)"),
            (SortMechanism.DATE_ASC, "Date (oldest first)"),
            (SortMechanism.SCORE_DESC, "Score (highest first)"),
            (SortMechanism.SCORE_ASC, "Score (lowest first)"),
            (SortMechanism.SEARCH_LIKENESS, "Sort by search likeness"),
        ]
    ]


class Pictures:
    INSIGHTFACE_CLEANUP_TIMEOUT = 20  # seconds

    def __init__(self, connection, picture_iterations, db_path, characters=None):
        self._connection = connection
        self._picture_iterations = picture_iterations
        self._db_path = db_path
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None
        self._characters = characters  # Should be a Characters manager or None
        self._picture_tagger = PictureTagger("cpu")

    def __getitem__(self, picture_id):
        # Return master Picture by picture_uuid
        import sqlite3
        import time

        logger.debug(f"Fetching picture with id={picture_id} (type={type(picture_id)})")
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
            except Exception as e:
                logger.error(f"Error fetching picture {picture_id}: {e}")
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

    def start_embeddings_worker(self, interval=0.1):
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
            self._tag_worker.join(timeout=10)

    def set_embedding_null(self, picture_id):
        """Set the embedding field to NULL for a given picture."""
        with self._connection:
            cursor = self._connection.cursor()
            cursor.execute(
                "UPDATE pictures SET embedding = NULL WHERE id = ?", (picture_id,)
            )

    def _tag_embeddings_loop(self, interval):
        import sqlite3

        # Create a new connection for this thread
        thread_conn = sqlite3.connect(self._db_path, check_same_thread=False)
        thread_conn.row_factory = sqlite3.Row

        calculate_face_embeddings = True

        while not self._tag_worker_stop.is_set():
            missing_tags = [pic for pic in self.find() if not pic.tags]
            missing_embeddings = [
                pic
                for pic in self.find()
                if not getattr(pic, "has_embedding", False) and pic.tags
            ]

            if missing_tags:
                logger.info(f"Tagging {len(missing_tags)} pictures missing tags.")
                self._tag_missing_pictures(
                    thread_conn, self._picture_tagger, missing_tags
                )

            if self._tag_worker_stop.is_set():
                break

            if missing_embeddings:
                logger.info(
                    f"Generating embeddings for {len(missing_embeddings)} pictures."
                )
                self._embed_tagged_pictures(
                    thread_conn, self._picture_tagger, missing_embeddings
                )

            if self._tag_worker_stop.is_set():
                break

            if calculate_face_embeddings:
                faces_needing_embeddings = self._find_faces_needing_embeddings(
                    thread_conn
                )
                calculate_face_embeddings = self._extract_face_embeddings(
                    thread_conn, faces_needing_embeddings
                )

            self._tag_worker_stop.wait(interval)

    def _tag_missing_pictures(self, thread_conn, picture_tagger, missing_tags):
        """Tag all pictures missing tags."""
        assert missing_tags is not None
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
            logger.info(f"Tagging {len(image_paths)} images")
            tag_results = picture_tagger.tag_images(image_paths)
            logger.info(f"Got tag results for {len(tag_results)} images.")
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                logger.info(f"Processing tags for image at path: {path}")
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

    def _find_faces_needing_embeddings(self, thread_conn):
        """Find picture IDs that need face embeddings."""
        if not hasattr(self, "_skip_pictures"):
            self._skip_pictures = set()

        cursor = thread_conn.cursor()
        cursor.execute(
            "SELECT id FROM pictures WHERE face_embedding IS NULL OR face_embedding = ''"
        )
        pic_ids = [row[0] for row in cursor.fetchall()]
        batch = [pic_id for pic_id in pic_ids if pic_id not in self._skip_pictures][
            :MAX_CONCURRENT_IMAGES
        ]
        return batch

    def _extract_face_embeddings(self, thread_conn, faces_needing_embeddings):
        """Extract face embeddings for pictures that have no face_embedding, using the master iteration image."""

        if not faces_needing_embeddings:
            if self._last_time_insightface_was_needed is not None:
                elapsed = time.time() - self._last_time_insightface_was_needed
                if elapsed > Pictures.INSIGHTFACE_CLEANUP_TIMEOUT:
                    if hasattr(self, "_insightface_app"):
                        del self._insightface_app
                        gc.collect()
                        logger.info("Unloaded InsightFace app due to inactivity.")
                    self._last_time_insightface_was_needed = None
            return True  # Keep going even if if there's nothing to do

        logger.info(
            f"Have {len(faces_needing_embeddings)} pictures needing face embeddings."
        )
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            logger.error(
                "InsightFace is not installed. Skipping face embedding extraction."
            )
            return False  # Without InsightFace, we cannot proceed

        # Initialize InsightFace only once
        if not hasattr(self, "_insightface_app"):
            self._insightface_app = FaceAnalysis()
            self._insightface_app.prepare(ctx_id=0)

        self._last_time_insightface_was_needed = time.time()

        # Find pictures missing face_embedding
        for pic_id in faces_needing_embeddings:
            logger.info("Looking for faces in picture %s", pic_id)
            if self._tag_worker_stop.is_set():
                break

            self._skip_pictures.add(pic_id)
            # Find master iteration for this picture
            master_iters = []
            for it in self._picture_iterations.find(picture_id=pic_id, is_master=1):
                master_iters.append(it)
                if self._tag_worker_stop.is_set():
                    break

            if self._tag_worker_stop.is_set():
                return False

            if not master_iters:
                continue
            master_iter = master_iters[0]
            try:
                import os
                import cv2
                from pixelurgy_vault.picture_iteration import PictureIteration

                file_path = master_iter.file_path
                ext = os.path.splitext(file_path)[1].lower()
                faces = []
                if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
                    img = cv2.imread(file_path)
                    if img is not None:
                        faces = self._insightface_app.get(img)
                elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
                    cap = cv2.VideoCapture(file_path)
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if frame_count < 1:
                        logger.warning(f"No frames found in video: {file_path}")
                        cap.release()
                    else:
                        frame_indices = [0]
                        if frame_count > 2:
                            frame_indices.append(frame_count // 2)
                        if frame_count > 1:
                            frame_indices.append(frame_count - 1)
                        for idx in frame_indices:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                            ret, frame = cap.read()
                            if not ret or frame is None:
                                logger.warning(
                                    f"Could not read frame {idx} from video: {file_path}"
                                )
                                continue
                            frame_faces = self._insightface_app.get(frame)
                            if frame_faces:
                                faces.extend(frame_faces)
                        cap.release()
                else:
                    logger.warning(
                        f"Unsupported file extension for face embedding: {file_path}"
                    )
                if not faces:
                    logger.warning(
                        f"No face found in {file_path} for picture {pic_id}."
                    )
                    continue
                else:
                    logger.debug(
                        f"Found {len(faces)} face(s) in {file_path} for picture {pic_id}."
                    )

                # Always use the largest face (by area)
                def face_area(f):
                    x1, y1, x2, y2 = f.bbox
                    return max(0, x2 - x1) * max(0, y2 - y1)

                face = max(faces, key=face_area)
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
                # Regenerate thumbnails for all iterations using face_bbox
                try:
                    bbox = [float(v) for v in face.bbox]
                    iterations = list(self._picture_iterations.find(picture_id=pic_id))
                    for it in iterations:
                        cropped = PictureIteration.load_and_crop_face_bbox(
                            it.file_path, bbox
                        )
                        if cropped is not None:
                            thumb = PictureIteration._generate_thumbnail_bytes(cropped)
                            self._picture_iterations.update_iteration(
                                it.__class__(**{**it.__dict__, "thumbnail": thumb})
                            )
                except Exception as e:
                    logger.error(
                        f"Failed to regenerate face-aware thumbnails for picture {pic_id}: {e}"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to extract/store face embedding for picture {pic_id}: {e}"
                )
        logger.info("Done extracting face embeddings for current batch.")
        return True

    def _embed_tagged_pictures(self, thread_conn, picture_tagger, missing_embeddings):
        """Generate embeddings for pictures that have tags but no embedding, including character name, description, and original_prompt if present."""
        assert missing_embeddings is not None
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
                # Look up full Character object if available
                character_obj = None
                char_id = getattr(pic, "character_id", None)
                if char_id is not None and self._characters is not None:
                    try:
                        character_obj = self._characters[int(char_id)]
                    except Exception:
                        character_obj = None
                embedding, full_text = picture_tagger.generate_embedding(
                    picture=pic, character=character_obj
                )
                with thread_conn:
                    cursor = thread_conn.cursor()
                    cursor.execute(
                        "UPDATE pictures SET embedding = ?, description = ? WHERE id = ?",
                        (embedding.astype("float32").tobytes(), full_text, pic.id),
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
        Uses a fresh SQLite connection per call for thread safety.
        """
        import sqlite3

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            if not kwargs:
                cursor.execute("SELECT * FROM pictures")
            else:
                clauses = []
                values = []
                for k, v in kwargs.items():
                    if v == "" or v == "null":
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
                    is_reference=row["is_reference"]
                    if "is_reference" in row.keys()
                    else 0,
                    has_embedding=has_embedding,
                )
                result.append(pic)
            return result
        finally:
            conn.close()

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
        query_emb, _ = self._picture_tagger.generate_embedding(
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

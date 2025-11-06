from enum import Enum

import gc
import sqlite3
import numpy as np
import json
import time
import os
import cv2
import threading


from pixelurgy_vault.logging import get_logger

from pixelurgy_vault.picture import Picture
from pixelurgy_vault.picture_quality import PictureQuality
from pixelurgy_vault.picture_tagger import PictureTagger, MAX_CONCURRENT_IMAGES
from pixelurgy_vault.picture_utils import PictureUtils

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

    def __init__(self, connection, db_path, characters=None):
        self._connection = connection
        self._db_path = db_path
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None
        self._characters = characters  # Should be a Characters manager or None
        self._picture_tagger = PictureTagger("cpu")

        self._tag_worker = None
        self._tag_worker_stop = None

        self._quality_worker = None
        self._quality_worker_stop = None

    def _get_tags_for_picture(self, picture_id):
        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT tag FROM picture_tags WHERE picture_id = ?", (picture_id,)
        )
        return [row[0] for row in cursor.fetchall()]

    def _set_tags_for_picture(self, picture_id, tags):
        cursor = self._connection.cursor()
        cursor.execute("DELETE FROM picture_tags WHERE picture_id = ?", (picture_id,))
        cursor.executemany(
            "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
            [(picture_id, tag) for tag in tags],
        )
        self._connection.commit()

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
                    "SELECT * FROM pictures WHERE id = ?",
                    (picture_id,),
                )
                row = cursor.fetchone()
                conn.close()
                if not row:
                    raise KeyError(f"Picture with id {picture_id} not found.")
                pic = Picture.from_dict(row)
                # Fetch tags from picture_tags table
                pic.tags = self._get_tags_for_picture(picture_id)
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
        cursor.execute("SELECT * FROM pictures")
        for row in cursor.fetchall():
            yield Picture.from_dict(row)

    def update_picture_tags(self, picture_id, tags):
        """
        Update the tags for a picture in the database using the picture_tags table.
        """
        self._set_tags_for_picture(picture_id, tags)

    def start_embeddings_worker(self, interval=0.1):
        import threading

        if self._tag_worker and self._tag_worker.is_alive():
            return
        self._tag_worker_stop = threading.Event()
        self._tag_worker = threading.Thread(
            target=self._tag_embeddings_loop, args=(interval,), daemon=True
        )
        self._tag_worker.start()

    def stop_embeddings_worker(self):
        if self._tag_worker_stop:
            self._tag_worker_stop.set()
        if self._tag_worker:
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

        calculate_face_bboxes = True

        while not self._tag_worker_stop.is_set():
            missing_tags = [pic for pic in self.find() if not pic.tags]
            missing_embeddings = [
                pic
                for pic in self.find()
                if not getattr(pic, "embedding", False) and pic.tags
            ]

            if missing_tags:
                logger.info(f"Tagging {len(missing_tags)} pictures missing tags.")
                self._tag_pictures(thread_conn, self._picture_tagger, missing_tags)

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

            if calculate_face_bboxes:
                logger.debug(
                    "Generating face bounding boxes for pictures needing them."
                )
                pics_needing_face_bboxes = self._find_pics_needing_face_bbox(
                    thread_conn
                )
                calculate_face_bboxes = self._calculate_face_bbox(
                    thread_conn, pics_needing_face_bboxes
                )

            self._tag_worker_stop.wait(interval)

    def _quality_worker_loop(self, interval):
        retries = 5
        delay = 0.2
        for attempt in range(retries):
            try:
                thread_conn = sqlite3.connect(self._db_path, check_same_thread=False)
                thread_conn.row_factory = sqlite3.Row
                thread_conn.execute("PRAGMA journal_mode=WAL;")
                break
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < retries - 1:
                    time.sleep(delay)
                    continue
                else:
                    raise
        while not self._quality_worker_stop.is_set():
            try:
                cursor = thread_conn.cursor()
                cursor.execute(
                    "SELECT * FROM pictures WHERE quality IS NULL OR face_quality IS NULL"
                )
                rows = cursor.fetchall()
                logger.debug(
                    f"Quality worker found {len(rows)} pictures needing quality or face quality calculation."
                )
                for row in rows:
                    logger.debug(f"Doing row {row}")
                    if self._quality_worker_stop.is_set():
                        break
                    pic = Picture.from_dict(row)
                    logger.debug("Checked stop event for iteration")
                    logger.debug(
                        f"Opening file {pic.file_path} for quality/face quality calculation"
                    )
                    self._calculate_and_store_quality(thread_conn, pic)
            except Exception as e:
                logger.error(f"Quality worker error: {e}")
            self._quality_worker_stop.wait(interval)

    def _calculate_and_store_quality(self, thread_conn, pic):
        try:
            image_np = PictureUtils.load_image_or_video(pic.file_path)
            # Only calculate and update quality if it is NULL
            if pic.quality is None:
                pic.quality = PictureQuality.calculate_metrics(image_np)
                if pic.quality:
                    try:
                        quality_json = json.dumps(pic.quality.__dict__)
                    except Exception as e:
                        logger.error(f"Failed to serialize quality for {pic.id}: {e}")

                    cursor = thread_conn.cursor()
                    logger.debug(f"Updating quality for picture {pic.id} in DB")
                    cursor.execute(
                        "UPDATE pictures SET quality = ? WHERE id = ?",
                        (quality_json, pic.id),
                    )
                    thread_conn.commit()
                    logger.debug(f"Calculated and stored quality for picture {pic.id}")

            # Always attempt to calculate and update face_quality if it is NULL
            if pic.face_quality is None and pic.face_bbox is not None:
                pic.face_quality = PictureQuality.calculate_face_quality(
                    image_np, pic.face_bbox
                )
                face_quality_json = json.dumps(pic.face_quality.__dict__)
                if face_quality_json is not None:
                    cursor = thread_conn.cursor()
                    logger.debug(f"Updating face_quality for picture {pic.id} in DB")
                    cursor.execute(
                        "UPDATE pictures SET face_quality = ? WHERE id = ?",
                        (face_quality_json, pic.id),
                    )
                    thread_conn.commit()
        except Exception as e:
            logger.error(f"Failed to calculate/store quality for {pic.id}: {e}")

    def _tag_pictures(self, thread_conn, picture_tagger, missing_tags):
        """Tag all pictures missing tags."""
        assert missing_tags is not None
        batch = missing_tags[:MAX_CONCURRENT_IMAGES]
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            image_paths.append(pic.file_path)
            pic_by_path[pic.file_path] = pic

        if image_paths:
            logger.info(f"Tagging {len(image_paths)} images: {image_paths}")
            tag_results = picture_tagger.tag_images(image_paths)
            logger.info(f"Got tag results for {len(tag_results)} images.")
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                logger.info(f"Processing tags for image at path: {path}: {tags}")
                if pic is not None:
                    # Remove character tag from tags if present
                    char_tag = getattr(pic, "character_id", None)
                    if char_tag and char_tag in tags:
                        tags = [t for t in tags if t != char_tag]
                    pic.tags = tags
                    # Replace all tags in picture_tags table
                    cursor = thread_conn.cursor()
                    cursor.execute(
                        "DELETE FROM picture_tags WHERE picture_id = ?", (pic.id,)
                    )
                    if tags:
                        cursor.executemany(
                            "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                            [(pic.id, tag) for tag in tags],
                        )
                    thread_conn.commit()
        return True

    def _find_pics_needing_face_bbox(self, thread_conn):
        """Find pictures that need face bounding boxes."""
        if not hasattr(self, "_skip_pictures"):
            self._skip_pictures = set()

        cursor = thread_conn.cursor()
        cursor.execute(
            "SELECT * FROM pictures WHERE face_bbox IS NULL OR face_bbox = ''"
        )
        pics = [Picture.from_dict(row) for row in cursor.fetchall()]
        batch = [pic for pic in pics if pic.id not in self._skip_pictures][
            :MAX_CONCURRENT_IMAGES
        ]
        return batch

    def _calculate_face_bbox(self, thread_conn, pics):
        """Calculate face bounding box for pictures"""

        if not pics:
            if self._last_time_insightface_was_needed is not None:
                elapsed = time.time() - self._last_time_insightface_was_needed
                if elapsed > Pictures.INSIGHTFACE_CLEANUP_TIMEOUT:
                    if hasattr(self, "_insightface_app"):
                        del self._insightface_app
                        gc.collect()
                        logger.info("Unloaded InsightFace app due to inactivity.")
                    self._last_time_insightface_was_needed = None
            return True  # Keep going even if if there's nothing to do

        logger.info(f"Have {len(pics)} pictures needing face embeddings.")
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

        for pic in pics:
            logger.info("Looking for faces in picture %s", pic.id)

            # Skip it regardless of whether we succeed or fail
            self._skip_pictures.add(pic.id)

            if self._tag_worker_stop.is_set():
                return False

            try:
                file_path = pic.file_path
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
                        f"No face found in {file_path} for picture {pic.id}."
                    )
                    continue
                else:
                    logger.debug(
                        f"Found {len(faces)} face(s) in {file_path} for picture {pic.id}."
                    )

                # Always use the largest face (by area)
                def face_area(f):
                    x1, y1, x2, y2 = f.bbox
                    return max(0, x2 - x1) * max(0, y2 - y1)

                face = max(faces, key=face_area)
                bbox = [float(v) for v in face.bbox]
                pic.face_bbox = bbox

                logger.debug(f"Calculated largest face bbox for picture {pic.id}.")

                # Regenerate thumbnails using face_bbox
                try:
                    cropped = PictureUtils.load_and_crop_face_bbox(
                        pic.file_path, face.bbox
                    )
                    if cropped is not None:
                        thumb = PictureUtils.generate_thumbnail_bytes(cropped)
                        if thumb is not None:
                            pic.thumbnail = thumb

                except Exception as e:
                    logger.error(
                        f"Failed to regenerate face-aware thumbnails for picture {pic.id}: {e}"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to extract/store face bbox for picture {pic.id}: {e}"
                )
        logger.info("Done extracting face bboxes for current batch.")

        self._update_thumbnails_and_embeddings(thread_conn, pics)

        return True

    def _embed_tagged_pictures(self, thread_conn, picture_tagger, missing_embeddings):
        """Generate embeddings for pictures that have tags but no embedding, including character name, description, and original_prompt if present."""
        assert missing_embeddings is not None
        batch = missing_embeddings[:MAX_CONCURRENT_IMAGES]
        for pic in batch:
            try:
                logger.debug(
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
                pic.embedding = embedding
            except Exception as e:
                logger.error(
                    f"Failed to generate/store embedding for picture {pic.id}: {e}"
                )
        return True

    def _update_thumbnails_and_embeddings(self, thread_conn, pictures):
        """Update a list of Picture instances in the database using executemany for efficiency."""
        with thread_conn:
            cursor = thread_conn.cursor()
            values = []
            for picture in pictures:
                row = picture.to_dict()
                values.append(
                    (
                        row["thumbnail"],
                        row["embedding"],
                        row["face_bbox"],
                        picture.id,
                    )
                )
                # logger.info(f"Updating picture {picture.id} with face bbox and thumbnails: {row}")
            cursor.executemany(
                """
                UPDATE pictures SET thumbnail=?, embedding=?, face_bbox=? WHERE id=?
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
                pic = Picture.from_dict(row)
                query = "SELECT tag FROM picture_tags WHERE picture_id = ?"
                cursor.execute(query, (pic.id,))
                tags = cursor.fetchall()
                pic.tags = [tag[0] for tag in tags]
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
            pic = Picture.from_dict(row)
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

    def start_quality_worker(self, interval=1):
        if self._quality_worker and self._quality_worker.is_alive():
            return
        self._quality_worker_stop = threading.Event()
        self._quality_worker = threading.Thread(
            target=self._quality_worker_loop, args=(interval,), daemon=True
        )
        self._quality_worker.start()

    def stop_quality_worker(self):
        logger.debug("Stopping quality worker...")
        if self._quality_worker_stop:
            self._quality_worker_stop.set()
        if self._quality_worker:
            self._quality_worker.join(timeout=10)  # Wait for thread to exit
            if self._quality_worker.is_alive():
                logger.warning("Quality worker thread did not exit within timeout.")

from enum import Enum
from typing import Union, List

import gc
import numpy as np
import json
import sqlite3
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

    def __init__(self, db, characters=None):
        self._db = db
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None
        self._characters = characters  # Should be a Characters manager or None
        self._picture_tagger = PictureTagger("cpu")

        self._tag_worker = None
        self._tag_worker_stop = None

        self._quality_worker = None
        self._quality_worker_stop = None

    def _get_tags_for_picture(self, picture_id):
        rows = self._db._query(
            "SELECT tag FROM picture_tags WHERE picture_id = ?", (picture_id,)
        )
        return [row["tag"] if isinstance(row, dict) else row[0] for row in rows]

    def _set_tags_for_picture(self, picture_id, tags):
        self._db._execute(
            "DELETE FROM picture_tags WHERE picture_id = ?", (picture_id,), commit=True
        )
        if tags:
            self._db._executemany(
                "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                [(picture_id, tag) for tag in tags],
                commit=True,
            )

    def __getitem__(self, picture_id):
        logger.debug(f"Fetching picture with id={picture_id} (type={type(picture_id)})")
        rows = self._db._query("SELECT * FROM pictures WHERE id = ?", (picture_id,))
        if not rows:
            raise KeyError(f"Picture with id {picture_id} not found.")
        pic = Picture.from_dict(rows[0])
        pic.tags = self._get_tags_for_picture(picture_id)
        return pic

    def __setitem__(self, picture_id, picture):
        picture.id = picture_id
        self.import_picture(picture)

    def __delitem__(self, picture_id):
        self._db._execute(
            "DELETE FROM pictures WHERE id = ?", (picture_id,), commit=True
        )

    def __iter__(self):
        rows = self._db._query("SELECT * FROM pictures")
        for row in rows:
            yield Picture.from_dict(row)

    def update_picture_tags(self, picture_id, tags):
        """
        Update the tags for a picture in the database using the picture_tags table.
        """
        self._set_tags_for_picture(picture_id, tags)

    def start_embeddings_worker(self, interval=1):
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
        self._db._execute(
            "UPDATE pictures SET embedding = NULL WHERE id = ?",
            (picture_id,),
            commit=True,
        )

    def _tag_embeddings_loop(self, interval):
        # Create a new connection for this thread
        calculate_face_bboxes = True

        while not self._tag_worker_stop.is_set():
            try:
                with self._db.threaded_connection as thread_conn:
                    data_updated = False

                    missing_tags = [pic for pic in self.find() if not pic.tags]
                    missing_embeddings = [
                        pic
                        for pic in self.find()
                        if not getattr(pic, "embedding", False) and pic.tags
                    ]

                    if missing_tags:
                        logger.info(
                            f"Tagging {len(missing_tags)} pictures missing tags."
                        )
                        tagged_pictures = self._tag_pictures(
                            thread_conn, self._picture_tagger, missing_tags
                        )
                        data_updated |= tagged_pictures > 0

                    if self._tag_worker_stop.is_set():
                        break

                    if missing_embeddings:
                        logger.info(
                            f"Generating embeddings for {len(missing_embeddings)} pictures."
                        )
                        data_updated = (
                            self._embed_tagged_pictures(
                                thread_conn, self._picture_tagger, missing_embeddings
                            )
                            or data_updated
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
                        calculate_face_bboxes, bboxes_updated = (
                            self._calculate_face_bboxes(
                                thread_conn, pics_needing_face_bboxes
                            )
                        )
                        data_updated |= bboxes_updated

                    if not data_updated:
                        self._tag_worker_stop.wait(interval)
            except (sqlite3.OperationalError, OSError) as e:
                # Database file was deleted or connection lost during shutdown
                logger.debug(
                    f"Worker thread exiting due to DB error (likely shutdown): {e}"
                )
                break

    def _quality_worker_loop(self, interval):
        # Create a new connection for this thread
        while not self._quality_worker_stop.is_set():
            quality_updates = 0
            try:
                with self._db.threaded_connection as thread_conn:
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
                        quality_updates += 1
            except (sqlite3.OperationalError, OSError) as e:
                # Database file was deleted or connection lost during shutdown
                logger.debug(
                    f"Quality worker exiting due to DB error (likely shutdown): {e}"
                )
                break
            except Exception as e:
                logger.error(f"Quality worker error: {e}")

            if quality_updates == 0:
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

    def _tag_pictures(self, thread_conn, picture_tagger, missing_tags) -> int:
        """Tag all pictures missing tags."""
        assert missing_tags is not None
        batch = missing_tags[:MAX_CONCURRENT_IMAGES]
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            image_paths.append(pic.file_path)
            pic_by_path[pic.file_path] = pic

        tagged_pictures = 0
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
                    if tags:
                        pic.tags = tags
                        # Replace all tags in picture_tags table
                        cursor = thread_conn.cursor()
                        cursor.execute(
                            "DELETE FROM picture_tags WHERE picture_id = ?", (pic.id,)
                        )
                        cursor.executemany(
                            "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                            [(pic.id, tag) for tag in tags],
                        )
                        tagged_pictures += 1
                    thread_conn.commit()
        return tagged_pictures

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

    def _calculate_face_bboxes(self, thread_conn, pics) -> int:
        """Calculate face bounding box for pictures"""

        bboxes_updated = 0
        if not pics:
            if self._last_time_insightface_was_needed is not None:
                elapsed = time.time() - self._last_time_insightface_was_needed
                if elapsed > Pictures.INSIGHTFACE_CLEANUP_TIMEOUT:
                    if hasattr(self, "_insightface_app"):
                        del self._insightface_app
                        gc.collect()
                        logger.info("Unloaded InsightFace app due to inactivity.")
                    self._last_time_insightface_was_needed = None
            return True, bboxes_updated  # Keep going even if if there's nothing to do

        logger.info(f"Have {len(pics)} pictures needing face embeddings.")
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            logger.error(
                "InsightFace is not installed. Skipping face embedding extraction."
            )
            return False, bboxes_updated  # Without InsightFace, we cannot proceed

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
                return False, bboxes_updated

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
                bboxes_updated += 1

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

        return True, bboxes_updated

    def _embed_tagged_pictures(
        self, thread_conn, picture_tagger, missing_embeddings
    ) -> int:
        """Generate embeddings for pictures that have tags but no embedding, including character name, description, and original_prompt if present."""
        assert missing_embeddings is not None
        batch = missing_embeddings[:MAX_CONCURRENT_IMAGES]

        embedded_pictures = 0
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
                    embedded_pictures += 1
                pic.embedding = embedding
            except Exception as e:
                logger.error(
                    f"Failed to generate/store embedding for picture {pic.id}: {e}"
                )
        return embedded_pictures

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
            thread_conn.commit()

    def contains(self, picture):
        """
        Check if a Picture with the same id exists in the database.
        """
        rows = self._db._query("SELECT 1 FROM pictures WHERE id = ?", (picture.id,))
        return bool(rows)

    def find(self, **kwargs):
        """
        Find and return a list of Picture objects matching all provided attribute=value pairs.
        Example: pictures.find(character_id="hero")
        Special case: if a value is an empty string, search for IS NULL.
        Uses VaultDatabase for all DB access.
        """
        if not kwargs:
            rows = self._db._query("SELECT * FROM pictures")
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
            rows = self._db._query(query, tuple(values))
        result = []
        for row in rows:
            pic = Picture.from_dict(row)
            tag_rows = self._db._query(
                "SELECT tag FROM picture_tags WHERE picture_id = ?", (pic.id,)
            )
            pic.tags = [
                tag_row["tag"] if isinstance(tag_row, dict) else tag_row[0]
                for tag_row in tag_rows
            ]
            result.append(pic)
        return result

    def find_by_tag_or_description(self, query):
        """
        Find pictures where the query matches any tag or appears in the description (case-insensitive, partial match).
        """
        q = f"%{query.lower()}%"
        rows = self._db._query(
            "SELECT * FROM pictures WHERE LOWER(description) LIKE ? OR LOWER(tags) LIKE ?",
            (q, q),
        )
        return [Picture.from_dict(row) for row in rows]

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
        rows = self._db._query(
            "SELECT id, embedding FROM pictures WHERE embedding IS NOT NULL"
        )
        logger.debug(
            f"Semantic search: found {len(rows)} candidate images with embeddings."
        )
        if not rows:
            return []
        # Compute similarities

        sims = []
        for row in rows:
            pic_id = row["id"] if isinstance(row, dict) else row[0]
            emb_blob = row["embedding"] if isinstance(row, dict) else row[1]
            if emb_blob is None:
                continue

            # Embedding is stored as base64 string in DB (from to_dict())
            # Decode it to bytes for numpy
            try:
                import base64

                if isinstance(emb_blob, str):
                    emb_bytes = base64.b64decode(emb_blob)
                else:
                    # Already bytes (shouldn't happen with consistent to_dict usage)
                    emb_bytes = emb_blob

                emb = np.frombuffer(emb_bytes, dtype=np.float32)
            except Exception as e:
                logger.error(f"Failed to parse embedding for {pic_id}: {e}")
                continue
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

    def delete(self, picture_ids: Union[str, List[str]]):
        """Delete one or more pictures. Supports both single ID and batch operations."""
        if not isinstance(picture_ids, list):
            picture_ids = [picture_ids]

        self._db._executemany(
            "DELETE FROM pictures WHERE id = ?",
            [(pid,) for pid in picture_ids],
            commit=True,
        )

    def add(self, pictures: Union[Picture, List[Picture]]):
        """Add one or more pictures. Supports both single picture and batch operations."""
        if not isinstance(pictures, list):
            pictures = [pictures]

        for picture in pictures:
            d = picture.to_dict()
            d.pop("tags", None)
            columns = ", ".join(d.keys())
            placeholders = ", ".join([f":{k}" for k in d.keys()])
            sql = f"INSERT INTO pictures ({columns}) VALUES ({placeholders})"
            self._db._execute(sql, d, commit=True)
            # Insert tags into picture_tags table
            if hasattr(picture, "tags") and picture.tags:
                self._db._executemany(
                    "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                    [(picture.id, tag) for tag in picture.tags],
                    commit=True,
                )

    def update(self, pictures: Union[Picture, List[Picture]]):
        """Update one or more pictures. Supports both single picture and batch operations."""
        if not isinstance(pictures, list):
            pictures = [pictures]

        for picture in pictures:
            d = picture.to_dict()
            d.pop("tags", None)
            set_clause = ", ".join([f"{k}=:{k}" for k in d.keys()])
            sql = f"UPDATE pictures SET {set_clause} WHERE id = :id"
            self._db._execute(sql, d, commit=True)
            # Update tags in picture_tags table
            if hasattr(picture, "tags") and picture.tags is not None:
                self._db._execute(
                    "DELETE FROM picture_tags WHERE picture_id = ?",
                    (picture.id,),
                    commit=True,
                )
                self._db._executemany(
                    "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                    [(picture.id, tag) for tag in picture.tags],
                    commit=True,
                )

    def fetch_by_shas(self, shas: list[str]) -> list[Picture]:
        if not shas:
            return []
        placeholders = ",".join(["?"] * len(shas))
        sql = f"SELECT * FROM pictures WHERE pixel_sha IN ({placeholders})"
        rows = self._db._query(sql, tuple(shas))

        result = []
        for row in rows:
            pic = Picture.from_dict(row)
            tag_rows = self._db._query(
                "SELECT tag FROM picture_tags WHERE picture_id = ?", (pic.id,)
            )
            pic.tags = [
                tag_row["tag"] if isinstance(tag_row, dict) else tag_row[0]
                for tag_row in tag_rows
            ]
            result.append(pic)
        return result

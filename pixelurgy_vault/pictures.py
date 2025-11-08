import gc
import numpy as np
import json
import sqlite3
import time
import os
import cv2
import threading

from enum import Enum
from typing import Union, List, Tuple

from pixelurgy_vault.logging import get_logger
from pixelurgy_vault.picture import PictureModel
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
    SHARPNESS_DESC = "sharpness_desc"
    SHARPNESS_ASC = "sharpness_asc"
    EDGE_DENSITY_DESC = "edge_density_desc"
    EDGE_DENSITY_ASC = "edge_density_asc"
    NOISE_LEVEL_DESC = "noise_level_desc"
    NOISE_LEVEL_ASC = "noise_level_asc"
    HAS_DESCRIPTION = "has_description"
    NO_DESCRIPTION = "no_description"


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
            (SortMechanism.SHARPNESS_DESC, "Sharpness (highest first)"),
            (SortMechanism.SHARPNESS_ASC, "Sharpness (lowest first)"),
            (SortMechanism.EDGE_DENSITY_DESC, "Edge Density (highest first)"),
            (SortMechanism.EDGE_DENSITY_ASC, "Edge Density (lowest first)"),
            (SortMechanism.NOISE_LEVEL_DESC, "Noise Level (highest first)"),
            (SortMechanism.NOISE_LEVEL_ASC, "Noise Level (lowest first)"),
            (SortMechanism.HAS_DESCRIPTION, "Has Description"),
            (SortMechanism.NO_DESCRIPTION, "No Description"),
        ]
    ]


class Pictures:
    INSIGHTFACE_CLEANUP_TIMEOUT = 20  # seconds

    def __init__(self, db, characters=None):
        self._db = db
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None
        self._characters = characters  # Should be a Characters manager or None
        # Let PictureTagger auto-detect device (will use GPU if available, CPU otherwise)
        self._picture_tagger = PictureTagger()

        # Enable Florence-2 for natural language descriptions
        logger.info(
            "Enabling Florence-2 captioning for natural language descriptions..."
        )
        self._picture_tagger.enable_florence_captioning()

        self._tag_worker = None
        self._tag_worker_stop = None

        self._quality_worker = None
        self._quality_worker_stop = None

    def _get_tags_for_picture(self, picture_id):
        rows = self._db.query(
            "SELECT tag FROM picture_tags WHERE picture_id = ?", (picture_id,)
        )
        return [row["tag"] if isinstance(row, dict) else row[0] for row in rows]

    def _set_tags_for_picture(self, picture_id, tags):
        self._db.execute(
            "DELETE FROM picture_tags WHERE picture_id = ?", (picture_id,), commit=True
        )
        if tags:
            self._db.executemany(
                "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                [(picture_id, tag) for tag in tags],
                commit=True,
            )

    def __getitem__(self, picture_id):
        logger.debug(f"Fetching picture with id={picture_id} (type={type(picture_id)})")
        rows = self._db.query("SELECT * FROM pictures WHERE id = ?", (picture_id,))
        if not rows:
            raise KeyError(f"Picture with id {picture_id} not found.")
        pic = PictureModel.from_dict(rows[0])
        pic.tags = self._get_tags_for_picture(picture_id)
        return pic

    def __setitem__(self, picture_id, picture):
        picture.id = picture_id
        self.import_picture(picture)

    def __delitem__(self, picture_id):
        self._db.execute(
            "DELETE FROM pictures WHERE id = ?", (picture_id,), commit=True
        )

    def __iter__(self):
        rows = self._db.query("SELECT * FROM pictures")
        for row in rows:
            yield PictureModel.from_dict(row)

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

    def _tag_embeddings_loop(self, interval):
        # Create a new connection for this thread
        calculate_face_bboxes = True

        while not self._tag_worker_stop.is_set():
            try:
                data_updated = False

                missing_tags = []
                missing_embeddings = []
                with self._db.threaded_connection as thread_conn:
                    missing_tags, missing_embeddings = (
                        self._fetch_missing_tags_and_embeddings(thread_conn)
                    )

                if self._tag_worker_stop.is_set():
                    break

                if missing_tags:
                    logger.debug(f"Tagging {len(missing_tags)} pictures missing tags.")
                    tagged_pictures = 0
                    tagged_pictures = self._tag_pictures(
                        self._picture_tagger, missing_tags
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
                            self._picture_tagger, missing_embeddings
                        )
                        or data_updated
                    )

                if self._tag_worker_stop.is_set():
                    break

                if calculate_face_bboxes:
                    logger.debug(
                        "Generating face bounding boxes for pictures needing them."
                    )
                    pics_needing_face_bboxes = self._find_pics_needing_face_bbox()
                    calculate_face_bboxes, bboxes_updated = self._calculate_face_bboxes(
                        pics_needing_face_bboxes
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

    def _fetch_missing_tags_and_embeddings(self, thread_conn):
        """Return PictureModels needing tags and embeddings using the provided connection."""
        cursor = thread_conn.cursor()

        cursor.execute(
            """
            SELECT p.*
            FROM pictures p
            LEFT JOIN picture_tags pt ON pt.picture_id = p.id
            GROUP BY p.id
            HAVING COUNT(pt.tag) = 0
            """
        )
        rows_missing_tags = cursor.fetchall()
        missing_tags = []
        if rows_missing_tags:
            empty_tags = [[] for _ in rows_missing_tags]
            missing_tags = self.from_batch_of_db_dicts(rows_missing_tags, empty_tags)

        cursor.execute(
            """
            SELECT p.*
            FROM pictures p
            WHERE p.embedding IS NULL
              AND EXISTS (
                  SELECT 1 FROM picture_tags pt WHERE pt.picture_id = p.id
              )
            """
        )
        rows_missing_embeddings = cursor.fetchall()
        missing_embeddings = []
        if rows_missing_embeddings:
            pic_ids = [row["id"] for row in rows_missing_embeddings]
            placeholders = ",".join(["?"] * len(pic_ids))
            cursor.execute(
                f"SELECT picture_id, tag FROM picture_tags WHERE picture_id IN ({placeholders})",
                pic_ids,
            )
            tag_rows = cursor.fetchall()
            tag_map = {pid: [] for pid in pic_ids}
            for tag_row in tag_rows:
                tag_map[tag_row["picture_id"]].append({"tag": tag_row["tag"]})

            tag_dicts = [tag_map.get(row["id"], []) for row in rows_missing_embeddings]
            missing_embeddings = self.from_batch_of_db_dicts(
                rows_missing_embeddings, tag_dicts
            )

        return missing_tags, missing_embeddings

    def _quality_worker_loop(self, interval):
        # Create a new connection for this thread
        while not self._quality_worker_stop.is_set():
            quality_updates = 0
            try:
                pics = []
                with self._db.threaded_connection as thread_conn:
                    cursor = thread_conn.cursor()
                    cursor.execute(
                        "SELECT * FROM pictures WHERE quality IS NULL OR face_quality IS NULL"
                    )
                    rows = cursor.fetchall()
                    logger.debug(
                        f"Quality worker found {len(rows)} pictures needing quality or face quality calculation."
                    )
                    pics = self.from_batch_of_db_dicts(rows, [])

                for pic in pics:
                    logger.debug(f"Doing picture {pic.id}")
                    if self._quality_worker_stop.is_set():
                        break
                    logger.debug("Checked stop event for iteration")
                    logger.debug(
                        f"Opening file {pic.file_path} for quality/face quality calculation"
                    )
                    self._calculate_quality(pic)
                    quality_updates += 1
                if pics:
                    with self._db.threaded_connection as thread_conn:
                        self._update_quality(thread_conn, pics)
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

    def _calculate_quality(self, pic):
        try:
            image_np = PictureUtils.load_image_or_video(pic.file_path)
            # Only calculate and update quality if it is NULL
            if pic.quality is None:
                pic.quality = PictureQuality.calculate_metrics(image_np)

            # Always attempt to calculate and update face_quality if it is NULL
            if pic.face_quality is None and pic.face_bbox is not None:
                pic.face_quality = PictureQuality.calculate_face_quality(
                    image_np, pic.face_bbox
                )
        except Exception as e:
            logger.error(f"Failed to calculate quality for {pic.id}: {e}")

    def _update_quality(self, thread_conn, pics):
        cursor = thread_conn.cursor()
        values = []
        for pic in pics:
            quality_json = json.dumps(pic.quality) if pic.quality is not None else None
            face_quality_json = (
                json.dumps(pic.face_quality) if pic.face_quality is not None else None
            )
            values.append((quality_json, face_quality_json, pic.id))

        cursor.executemany(
            "UPDATE pictures SET quality = ?, face_quality = ? WHERE id = ?",
            values,
        )
        thread_conn.commit()

    def _tag_pictures(self, picture_tagger, missing_tags) -> int:
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
                        with self._db.threaded_connection as thread_conn:
                            cursor = thread_conn.cursor()
                            cursor.execute(
                                "DELETE FROM picture_tags WHERE picture_id = ?",
                                (pic.id,),
                            )
                            cursor.executemany(
                                "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                                [(pic.id, tag) for tag in tags],
                            )
                            thread_conn.commit()
                        tagged_pictures += 1

        return tagged_pictures

    def _find_pics_needing_face_bbox(self):
        """Find pictures that need face bounding boxes."""
        if not hasattr(self, "_skip_pictures"):
            self._skip_pictures = set()

        pics = []
        with self._db.threaded_connection as thread_conn:
            cursor = thread_conn.cursor()
            cursor.execute(
                "SELECT * FROM pictures WHERE face_bbox IS NULL OR face_bbox = ''"
            )
            pics = [PictureModel.from_dict(row) for row in cursor.fetchall()]
        batch = [pic for pic in pics if pic.id not in self._skip_pictures][
            :MAX_CONCURRENT_IMAGES
        ]
        return batch

    def _calculate_face_bboxes(self, pics) -> int:
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
            logger.info("Initializing InsightFace with CPU only (ctx_id=-1)")
            self._insightface_app = FaceAnalysis()
            self._insightface_app.prepare(ctx_id=-1)  # -1 = CPU only

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

        with self._db.threaded_connection as thread_conn:
            self._update_thumbnails_and_embeddings(thread_conn, pics)

        return True, bboxes_updated

    def _embed_tagged_pictures(self, picture_tagger, missing_embeddings) -> int:
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
                logger.info(
                    f"Generating embedding for picture {pic.id} with character {char_id} and character name {getattr(character_obj, 'name', None)}"
                )
                embedding, full_text = picture_tagger.generate_embedding(
                    picture=pic, character=character_obj
                )
                # Use to_dict to ensure base64 encoding
                pic.embedding = embedding
                row = pic.to_dict()
                with self._db.threaded_connection as thread_conn:
                    cursor = thread_conn.cursor()
                    logger.info(
                        f"Updating database with description {full_text} for picture {pic.id}"
                    )
                    cursor.execute(
                        "UPDATE pictures SET embedding = ?, description = ? WHERE id = ?",
                        (row["embedding"], full_text, pic.id),
                    )
                    thread_conn.commit()
                    embedded_pictures += 1
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

    def find(self, **kwargs):
        """
        Find and return a list of Picture objects matching all provided attribute=value pairs.
        Example: pictures.find(character_id="hero")
        Special case: if a value is an empty string, search for IS NULL.
        Uses VaultDatabase for all DB access.
        """
        if not kwargs:
            rows = self._db.query("SELECT * FROM pictures")
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
            rows = self._db.query(query, tuple(values))
        result = []
        for row in rows:
            pic = PictureModel.from_dict(row)
            tag_rows = self._db.query(
                "SELECT tag FROM picture_tags WHERE picture_id = ?", (pic.id,)
            )
            pic.tags = [
                tag_row["tag"] if isinstance(tag_row, dict) else tag_row[0]
                for tag_row in tag_rows
            ]
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
        rows = self._db.query(
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

        self._db.executemany(
            "DELETE FROM pictures WHERE id = ?",
            [(pid,) for pid in picture_ids],
            commit=False,
        )
        self._db.executemany(
            "DELETE FROM picture_tags WHERE picture_id = ?",
            [(pid,) for pid in picture_ids],
            commit=True,
        )

    def add(self, pictures: Union[PictureModel, List[PictureModel]]):
        """Add one or more pictures. Supports both single picture and batch operations."""
        if not isinstance(pictures, list):
            pictures = [pictures]

        # Batch insert
        picture_dicts, list_of_tag_dicts = self.to_batch_of_db_dicts(pictures)
        if picture_dicts:
            columns = ", ".join(picture_dicts[0].keys())
            placeholders = ", ".join([f":{k}" for k in picture_dicts[0].keys()])
            sql = f"INSERT INTO pictures ({columns}) VALUES ({placeholders})"
            self._db.executemany(sql, picture_dicts, commit=True)
        # Flatten tag dicts for batch insert
        all_tag_dicts = [
            tag_dict for tag_dicts in list_of_tag_dicts for tag_dict in tag_dicts
        ]
        if all_tag_dicts:
            self._db.executemany(
                "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                [
                    (tag_dict["picture_id"], tag_dict["tag"])
                    for tag_dict in all_tag_dicts
                ],
                commit=True,
            )

    def update(self, pictures: Union[PictureModel, List[PictureModel]]):
        """Update one or more pictures. Supports both single picture and batch operations."""
        if not isinstance(pictures, list):
            pictures = [pictures]

        picture_dicts, list_of_tag_dicts = self.to_batch_of_db_dicts(pictures)
        for pic_dict, tag_dicts in zip(picture_dicts, list_of_tag_dicts):
            set_clause = ", ".join([f"{k}=:{k}" for k in pic_dict.keys()])
            sql = f"UPDATE pictures SET {set_clause} WHERE id = :id"
            self._db.execute(sql, pic_dict, commit=False)

            # Update tags in picture_tags table
            if tag_dicts:
                self._db.execute(
                    "DELETE FROM picture_tags WHERE picture_id = ?",
                    (pic_dict["id"],),
                    commit=False,
                )
                self._db.executemany(
                    "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                    [
                        (tag_dict["picture_id"], tag_dict["tag"])
                        for tag_dict in tag_dicts
                    ],
                    commit=False,
                )
        self._db.commit()

    def fetch_by_shas(self, shas: list[str]) -> list[PictureModel]:
        if not shas:
            return []
        placeholders = ",".join(["?"] * len(shas))
        sql = f"SELECT * FROM pictures WHERE pixel_sha IN ({placeholders})"
        pic_dicts = self._db.query(sql, tuple(shas))

        if not pic_dicts:
            return []

        # Collect all picture IDs
        pic_ids = [pic_row["id"] for pic_row in pic_dicts]
        tag_dicts_map = {pid: [] for pid in pic_ids}
        if pic_ids:
            tag_placeholders = ",".join(["?"] * len(pic_ids))
            tag_sql = f"SELECT picture_id, tag FROM picture_tags WHERE picture_id IN ({tag_placeholders})"
            tag_rows = self._db.query(tag_sql, tuple(pic_ids))
            for row in tag_rows:
                tag_dicts_map[row["picture_id"]].append({"tag": row["tag"]})

        # Prepare tag_dicts as a list of lists of tag dicts, in the same order as pic_dicts
        tag_dicts = [tag_dicts_map.get(pic_row["id"], []) for pic_row in pic_dicts]
        pic_models = Pictures.from_batch_of_db_dicts(pic_dicts, tag_dicts)
        return pic_models

    @staticmethod
    def from_db_dicts(
        picture_dicts: Union[dict, list[dict]],
        tag_dicts: Union[list[dict], list[list[dict]]],
    ):
        """
        Convert dicts from DB rows to a PictureModel object
        """
        pic = PictureModel.from_dict(picture_dicts)
        pic.tags = [tag_dict["tag"] for tag_dict in tag_dicts]

        return pic

    @staticmethod
    def from_batch_of_db_dicts(
        picture_dicts: Union[dict, list[dict]],
        tag_dicts: Union[list[dict], list[list[dict]]],
    ):
        """
        Convert list of dicts from DB rows to a list of PictureModel objects
        """
        return [Pictures.from_db_dicts(p, t) for p, t in zip(picture_dicts, tag_dicts)]

    @staticmethod
    def to_db_dicts(pic: PictureModel) -> Tuple[dict, list[dict]]:
        """
        Convert PictureModel to dicts suitable for DB insertion.
        Supports single model.
        Returns tuple of (picture_dict, list[tag_dict]).
        """
        pic = pic
        tags = pic.tags if hasattr(pic, "tags") and pic.tags is not None else []
        tag_dicts = [{"picture_id": pic.id, "tag": tag} for tag in tags]
        picture_dict = {}
        for key, value in pic.to_dict().items():
            if key == "tags":
                continue
            picture_dict[key] = value
        return picture_dict, tag_dicts

    @staticmethod
    def to_batch_of_db_dicts(
        pics: list[PictureModel],
    ) -> Tuple[list[dict], list[list[dict]]]:
        """
        Convert PictureModels to dicts suitable for DB insertion.
        Supports a list of models.
        Returns tuple of (list[picture_dict], list[list[tag_dict]]).
        """
        if isinstance(pics, list):
            picture_dicts = []
            list_of_tag_dicts = []
            for pic in pics:
                pic_dict, tag_dicts = Pictures.to_db_dicts(pic)
                picture_dicts.append(pic_dict)
                list_of_tag_dicts.append(tag_dicts)
            return picture_dicts, list_of_tag_dicts

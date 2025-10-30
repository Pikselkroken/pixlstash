import json
import numpy as np
import sqlite3
import threading
import time

from PIL import Image
from typing import List

from pixelurgy_vault.logging import get_logger
from pixelurgy_vault.picture_iteration import PictureIteration
from pixelurgy_vault.picture_quality import PictureQuality

logger = get_logger(__name__)


class PictureIterations:
    def __contains__(self, iteration_id):
        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT 1 FROM picture_iterations WHERE id = ? LIMIT 1", (iteration_id,)
        )
        return cursor.fetchone() is not None

    def __init__(self, connection, db_path):
        self._connection = connection
        self._db_path = db_path

    def start_quality_worker(self, interval=1):
        if hasattr(self, "_quality_worker") and self._quality_worker.is_alive():
            return
        self._quality_worker_stop = threading.Event()
        self._quality_worker = threading.Thread(
            target=self._quality_worker_loop, args=(interval,), daemon=True
        )
        self._quality_worker.start()

    def stop_quality_worker(self):
        logger.debug("Stopping quality worker...")
        if hasattr(self, "_quality_worker_stop"):
            self._quality_worker_stop.set()
        if hasattr(self, "_quality_worker"):
            self._quality_worker.join(timeout=5)  # Wait for thread to exit
            if self._quality_worker.is_alive():
                logger.warning("Quality worker thread did not exit within timeout.")

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
                    "SELECT id, file_path, quality, face_quality FROM picture_iterations WHERE quality IS NULL OR face_quality IS NULL"
                )
                rows = cursor.fetchall()
                logger.debug(
                    f"Quality worker found {len(rows)} iterations needing quality or face quality calculation."
                )
                for row in rows:
                    logger.debug(f"Doing row {row}")
                    if self._quality_worker_stop.is_set():
                        break
                    it_id, file_path, quality_val, face_quality_val = row
                    logger.debug("Checked stop event for iteration")
                    logger.debug(
                        f"Opening file {file_path} for quality/face quality calculation"
                    )
                    image_np = self._load_image_for_quality(file_path)
                    if image_np is not None:
                        self._calculate_and_store_quality(
                            thread_conn, it_id, image_np, quality_val, face_quality_val
                        )
            except Exception as e:
                logger.error(f"Quality worker error: {e}")
            self._quality_worker_stop.wait(interval)

    def _load_image_for_quality(self, file_path):
        try:
            with Image.open(file_path) as img:
                return np.array(img.convert("RGB"))
        except Exception as e:
            logger.error(f"Failed to load image at {file_path} for quality worker: {e}")
            return None

    def _calculate_and_store_quality(
        self, thread_conn, it_id, image_np, quality_val=None, face_quality_val=None
    ):
        try:
            quality_json = None
            # Only calculate and update quality if it is NULL
            if quality_val is None:
                quality = PictureQuality.calculate_metrics(image_np)
                if quality:
                    try:
                        quality_json = json.dumps(quality.__dict__)
                    except Exception as e:
                        logger.error(f"Failed to serialize quality for {it_id}: {e}")
                        quality_json = None
                    cursor = thread_conn.cursor()
                    logger.debug(f"Updating quality for iteration {it_id} in DB")
                    cursor.execute(
                        "UPDATE picture_iterations SET quality = ? WHERE id = ?",
                        (quality_json, it_id),
                    )
                    thread_conn.commit()
                    logger.debug(f"Calculated and stored quality for iteration {it_id}")
            else:
                quality_json = quality_val

            # Always attempt to calculate and update face_quality if it is NULL
            face_quality_json = None
            if face_quality_val is None:
                cursor = thread_conn.cursor()
                cursor.execute(
                    "SELECT picture_id FROM picture_iterations WHERE id = ?", (it_id,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    picture_id = row[0]
                    cursor.execute(
                        "SELECT face_embedding, face_bbox FROM pictures WHERE id = ?",
                        (picture_id,),
                    )
                    pic_row = cursor.fetchone()
                    if pic_row and pic_row[0] and pic_row[1]:
                        try:
                            bbox = (
                                json.loads(pic_row[1])
                                if isinstance(pic_row[1], str)
                                else pic_row[1]
                            )
                            x1, y1, x2, y2 = [int(round(v)) for v in bbox]
                            h, w = image_np.shape[:2]
                            # Clamp bbox to image bounds
                            x1_clamped = max(0, min(w, x1))
                            x2_clamped = max(0, min(w, x2))
                            y1_clamped = max(0, min(h, y1))
                            y2_clamped = max(0, min(h, y2))
                            if x2_clamped > x1_clamped and y2_clamped > y1_clamped:
                                face_crop = image_np[
                                    y1_clamped:y2_clamped, x1_clamped:x2_clamped
                                ]
                                if face_crop.size == 0:
                                    logger.error(
                                        f"Face crop is empty after clamping for {it_id}, bbox: {bbox}, clamped: {(x1_clamped, y1_clamped, x2_clamped, y2_clamped)}"
                                    )
                                else:
                                    face_quality = PictureQuality.calculate_metrics(
                                        face_crop
                                    )
                                    face_quality_json = json.dumps(
                                        face_quality.__dict__
                                    )
                                    logger.debug(
                                        f"Calculated face quality for iteration {it_id}: {face_quality_json}"
                                    )
                            else:
                                logger.error(
                                    f"Invalid bbox after clamping for {it_id}: {bbox}, clamped: {(x1_clamped, y1_clamped, x2_clamped, y2_clamped)}"
                                )
                        except Exception as e:
                            logger.error(
                                f"Failed to calculate face quality for {it_id} using stored bbox: {e}"
                            )
                if face_quality_json is not None:
                    logger.debug(f"Updating face_quality for iteration {it_id} in DB")
                    cursor.execute(
                        "UPDATE picture_iterations SET face_quality = ? WHERE id = ?",
                        (face_quality_json, it_id),
                    )
                    thread_conn.commit()
        except Exception as e:
            logger.error(f"Failed to calculate/store quality for {it_id}: {e}")

    def __getitem__(self, iteration_id):
        cursor = self._connection.cursor()
        cursor.execute(
            """
            SELECT * FROM picture_iterations WHERE id = ?
            """,
            (iteration_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"PictureIteration with id {iteration_id} not found.")
        quality = None
        if row["quality"]:
            try:
                quality = PictureQuality(**json.loads(row["quality"]))
            except Exception:
                quality = None
        it = PictureIteration(
            id=row["id"],
            picture_id=row["picture_id"],
            file_path=row["file_path"],
            format=row["format"],
            width=row["width"],
            height=row["height"],
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
            is_master=row["is_master"],
            derived_from=row["derived_from"],
            transform_metadata=row["transform_metadata"],
            thumbnail=row["thumbnail"],
            quality=quality,
            score=row["score"],
            pixel_sha=row["pixel_sha"],
            character_id=row["character_id"] if "character_id" in row.keys() else None,
        )
        return it

    def __iter__(self):
        cursor = self._connection.cursor()
        cursor.execute("SELECT id FROM picture_iterations")
        for row in cursor.fetchall():
            yield row["id"]

    def import_iterations(self, iterations: List[PictureIteration]):
        cursor = self._connection.cursor()
        vals = []
        for it in iterations:
            logger.debug(
                f"Importing picture {it.id}: file path {getattr(it, 'file_path', None)}"
            )
            if hasattr(it, "file_path") and it.file_path:
                import os

                if os.path.exists(it.file_path):
                    logger.debug(f"File {it.file_path} exists at import time.")
                else:
                    logger.warning(
                        f"File {it.file_path} does NOT exist at import time."
                    )
            quality_json = None
            if it.quality:
                try:
                    quality_json = json.dumps(it.quality.__dict__)
                except Exception:
                    quality_json = None
            vals.append(
                (
                    it.id,
                    it.picture_id,
                    it.file_path,
                    it.format,
                    it.width,
                    it.height,
                    it.size_bytes,
                    it.created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    it.is_master,
                    it.derived_from,
                    it.transform_metadata,
                    it.thumbnail,
                    quality_json,
                    it.score,
                    it.pixel_sha if hasattr(it, "pixel_sha") else None,
                )
            )
        cursor.executemany(
            """
            INSERT OR REPLACE INTO picture_iterations (
                id, picture_id, file_path, format, width, height, size_bytes, created_at, is_master,
                derived_from, transform_metadata, thumbnail, quality, score, pixel_sha
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            vals,
        )
        self._connection.commit()

    def update_iterations(self, iterations):
        """Update a list of PictureIteration instances in the database using executemany for efficiency."""
        cursor = self._connection.cursor()
        values = []
        for it in iterations:
            values.append(
                (
                    getattr(it, "picture_id", None),
                    getattr(it, "character_id", None),
                    getattr(it, "file_path", None),
                    getattr(it, "format", None),
                    getattr(it, "width", None),
                    getattr(it, "height", None),
                    getattr(it, "size_bytes", None),
                    getattr(it, "created_at", None),
                    getattr(it, "is_master", 0),
                    getattr(it, "derived_from", None),
                    getattr(it, "transform_metadata", None),
                    getattr(it, "thumbnail", None),
                    getattr(it, "quality", None),
                    getattr(it, "face_quality", None),
                    getattr(it, "score", None),
                    getattr(it, "character_likeness", None),
                    getattr(it, "pixel_sha", None),
                    it.id,
                )
            )
        cursor.executemany(
            """
            UPDATE picture_iterations SET picture_id=?, character_id=?, file_path=?, format=?, width=?, height=?, size_bytes=?, created_at=?, is_master=?, derived_from=?, transform_metadata=?, thumbnail=?, quality=?, face_quality=?, score=?, character_likeness=?, pixel_sha=? WHERE id=?
            """,
            values,
        )
        self._connection.commit()

    def update_quality(self, iteration_id, quality):
        """
        Update only the quality field for a given iteration.
        """
        cursor = self._connection.cursor()
        quality_json = None
        if quality:
            try:
                quality_json = json.dumps(quality.__dict__)
            except Exception:
                quality_json = None
        cursor.execute(
            "UPDATE picture_iterations SET quality = ? WHERE id = ?",
            (quality_json, iteration_id),
        )
        self._connection.commit()

    def find(self, **kwargs):
        # Use named columns for safety
        cursor = self._connection.cursor()
        # Coerce is_master to int if present (avoid bool/numpy types)
        if "is_master" in kwargs:
            try:
                kwargs["is_master"] = int(kwargs["is_master"])
            except Exception as e:
                logger.error(
                    f"[PICTURE_ITERATIONS.FIND] Could not coerce is_master to int: {kwargs['is_master']} ({type(kwargs['is_master'])}): {e}"
                )
                raise
        if not kwargs:
            cursor.execute("SELECT * FROM picture_iterations")
        else:
            query = "SELECT * FROM picture_iterations WHERE " + " AND ".join(
                [f"{k}=?" for k in kwargs.keys()]
            )
            params = tuple(kwargs.values())
            # Debug: log query and params
            logger.debug(f"[PICTURE_ITERATIONS.FIND] SQL: {query} | PARAMS: {params}")
            # Check for parameter mismatch
            if query.count("?") != len(params):
                logger.error(
                    f"[PICTURE_ITERATIONS.FIND] Parameter count mismatch: {query.count('?')} placeholders, {len(params)} params"
                )
            try:
                cursor.execute(query, params)
            except Exception as e:
                logger.error(
                    f"[PICTURE_ITERATIONS.FIND] Exception: {e} | SQL: {query} | PARAMS: {params}"
                )
                raise
        rows = cursor.fetchall()
        result = []

        for row in rows:
            quality = None
            if row["quality"]:
                try:
                    qdata = json.loads(row["quality"])
                    if isinstance(qdata, dict):
                        quality = PictureQuality(**qdata)
                    else:
                        logger.warning(
                            f"Quality field for iteration {row['id']} is not a dict: {qdata}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to parse quality for iteration {row['id']}: {e}; value: {row['quality']}"
                    )
            it = PictureIteration(
                id=row["id"],
                picture_id=row["picture_id"],
                file_path=row["file_path"],
                format=row["format"],
                width=row["width"],
                height=row["height"],
                size_bytes=row["size_bytes"],
                created_at=row["created_at"],
                is_master=row["is_master"],
                derived_from=row["derived_from"],
                transform_metadata=row["transform_metadata"],
                thumbnail=row["thumbnail"],
                quality=quality,
                score=row["score"],
                pixel_sha=row["pixel_sha"],
                character_id=row["character_id"],
            )
            result.append(it)
        return result

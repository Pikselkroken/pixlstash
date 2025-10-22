import json
import threading
import time
from pixelurgy_vault.logging import get_logger
from pixelurgy_vault.picture_iteration import PictureIteration
from pixelurgy_vault.picture_quality import PictureQuality
from typing import List


class PictureIterations:
    def __init__(self, connection, db_path):
        self.connection = connection
        self.db_path = db_path

    def start_quality_worker(self, interval=1):
        if hasattr(self, "_quality_worker") and self._quality_worker.is_alive():
            return
        self._quality_worker_stop = threading.Event()
        self._quality_worker = threading.Thread(
            target=self._quality_worker_loop, args=(interval,), daemon=True
        )
        self._quality_worker.start()

    def stop_quality_worker(self):
        if hasattr(self, "_quality_worker_stop"):
            self._quality_worker_stop.set()
        if hasattr(self, "_quality_worker"):
            self._quality_worker.join(timeout=5)  # Wait for thread to exit

    def _quality_worker_loop(self, interval):
        import numpy as np
        from PIL import Image
        import sqlite3

        logger = get_logger(__name__)
        # Create a new connection for this thread
        thread_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        thread_conn.row_factory = sqlite3.Row
        while not self._quality_worker_stop.is_set():
            try:
                cursor = thread_conn.cursor()
                cursor.execute(
                    "SELECT id, file_path FROM picture_iterations WHERE quality IS NULL"
                )
                rows = cursor.fetchall()
                logger.debug(
                    f"Quality worker found {len(rows)} iterations needing quality calculation."
                )
                for row in rows:
                    logger.debug(f"Doing row {row}")
                    if self._quality_worker_stop.is_set():
                        break
                    logger.debug("Checked stop event for iteration")
                    it_id, file_path = row
                    try:
                        logger.debug(
                            f"Opening file {file_path} for quality calculation"
                        )
                        with Image.open(file_path) as img:
                            image_np = np.array(img.convert("RGB"))
                        logger.debug(f"Calculating quality for iteration {it_id}")
                        quality = PictureQuality.calculate_metrics(image_np)
                        # Update quality in DB using thread_conn
                        quality_json = None
                        if quality:
                            try:
                                quality_json = json.dumps(quality.__dict__)
                            except Exception:
                                quality_json = None
                        cursor.execute(
                            "UPDATE picture_iterations SET quality = ? WHERE id = ?",
                            (quality_json, it_id),
                        )
                        thread_conn.commit()
                        logger.debug(f"Calculated quality for iteration {it_id}")
                        logger.debug(f"Updated iteration {it_id} with new quality")
                    except Exception as e:
                        logger.error(f"Failed to calculate quality for {it_id}: {e}")
            except Exception as e:
                logger.error(f"Quality worker error: {e}")
            self._quality_worker_stop.wait(interval)

    def __getitem__(self, iteration_id):
        cursor = self.connection.cursor()
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
        cursor = self.connection.cursor()
        cursor.execute("SELECT id FROM picture_iterations")
        for row in cursor.fetchall():
            yield row["id"]

    def import_iterations(self, iterations: List[PictureIteration]):
        cursor = self.connection.cursor()
        vals = []
        for it in iterations:
            logger = get_logger(__name__)
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
        self.connection.commit()

    def update_quality(self, iteration_id, quality):
        """
        Update only the quality field for a given iteration.
        """
        cursor = self.connection.cursor()
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
        self.connection.commit()

    def find(self, **kwargs):
        # Use named columns for safety
        cursor = self.connection.cursor()
        if not kwargs:
            cursor.execute("SELECT * FROM picture_iterations")
        else:
            query = "SELECT * FROM picture_iterations WHERE " + " AND ".join(
                [f"{k}=?" for k in kwargs.keys()]
            )
            cursor.execute(query, tuple(kwargs.values()))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            quality = (
                PictureQuality(**json.loads(row["quality"])) if row["quality"] else None
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

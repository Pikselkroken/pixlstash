import gc
import numpy as np
import sqlite3
import time
import math
import os
import cv2
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed

from enum import Enum
from typing import Union, List, Tuple

from pixlvault.logging import get_logger
from pixlvault.picture import PictureModel
from pixlvault.picture_quality import PictureQuality
from pixlvault.picture_tagger import PictureTagger
from pixlvault.picture_utils import PictureUtils
from pixlvault.database import DBPriority

logger = get_logger(__name__)


# Enum for sorting mechanisms
class SortMechanism(str, Enum):
    # If value starts with "ORDER BY", it's SQL-sortable; underscores become spaces for SQL
    DATE_DESC = "ORDER BY created_at DESC"
    DATE_ASC = "ORDER BY created_at ASC"
    SCORE_DESC = "ORDER BY score DESC"
    SCORE_ASC = "ORDER BY score ASC"
    SEARCH_LIKENESS = "search_likeness"
    SHARPNESS_DESC = "ORDER BY sharpness DESC"
    SHARPNESS_ASC = "ORDER BY sharpness ASC"
    EDGE_DENSITY_DESC = "ORDER BY edge_density DESC"
    EDGE_DENSITY_ASC = "ORDER BY edge_density ASC"
    NOISE_LEVEL_DESC = "ORDER BY noise_level DESC"
    NOISE_LEVEL_ASC = "ORDER BY noise_level ASC"
    HAS_DESCRIPTION = "ORDER BY description IS NOT NULL DESC"
    NO_DESCRIPTION = "ORDER BY escription IS NULL DESC"
    FORMAT_ASC = "ORDER BY format ASC"
    FORMAT_DESC = "ORDER BY format DESC"

    @classmethod
    def is_sql_sortable(cls, sort):
        return sort and str(sort).startswith("ORDER BY")


class PictureWorker(str, Enum):
    FACIAL_FEATURES = "facial_features"
    TAGGER = "tagger"
    QUALITY = "quality"
    LIKENESS = "likeness"

    @staticmethod
    def all():
        return set(item for item in PictureWorker)


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
            (SortMechanism.FORMAT_ASC, "Format (A-Z)"),
            (SortMechanism.FORMAT_DESC, "Format (Z-A)"),
        ]
    ]


class Pictures:
    INSIGHTFACE_CLEANUP_TIMEOUT = 20  # seconds
    NUM_LIKENESS_THREADS = 4

    def __init__(self, db, characters=None, device=None):
        self._db = db
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None
        self._characters = characters  # Should be a Characters manager or None
        # Pass device to PictureTagger (default: None lets PictureTagger auto-detect)
        self._device = device
        self._picture_tagger = PictureTagger(device=device)
        logger.info(
            f"Initialized PictureTagger for Pictures manager with device={device!r}."
        )

        self._facial_features_worker = None
        self._facial_features_worker_stop = None

        self._tagger = None
        self._tagger_stop = None

        self._quality_worker = None
        self._quality_worker_stop = None

        self._likeness_worker = None
        self._likeness_worker_stop = None

    def __getitem__(self, picture_id):
        logger.debug(f"Fetching picture with id={picture_id} (type={type(picture_id)})")

        def get_pictures(conn, picture_id):
            picture_row = conn.execute(
                "SELECT * FROM pictures WHERE id = ?", picture_id
            ).fetchone()
            if not picture_row:
                raise KeyError(f"Picture with id {picture_id} not found.")
            pic = PictureModel.from_dict(picture_row)
            tags_rows = conn.execute(
                "SELECT tag FROM picture_tags WHERE picture_id = ?", picture_id
            )
            pic.tags = [tag_row["tag"] for tag_row in tags_rows]
            return pic

        return self._db.execute_read(get_pictures, (picture_id,))

    def __setitem__(self, picture_id, picture):
        picture.id = picture_id
        self.import_picture(picture)

    def __delitem__(self, picture_id):
        self._db.submit_write(
            lambda conn: conn.execute(
                "DELETE FROM picture_tags WHERE picture_id = ?", (picture_id,)
            ),
            priority=DBPriority.IMMEDIATE,
        ).result()

    def __iter__(self):
        def row_generator(conn):
            cursor = conn.execute("SELECT * FROM pictures")
            picture_rows = list(cursor)
            if not picture_rows:
                return
            # Fetch all tags for these pictures in one query
            pic_ids = [row["id"] for row in picture_rows]
            placeholders = ",".join(["?"] * len(pic_ids))
            tag_cursor = conn.execute(
                f"SELECT picture_id, tag FROM picture_tags WHERE picture_id IN ({placeholders})",
                pic_ids,
            )
            tag_map = {}
            for tag_row in tag_cursor:
                tag_map.setdefault(tag_row["picture_id"], []).append(tag_row["tag"])
            for row in picture_rows:
                pic = PictureModel.from_dict(row)
                pic.tags = tag_map.get(row["id"], [])
                yield pic

        yield from self._db.execute_read(row_generator)

    def start_worker(self, worker: PictureWorker, interval=5):
        if worker == PictureWorker.FACIAL_FEATURES:
            self._start_facial_features_worker(interval)
        elif worker == PictureWorker.TAGGER:
            self._start_tagger(interval)
        elif worker == PictureWorker.QUALITY:
            self._start_quality_worker(interval)
        elif worker == PictureWorker.LIKENESS:
            self._start_likeness_worker(interval=interval)
        else:
            raise ValueError(f"Unknown worker type: {worker}")

    def stop_worker(self, worker: PictureWorker):
        if worker == PictureWorker.FACIAL_FEATURES:
            self._stop_facial_features_worker()
        elif worker == PictureWorker.TAGGER:
            self._stop_tagger()
        elif worker == PictureWorker.QUALITY:
            self._stop_quality_worker()
        elif worker == PictureWorker.LIKENESS:
            self._stop_likeness_worker()
        else:
            raise ValueError(f"Unknown worker type: {worker}")

    def _start_facial_features_worker(self, interval=5):
        import threading

        if self._facial_features_worker and self._facial_features_worker.is_alive():
            return
        self._facial_features_worker_stop = threading.Event()
        self._facial_features_worker = threading.Thread(
            target=self._facial_features_loop, args=(interval,), daemon=True
        )
        self._facial_features_worker.start()

    def _stop_facial_features_worker(self):
        if self._facial_features_worker_stop:
            self._facial_features_worker_stop.set()
        if self._facial_features_worker:
            self._facial_features_worker.join(timeout=10)

    def _start_tagger(self, interval=5):
        import threading

        if self._tagger and self._tagger.is_alive():
            return
        self._tagger_stop = threading.Event()
        self._tagger = threading.Thread(
            target=self._tagger_loop, args=(interval,), daemon=True
        )
        self._tagger.start()

    def _stop_tagger(self):
        if self._tagger_stop:
            self._tagger_stop.set()
        if self._tagger:
            self._tagger.join(timeout=10)

    def _start_likeness_worker(self, batch_size=200000, interval=5):
        if (
            hasattr(self, "_likeness_worker")
            and self._likeness_worker
            and self._likeness_worker.is_alive()
        ):
            logger.info("Likeness worker already running.")
            return
        self._likeness_worker_stop = threading.Event()
        self._likeness_worker = threading.Thread(
            target=self._likeness_loop, args=(batch_size, interval), daemon=True
        )
        self._likeness_worker.start()

    def _stop_likeness_worker(self):
        if hasattr(self, "_likeness_worker_stop") and self._likeness_worker_stop:
            self._likeness_worker_stop.set()
        if hasattr(self, "_likeness_worker") and self._likeness_worker:
            self._likeness_worker.join(timeout=10)

    def _likeness_loop(self, batch_size, interval):
        while not self._likeness_worker_stop.is_set():
            data_updated = False
            likeness_score_count = 0
            start = time.time()
            logger.debug("[LIKENESS] Starting iteration...")
            pending_pairs = []

            self._db.submit_write(
                lambda conn: conn.execute(
                    """
                DELETE FROM likeness_work_queue
                WHERE EXISTS (
                    SELECT 1 FROM picture_likeness
                    WHERE picture_likeness.picture_id_a = likeness_work_queue.picture_id_a
                        AND picture_likeness.picture_id_b = likeness_work_queue.picture_id_b
                )
            """
                ),
                priority=DBPriority.LOW,
            ).result()
            time_after_cleanup = time.time()
            logger.debug(
                f"[LIKENESS] DELETING existing items from likeness_work_queue took {time_after_cleanup - start:.2f} seconds."
            )

            rows = self._db.execute_read(
                lambda conn: conn.execute(
                    "SELECT picture_id_a, picture_id_b FROM likeness_work_queue ORDER BY rowid LIMIT ?",
                    (batch_size,),
                ).fetchall()
            )

            total_pending = self._db.execute_read(
                lambda conn: conn.execute(
                    "SELECT COUNT(*) FROM likeness_work_queue"
                ).fetchone()
            )[0]
            logger.debug(
                f"[LIKENESS] Fetched {len(rows)} rows from likeness_work_queue out of {total_pending}."
            )
            # Batch fetch all required pictures
            all_ids = set()
            for row in rows:
                all_ids.add(row[0])
                all_ids.add(row[1])
            if all_ids:
                placeholders = ",".join(["?"] * len(all_ids))
                pic_rows = self._db.execute_read(
                    lambda conn: conn.execute(
                        f"SELECT * FROM pictures WHERE id IN ({placeholders})",
                        tuple(all_ids),
                    ).fetchall()
                )
                pic_dict = {
                    row["id"]
                    if isinstance(row, dict)
                    else row[0]: PictureModel.from_dict(row)
                    for row in pic_rows
                }
                for row in rows:
                    pic_a_id, pic_b_id = row[0], row[1]
                    pic_a = pic_dict.get(pic_a_id)
                    pic_b = pic_dict.get(pic_b_id)
                    if not pic_a or not pic_b:
                        continue
                    # Only process if both have facial features
                    if not pic_a.facial_features or not pic_b.facial_features:
                        continue
                    pending_pairs.append((pic_a_id, pic_b_id, pic_a, pic_b))

            logger.debug(
                "[LIKENESS] Got %d pending likeness pairs to process from work queue."
                % (len(pending_pairs))
            )
            if pending_pairs:
                batches = [
                    pending_pairs[i * batch_size : (i + 1) * batch_size]
                    for i in range(
                        min(
                            Pictures.NUM_LIKENESS_THREADS,
                            (len(pending_pairs) + batch_size - 1) // batch_size,
                        )
                    )
                ]

                def process_batch(batch):
                    pic_a_list = [item[2] for item in batch]
                    pic_b_list = [item[3] for item in batch]
                    features_a = [
                        np.frombuffer(pic.facial_features, dtype=np.float32)
                        for pic in pic_a_list
                    ]
                    features_b = [
                        np.frombuffer(pic.facial_features, dtype=np.float32)
                        for pic in pic_b_list
                    ]
                    likeness_values = PictureQuality.batch_likeness_scores(
                        features_a, features_b
                    )
                    likeness_scores = [
                        (item[0], item[1], float(likeness), "cosine")
                        for item, likeness in zip(batch, likeness_values)
                    ]
                    # Remove processed pairs from work queue
                    queue_pairs = [(item[0], item[1]) for item in batch]
                    return likeness_scores, queue_pairs

                # Run batches in thread pool and join
                processed_total = 0
                all_likeness_scores = []
                all_processed_pairs = []
                with ThreadPoolExecutor(max_workers=len(batches)) as executor:
                    futures = [
                        executor.submit(process_batch, batch)
                        for batch in batches
                        if batch
                    ]
                    for future in as_completed(futures):
                        batch_scores, processed_pairs = future.result()
                        all_likeness_scores.extend(batch_scores)
                        all_processed_pairs.extend(processed_pairs)
                        processed_total += len(batch_scores)

                logger.debug(
                    f"[LIKENESS] Processed {processed_total} likeness scores in this iteration."
                )
                # Bulk insert all likeness scores and remove processed pairs
                if all_likeness_scores:

                    def insert_likeness_scores(conn, all_likeness_scores):
                        cursor = conn.cursor()
                        cursor.executemany(
                            """
                            INSERT OR IGNORE INTO picture_likeness (
                                picture_id_a, picture_id_b, likeness, metric
                            ) VALUES (?, ?, ?, ?)
                            """,
                            all_likeness_scores,
                        )
                        # Remove processed pairs from work queue
                        cursor.executemany(
                            "DELETE FROM likeness_work_queue WHERE picture_id_a = ? AND picture_id_b = ?",
                            all_processed_pairs,
                        )
                        conn.commit()

                    self._db.submit_write(
                        insert_likeness_scores,
                        all_likeness_scores,
                        priority=DBPriority.LOW,
                    )
                    logger.debug(
                        f"[LIKENESS] Bulk inserted {len(all_likeness_scores)} likeness scores and removed {len(all_processed_pairs)} processed pairs from work queue."
                    )
                    likeness_score_count = len(all_likeness_scores)
                    data_updated = True

            timing = time.time() - start
            if timing > 0.5:
                logger.info(
                    "[LIKENESS] Calculated and updated %d likeness scores in %.2f seconds."
                    % (likeness_score_count, time.time() - start)
                )
            if not data_updated:
                self._likeness_worker_stop.wait(interval)
        logger.info("[LIKENESS] Likeness worker stopped.")

    def _facial_features_loop(self, interval):
        while not self._facial_features_worker_stop.is_set():
            try:
                data_updated = False

                start = time.time()
                # 1. Calculate face bboxes
                logger.debug("[FACIAL_FEATURES] Starting iteration...")
                pics_needing_face_bboxes = self._find_pics_needing_face_bbox()
                time_after_fetch = time.time()

                logger.debug(
                    "[FACIAL_FEATURES] It took %.2f seconds to fetch %d pictures needing face bboxes."
                    % (time_after_fetch - start, len(pics_needing_face_bboxes))
                )
                logger.debug(
                    f"Found {len(pics_needing_face_bboxes)} pictures needing face bboxes. Doing {self._picture_tagger.max_concurrent_images()} at a time."
                )
                insightface_ok, bboxes_updated = self._calculate_face_bboxes(
                    pics_needing_face_bboxes[
                        : self._picture_tagger.max_concurrent_images()
                    ]
                )
                time_after_calculate = time.time()
                logger.debug(
                    "[FACIAL_FEATURES] It took %.2f seconds to calculate face bboxes."
                    % (time_after_calculate - time_after_fetch)
                )
                if not insightface_ok:
                    logger.warning(
                        "InsightFace model not available, skipping facial feature generation."
                    )
                    break

                data_updated |= bboxes_updated

                if self._facial_features_worker_stop.is_set():
                    break

                # 2. Generate facial features for pictures missing them
                missing_facial_features = self._fetch_missing_facial_features()
                logger.debug(
                    "[FACIAL_FEATURES] It took %.2f seconds to fetch %d pictures needing facial features."
                    % (time.time() - time_after_calculate, len(missing_facial_features))
                )
                if missing_facial_features:
                    logger.debug(
                        f"Generating facial features for {len(missing_facial_features)} pictures."
                    )
                    features_updated = self._generate_facial_features(
                        self._picture_tagger, missing_facial_features
                    )
                    self._update_attributes(
                        missing_facial_features, ["facial_features"]
                    )
                    data_updated |= features_updated

                timing = time.time() - start

                if timing > 0.5:
                    logger.info("[FACIAL_FEATURES] Done after %.2f seconds." % timing)
                if not data_updated:
                    # Wait for the specified interval before checking again
                    self._facial_features_worker_stop.wait(interval)
            except (sqlite3.OperationalError, OSError) as e:
                # Database file was deleted or connection lost during shutdown
                logger.debug(
                    f"Worker thread exiting due to DB error (likely shutdown): {e}"
                )
                break

    def _tagger_loop(self, interval):
        # Create a new connection for this thread
        while not self._tagger_stop.is_set():
            try:
                start = time.time()
                logger.debug("[TEXT_EMBEDDING] Starting iteration...")

                data_updated = False

                # 1. Fetch missing descriptions
                missing_descriptions = self._fetch_missing_descriptions()
                logger.debug(
                    "Got %d pictures needing descriptions." % len(missing_descriptions)
                )

                if self._tagger_stop.is_set():
                    break

                logger.debug(
                    "[TEXT_EMBEDDING] It took %.2f seconds to fetch missing descriptions."
                    % (time.time() - start)
                )
                # 2. Generate descriptions
                descriptions_generated = self._generate_descriptions(
                    self._picture_tagger, missing_descriptions
                )

                logger.debug(
                    "Generated descriptions for %d pictures."
                    % len(descriptions_generated)
                )
                if self._tagger_stop.is_set():
                    break

                # 3. Store descriptions
                if descriptions_generated:
                    self._update_attributes(descriptions_generated, ["description"])
                    data_updated = True

                tag_start = time.time()
                # 4. Fetch missing tags
                missing_tags = self._fetch_pictures_missing_tags()

                logger.debug(
                    "[TEXT_EMBEDDING] It took %.2f seconds to fetch missing tags."
                    % (time.time() - tag_start)
                )
                # 5. Generate missing tags
                tagged_pictures = self._tag_pictures(self._picture_tagger, missing_tags)

                if self._tagger_stop.is_set():
                    break

                # 6. Store generated tags
                if tagged_pictures:
                    self._update_picture_tags(tagged_pictures)
                    data_updated = True

                embed_start = time.time()
                # 7. Fetch pictures to embed
                pictures_to_embed = self._fetch_missing_text_embeddings()

                logger.debug(
                    "[TEXT_EMBEDDING] It took %.2f seconds to fetch missing text embeddings."
                    % (time.time() - embed_start)
                )

                # 8. Generate text embeddings for fetched pictures from descriptions and tags
                embeddings_generated = self._generate_text_embeddings(pictures_to_embed)

                # 9. Store generated embeddings
                if embeddings_generated:
                    self._update_attributes(embeddings_generated, ["text_embedding"])
                    data_updated = True

                timing = time.time() - start
                if timing > 0.5:
                    logger.info("[TEXT_EMBEDDING] Done after %.2f seconds." % timing)
                if not data_updated:
                    self._tagger_stop.wait(interval)
            except (sqlite3.OperationalError, OSError) as e:
                # Database file was deleted or connection lost during shutdown
                logger.debug(
                    f"Worker thread exiting due to DB error (likely shutdown): {e}"
                )
                break
        logger.info("Exiting text embedding worker loop.")

    def _fetch_pictures_missing_tags(self):
        """Return PictureModels needing tags using the provided connection."""

        logger.debug("Starting the optimized database fetch for missing tags.")
        pictures_missing_tags = self._db.execute_read(
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
        )
        return self.from_batch_of_db_dicts(pictures_missing_tags)

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

        self._db.submit_write(bulk_update_tags, pictures, priority=DBPriority.LOW)

    def _fetch_missing_text_embeddings(self):
        """Return PictureModels needing text embeddings."""

        rows_missing_embeddings = self._db.execute_read(
            lambda conn: conn.execute(
                """
            SELECT *
            FROM pictures WHERE description IS NOT NULL AND text_embedding IS NULL
            """
            ).fetchall()
        )
        return self.from_batch_of_db_dicts(rows_missing_embeddings, [])

    def _fetch_missing_descriptions(self):
        logger.debug("Starting the database fetch for missing descriptions")

        rows_missing_descriptions = self._db.execute_read(
            lambda conn: conn.execute(
                """
            SELECT p.*
            FROM pictures p
            WHERE p.description IS NULL
            """
            ).fetchall()
        )

        return self.from_batch_of_db_dicts(rows_missing_descriptions, [])

    def _fetch_missing_facial_features(self):
        """Return PictureModels needing facial features using the provided connection."""
        rows_missing_facial_features = self._db.execute_read(
            lambda conn: conn.execute(
                """
            SELECT p.*
            FROM pictures p
            WHERE p.face_bbox IS NOT NULL
              AND p.face_bbox != ''
              AND p.facial_features IS NULL
            GROUP BY p.id
            """
            ).fetchall()
        )
        return self.from_batch_of_db_dicts(rows_missing_facial_features, [])

    def _quality_worker_loop(self, interval):
        # ...existing code...
        # Efficiently group pictures by size before batch quality calculation
        # (inside the main loop, after fetching pics)
        BATCH_SIZE = 8
        while not self._quality_worker_stop.is_set():
            start = time.time()
            logger.debug("[QUALITY] Starting iteration...")
            quality_updates = 0
            try:
                start_quality_fetch = time.time()
                # 1. Full image quality measures
                logger.debug(
                    "Searching for pictures needing full image quality calculation."
                )
                rows = self._db.execute_read(
                    lambda conn: conn.execute(
                        "SELECT * FROM pictures WHERE sharpness IS NULL OR edge_density IS NULL OR noise_level IS NULL OR contrast IS NULL OR brightness IS NULL",
                    ).fetchall()
                )

                pics_full = self.from_batch_of_db_dicts(rows)

                start_quality_grouping = time.time()
                logger.debug(
                    "[QUALITY] It took %.2f seconds to fetch pictures needing quality calculation."
                    % (start_quality_grouping - start_quality_fetch)
                )
                logger.debug(
                    f"Read metadata for {len(pics_full)} pictures needing full image quality."
                )
                grouped_full = self._group_pictures_by_size(pics_full, region="full")
                logger.debug(
                    f"Grouped {len(grouped_full)} full image batches by size. Will calculate quality now."
                )
                start_quality_calculation = time.time()
                logger.debug(
                    "[QUALITY] It took %.2f seconds to group pictures by size."
                    % (start_quality_calculation - start_quality_grouping)
                )

                for group in grouped_full.values():
                    batch = group[:BATCH_SIZE]
                    if batch:
                        size = PictureUtils.load_metadata(batch[0].file_path)
                        logger.debug(
                            f"Processing batch of {len(batch)} images of size {size} out of a total of {len(group)}."
                        )
                        quality_updates = self._calculate_quality(batch)
                        if quality_updates:
                            self._db.submit_write(
                                self._update_quality,
                                quality_updates,
                                priority=DBPriority.LOW,
                            ).result()
                start_face_quality_fetch = time.time()
                logger.debug(
                    "[QUALITY] It took %.2f seconds to calculate full image quality."
                    % (start_face_quality_fetch - start_quality_calculation)
                )
                # 2. Face quality measures
                logger.debug("Searching for pictures needing face quality calculation.")
                face_rows = self._db.execute_read(
                    lambda conn: conn.execute(
                        "SELECT * FROM pictures WHERE face_sharpness IS NULL OR face_edge_density IS NULL OR face_noise_level IS NULL OR face_contrast IS NULL OR face_brightness IS NULL",
                    ).fetchall()
                )
                pics_face = self.from_batch_of_db_dicts(face_rows)

                logger.debug(
                    "[QUALITY] It took %.2f seconds to fetch pictures needing face quality calculation."
                    % (time.time() - start_face_quality_fetch)
                )
                logger.debug(
                    f"Read metadata for {len(pics_face)} pictures needing face quality."
                )
                grouped_face = self._group_pictures_by_size(pics_face, region="face")
                logger.debug(
                    f"Grouped {len(grouped_face)} face crop batches by bbox size. Will calculate face quality now."
                )
                for group in grouped_face.values():
                    batch = group[:BATCH_SIZE]
                    if batch:
                        bbox_size = None
                        if (
                            batch[0].face_bbox is not None
                            and len(batch[0].face_bbox) == 4
                        ):
                            x1, y1, x2, y2 = batch[0].face_bbox
                            w = int(round((x2 - x1) / 64.0) * 64)
                            h = int(round((y2 - y1) / 64.0) * 64)
                            bbox_size = (h, w, 3)
                        logger.debug(
                            f"Processing face batch of {len(batch)} images of bbox size {bbox_size}."
                        )
                        quality_updates = self._calculate_quality(batch, True)
                        self._db.submit_write(
                            self._update_face_quality,
                            quality_updates,
                            priority=DBPriority.LOW,
                        ).result()

            except (sqlite3.OperationalError, OSError) as e:
                msg = str(e)
                if "no such column" in msg:
                    logger.error(
                        f"Quality worker exiting due to schema error (missing column): {e}"
                    )
                else:
                    logger.error(
                        f"Quality worker exiting due to DB error (likely shutdown): {e}"
                    )
                break
            except Exception as e:
                logger.error(f"Quality worker error: {e}")

            timing = time.time() - start
            if timing > 0.5:
                logger.info("[QUALITY] Done after %.2f seconds." % timing)
            if quality_updates == 0:
                self._quality_worker_stop.wait(interval)

    def _group_pictures_by_size(self, pics: List[PictureModel], region: str = "full"):
        """
        Group pictures by region size (full image or face bbox).
        region: "full" for full image, "face" for face bbox.
        Returns a dict: {key: [PictureModel, ...]}, where key is (h, w, c) for images, or (h, w) for face crops, or file_path for videos.
        """
        from collections import defaultdict
        import os

        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        sizes = []
        pic_groups = []
        video_groups = defaultdict(list)
        for pic in pics:
            ext = os.path.splitext(pic.file_path)[1].lower()
            if ext in video_exts:
                video_groups[pic.file_path].append(pic)
            else:
                try:
                    if region == "full":
                        size = PictureUtils.load_metadata(pic.file_path)
                    elif (
                        region == "face"
                        and pic.face_bbox is not None
                        and len(pic.face_bbox) == 4
                    ):
                        x1, y1, x2, y2 = pic.face_bbox
                        w = int(round((x2 - x1) / 64.0) * 64)
                        h = int(round((y2 - y1) / 64.0) * 64)
                        # Assume 3 channels for face crops
                        size = (h, w, 3)
                    else:
                        size = None
                    if size:
                        sizes.append(size)
                        pic_groups.append((size, pic))
                except Exception as e:
                    logger.error(
                        f"Failed to read metadata for grouping: {pic.file_path}, error: {e}"
                    )
        all_groups = dict(video_groups)
        if sizes:
            unique_sizes = set(sizes)
            logger.debug(
                f"Read metadata for {len(pic_groups)} images. Found {len(unique_sizes)} unique sizes."
            )
            if len(unique_sizes) == 1:
                batch_size = len(pic_groups)
                logger.debug(
                    f"All images same size: batching {batch_size} images together."
                )
                all_groups[list(unique_sizes)[0]] = [pic for _, pic in pic_groups]
            else:
                image_groups = defaultdict(list)
                for size, pic in pic_groups:
                    image_groups[size].append(pic)
                for size, group in image_groups.items():
                    if region == "face":
                        logger.debug(
                            f"Face batch for bbox size {size}: {len(group)} images."
                        )
                    else:
                        logger.debug(f"Batch for size {size}: {len(group)} images.")
                all_groups.update(image_groups)
        if video_groups:
            for vkey, vgroup in video_groups.items():
                logger.debug(f"Video batch for {vkey}: {len(vgroup)} video(s).")
        return all_groups

    def _calculate_quality(
        self, pics: List[PictureModel], is_face: bool = False
    ) -> List[PictureModel]:
        try:
            images = []
            if not is_face:
                # Full image quality
                for pic in pics:
                    images.append(PictureUtils.load_image_or_video(pic.file_path))
                batch_array = np.stack(images, axis=0)
                qualities = PictureQuality.calculate_quality_batch(batch_array)
                for pic, q in zip(pics, qualities):
                    logger.debug(
                        "[QUALITY] Picture id %s calculated sharpness: %s edge_density: %s contrast: %s brightness: %s noise_level: %s",
                        pic.id,
                        q.sharpness,
                        q.edge_density,
                        q.contrast,
                        q.brightness,
                        q.noise_level,
                    )
                    pic.sharpness = q.sharpness
                    pic.edge_density = q.edge_density
                    pic.contrast = q.contrast
                    pic.brightness = q.brightness
                    pic.noise_level = q.noise_level
            else:
                # Face crop quality
                h, w, _ = None, None, None
                if (
                    pics
                    and pics[0].face_bbox is not None
                    and len(pics[0].face_bbox) == 4
                ):
                    x1, y1, x2, y2 = pics[0].face_bbox
                    w = int(round((x2 - x1) / 64.0) * 64)
                    h = int(round((y2 - y1) / 64.0) * 64)
                valid_images = []
                valid_pics = []
                for pic in pics:
                    # Only check if bbox is present and well-formed
                    if pic.face_bbox is not None and len(pic.face_bbox) == 4:
                        x1, y1, x2, y2 = pic.face_bbox
                        # Check for zero area or malformed bbox
                        if x2 > x1 and y2 > y1:
                            img = PictureUtils.load_image_or_video(pic.file_path)
                            if img is not None:
                                crop = img[int(y1) : int(y2), int(x1) : int(x2)]
                                if (
                                    h is not None
                                    and w is not None
                                    and (crop.shape[0] != h or crop.shape[1] != w)
                                ):
                                    try:
                                        crop = cv2.resize(crop, (w, h))
                                    except Exception:
                                        # Silently drop this bbox and skip picture
                                        pic.face_bbox = None
                                        continue
                                if crop.size > 0:
                                    valid_images.append(crop)
                                    valid_pics.append(pic)
                                    continue
                        # If bbox is present but invalid, set metrics to -1.0
                        pic.face_sharpness = -1.0
                        pic.face_edge_density = -1.0
                        pic.face_noise_level = -1.0
                        pic.face_contrast = -1.0
                        pic.face_brightness = -1.0
                if valid_images:
                    batch_array = np.stack(valid_images, axis=0)
                    qualities = PictureQuality.calculate_quality_batch(batch_array)
                    for pic, fq in zip(valid_pics, qualities):
                        pic.face_sharpness = fq.sharpness
                        pic.face_edge_density = fq.edge_density
                        pic.face_contrast = fq.contrast
                        pic.face_brightness = fq.brightness
                        pic.face_noise_level = fq.noise_level
            return pics
        except Exception as e:
            logger.error(f"Failed to calculate quality for batch: {e}")

    def _update_quality(self, conn, pics):
        cursor = conn.cursor()
        values = []
        for pic in pics:
            # Assert metrics are not bytes
            for metric_name in [
                "sharpness",
                "edge_density",
                "contrast",
                "brightness",
                "noise_level",
            ]:
                metric_value = getattr(pic, metric_name)
                assert not isinstance(metric_value, bytes), (
                    f"Corrupt metric: {metric_name} for picture {pic.id} is bytes: {metric_value!r}"
                )
            logger.debug(
                "[UPDATE] Picture id %s sharpness: %s edge_density: %s contrast: %s brightness: %s noise_level: %s",
                pic.id,
                pic.sharpness,
                pic.edge_density,
                pic.contrast,
                pic.brightness,
                pic.noise_level,
            )
            values.append(
                (
                    pic.sharpness,
                    pic.edge_density,
                    pic.contrast,
                    pic.brightness,
                    pic.noise_level,
                    pic.id,
                )
            )

        logger.debug(
            f"[UPDATE] Committing {len(values)} full image quality updates to database."
        )
        cursor.executemany(
            "UPDATE pictures SET sharpness = ?, edge_density = ?, contrast = ?, brightness = ?, noise_level = ? WHERE id = ?",
            values,
        )
        conn.commit()

    def _update_face_quality(self, conn, pics):
        cursor = conn.cursor()
        values = []
        for pic in pics:
            # Assert face metrics are not bytes
            for metric_name in [
                "face_sharpness",
                "face_edge_density",
                "face_contrast",
                "face_brightness",
                "face_noise_level",
            ]:
                metric_value = getattr(pic, metric_name)
                assert not isinstance(metric_value, bytes), (
                    f"Corrupt face metric: {metric_name} for picture {pic.id} is bytes: {metric_value!r}"
                )
            logger.debug(
                "[UPDATE] Picture id %s face_sharpness: %s face_edge_density: %s face_contrast: %s face_brightness: %s face_noise_level: %s",
                pic.id,
                pic.face_sharpness,
                pic.face_edge_density,
                pic.face_contrast,
                pic.face_brightness,
                pic.face_noise_level,
            )
            values.append(
                (
                    pic.face_sharpness,
                    pic.face_edge_density,
                    pic.face_contrast,
                    pic.face_brightness,
                    pic.face_noise_level,
                    pic.id,
                )
            )

        logger.debug(
            f"[UPDATE] Committing {len(values)} face quality updates to database."
        )
        cursor.executemany(
            "UPDATE pictures SET face_sharpness = ?, face_edge_density = ?, face_contrast = ?, face_brightness = ?, face_noise_level = ? WHERE id = ?",
            values,
        )
        conn.commit()

    def _tag_pictures(self, picture_tagger, missing_tags) -> int:
        """Tag all pictures missing tags."""
        assert missing_tags is not None
        batch = missing_tags[: picture_tagger.max_concurrent_images()]
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            image_paths.append(pic.file_path)
            pic_by_path[pic.file_path] = pic

        tagged_pictures = []
        if image_paths:
            logger.debug(f"Tagging {len(image_paths)} images: {image_paths}")
            tag_results = picture_tagger.tag_images(image_paths)
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
                        corrected_tags = picture_tagger.correct_tags_with_florence(
                            pic.description, tags
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

    def _find_pics_needing_face_bbox(self):
        """Find pictures that need face bounding boxes."""
        rows = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT * FROM pictures WHERE face_bbox IS NULL"
            ).fetchall()
        )

        return self.from_batch_of_db_dicts(rows, [])

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

        logger.debug(f"Have {len(pics)} pictures needing facial featuress.")
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            logger.error(
                "InsightFace is not installed. Skipping facial features extraction."
            )
            return False, bboxes_updated  # Without InsightFace, we cannot proceed

        # Initialize InsightFace only once
        if not hasattr(self, "_insightface_app"):
            logger.debug("initialising InsightFace with CPU only (ctx_id=-1)")
            self._insightface_app = FaceAnalysis()
            self._insightface_app.prepare(ctx_id=-1)  # -1 = CPU only

        self._last_time_insightface_was_needed = time.time()

        for pic in pics:
            logger.debug("Looking for faces in picture %s", pic.id)

            # Skip it regardless of whether we succeed or fail
            self._skip_pictures.add(pic.id)

            if self._facial_features_worker_stop.is_set():
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
                        f"Unsupported file extension for facial features: {file_path}"
                    )
                if not faces:
                    logger.warning(
                        f"No face found in {file_path} for picture {pic.id}."
                    )
                    pic.face_bbox = ""
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
                x1, y1, x2, y2 = [float(v) for v in face.bbox]
                # Round width and height to nearest multiple of 64
                w = x2 - x1
                h = y2 - y1

                def round64(val):
                    return int(math.ceil(val / 64.0) * 64)

                w_rounded = round64(w)
                h_rounded = round64(h)
                # Center the bbox after rounding
                cx = x1 + w / 2.0
                cy = y1 + h / 2.0
                x1_new = cx - w_rounded / 2.0
                y1_new = cy - h_rounded / 2.0
                x2_new = cx + w_rounded / 2.0
                y2_new = cy + h_rounded / 2.0
                # Clamp to image edges
                img_h, img_w = None, None
                if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
                    img = cv2.imread(file_path)
                    if img is not None:
                        img_h, img_w = img.shape[0], img.shape[1]
                elif ext in [".mp4", ".avi", ".mov", ".mkv"]:
                    cap = cv2.VideoCapture(file_path)
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        img_h, img_w = frame.shape[0], frame.shape[1]
                    cap.release()
                if img_w is not None and img_h is not None:
                    x1_new = max(0, min(x1_new, img_w - 1))
                    y1_new = max(0, min(y1_new, img_h - 1))
                    x2_new = max(0, min(x2_new, img_w - 1))
                    y2_new = max(0, min(y2_new, img_h - 1))
                bbox_rounded = [x1_new, y1_new, x2_new, y2_new]
                pic.face_bbox = bbox_rounded

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
                pic.face_bbox = bytes()
        logger.debug("Done extracting face bboxes for current batch.")

        logger.debug(
            f"Storing {bboxes_updated} updated face bboxes and thumbnails to database."
        )
        self._update_attributes(pics, ["face_bbox", "thumbnail"])

        return True, bboxes_updated

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

    def _update_attributes(self, pictures, attributes):
        """Update specified attributes for a list of Picture instances in the database using executemany for efficiency."""

        values = []
        for picture in pictures:
            row = picture.to_dict()
            attr_values = [row[attr] for attr in attributes]
            attr_values.append(picture.id)
            values.append(tuple(attr_values))
            # logger.info(f"Updating picture {picture.id} with attributes: {row}")
        set_clause = ", ".join([f"{attr}=?" for attr in attributes])
        query = f"UPDATE pictures SET {set_clause} WHERE id=?"
        return self._db.submit_write(
            lambda conn: conn.executemany(query, values), priority=DBPriority.LOW
        ).result()

    def find(self, **kwargs):
        """
        Find and return a list of Picture objects matching all provided attribute=value pairs.
        Example: pictures.find(primary_character_id="hero")
        Special case: if a value is an empty string, search for IS NULL.
        Uses VaultDatabase for all DB access.
        """
        sort = kwargs.pop("sort", None)
        offset = kwargs.pop("offset", None)
        limit = kwargs.pop("limit", None)
        info = kwargs.pop("info", False)
        count = kwargs.pop("count", False)
        if count:
            # Return count of matching pictures
            clauses = []
            values = []
            for k, v in kwargs.items():
                if v == "" or v == "null":
                    clauses.append(f"{k} IS NULL")
                else:
                    clauses.append(f"{k}=?")
                    values.append(v)
            where_clause = ""
            if clauses:
                where_clause = "WHERE " + " AND ".join(clauses)
            query = f"SELECT COUNT(*) FROM pictures {where_clause}".strip()
            rows = self._db.execute_read(
                lambda conn: conn.execute(query, tuple(values)).fetchall()
            )
            if rows:
                return rows[0][0]
            else:
                return 0

        order_by = ""
        if SortMechanism.is_sql_sortable(sort):
            order_by = sort
        clauses = []
        values = []
        for k, v in kwargs.items():
            if v == "" or v == "null":
                clauses.append(f"{k} IS NULL")
            else:
                clauses.append(f"{k}=?")
                values.append(v)
        where_clause = ""
        if clauses:
            where_clause = "WHERE " + " AND ".join(clauses)
        if info:
            fields = PictureModel.metadata()
            select_fields = ", ".join(fields)
            query = f"SELECT {select_fields} FROM pictures {where_clause} {order_by}".strip()
        else:
            query = f"SELECT * FROM pictures {where_clause} {order_by}".strip()
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        if offset is not None:
            query += f" OFFSET {int(offset)}"
        rows = self._db.execute_read(
            lambda conn: conn.execute(query, tuple(values)).fetchall()
        )
        result = []
        for row in rows:
            pic = PictureModel.from_dict(row)
            if not info:
                tag_rows = self._db.execute_read(
                    lambda conn: conn.execute(
                        "SELECT tag FROM picture_tags WHERE picture_id = ?", (pic.id,)
                    ).fetchall()
                )
                pic.tags = [
                    tag_row["tag"] if isinstance(tag_row, dict) else tag_row[0]
                    for tag_row in tag_rows
                ]
            result.append(pic)
        return result

    def find_by_text(
        self, text, top_n=5, include_scores=False, threshold=0.5, count=False
    ):
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
        (
            query_emb,
            _,
        ) = self._picture_tagger.generate_text_embedding(picture={"description": text})
        logger.debug(
            f"Semantic search: query embedding shape: {getattr(query_emb, 'shape', None)}"
        )
        # Load all picture embeddings and ids
        rows = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT id, text_embedding FROM pictures WHERE text_embedding IS NOT NULL"
            ).fetchall()
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
            emb_blob = row["text_embedding"] if isinstance(row, dict) else row[1]
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

        if count:
            return len(results)
        return results

    def _start_quality_worker(self, interval=5):
        if self._quality_worker and self._quality_worker.is_alive():
            return
        self._quality_worker_stop = threading.Event()
        self._quality_worker = threading.Thread(
            target=self._quality_worker_loop, args=(interval,), daemon=True
        )
        self._quality_worker.start()

    def _stop_quality_worker(self):
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

        def delete_pictures(conn, picture_ids=picture_ids):
            cursor = conn.cursor()
            cursor.executemany(
                "DELETE FROM pictures WHERE id = ?", [(pid,) for pid in picture_ids]
            )
            cursor.executemany(
                "DELETE FROM picture_tags WHERE picture_id = ?",
                [(pid,) for pid in picture_ids],
            )
            conn.commit()

        self._db.submit_write(delete_pictures, picture_ids).result()

    def likeness_query(self, treshold: float):
        """Return pairs of picture IDs with a likeness score above threshold."""
        rows = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT picture_id_a, picture_id_b, likeness FROM picture_likeness WHERE likeness >= ?",
                (treshold,),
            ).fetchall()
        )
        result = []
        for row in rows:
            result.append((row["picture_id_a"], row["picture_id_b"], row["likeness"]))
        return result

    def add(self, pictures: Union[PictureModel, List[PictureModel]]):
        """Add one or more pictures. Supports both single picture and batch operations."""
        if not isinstance(pictures, list):
            pictures = [pictures]

        # Batch insert
        picture_dicts, list_of_tag_dicts = self.to_batch_of_db_dicts(pictures)
        new_picture_ids = []

        def insert_pictures_and_tags(conn, picture_dicts, list_of_tag_dicts):
            cursor = conn.cursor()
            # Insert pictures
            new_ids = []
            for pic_dict, tag_dicts in zip(picture_dicts, list_of_tag_dicts):
                logger.debug(f"Inserting picture: {pic_dict}")

                try:
                    cursor.execute(
                        f"INSERT INTO pictures ({', '.join(pic_dict.keys())}) VALUES ({', '.join(['?' for _ in pic_dict.keys()])})",
                        tuple(pic_dict.values()),
                    )
                    new_ids.append(pic_dict["id"])

                except Exception as e:
                    logger.error(f"Failed to insert picture {pic_dict}: {e}")

                for tag_dict in tag_dicts:
                    cursor.executemany(
                        "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                        [
                            (tag_dict["picture_id"], tag_dict["tag"])
                            for tag_dict in tag_dict
                        ],
                    )
            conn.commit()

        new_picture_ids = self._db.submit_write(
            lambda conn: insert_pictures_and_tags(
                conn, picture_dicts, list_of_tag_dicts
            ),
            priority=DBPriority.IMMEDIATE,
        ).result()
        if new_picture_ids:
            # Get all existing picture IDs (excluding new ones)
            def append_work_queue(conn, new_picture_ids=new_picture_ids):
                cursor = conn.cursor()
                rows = cursor.execute(
                    f"SELECT id FROM pictures WHERE id NOT IN ({','.join(['?'] * len(new_picture_ids))})",
                    tuple(new_picture_ids),
                ).fetchall()
                existing_ids = [row["id"] for row in rows]
                # Prepare all pairs (new_id, existing_id) and (existing_id, new_id)
                queue_pairs = []
                for new_id in new_picture_ids:
                    for existing_id in existing_ids:
                        queue_pairs.append(
                            (min(new_id, existing_id), max(new_id, existing_id))
                        )
                if queue_pairs:
                    cursor.executemany(
                        "INSERT OR IGNORE INTO likeness_work_queue (picture_id_a, picture_id_b) VALUES (?, ?)",
                        queue_pairs,
                    )
                    conn.commit()

            self._db.submit_write(
                append_work_queue, new_picture_ids, priority=DBPriority.LOW
            ).result()
        return new_picture_ids

    def update(self, pictures: Union[PictureModel, List[PictureModel]]):
        """Update one or more pictures. Supports both single picture and batch operations."""
        if not isinstance(pictures, list):
            pictures = [pictures]

        picture_dicts, list_of_tag_dicts = self.to_batch_of_db_dicts(pictures)

        def update_picture_and_tag(conn, pic_dict, tag_dicts):
            set_clause = ", ".join([f"{k}=?" for k in pic_dict.keys()])
            sql = f"UPDATE pictures SET {set_clause} WHERE id = ?"
            values = list(pic_dict.values()) + [pic_dict["id"]]
            cursor = conn.cursor()
            cursor.execute(sql, tuple(values))

            # Update tags in picture_tags table
            if tag_dicts:
                cursor.execute(
                    "DELETE FROM picture_tags WHERE picture_id = ?",
                    (pic_dict["id"],),
                )
                cursor.executemany(
                    "INSERT INTO picture_tags (picture_id, tag) VALUES (?, ?)",
                    [
                        (tag_dict["picture_id"], tag_dict["tag"])
                        for tag_dict in tag_dicts
                    ],
                )
            conn.commit()

        for pic_dict, tag_dicts in zip(picture_dicts, list_of_tag_dicts):
            self._db.submit_write(update_picture_and_tag, pic_dict, tag_dicts)

    def fetch_by_shas(self, shas: list[str]) -> list[PictureModel]:
        if not shas:
            return []

        placeholders = ",".join(["?"] * len(shas))
        sql = f"SELECT id FROM pictures WHERE pixel_sha IN ({placeholders})"
        ids = self._db.execute_read(
            lambda conn: conn.execute(sql, tuple(shas)).fetchall()
        )

        pics = []
        for id in ids:
            pic = self[id["id"]]
            if pic is not None:
                pics.append(pic)
        return pics

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
        tag_dicts: Union[list[dict], list[list[dict]]] = None,
    ):
        """
        Convert list of dicts from DB rows to a list of PictureModel objects
        """
        if not tag_dicts:
            tag_dicts = [[] for _ in picture_dicts]
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
            if key in ("tags", "character_ids"):
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

    def _generate_facial_features(self, picture_tagger, missing_facial_features):
        """
        Generate facial features for a batch of PictureModel objects using PictureTagger.
        Returns the number of pictures updated.
        """
        updated = 0
        for pic in missing_facial_features:
            try:
                features = picture_tagger.generate_facial_features(pic)
                if features is not None:
                    pic.facial_features = features
                    updated += 1
                else:
                    pic.facial_features = bytes()
            except Exception as e:
                logger.error(f"Failed to generate facial features for {pic.id}: {e}")
        return updated

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

import numpy as np
import time
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed

from enum import Enum
from typing import Union, List

import pixlvault.picture_db_tools as db_tools

from pixlvault.logging import get_logger
from pixlvault.picture import PictureModel
from pixlvault.picture_quality import PictureQuality
from pixlvault.picture_tagger import PictureTagger
from pixlvault.database import DBPriority
from pixlvault.quality_worker import QualityWorker  # noqa: F401
from pixlvault.facial_features_worker import FacialFeaturesWorker  # noqa: F401
from pixlvault.tag_worker import TagWorker  # noqa: F401
from pixlvault.worker_registry import WorkerType, WorkerRegistry

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
    NUM_LIKENESS_THREADS = 4

    def __init__(self, db, characters=None, device=None):
        self._db = db
        self._skip_pictures = set()
        self._characters = characters  # Should be a Characters manager or None
        # Pass device to PictureTagger (default: None lets PictureTagger auto-detect)
        self._device = device
        self._picture_tagger = PictureTagger(device=device)
        logger.info(
            f"Initialized PictureTagger for Pictures manager with device={device!r}."
        )

        self._workers = {}

        for worker_type in WorkerType.all():
            if worker_type == WorkerType.LIKENESS:
                continue
            self._workers[worker_type] = WorkerRegistry.create_worker(
                worker_type, self._db, self._picture_tagger, self._characters
            )

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

    def start_worker(self, worker: WorkerType):
        if worker in self._workers:
            self._workers[worker].start()
        elif worker == WorkerType.LIKENESS:
            self._start_likeness_worker()
        else:
            raise ValueError(f"Unknown worker type: {worker}")

    def stop_worker(self, worker: WorkerType):
        if worker in self._workers:
            self._workers[worker].stop()
        elif worker == WorkerType.LIKENESS:
            self._stop_likeness_worker()
        else:
            raise ValueError(f"Unknown worker type: {worker}")

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

            total_pending = self._db.execute_read(
                lambda conn: conn.execute(
                    "SELECT COUNT(*) FROM likeness_work_queue"
                ).fetchone()
            )[0]
            logger.info(
                "Got %d pending likeness pairs to process from work queue."
                % (total_pending)
            )
            if total_pending == 0:
                logger.info(
                    "[LIKENESS] No pending pairs, sleeping and skipping any deletion..."
                )
                time.sleep(interval)
                continue

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
            logger.info(
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
                logger.info(
                    "Got %d pictures for likeness calculation." % (len(pic_rows))
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
                    if not pic_a.facial_features:
                        logger.warning(
                            f"[LIKENESS] Picture id={pic_a_id} missing facial features, skipping pair."
                        )
                    if not pic_b.facial_features:
                        logger.warning(
                            f"[LIKENESS] Picture id={pic_b_id} missing facial features, skipping pair."
                        )
                    if not pic_a.facial_features or not pic_b.facial_features:
                        continue
                    pending_pairs.append((pic_a_id, pic_b_id, pic_a, pic_b))

            logger.info(
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
            # Also delete from picture_likeness where either side matches
            cursor.executemany(
                "DELETE FROM picture_likeness WHERE picture_id_a = ? OR picture_id_b = ?",
                [(pid, pid) for pid in picture_ids],
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
        picture_dicts, list_of_tag_dicts = db_tools.to_batch_of_db_dicts(pictures)
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

        picture_dicts, list_of_tag_dicts = db_tools.to_batch_of_db_dicts(pictures)

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

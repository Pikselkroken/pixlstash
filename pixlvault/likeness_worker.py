import cv2
import numpy as np
import time

from concurrent.futures import ThreadPoolExecutor, as_completed


from pixlvault.characters import Characters
from pixlvault.database import DBPriority
from pixlvault.logging import get_logger
from pixlvault.picture import PictureModel
from pixlvault.picture_quality import PictureQuality
from pixlvault.picture_tagger import PictureTagger
from pixlvault.worker_registry import BaseWorker, WorkerType

logger = get_logger(__name__)


class LikenessWorker(BaseWorker):
    BATCH_SIZE = 50000
    NUM_THREADS = 4

    def __init__(
        self, db_connection, picture_tagger: PictureTagger, characters: Characters
    ):
        super().__init__(db_connection, picture_tagger, characters)

    def worker_type(self) -> WorkerType:
        return WorkerType.LIKENESS

    def _run(self):
        time.sleep(1.0)  # Stagger start times for multiple workers
        while not self._stop.is_set():
            data_updated = False
            likeness_score_count = 0
            start = time.time()
            logger.debug("LikenessWorker: Starting iteration...")

            total_pending = self._get_total_pending()
            logger.debug(
                "Got %d pending likeness pairs to process from work queue."
                % (total_pending)
            )
            if total_pending == 0:
                logger.info(
                    "LikenessWorker: Sleeping after %.2f seconds. No pending work."
                    % (time.time() - start)
                )
                self._stop.wait(self.INTERVAL)
                continue

            self._cleanup_work_queue(start)
            rows = self._fetch_work_queue_rows()
            total_pending = self._get_total_pending()
            logger.info(
                f"LikenessWorker: Fetched {len(rows)} rows from likeness_work_queue out of {total_pending}."
            )

            if self._stop.is_set():
                break
            pending_pairs = self._fetch_pending_pairs(rows)
            logger.info(
                "LikenessWorker: Got %d pending likeness pairs to process from work queue."
                % (len(pending_pairs))
            )
            if self._stop.is_set():
                break
            if pending_pairs:
                all_likeness_scores, all_processed_pairs, processed_total = (
                    self._process_batches_for_combined_likeness(pending_pairs)
                )
                logger.debug(
                    f"LikenessWorker: Processed {processed_total} likeness scores in this iteration."
                )
                if all_likeness_scores:
                    self._insert_likeness_scores(
                        all_likeness_scores, all_processed_pairs
                    )
                    logger.debug(
                        f"LikenessWorker: Bulk inserted {len(all_likeness_scores)} likeness scores and removed {len(all_processed_pairs)} processed pairs from work queue."
                    )
                    likeness_score_count = len(all_likeness_scores)
                    data_updated = True

            if self._stop.is_set():
                break

            timing = time.time() - start
            if timing > 0.5:
                logger.info(
                    "LikenessWorker: Calculated and updated %d likeness scores in %.2f seconds."
                    % (likeness_score_count, time.time() - start)
                )
            if not data_updated:
                logger.info(
                    "LikenessWorker: Sleeping after %.2f seconds. No work needed."
                    % (time.time() - start)
                )
                self._wait()
        logger.info("LikenessWorker: Likeness worker stopped.")

    def _process_batches_for_combined_likeness(self, pending_pairs, bins=32):
        """
        Batch process both facial and color histogram likeness, average them, and return for DB insert.
        Returns (likeness_scores, processed_pairs, processed_total)
        """
        batches = [
            pending_pairs[i * self.BATCH_SIZE : (i + 1) * self.BATCH_SIZE]
            for i in range(
                min(
                    self.NUM_THREADS,
                    (len(pending_pairs) + self.BATCH_SIZE - 1) // self.BATCH_SIZE,
                )
            )
        ]

        def process_batch(batch):
            likeness_scores = []
            queue_pairs = []
            for item in batch:
                pic_a_id, pic_b_id, pic_a, pic_b = item
                try:
                    # Facial likeness
                    features_a = np.frombuffer(pic_a.facial_features, dtype=np.float32)
                    features_b = np.frombuffer(pic_b.facial_features, dtype=np.float32)
                    facial_likeness = PictureQuality.likeness_score(
                        features_a, features_b
                    )
                    # Color histogram likeness
                    img_a = (
                        pic_a.get_image()
                        if hasattr(pic_a, "get_image")
                        else getattr(pic_a, "image_data", None)
                    )
                    img_b = (
                        pic_b.get_image()
                        if hasattr(pic_b, "get_image")
                        else getattr(pic_b, "image_data", None)
                    )
                    if img_a is None or img_b is None:
                        logger.warning(
                            f"Missing image data for pair ({pic_a_id}, {pic_b_id}), skipping."
                        )
                        continue
                    color_likeness = self._color_histogram_likeness(img_a, img_b, bins)
                    avg_likeness = float((facial_likeness + color_likeness) / 2.0)
                    likeness_scores.append(
                        (pic_a_id, pic_b_id, avg_likeness, "avg_facial_colorhist")
                    )
                    queue_pairs.append((pic_a_id, pic_b_id))
                except Exception as e:
                    logger.warning(
                        f"Combined likeness failed for pair ({pic_a_id}, {pic_b_id}): {e}"
                    )
            return likeness_scores, queue_pairs

        processed_total = 0
        all_likeness_scores = []
        all_processed_pairs = []
        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            futures = [
                executor.submit(process_batch, batch) for batch in batches if batch
            ]
            for future in as_completed(futures):
                batch_scores, processed_pairs = future.result()
                all_likeness_scores.extend(batch_scores)
                all_processed_pairs.extend(processed_pairs)
                processed_total += len(batch_scores)
        return all_likeness_scores, all_processed_pairs, processed_total

    def _get_total_pending(self):
        return self._db.submit_task(
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM likeness_work_queue"
            ).fetchone()
        ).result()[0]

    def _cleanup_work_queue(self, start):
        self._db.submit_task(
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
            f"LikenessWorker: DELETING existing items from likeness_work_queue took {time_after_cleanup - start:.2f} seconds."
        )

    def _fetch_work_queue_rows(self):
        return self._db.submit_task(
            lambda conn: conn.execute(
                "SELECT picture_id_a, picture_id_b FROM likeness_work_queue ORDER BY rowid LIMIT ?",
                (self.BATCH_SIZE,),
            ).fetchall()
        ).result()

    def _fetch_pending_pairs(self, rows):
        pending_pairs = []
        all_ids = set()
        pairs_to_remove = []
        for row in rows:
            all_ids.add(row[0])
            all_ids.add(row[1])
        if all_ids:
            placeholders = ",".join(["?"] * len(all_ids))
            pic_rows = self._db.submit_task(
                lambda conn: conn.execute(
                    f"SELECT * FROM pictures WHERE id IN ({placeholders})",
                    tuple(all_ids),
                ).fetchall()
            ).result()
            logger.info("Got %d pictures for likeness calculation." % (len(pic_rows)))
            pic_dict = {
                row["id"] if isinstance(row, dict) else row[0]: PictureModel.from_dict(
                    row
                )
                for row in pic_rows
            }
            for row in rows:
                pic_a_id, pic_b_id = row[0], row[1]
                pic_a = pic_dict.get(pic_a_id)
                pic_b = pic_dict.get(pic_b_id)
                if not pic_a or not pic_b:
                    logger.warning(f"Skipping pair ({pic_a_id}, {pic_b_id}): picture(s) missing from DB.")
                    pairs_to_remove.append((pic_a_id, pic_b_id))
                    continue
                # Only process if both have facial features
                def is_empty_features(f):
                    return isinstance(f, (bytes, str)) and len(f) == 0
                if pic_a.facial_features is None or pic_b.facial_features is None:
                    logger.info(f"Retrying pair ({pic_a_id}, {pic_b_id}): facial_features not yet calculated.")
                    continue  # retry later
                if is_empty_features(pic_a.facial_features) or is_empty_features(pic_b.facial_features):
                    logger.info(f"Skipping and removing pair ({pic_a_id}, {pic_b_id}): facial_features is empty string (no face found).")
                    pairs_to_remove.append((pic_a_id, pic_b_id))
                    continue
                pending_pairs.append((pic_a_id, pic_b_id, pic_a, pic_b))
        # Remove pairs that should not be retried
        if pairs_to_remove:
            self._db.submit_task(
                lambda conn: conn.executemany(
                    "DELETE FROM likeness_work_queue WHERE picture_id_a = ? AND picture_id_b = ?",
                    pairs_to_remove,
                )
            ).result()
            logger.info(f"Removed pairs from work queue (no face found): {pairs_to_remove}")
        logger.info(f"Pending pairs to process this batch: {[(p[0], p[1]) for p in pending_pairs]}")
        return pending_pairs

    def _process_batches_for_facial_likeness(self, pending_pairs):
        batches = [
            pending_pairs[i * self.BATCH_SIZE : (i + 1) * self.BATCH_SIZE]
            for i in range(
                min(
                    self.NUM_THREADS,
                    (len(pending_pairs) + self.BATCH_SIZE - 1) // self.BATCH_SIZE,
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

        processed_total = 0
        all_likeness_scores = []
        all_processed_pairs = []
        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            futures = [
                executor.submit(process_batch, batch) for batch in batches if batch
            ]
            for future in as_completed(futures):
                batch_scores, processed_pairs = future.result()
                all_likeness_scores.extend(batch_scores)
                all_processed_pairs.extend(processed_pairs)
                processed_total += len(batch_scores)
        return all_likeness_scores, all_processed_pairs, processed_total

    def _insert_likeness_scores(self, all_likeness_scores, all_processed_pairs):
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
            logger.info(f"Removed processed pairs from work queue: {all_processed_pairs}")
            conn.commit()

        self._db.submit_task(
            insert_likeness_scores,
            all_likeness_scores,
            priority=DBPriority.LOW,
        )

    def _color_histogram_likeness(self, img_a, img_b, bins=32):
        """
        Compute a simple color histogram likeness measure between two images.
        Returns a float in [0, 1], where 1 means identical histograms.
        Uses normalized L1 distance between concatenated RGB histograms.
        """

        def get_hist(img):
            # img: numpy array, shape (H, W, 3), dtype uint8
            chans = cv2.split(img)
            hist = [
                cv2.calcHist([c], [0], None, [bins], [0, 256]).flatten() for c in chans
            ]
            hist = np.concatenate(hist)
            hist = hist / (np.sum(hist) + 1e-8)
            return hist

        hist_a = get_hist(img_a)
        hist_b = get_hist(img_b)
        l1 = np.sum(np.abs(hist_a - hist_b))
        # Normalize: max possible L1 is 2 (if histograms are disjoint and sum to 1)
        likeness = 1.0 - (l1 / 2.0)
        return float(np.clip(likeness, 0.0, 1.0))

    def _process_batches_for_color_histogram_likeness(self, pending_pairs, bins=32):
        """
        Batch process color histogram likeness for all pending pairs.
        Returns (likeness_scores, processed_pairs, processed_total)
        """
        batches = [
            pending_pairs[i * self.BATCH_SIZE : (i + 1) * self.BATCH_SIZE]
            for i in range(
                min(
                    self.NUM_THREADS,
                    (len(pending_pairs) + self.BATCH_SIZE - 1) // self.BATCH_SIZE,
                )
            )
        ]

        def process_batch(batch):
            likeness_scores = []
            queue_pairs = []
            for item in batch:
                pic_a_id, pic_b_id, pic_a, pic_b = item
                # Assume PictureModel has .image_data or .get_image() returning np.ndarray (H,W,3)
                try:
                    img_a = (
                        pic_a.get_image()
                        if hasattr(pic_a, "get_image")
                        else pic_a.image_data
                    )
                    img_b = (
                        pic_b.get_image()
                        if hasattr(pic_b, "get_image")
                        else pic_b.image_data
                    )
                    if img_a is None or img_b is None:
                        continue
                    likeness = self._color_histogram_likeness(img_a, img_b, bins)
                    likeness_scores.append(
                        (pic_a_id, pic_b_id, float(likeness), "color_hist")
                    )
                    queue_pairs.append((pic_a_id, pic_b_id))
                except Exception as e:
                    logger.warning(
                        f"Color histogram likeness failed for pair ({pic_a_id}, {pic_b_id}): {e}"
                    )
            return likeness_scores, queue_pairs

        processed_total = 0
        all_likeness_scores = []
        all_processed_pairs = []
        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            futures = [
                executor.submit(process_batch, batch) for batch in batches if batch
            ]
            for future in as_completed(futures):
                batch_scores, processed_pairs = future.result()
                all_likeness_scores.extend(batch_scores)
                all_processed_pairs.extend(processed_pairs)
                processed_total += len(batch_scores)
        return all_likeness_scores, all_processed_pairs, processed_total

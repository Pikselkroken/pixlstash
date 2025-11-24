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
            pending_pairs = []

            total_pending = self._db.submit_task(
                lambda conn: conn.execute(
                    "SELECT COUNT(*) FROM likeness_work_queue"
                ).fetchone()
            ).result()[0]
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

            rows = self._db.submit_task(
                lambda conn: conn.execute(
                    "SELECT picture_id_a, picture_id_b FROM likeness_work_queue ORDER BY rowid LIMIT ?",
                    (self.BATCH_SIZE,),
                ).fetchall()
            ).result()

            total_pending = self._db.submit_task(
                lambda conn: conn.execute(
                    "SELECT COUNT(*) FROM likeness_work_queue"
                ).fetchone()
            ).result()[0]
            logger.info(
                f"LikenessWorker: Fetched {len(rows)} rows from likeness_work_queue out of {total_pending}."
            )
            # Batch fetch all required pictures
            all_ids = set()
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
                            f"LikenessWorker: Picture id={pic_a_id} missing facial features, skipping pair."
                        )
                    if not pic_b.facial_features:
                        logger.warning(
                            f"LikenessWorker: Picture id={pic_b_id} missing facial features, skipping pair."
                        )
                    if not pic_a.facial_features or not pic_b.facial_features:
                        continue
                    pending_pairs.append((pic_a_id, pic_b_id, pic_a, pic_b))

            logger.info(
                "LikenessWorker: Got %d pending likeness pairs to process from work queue."
                % (len(pending_pairs))
            )
            if pending_pairs:
                batches = [
                    pending_pairs[i * self.BATCH_SIZE : (i + 1) * self.BATCH_SIZE]
                    for i in range(
                        min(
                            self.NUM_THREADS,
                            (len(pending_pairs) + self.BATCH_SIZE - 1)
                            // self.BATCH_SIZE,
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
                    f"LikenessWorker: Processed {processed_total} likeness scores in this iteration."
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

                    self._db.submit_task(
                        insert_likeness_scores,
                        all_likeness_scores,
                        priority=DBPriority.LOW,
                    )
                    logger.debug(
                        f"LikenessWorker: Bulk inserted {len(all_likeness_scores)} likeness scores and removed {len(all_processed_pairs)} processed pairs from work queue."
                    )
                    likeness_score_count = len(all_likeness_scores)
                    data_updated = True

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

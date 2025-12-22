from concurrent.futures import ThreadPoolExecutor, as_completed
import cv2
import numpy as np

from sqlmodel import select, Session
from typing import List, Optional, Tuple

from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.quality import Quality
from pixlvault.db_models.picture_likeness import (
    PictureLikeness,
    PictureLikenessFrontier,
)

logger = get_logger(__name__)


class LikenessWorker(BaseWorker):
    BATCH_SIZE = 5000
    TOP_K = 200

    def worker_type(self) -> WorkerType:
        return WorkerType.LIKENESS

    @classmethod
    def get_next_batch(cls, session: Session) -> Optional[Tuple[int, List[int]]]:
        """
        Return the next work chunk as (a, bs), where:
        - a is the next picture_id_a with remaining work and quality ready,
        - bs is a contiguous list of b ids (a < b) with quality ready,
        - len(bs) <= batch_size and starts at the current frontier start_b.
        Returns None if nothing to do.
        """

        a = PictureLikenessFrontier.get_next_a_candidate(
            session, quality_ready=Quality.quality_read_for_picture
        )
        if a is None:
            return None, None

        max_id = PictureLikenessFrontier.max_picture_id(session)
        # Ask the model to compute the next contiguous [start_b, end_b] window
        rng = PictureLikenessFrontier.range_to_compare(
            session, a, max_id=max_id, batch_limit=cls.BATCH_SIZE
        )
        if not rng:
            return None, None  # frontier already at max or race

        start_b, end_b = rng

        # Filter window to b with quality ready on both sides
        # Note: we require Quality for 'a' (already checked) and for each 'b'
        # Query all b rows in one go for efficiency
        b_rows = session.exec(
            select(Quality.picture_id)
            .where((Quality.picture_id >= start_b) & (Quality.picture_id <= end_b))
            .order_by(Quality.picture_id)
        ).all()
        eligible_bs_all = [int(pid) for pid in b_rows]

        # Take the longest consecutive prefix from start_b
        bs_prefix = PictureLikenessFrontier.consecutive_prefix(start_b, eligible_bs_all)

        if not bs_prefix:
            return None, None  # No eligible b in the window

        return a, bs_prefix[: cls.BATCH_SIZE]

    def _run(self):
        logger.info("LikenessWorker: Likeness worker started.")

        self._db.run_task(PictureLikenessFrontier.ensure_all)

        logger.info("LikenessWorker: PictureLikenessFrontier initialized.")

        while not self._stop.is_set():
            a, bs = self._db.run_immediate_read_task(LikenessWorker.get_next_batch)

            if not a or not bs:
                logger.debug("LikenessWorker: No pending pairs. Sleeping...")
                self._wait()
                continue

            logger.debug(f"LikenessWorker: Processing {len(bs)} pairs.")

            pids_needed = set()
            for b in bs:
                pids_needed.add(a)
                pids_needed.add(b)

            def fetch_quality(session, ids):
                qualities = session.exec(
                    select(Quality).where(Quality.picture_id.in_(ids))
                ).all()
                return {quality.picture_id: quality for quality in qualities}

            quality_dict = self._db.run_task(
                fetch_quality, list(pids_needed), priority=DBPriority.LOW
            )

            likeness_results = []
            processed_notify_ids = []
            for b in bs:
                if self._stop.is_set():
                    break
                logger.debug(f"LikenessWorker: Processing pair (a={a}, b={b})")
                quality_a = quality_dict.get(a).get_color_histogram()
                quality_b = quality_dict.get(b).get_color_histogram()
                likeness = self._color_histogram_likeness(quality_a, quality_b)

                likeness_results.append(
                    PictureLikeness(
                        picture_id_a=a,
                        picture_id_b=b,
                        likeness=likeness,
                        metric="color_histogram",
                    )
                )
                processed_notify_ids.append((PictureLikeness, (a, b), "pair", likeness))
                if self._stop.is_set():
                    break

            logger.debug("LikenessWorker: Writing likeness scores to database...")

            def insert_likeness_and_update_frontier(
                session, likeness_results, a, max_b
            ):
                PictureLikeness.bulk_insert_ignore(session, likeness_results)
                PictureLikenessFrontier.update(session, a, max_b)
                PictureLikeness.prune_below_top_k(session, a, self.TOP_K)
                session.commit()

            self._db.run_task(
                insert_likeness_and_update_frontier,
                likeness_results,
                a,
                max(bs),
                priority=DBPriority.LOW,
            )

            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)
                # Update completed_tasks to track all b's for each a
                logger.info(
                    f"LikenessWorker: Processed {len(processed_notify_ids)} likeness scores."
                )
            else:
                logger.info("LikenessWorker: No likeness scores computed. Sleeping...")
                self._wait()
        logger.info("LikenessWorker: Likeness worker stopped.")

    def _color_histogram_likeness(self, hist_a, hist_b):
        l1 = np.sum(np.abs(hist_a - hist_b))
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
                    self.CHUNKS,
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

    def _color_histogram_likeness_batch(self, img_a, imgs_b, bins=32):
        """
        Compute color histogram likeness between img_a and a list of imgs_b efficiently.
        Returns a list of likeness scores.
        """

        def get_hist(img):
            chans = cv2.split(img)
            hist = [
                cv2.calcHist([c], [0], None, [bins], [0, 256]).flatten() for c in chans
            ]
            hist = np.concatenate(hist)
            hist = hist / (np.sum(hist) + 1e-8)
            return hist

        hist_a = get_hist(img_a)
        hists_b = [get_hist(img) for img in imgs_b]
        if not hists_b:
            return []
        hists_b = np.stack(hists_b, axis=0)
        l1 = np.sum(np.abs(hists_b - hist_a), axis=1)
        likeness = 1.0 - (l1 / 2.0)
        return np.clip(likeness, 0.0, 1.0).tolist()

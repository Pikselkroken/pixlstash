from concurrent.futures import ThreadPoolExecutor, as_completed
import cv2
import numpy as np
from sqlmodel import select

from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.quality import Quality
from pixlvault.picture_utils import PictureUtils
from pixlvault.db_models.picture_likeness import PictureLikeness

logger = get_logger(__name__)


class LikenessWorker(BaseWorker):
    BATCH_SIZE = 5000

    def worker_type(self) -> WorkerType:
        return WorkerType.LIKENESS

    def _run(self):
        while not self._stop.is_set():
            # 1. Fetch pending pairs from the work queue

            def fetch_missing_pairs(session):
                from sqlalchemy import and_
                from sqlalchemy.orm import aliased
                from sqlmodel import select
                Q2 = aliased(Quality)
                stmt = (
                    select(Quality.picture_id.label('a_id'), Q2.picture_id.label('b_id'))
                    .select_from(Quality)
                    .join(Q2, and_(Quality.picture_id < Q2.picture_id, Q2.color_histogram.is_not(None)))
                    .outerjoin(
                        PictureLikeness,
                        and_(
                            PictureLikeness.picture_id_a == Quality.picture_id,
                            PictureLikeness.picture_id_b == Q2.picture_id,
                        )
                    )
                    .where(
                        Quality.picture_id.is_not(None),
                        Quality.color_histogram.is_not(None),
                        PictureLikeness.picture_id_a == None
                    )
                    .limit(self.BATCH_SIZE)
                )
                result = session.exec(stmt)
                pairs = [(row.a_id, row.b_id) for row in result]
                return pairs

            logger.info("LikenessWorker: Fetching pending picture pairs...")
            pairs = self._db.run_task(fetch_missing_pairs, priority=DBPriority.LOW)

            if not pairs:
                logger.debug("LikenessWorker: No pending pairs. Sleeping...")
                self._wait()
                continue

            total_pairs = len(pairs)
            logger.info(f"LikenessWorker: Processing {total_pairs} pairs.")

            pids_needed = set()
            for a, b in pairs:
                pids_needed.add(a)
                pids_needed.add(b)

            def fetch_quality(session, ids):
                qualities = session.exec(select(Quality).where(Quality.picture_id.in_(ids))).all()
                return {quality.picture_id: quality for quality in qualities}
            quality_dict = self._db.run_task(fetch_quality, list(pids_needed), priority=DBPriority.LOW)

            # 2. Do likeness computation outside the session
            likeness_results = []
            processed_notify_ids = []
            for a, b in pairs:
                quality_a = quality_dict.get(a).get_color_histogram()
                quality_b = quality_dict.get(b).get_color_histogram()
                likeness = self._color_histogram_likeness(
                    quality_a, quality_b
                )

                likeness_results.append(
                    PictureLikeness(
                        picture_id_a=a,
                        picture_id_b=b,
                        likeness=likeness,
                        metric="color_histogram",
                    )
                )
                processed_notify_ids.append(
                    (PictureLikeness, (a, b), "pair", likeness)
                )
                if self._stop.is_set():
                    break

            # 3. Write results and remove processed pairs in a new DB task
            def write_results(session):
                if likeness_results:
                    session.add_all(likeness_results)
                    logger.debug(f"Inserted {len(likeness_results)} likeness scores.")
                session.commit()

            logger.info("LikenessWorker: Writing likeness scores to database...")
            self._db.run_task(write_results, priority=DBPriority.LOW)

            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)
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

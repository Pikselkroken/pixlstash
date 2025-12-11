from concurrent.futures import ThreadPoolExecutor, as_completed
import cv2
import numpy as np
import time
from sqlmodel import select

from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.likeness_work_queue import LikenessWorkQueue
from pixlvault.picture_utils import PictureUtils
from pixlvault.db_models.picture_likeness import PictureLikeness

logger = get_logger(__name__)


class LikenessWorker(BaseWorker):
    BATCH_SIZE = 5000
    NUM_THREADS = 4
    INTERVAL = 5

    def worker_type(self) -> WorkerType:
        return WorkerType.LIKENESS

    def _run(self):
        while not self._stop.is_set():
            start = time.time()

            # 1. Fetch pending pairs from the work queue
            def fetch_pending_pairs(session):
                pairs = session.exec(
                    select(LikenessWorkQueue).limit(self.BATCH_SIZE)
                ).all()
                pic_ids = set()
                for pair in pairs:
                    pic_ids.add(pair.picture_id_a)
                    pic_ids.add(pair.picture_id_b)
                pics = session.exec(
                    select(Picture).where(Picture.id.in_(pic_ids))
                ).all()
                pic_dict = {pic.id: pic for pic in pics}
                return pairs, pic_dict

            pending_pairs, pic_dict = self._db.run_task(fetch_pending_pairs)
            if not pending_pairs:
                logger.debug("LikenessWorker: No pending pairs. Sleeping...")
                self._stop.wait(self.INTERVAL)
                continue

            logger.info(f"LikenessWorker: Processing {len(pending_pairs)} pairs.")

            # 2. Do likeness computation outside the session
            likeness_results = []
            to_remove = []
            processed_notify_ids = []
            for pair in pending_pairs:
                pic_a = pic_dict.get(pair.picture_id_a)
                pic_b = pic_dict.get(pair.picture_id_b)
                if not pic_a or not pic_b:
                    logger.warning(
                        f"Skipping pair ({pair.picture_id_a}, {pair.picture_id_b}): missing picture(s). Removing from queue."
                    )
                    to_remove.append(pair)
                    continue
                img_a = None
                img_b = None
                if pic_a.file_path:
                    img_a = PictureUtils.load_image_or_video(pic_a.file_path)
                if pic_b.file_path:
                    img_b = PictureUtils.load_image_or_video(pic_b.file_path)
                if img_a is None or img_b is None:
                    logger.warning(
                        f"Skipping pair ({pair.picture_id_a}, {pair.picture_id_b}): missing image data. Removing from queue."
                    )
                    to_remove.append(pair)
                    continue
                likeness = self._color_histogram_likeness(img_a, img_b)
                likeness_results.append(
                    PictureLikeness(
                        picture_id_a=pair.picture_id_a,
                        picture_id_b=pair.picture_id_b,
                        likeness=likeness,
                        metric="color_histogram",
                    )
                )
                to_remove.append(pair)
                processed_notify_ids.append(
                    (PictureLikeness, (pair.picture_id_a, pair.picture_id_b), "pair")
                )

            # 3. Write results and remove processed pairs in a new DB task
            def write_results(session):
                if likeness_results:
                    session.add_all(likeness_results)
                    logger.debug(f"Inserted {len(likeness_results)} likeness scores.")
                if to_remove:
                    for pair in to_remove:
                        session.delete(pair)
                    logger.debug(
                        f"Removed {len(to_remove)} processed pairs from work queue."
                    )
                session.commit()

            self._db.run_task(write_results)

            if processed_notify_ids:
                self._notify_ids_processed(processed_notify_ids)

            elapsed = time.time() - start
            if elapsed < self.INTERVAL:
                self._stop.wait(self.INTERVAL - elapsed)
        logger.info("LikenessWorker: Likeness worker stopped.")

    def _color_histogram_likeness(self, img_a, img_b, bins=32):
        def get_hist(img):
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

    def queue_pair(self, picture_id_a: str, picture_id_b: str):
        """
        Public method to queue a pair for likeness computation.
        """
        self._db.run_task(LikenessWorker._add_pair_to_queue, picture_id_a, picture_id_b)
        self.notify()  # Wake up the worker if sleeping

    @staticmethod
    def _add_pair_to_queue(session, picture_id_a: str, picture_id_b: str):
        """
        Add a pair to the likeness work queue, ensuring uniqueness and order (a < b).
        """
        if picture_id_a == picture_id_b:
            return  # Don't add self-pairs
        a, b = sorted([picture_id_a, picture_id_b])
        # Check if already exists in queue or results

        from pixlvault.db_models.likeness_work_queue import LikenessWorkQueue
        from pixlvault.db_models.picture_likeness import PictureLikeness

        exists = session.exec(
            select(LikenessWorkQueue).where(
                (LikenessWorkQueue.picture_id_a == a)
                & (LikenessWorkQueue.picture_id_b == b)
            )
        ).first()
        if exists:
            return
        exists = session.exec(
            select(PictureLikeness).where(
                (PictureLikeness.picture_id_a == a)
                & (PictureLikeness.picture_id_b == b)
            )
        ).first()
        if exists:
            return
        session.add(LikenessWorkQueue(picture_id_a=a, picture_id_b=b))
        session.commit()

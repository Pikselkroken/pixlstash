import gc
import cv2
import math
import os
import sqlite3
import time

from .characters import Characters
from .picture_tagger import PictureTagger

from .database import DBPriority
from .logging import get_logger
from .picture_utils import PictureUtils
import pixlvault.picture_db_tools as db_tools
from .worker_registry import BaseWorker, WorkerType

logger = get_logger(__name__)


class FacialFeaturesWorker(BaseWorker):
    INSIGHTFACE_CLEANUP_TIMEOUT = 20  # seconds

    def __init__(
        self, db_connection, picture_tagger: PictureTagger, characters: Characters
    ):
        super().__init__(db_connection, picture_tagger, characters)
        self._skip_pictures = set()
        self._last_time_insightface_was_needed = None

    def worker_type(self) -> WorkerType:
        return WorkerType.FACIAL_FEATURES

    def _run(self):
        time.sleep(1.25)  # Stagger start times for multiple workers
        while not self._stop.is_set():
            try:
                data_updated = False

                start = time.time()
                # 1. Calculate face bboxes
                logger.debug("FacialFeaturesWorker: Starting iteration...")
                pics_needing_face_bboxes = self._find_pics_needing_face_bbox()
                time_after_fetch = time.time()

                logger.debug(
                    "FacialFeaturesWorker: It took %.2f seconds to fetch %d pictures needing face bboxes."
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
                    "FacialFeaturesWorker: It took %.2f seconds to calculate face bboxes."
                    % (time_after_calculate - time_after_fetch)
                )
                if not insightface_ok:
                    logger.warning(
                        "InsightFace model not available, skipping facial feature generation."
                    )
                    break

                data_updated |= bboxes_updated

                if self._stop.is_set():
                    break

                # 2. Generate facial features for pictures missing them
                missing_facial_features = self._fetch_missing_facial_features()
                logger.debug(
                    "FacialFeaturesWorker: It took %.2f seconds to fetch %d pictures needing facial features."
                    % (time.time() - time_after_calculate, len(missing_facial_features))
                )
                if missing_facial_features:
                    logger.info(
                        f"Generating facial features for {len(missing_facial_features)} pictures."
                    )
                    features_updated = self._generate_facial_features(
                        missing_facial_features
                    )
                    self._update_attributes(
                        missing_facial_features, ["facial_features"]
                    )
                    # Add new likeness work queue entries for all pairs involving these pictures
                    new_ids = [
                        pic.id
                        for pic in missing_facial_features
                        if pic.facial_features is not None
                    ]
                    logger.info(
                        "Adding %d new pictures to likeness work queue."
                        % (len(new_ids))
                    )
                    if new_ids:

                        def insert_likeness_work_queue(conn, new_ids):
                            cursor = conn.cursor()
                            placeholders = ",".join(["?"] * len(new_ids))
                            other_rows = cursor.execute(
                                f"SELECT id FROM pictures WHERE facial_features IS NOT NULL AND facial_features !='' AND id NOT IN ({placeholders})",
                                tuple(new_ids),
                            ).fetchall()
                            other_ids = [
                                row[0] if isinstance(row, tuple) else row["id"]
                                for row in other_rows
                            ]
                            # Always enforce sorted order for all pairs
                            pairs = set()
                            for new_id in new_ids:
                                for other_id in other_ids:
                                    a, b = (new_id, other_id)
                                    if a != b:
                                        pair = (min(a, b), max(a, b))
                                        pairs.add(pair)
                            # Also insert all pairs among new_ids themselves
                            for i in range(len(new_ids)):
                                for j in range(i + 1, len(new_ids)):
                                    a, b = new_ids[i], new_ids[j]
                                    pair = (min(a, b), max(a, b))
                                    pairs.add(pair)
                            logger.info(
                                "Adding %d new picture pairs to likeness work queue."
                                % (len(pairs))
                            )
                            # Log a sample of the pairs and their IDs
                            sample_pairs = list(pairs)[:10]
                            logger.info(f"Sample pairs to insert: {sample_pairs}")
                            if pairs:
                                for item in pairs:
                                    logger.debug(f"Pair to insert: {item}")
                                    # First delete any existing entries for these pairs to avoid duplicates
                                    cursor.execute(
                                        "DELETE FROM picture_likeness WHERE (picture_id_a = ? AND picture_id_b = ?)",
                                        (item[0], item[1]),
                                    )
                                logger.info(
                                    "Deleted existing entries for new pairs to avoid duplicates."
                                )
                                cursor.executemany(
                                    "INSERT OR IGNORE INTO likeness_work_queue (picture_id_a, picture_id_b) VALUES (?, ?)",
                                    list(pairs),
                                )
                                conn.commit()
                                # Log the number of rows in likeness_work_queue after insertion
                                cursor.execute(
                                    "SELECT COUNT(*) FROM likeness_work_queue"
                                )
                                count = cursor.fetchone()[0]
                                logger.info(
                                    f"likeness_work_queue now contains {count} rows after insertion."
                                )

                        self._db.submit_task(
                            insert_likeness_work_queue, new_ids, priority=DBPriority.LOW
                        ).result()
                    data_updated |= features_updated

                timing = time.time() - start

                if timing > 0.5:
                    logger.info(
                        "FacialFeaturesWorker: Done after %.2f seconds." % timing
                    )
                if not data_updated:
                    # Wait for the specified interval before checking again
                    logger.info(
                        "FacialFeaturesWorker: Sleeping after %.2f seconds. No work needed."
                        % timing
                    )
                    self._wait()
            except (sqlite3.OperationalError, OSError) as e:
                # Database file was deleted or connection lost during shutdown
                logger.debug(
                    f"Worker thread exiting due to DB error (likely shutdown): {e}"
                )
                break

    def _fetch_missing_facial_features(self):
        """Return PictureModels needing facial features using the provided connection."""
        rows_missing_facial_features = self._db.submit_task(
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
        ).result()
        return db_tools.from_batch_of_db_dicts(rows_missing_facial_features, [])

    def _find_pics_needing_face_bbox(self):
        """Find pictures that need face bounding boxes."""
        rows = self._db.submit_task(
            lambda conn: conn.execute(
                "SELECT * FROM pictures WHERE face_bbox IS NULL"
            ).fetchall()
        ).result()
        return db_tools.from_batch_of_db_dicts(rows, [])

    def _calculate_face_bboxes(self, pics) -> int:
        """Calculate face bounding box for pictures"""

        bboxes_updated = 0
        if not pics:
            if self._last_time_insightface_was_needed is not None:
                elapsed = time.time() - self._last_time_insightface_was_needed
                if elapsed > FacialFeaturesWorker.INSIGHTFACE_CLEANUP_TIMEOUT:
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
            self._insightface_app.prepare(ctx_id=-1, det_thresh=0.25)  # -1 = CPU only

        self._last_time_insightface_was_needed = time.time()

        for pic in pics:
            logger.debug("Looking for faces in picture %s", pic.id)

            # Skip it regardless of whether we succeed or fail
            self._skip_pictures.add(pic.id)

            if self._stop.is_set():
                return False, bboxes_updated

            try:
                file_path = pic.file_path
                ext = os.path.splitext(file_path)[1].lower()
                faces = []
                if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"]:
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
                        step = max(1, frame_count // 10)
                        found = False
                        for idx in range(0, frame_count, step):
                            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                            ret, frame = cap.read()
                            if not ret or frame is None:
                                logger.warning(
                                    f"Could not read frame {idx} from video: {file_path}"
                                )
                                continue
                            # Upscale frame by 2x using LANCZOS
                            upscaled = cv2.resize(
                                frame,
                                (frame.shape[1] * 2, frame.shape[0] * 2),
                                interpolation=cv2.INTER_LANCZOS4,
                            )
                            frame_faces = self._insightface_app.get(upscaled)
                            if frame_faces:
                                # Use the largest face in this frame
                                def face_area(f):
                                    x1, y1, x2, y2 = f.bbox
                                    return max(0, x2 - x1) * max(0, y2 - y1)

                                face = max(frame_faces, key=face_area)
                                # Scale bbox back to original frame coordinates
                                x1, y1, x2, y2 = [float(v) / 2.0 for v in face.bbox]
                                w = x2 - x1
                                h = y2 - y1
                                # Sensible size threshold (e.g., 32x32 in original scale)
                                if w >= 32 and h >= 32:
                                    # Store the scaled-down bbox in the face object for downstream use
                                    face.bbox = [x1, y1, x2, y2]
                                    faces = [face]
                                    found = True
                                    logger.info(
                                        f"Selected frame {idx} for face bbox in video {file_path} (w={w}, h={h}) after upscaling"
                                    )
                                    break
                        cap.release()
                        if not found:
                            logger.warning(
                                f"No suitable face found in any sampled frame for video: {file_path}"
                            )
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

                # Expand bbox by 10% on all sides
                x1 -= 0.1 * (x2 - x1)
                y1 -= 0.1 * (y2 - y1)
                x2 += 0.1 * (x2 - x1)
                y2 += 0.1 * (y2 - y1)

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

    def _generate_facial_features(self, missing_facial_features):
        """
        Generate facial features for a batch of PictureModel objects using PictureTagger.
        Returns the number of pictures updated.
        """
        updated = 0
        for pic in missing_facial_features:
            try:
                features = self._picture_tagger.generate_facial_features(pic)
                if features is not None:
                    logger.info("Got facial features for picture %s", pic.id)
                    pic.facial_features = features
                    updated += 1
                else:
                    pic.facial_features = bytes()
            except Exception as e:
                logger.error(f"Failed to generate facial features for {pic.id}: {e}")
        return updated

import gc
import os
import platform
import threading
import time
import warnings
import torch
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import cv2
from insightface.app import FaceAnalysis
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import NO_VALUE
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.inference.engine import InferenceEngine
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.face_utils import FaceUtils
from pixlstash.utils.insightface_batched import BatchedFaceRunner
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

# Suppress noisy FutureWarning from insightface's face_align.py about
# SimilarityTransform.estimate being deprecated in scikit-image >= 0.26.
# This warning is triggered at FaceAnalysis initialization time, not import time,
# so suppressing it here (after imports) is safe.
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    module="insightface",
)


logger = get_logger(__name__)


CROP_EXPAND_SCALE = 1.25

# Inference-only VRAM scratch when InsightFace models are already resident.
# The cold-load cost is covered by estimate_face_extraction_vram_mb the first
# time; subsequent tasks only pay for activation memory during a forward pass.
INSIGHTFACE_INFERENCE_SCRATCH_MB = 150


class FaceExtractionTask(BaseTask):
    """Task that extracts and persists face/hand detections for a picture batch.

    Args:
        database: Vault database instance.
        engine: :class:`~pixlstash.inference.engine.InferenceEngine` used for
            model settings.
        pictures: Pictures included in this extraction batch.
    """

    _global_insightface_app = None
    _global_cpu_insightface_app = None
    _cpu_insightface_lock = threading.Lock()
    # Number of FaceExtractionTask instances currently executing _run_task.
    # Models are only released when this drops to zero so paired tasks
    # (submitted together by the planner) do not pay a reload cost.
    _active_task_count: int = 0
    _active_task_lock = threading.Lock()
    # Semaphore that limits concurrent ONNX inference to 1 session at a time.
    # With INFLIGHT=2, Task 2's preload runs while Task 1 holds this semaphore,
    # so Task 2 can start ONNX immediately after Task 1 finishes — no I/O wait.
    # Uses the shared GPU queue — the single GPU worker ensures only one
    # face-extraction task runs at a time.  HIGH priority in the GPU queue
    # means face extraction is always preferred over tagging or embeddings.
    # gate so tagging/embedding tasks never compete while FE is active.
    # Timing feedback shared across instances so the finder can tune batch sizes.
    _feedback_lock = threading.Lock()
    _last_preload_s: float = 0.0
    _last_batch_size: int = 0

    def __init__(self, database, engine: InferenceEngine, pictures: list):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="FaceExtractionTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._engine = engine
        self._pictures = pictures or []
        self._insightface_app = None
        self._cpu_spillover_enabled = False
        self._stop_event = threading.Event()
        self._preloaded_images: dict = {}
        self._preload_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_cancel = threading.Event()
        self._preload_started_at: float | None = None
        self._preload_finished_at: float | None = None

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    def on_queued(self) -> None:
        """Start background image preload as soon as the task is queued."""
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return
        self._preload_cancel.clear()
        self._preload_started_at = time.perf_counter()
        self._preload_thread = threading.Thread(
            target=self._preload_images,
            name=f"FaceExtractionPreload-{self.id[:8]}",
            daemon=True,
        )
        self._preload_thread.start()

    def _preload_images(self) -> None:
        """Load every still image in the batch from disk into memory (background thread).

        Only handles still images; videos are skipped here and loaded
        synchronously in _extract_features because cv2.VideoCapture is not
        thread-safe.
        """

        def _load_one(pic):
            if self._preload_cancel.is_set():
                return None, None, 1.0
            try:
                file_path = str(
                    ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
                )
                ext = os.path.splitext(file_path)[1].lower()
                if ext not in self._IMAGE_EXTS:
                    return file_path, None, 1.0  # video — skip
                img, inv_scale = ImageUtils.load_image_bgr_reduced(
                    file_path, FaceExtractionTask.INFERENCE_MAX_SIDE
                )
                return file_path, img, inv_scale
            except Exception as exc:
                logger.debug(
                    "Preload failed for %s: %s",
                    getattr(pic, "file_path", None),
                    exc,
                )
                return None, None, 1.0

        preloaded: dict = {}
        n_workers = min(self._PRELOAD_WORKERS, max(1, len(self._pictures)))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_load_one, pic): pic for pic in self._pictures}
            for future in as_completed(futures):
                if self._preload_cancel.is_set():
                    break
                file_path, img, inv_scale = future.result()
                if file_path is not None:
                    preloaded[file_path] = (img, inv_scale)

        with self._preload_lock:
            self._preloaded_images = preloaded
        self._preload_finished_at = time.perf_counter()
        started_at = self._preload_started_at
        if started_at is not None:
            elapsed = self._preload_finished_at - started_at
            with FaceExtractionTask._feedback_lock:
                FaceExtractionTask._last_preload_s = elapsed
                FaceExtractionTask._last_batch_size = len(self._pictures)
            logger.debug(
                "[FACE_PRELOAD] task_id=%s preloaded=%s preload_s=%.3f",
                self.id,
                len(preloaded),
                elapsed,
            )

    def on_cancel(self) -> None:
        self._stop_event.set()
        self._preload_cancel.set()
        if self._preload_thread is not None:
            self._preload_thread.join(timeout=10)

    def _wait_for_preload(self) -> dict:
        """Block until the preload thread finishes and return the image cache."""
        if self._preload_thread is not None:
            self._preload_thread.join()
        with self._preload_lock:
            return dict(self._preloaded_images)

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": [], "picture_ids": []}

        _preload_wait_start = time.time()
        with FaceExtractionTask._active_task_lock:
            FaceExtractionTask._active_task_count += 1
        try:
            self._wait_for_preload()
            preload_wait_s = time.time() - _preload_wait_start
            all_changed: list = []
            pending_flushes: list = []
            for i in range(0, len(self._pictures), self._FLUSH_CHUNK_SIZE):
                chunk = self._pictures[i : i + self._FLUSH_CHUNK_SIZE]
                changed, bulk_faces, bulk_thumbnail_crops = self._extract_features(
                    chunk,
                    semaphore_wait_s=0.0,
                    preload_wait_s=preload_wait_s,
                )
                preload_wait_s = 0.0  # only charge it to the first chunk
                pending_flushes.append((bulk_faces, bulk_thumbnail_crops))
                all_changed.extend(changed or [])
                if self._stop_event.is_set():
                    break

            # Release preloaded numpy arrays immediately — each BGR image can
            # be several MB; a batch of 64 can be 500+ MB held unnecessarily.
            self._preloaded_images = {}

            for bulk_faces, bulk_thumbnail_crops in pending_flushes:
                self._flush_to_db(bulk_faces, bulk_thumbnail_crops)

            picture_ids = sorted(
                {pic_id for _, pic_id, _, _ in all_changed if pic_id is not None}
            )
        finally:
            with FaceExtractionTask._active_task_lock:
                FaceExtractionTask._active_task_count -= 1
                remaining = FaceExtractionTask._active_task_count

        if not self._should_keep_models_in_memory() and not self._cpu_spillover_enabled:
            if remaining == 0:
                # Only release when no other face extraction task is still running
                # so paired tasks (submitted together) do not pay a reload cost.
                self.release_detection_models()

        return {
            "changed_count": len(all_changed),
            "changed": all_changed,
            "picture_ids": picture_ids,
        }

    def _should_keep_models_in_memory(self) -> bool:
        return self._engine.keep_models_in_memory

    def estimated_vram_mb(self) -> int:
        if FaceExtractionTask._global_insightface_app is not None:
            # InsightFace models are already resident in VRAM; only charge for
            # the inference activation scratch, not the cold model-load cost.
            return INSIGHTFACE_INFERENCE_SCRATCH_MB
        fn = getattr(self._engine.face_embedding_workflow, "estimated_vram_mb", None)
        if callable(fn):
            try:
                return max(0, int(fn()))
            except Exception:
                return 0
        return 0

    @classmethod
    def release_detection_models(cls):
        cls._global_insightface_app = None

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        cls._trim_process_memory()

    @staticmethod
    def _trim_process_memory():
        if not platform.system().lower().startswith("linux"):
            return
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6")
            trim = getattr(libc, "malloc_trim", None)
            if trim is not None:
                trim(0)
        except Exception as exc:
            logger.debug("malloc_trim call failed: %s", exc)

    @classmethod
    def get_or_init_insightface(cls, engine, cpu_spillover: bool = False):
        """Return a ready-to-use InsightFace app, initialising it if necessary.

        This is the single authoritative initialisation path shared by
        :class:`FaceExtractionTask` and :class:`~pixlstash.tasks.face_detection_task.FaceDetectionTask`.
        It should be called from the GPU worker thread so that model loading is
        serialised and VRAM gating applies.

        Args:
            engine: :class:`~pixlstash.inference.engine.InferenceEngine` used to
                determine whether to use CUDA or CPU-only execution.
            cpu_spillover: When ``True`` use the CPU-only fallback app instead
                of the GPU app.

        Returns:
            An initialised :class:`insightface.app.FaceAnalysis` instance.
        """
        if cpu_spillover:
            with cls._cpu_insightface_lock:
                if cls._global_cpu_insightface_app is None:
                    logger.debug(
                        "FaceExtractionTask: initialising CPU spillover InsightFace app (ctx_id=-1)."
                    )
                    app = FaceAnalysis(providers=["CPUExecutionProvider"])
                    app.prepare(ctx_id=-1, det_thresh=0.25, det_size=(256, 256))
                    cls._global_cpu_insightface_app = app
                else:
                    logger.debug(
                        "FaceExtractionTask: reusing CPU spillover InsightFace app."
                    )
                return cls._global_cpu_insightface_app

        if cls._global_insightface_app is not None:
            logger.debug("Reusing global InsightFace app")
            return cls._global_insightface_app

        use_cuda = not engine.force_cpu and torch.cuda.is_available()
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if use_cuda
            else ["CPUExecutionProvider"]
        )
        logger.debug(
            "Initialising InsightFace with providers=%s (ctx_id=%d)",
            providers,
            0 if use_cuda else -1,
        )
        app = FaceAnalysis(providers=providers)
        app.prepare(
            ctx_id=0 if use_cuda else -1,
            det_thresh=0.25,
            det_size=(256, 256),
        )
        cls._global_insightface_app = app
        return app

    def _init_insightface_app(self):
        if self._insightface_app is not None:
            return
        self._insightface_app = FaceExtractionTask.get_or_init_insightface(
            self._engine, cpu_spillover=self._cpu_spillover_enabled
        )

    @staticmethod
    def _get_loaded_relationship(obj, name):
        try:
            state = sa_inspect(obj)
        except Exception:
            return False, None
        attr = state.attrs.get(name)
        if attr is None:
            return False, None
        loaded = attr.loaded_value
        if loaded is NO_VALUE:
            return False, None
        return True, loaded

    def _has_faces(self, picture_id: int) -> bool:
        def fetch(session):
            return (
                session.exec(
                    select(Face.id).where(Face.picture_id == picture_id)
                ).first()
                is not None
            )

        return bool(self._db.run_immediate_read_task(fetch))

    @staticmethod
    def detect_faces_in_images(insightface_app, images: list) -> list:
        """Run face detection and recognition on a list of BGR numpy arrays.

        This is the lowest-level detection entry point, intended to be called
        from ``_extract_features`` and from tests that need to exercise the
        InsightFace pipeline without a database or ``Picture`` objects.

        Images with either dimension below ``_MIN_DETECTION_DIM`` are skipped
        and returned as empty (no-face) results — they cannot contain a
        detectable face and would crash InsightFace's internal cv2.resize.

        Args:
            insightface_app: A prepared ``FaceAnalysis`` instance.
            images: BGR ``np.ndarray`` frames (any size), or ``None`` for
                positions that should yield an empty result.

        Returns:
            A list with one inner list of
            :class:`~pixlstash.utils.insightface_batched.FaceResult`
            per input image.
        """
        results: list = [[] for _ in images]
        safe_indices: list[int] = []
        safe_images: list = []
        for i, img in enumerate(images):
            if img is None:
                continue
            if min(img.shape[:2]) < FaceExtractionTask._MIN_DETECTION_DIM:
                logger.warning(
                    "Skipping face detection: image dimensions %dx%d are too small",
                    img.shape[1],
                    img.shape[0],
                )
                continue
            safe_indices.append(i)
            safe_images.append(img)
        if safe_images:
            runner = BatchedFaceRunner(insightface_app)
            try:
                batch_results = runner.run_batch(safe_images)
                for idx, res in zip(safe_indices, batch_results):
                    results[idx] = res
            except Exception as exc:
                logger.warning(
                    "Batch face detection failed (%s) for %d images "
                    "\u2014 treating all as having no faces: %s",
                    type(exc).__name__,
                    len(safe_images),
                    exc,
                )
        return results

    @staticmethod
    def _expand_bbox(bbox, frame_w, frame_h, scale):
        if bbox is None or len(bbox) != 4:
            return None
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        w = max(1.0, x2 - x1)
        h = max(1.0, y2 - y1)
        half_w = (w * scale) / 2.0
        half_h = (h * scale) / 2.0
        ex1 = int(max(0, min(frame_w - 1, round(cx - half_w))))
        ey1 = int(max(0, min(frame_h - 1, round(cy - half_h))))
        ex2 = int(max(0, min(frame_w, round(cx + half_w))))
        ey2 = int(max(0, min(frame_h, round(cy + half_h))))
        if ex2 <= ex1 or ey2 <= ey1:
            return None
        return [ex1, ey1, ex2, ey2]

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".avif"}
    _VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv"}
    # Minimum pixel dimension (width or height) required for InsightFace to run
    # without triggering an internal cv2.resize assertion failure.  RetinaFace
    # computes new_width = int(det_size / aspect_ratio); if aspect_ratio > 256
    # the result is 0, causing a cv2 assertion error.  Images with either
    # dimension below this threshold cannot contain a detectable face anyway.
    _MIN_DETECTION_DIM = 8
    # Workers for the image-preload pool.  Each worker only does I/O + PIL
    # decode (GIL released for JPEG), so 4 threads hide disk latency while the
    # main thread runs sequential InsightFace inference.
    _PRELOAD_WORKERS = 4
    # Maximum side length (px) used when loading images for inference.  Loading
    # at 960 px (2× the det_size=480) avoids decoding multi-megapixel originals
    # while still giving InsightFace enough resolution for accurate detection.
    INFERENCE_MAX_SIDE = 512
    # How many pictures to detect+recognise before committing results to the DB.
    # Smaller chunks → more frequent progress updates; larger → fewer ONNX
    # recognition calls but longer gaps between visible DB progress ticks.
    _FLUSH_CHUNK_SIZE = 100

    def _extract_features(
        self, pics, *, semaphore_wait_s: float = 0.0, preload_wait_s: float = 0.0
    ) -> List[tuple]:
        profile_enabled = os.getenv("PIXLSTASH_FEATURE_TIMING", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        batch_start = time.time()
        _init_start = time.time()
        self._init_insightface_app()
        init_s = time.time() - _init_start

        updates = []
        setup_s = 0.0
        batch_infer_s = 0.0
        precheck_s = 0.0
        image_load_s = 0.0
        inference_s = 0.0
        thumb_gen_s = 0.0
        thumb_write_s = 0.0
        processed_images = 0
        detected_faces_total = 0

        # Images are preloaded in on_queued() via a background thread so that
        # I/O runs while the previous task holds the inference semaphore.
        # Retrieve the completed dict here (instant — _run_task already joined
        # the preload thread via _wait_for_preload).
        preloaded = self._preloaded_images

        # ── Batched detection + recognition ─────────────────────────────────
        # Run detection (per-image, detector ONNX batch=1) and recognition
        # (batched — all crops from all images in one ONNX call) up front.
        # This replaces N×(detector + recogniser + landmark + genderage) calls
        # with N detector calls + 1 recogniser call.
        runner = BatchedFaceRunner(self._insightface_app)
        # Build the set of resolved paths for the current chunk only.  The
        # preloaded dict contains ALL task images; without this filter, every
        # chunk would run run_batch() on the full task and get_feat() on all
        # crops — O(chunks × images) wasted work and proportionally higher
        # peak GPU activation memory.
        _setup_start = time.time()
        chunk_paths: set[str] = {
            str(ImageUtils.resolve_picture_path(self._db.image_root, p.file_path))
            for p in pics
        }
        _batch_paths: list[str] = []
        _batch_imgs: list = []
        for _p, (_bimg, _) in preloaded.items():
            if (
                _p in chunk_paths
                and _bimg is not None
                and os.path.splitext(_p)[1].lower() in self._IMAGE_EXTS
            ):
                _batch_paths.append(_p)
                _batch_imgs.append(_bimg)
        setup_s += time.time() - _setup_start
        if _batch_imgs:
            _infer_start = time.time()
            _batch_results = FaceExtractionTask.detect_faces_in_images(
                self._insightface_app, _batch_imgs
            )
            batch_infer_s = time.time() - _infer_start
        else:
            _batch_results = []
        batched_detections: dict[str, list] = dict(zip(_batch_paths, _batch_results))

        # Accumulate all DB work so we can commit in a single run_task() call
        # instead of one per picture (which serialises on the write queue).
        bulk_faces: list[Face] = []  # Face rows to INSERT
        bulk_thumbnail_crops: list[tuple] = []  # (picture_id, crop_dict)
        # Deferred thumbnail work: generated in parallel after the inference loop.
        pending_thumb_work: list[
            tuple
        ] = []  # (pic_id, src_path, img, bboxes_loaded, inv_scale)

        _loop_start = time.time()
        for pic in pics:
            file_path = str(
                ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            )
            ext = os.path.splitext(file_path)[1].lower()
            if self._stop_event.is_set():
                logger.debug(
                    "FaceExtractionTask: stop requested, aborting after %d pictures.",
                    processed_images,
                )
                break
            pic_start = time.time()
            if pic.id is None:
                logger.warning(
                    "Skipping feature extraction for %s: missing picture id",
                    getattr(pic, "file_path", "<unknown>"),
                )
                continue

            # ── precheck ────────────────────────────────────────────────
            check_start = time.time()
            faces_loaded, faces_value = self._get_loaded_relationship(pic, "faces")
            if faces_loaded:
                need_faces = not faces_value
            else:
                need_faces = not self._has_faces(pic.id)
            precheck_s += time.time() - check_start
            logger.debug("Looking for faces in picture %s %s", pic.id, pic.description)

            face_objects = []

            if ext in self._IMAGE_EXTS:
                read_start = time.time()
                preloaded_entry = preloaded.get(file_path)
                if preloaded_entry is not None:
                    img, inv_scale = preloaded_entry
                else:
                    img, inv_scale = ImageUtils.load_image_bgr_reduced(
                        file_path, self.INFERENCE_MAX_SIDE
                    )
                image_load_s += time.time() - read_start

                if img is not None and need_faces:
                    faces = batched_detections.get(file_path)
                    if faces is None:
                        # Image was loaded on-demand (not in preloaded cache).
                        _infer_start = time.time()
                        faces = FaceExtractionTask.detect_faces_in_images(
                            self._insightface_app, [img]
                        )[0]
                        inference_s += time.time() - _infer_start
                    detected_faces_total += len(faces)
                    logger.debug("Found %d faces in image %s", len(faces), file_path)
                    face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
                    for face in faces:
                        expanded_bbox = Face.expand_face_bbox(
                            face.bbox,
                            img.shape[1],
                            img.shape[0],
                            face_expand_fraction,
                        )
                        # Scale bbox from loaded-image space to original pixel space.
                        if inv_scale != 1.0 and expanded_bbox:
                            expanded_bbox = [v * inv_scale for v in expanded_bbox]
                        features_bytes = None
                        if hasattr(face, "embedding") and face.embedding is not None:
                            features_bytes = face.embedding.astype("float32").tobytes()
                        face_objects.append(
                            Face(
                                picture_id=pic.id,
                                face_index=-1,
                                bbox=expanded_bbox,
                                character_id=None,
                                frame_index=0,
                                features=features_bytes,
                            )
                        )
                    if face_objects:
                        # Pass bboxes in loaded-image space (matching img dimensions).
                        bboxes_loaded = [
                            [v / inv_scale for v in f.bbox]
                            if (inv_scale != 1.0 and f.bbox)
                            else f.bbox
                            for f in face_objects
                            if f.bbox
                        ]
                        if bboxes_loaded:
                            pending_thumb_work.append(
                                (pic.id, pic.file_path, img, bboxes_loaded, inv_scale)
                            )

            elif ext in self._VIDEO_EXTS:
                if need_faces:
                    read_start = time.time()
                    cap = cv2.VideoCapture(file_path)
                    image_load_s += time.time() - read_start
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if frame_count < 1:
                        logger.warning("No frames found in video: %s", file_path)
                        cap.release()
                    else:
                        first_frame = None
                        first_bboxes = []
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            first_frame = frame
                            _infer_start = time.time()
                            frame_faces = runner.run_batch([frame])[0]
                            inference_s += time.time() - _infer_start
                            detected_faces_total += len(frame_faces)
                            face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
                            for face in frame_faces:
                                expanded_bbox = Face.expand_face_bbox(
                                    face.bbox,
                                    frame.shape[1],
                                    frame.shape[0],
                                    face_expand_fraction,
                                )
                                features_bytes = None
                                if (
                                    hasattr(face, "embedding")
                                    and face.embedding is not None
                                ):
                                    features_bytes = face.embedding.astype(
                                        "float32"
                                    ).tobytes()
                                else:
                                    logger.warning(
                                        "Face embedding missing for face in video %s, frame 0",
                                        file_path,
                                    )
                                first_bboxes.append(expanded_bbox)
                                face_objects.append(
                                    Face(
                                        picture_id=pic.id,
                                        face_index=-1,
                                        bbox=expanded_bbox,
                                        character_id=None,
                                        frame_index=0,
                                        features=features_bytes,
                                    )
                                )
                        step = max(1, frame_count // 3)
                        for frame_index in range(step, frame_count, step):
                            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                            ret, frame = cap.read()
                            if not ret or frame is None:
                                logger.warning(
                                    "Could not read frame %s from video: %s",
                                    frame_index,
                                    file_path,
                                )
                                continue
                            _infer_start = time.time()
                            frame_faces = runner.run_batch([frame])[0]
                            inference_s += time.time() - _infer_start
                            detected_faces_total += len(frame_faces)
                            face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
                            for face in frame_faces:
                                expanded_bbox = Face.expand_face_bbox(
                                    face.bbox,
                                    frame.shape[1],
                                    frame.shape[0],
                                    face_expand_fraction,
                                )
                                features_bytes = None
                                if (
                                    hasattr(face, "embedding")
                                    and face.embedding is not None
                                ):
                                    features_bytes = face.embedding.astype(
                                        "float32"
                                    ).tobytes()
                                else:
                                    logger.warning(
                                        "Face embedding missing for face in video %s, frame %s",
                                        file_path,
                                        frame_index,
                                    )
                                face_objects.append(
                                    Face(
                                        picture_id=pic.id,
                                        face_index=-1,
                                        bbox=expanded_bbox,
                                        character_id=None,
                                        frame_index=frame_index,
                                        features=features_bytes,
                                    )
                                )
                        cap.release()
                        if first_frame is not None and first_bboxes:
                            pending_thumb_work.append(
                                (pic.id, pic.file_path, first_frame, first_bboxes, 1.0)
                            )
            else:
                logger.warning(
                    "Unsupported file extension for feature extraction: %s",
                    file_path,
                )

            face_objects.sort(
                key=lambda f: (
                    (f.bbox[1], f.bbox[0], f.bbox[3], f.bbox[2])
                    if f.bbox
                    else (0, 0, 0, 0)
                )
            )
            for idx, face_obj in enumerate(face_objects):
                face_obj.face_index = idx

            if need_faces:
                if not face_objects:
                    logger.warning(
                        "No face found in %s for picture %s. Inserting sentinel record.",
                        file_path,
                        pic.id,
                    )
                    # Sentinel face — no bbox, face_index=-1
                    bulk_faces.append(
                        Face(
                            picture_id=pic.id,
                            face_index=-1,
                            character_id=None,
                            bbox=None,
                        )
                    )
                else:
                    bulk_faces.extend(face_objects)

                updates.append(
                    (Picture, pic.id, "faces", None)
                )  # bulk insert determines final face ids; waiters must re-read from DB

            processed_images += 1
            if profile_enabled and (time.time() - pic_start) > 0.75:
                logger.info(
                    "[FEATURE_TIMING] Slow image id=%s path=%s elapsed=%.3fs need_faces=%s faces=%s",
                    pic.id,
                    pic.file_path,
                    time.time() - pic_start,
                    need_faces,
                    len(face_objects),
                )
        loop_s = time.time() - _loop_start

        # ── Parallel thumbnail generation ─────────────────────────────────
        # All inference is already done; thumbnail generation is pure CPU work
        # (cv2 crop + resize + JPEG encode).  Run it across a thread pool so the
        # ~0.3 s serial cost becomes ~0.3 s / n_workers.
        if pending_thumb_work:
            _thumb_gen_start = time.time()
            thumb_results: dict[int, tuple] = {}  # pic_id → (bytes, crop, src_path)
            with ThreadPoolExecutor(max_workers=self._PRELOAD_WORKERS) as pool:
                futures = {
                    pool.submit(
                        FaceUtils.generate_face_weighted_thumbnail_with_crop,
                        img,
                        bboxes,
                        256,
                        (256, 256),
                    ): (pic_id, src_path, inv_scale)
                    for pic_id, src_path, img, bboxes, inv_scale in pending_thumb_work
                }
                for fut in as_completed(futures):
                    pic_id, src_path, inv_scale = futures[fut]
                    try:
                        tb, tc = fut.result()
                    except Exception as exc:
                        logger.warning(
                            "Thumbnail generation failed for picture %s: %s",
                            pic_id,
                            exc,
                        )
                        continue
                    if not tb or not tc:
                        continue
                    if inv_scale != 1.0:
                        tc = {
                            "left": int(round(tc["left"] * inv_scale)),
                            "top": int(round(tc["top"] * inv_scale)),
                            "side": int(round(tc["side"] * inv_scale)),
                        }
                    thumb_results[pic_id] = (tb, tc, src_path)
            thumb_gen_s += time.time() - _thumb_gen_start

            _thumb_write_start = time.time()
            for pic_id, (tb, tc, src_path) in thumb_results.items():
                saved = ImageUtils.write_thumbnail_bytes(
                    self._db.image_root, src_path, tb
                )
                if not saved:
                    logger.warning("Failed to persist thumbnail for picture %s", pic_id)
                bulk_thumbnail_crops.append((pic_id, tc))
            thumb_write_s += time.time() - _thumb_write_start

        if profile_enabled:
            elapsed = time.time() - batch_start
            logger.info(
                "[FEATURE_TIMING] batch=%s processed=%s updates=%s faces=%s elapsed=%.3fs semaphore_wait=%.3fs preload_wait=%.3fs init=%.3fs setup=%.3fs batch_infer=%.3fs loop=%.3fs(precheck=%.3fs load=%.3fs infer=%.3fs) thumb_gen=%.3fs thumb_write=%.3fs",
                len(pics),
                processed_images,
                len(updates),
                detected_faces_total,
                elapsed,
                semaphore_wait_s,
                preload_wait_s,
                init_s,
                setup_s,
                batch_infer_s,
                loop_s,
                precheck_s,
                image_load_s,
                inference_s,
                thumb_gen_s,
                thumb_write_s,
            )

        return updates, bulk_faces, bulk_thumbnail_crops

    def _flush_to_db(
        self,
        bulk_faces: list,
        bulk_thumbnail_crops: list,
    ) -> None:
        """Write accumulated face rows and thumbnail crops to the database.

        Called AFTER releasing the inference semaphore so that the SQLite
        commit does not block the next task from starting inference.
        """
        if not bulk_faces and not bulk_thumbnail_crops:
            return

        def bulk_write(session, faces, crops):
            for face in faces:
                session.add(face)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                logger.warning(
                    "Bulk face insert failed (IntegrityError) — skipping batch."
                )
                return

            for picture_id, crop in crops:
                picture = session.get(Picture, picture_id)
                if picture is None:
                    continue
                if crop:
                    picture.thumbnail_left = crop.get("left")
                    picture.thumbnail_top = crop.get("top")
                    picture.thumbnail_side = crop.get("side")
                session.add(picture)
            try:
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.warning("Bulk thumbnail crop update failed: %s", exc)

        # Fire-and-forget: submit to the DB queue without blocking the worker
        # thread.  Errors are logged inside bulk_write.  Freeing the worker
        # thread immediately lets it pick up the next queued task so inference
        # can restart before the SQLite commit finishes (1-3 s for 512 rows).
        self._db.submit_task(
            bulk_write, bulk_faces, bulk_thumbnail_crops, priority=DBPriority.HIGH
        )

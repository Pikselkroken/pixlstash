import gc
import os
import platform
import threading
import time
import warnings
import weakref
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import cv2
import numpy as np
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import NO_VALUE
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.inference.engine import InferenceEngine
from pixlstash.inference.vram_budget import ORT_ARENA_SHARE
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.face_utils import FaceUtils
from pixlstash.utils.insightface_batched import BatchedFaceRunner
from pixlstash.utils.insightface_model_utils import (
    DEFAULT_MODEL_PACK,
    ensure_model_pack_available,
    insightface_root,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority
from pixlstash.utils.vram_utils import empty_cuda_cache, is_vram_oom

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
    # Live task instances that hold a reference to an InsightFace app. The app is
    # an onnxruntime session whose VRAM lives in ORT's own CUDA arena (torch's
    # empty_cache cannot free it) - the arena is only returned to the driver when
    # the last reference to the session is dropped and it is garbage-collected.
    # Nulling only the class globals is not enough: each running task also holds
    # self._insightface_app pointing at the same object, so release must clear
    # those too or nvidia-smi never moves. WeakSet so we never keep a task alive.
    _app_instances: "weakref.WeakSet" = weakref.WeakSet()
    _app_instances_lock = threading.Lock()
    # Number of FaceExtractionTask instances currently executing _run_task.
    # Models are only released when this drops to zero so paired tasks
    # (submitted together by the planner) do not pay a reload cost.
    _active_task_count: int = 0
    _active_task_lock = threading.Lock()
    # Semaphore that limits concurrent ONNX inference to 1 session at a time.
    # With INFLIGHT=2, Task 2's preload runs while Task 1 holds this semaphore,
    # so Task 2 can start ONNX immediately after Task 1 finishes - no I/O wait.
    # Uses the shared GPU queue - the single GPU worker ensures only one
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
        # The DB writes this task submitted and did not wait for; the finder
        # releases the pictures' claims once every one of them has landed.
        self.pending_writes: list = []
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
        """Load every picture in the batch from disk into memory (background thread).

        Stills are stored as ``(bgr_image, inv_scale)``; videos as
        ``(frames, 1.0)`` where ``frames`` is the ``(frame_index, bgr_frame)``
        list :meth:`_read_video_frames` selects. Each call opens its own
        ``cv2.VideoCapture`` - the thread-safety concern is sharing one capture,
        not decoding in parallel. A video that fails to decode here gets no
        entry, so the batch loop reads it synchronously instead.
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
                    if ext not in self._VIDEO_EXTS:
                        return None, None, 1.0
                    try:
                        return (file_path, *self._read_video_frames(file_path))
                    except Exception as exc:
                        logger.warning(
                            "Video preload failed for %s (%s: %s); "
                            "falling back to synchronous decode",
                            file_path,
                            type(exc).__name__,
                            exc,
                        )
                        return None, None, 1.0
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

            # Release preloaded numpy arrays immediately - each BGR image can
            # be several MB; a batch of 64 can be 500+ MB held unnecessarily.
            self._preloaded_images = {}

            # Submitted, not awaited. Waiting here parked the single GPU
            # worker behind whatever LOW write the five CPU stages had on the
            # writer thread - a measured pass ran at gpu_busy=0.08. But the
            # pictures must stay CLAIMED until the rows land, or the next sweep
            # re-offers them and a second detection pass hits the unique face
            # key (seen as 7 face tasks for 12 pictures). So the worker is
            # freed now and the finder holds the claims on these futures:
            # see MissingFaceExtractionFinder.on_task_complete.
            self.pending_writes = [
                future
                for future in (
                    self._flush_to_db(bulk_faces, bulk_thumbnail_crops)
                    for bulk_faces, bulk_thumbnail_crops in pending_flushes
                )
                if future is not None
            ]

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
            except Exception as exc:
                logger.debug(
                    "FaceExtractionTask: VRAM estimate failed; assuming 0: %s", exc
                )
                return 0
        return 0

    @classmethod
    def release_detection_models(cls):
        # Drop the class globals AND every live task's per-instance reference.
        # ORT frees the CUDA arena only when the session is garbage-collected,
        # so a single surviving reference keeps the VRAM resident and makes the
        # "keep models in memory" toggle appear to do nothing in nvidia-smi.
        cls._global_insightface_app = None
        cls._global_cpu_insightface_app = None
        with cls._app_instances_lock:
            for inst in list(cls._app_instances):
                inst._insightface_app = None
            cls._app_instances.clear()

        gc.collect()
        empty_cuda_cache()
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

        Raises:
            ValueError: If the engine's ``insightface_model_pack`` is not a known
                pack (fail-closed; see
                :func:`~pixlstash.utils.insightface_model_utils.validate_model_pack`).
            RuntimeError: If a required model-pack download fails.
        """
        # Local imports: insightface (which drags in onnxruntime) and torch each
        # cost seconds to import, and this module is on the server's import
        # path. Both are only needed once face detection actually initialises.
        import torch
        from insightface.app import FaceAnalysis

        # Validate the configured pack and provision it on disk (no-op for
        # auto-downloaded packs like buffalo_l) before constructing FaceAnalysis.
        # Fails closed for unknown names instead of letting FaceAnalysis try to
        # fetch an arbitrary zoo entry.
        model_pack = getattr(engine, "insightface_model_pack", DEFAULT_MODEL_PACK)
        ensure_model_pack_available(model_pack)

        # Passed explicitly rather than left to FaceAnalysis's own
        # ``~/.insightface`` default: the root is a setting, so the owner can
        # keep several gigabytes of packs off the system drive. It is the same
        # value `ensure_model_pack_available` just downloaded into, which is the
        # whole point of both going through `insightface_root()`.
        root = insightface_root()

        if cpu_spillover:
            with cls._cpu_insightface_lock:
                if cls._global_cpu_insightface_app is None:
                    logger.debug(
                        "FaceExtractionTask: initialising CPU spillover InsightFace "
                        "app (ctx_id=-1, pack=%s).",
                        model_pack,
                    )
                    app = FaceAnalysis(
                        name=model_pack,
                        root=root,
                        providers=["CPUExecutionProvider"],
                    )
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
        # One dict per provider; the CUDA one bounds each of the five ORT
        # sessions' arenas so the pack can stay resident for a pass.
        provider_options = (
            [
                engine.vram_budget.ort_cuda_provider_options(
                    ORT_ARENA_SHARE["insightface_session"]
                ),
                {},
            ]
            if use_cuda
            else [{}]
        )
        logger.debug(
            "Initialising InsightFace with providers=%s options=%s (ctx_id=%d, pack=%s, root=%s)",
            providers,
            provider_options,
            0 if use_cuda else -1,
            model_pack,
            root,
        )
        app = FaceAnalysis(
            name=model_pack,
            root=root,
            providers=providers,
            provider_options=provider_options,
        )
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
        # Track this holder so release_detection_models can drop every reference
        # to the ORT session, not just the class global.
        with FaceExtractionTask._app_instances_lock:
            FaceExtractionTask._app_instances.add(self)

    @staticmethod
    def _get_loaded_relationship(obj, name):
        try:
            state = sa_inspect(obj)
        except Exception:
            # Deliberate best-effort probe (allowlisted in the except-hygiene
            # guardrail): a non-inspectable object simply is not a loaded
            # relationship, so (False, None) IS the answer, not an error to log.
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
        and returned as empty (no-face) results - they cannot contain a
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
                if is_vram_oom(exc):
                    # Never "no faces". Inside FaceExtractionTask this result
                    # becomes a sentinel row per picture, and sentinels are
                    # never re-scanned - so a full card (another process
                    # holding VRAM) would silently and permanently mark every
                    # picture in the batch as faceless. The runner retries an
                    # OOM; let it.
                    raise
                logger.warning(
                    "Batch face detection failed (%s) for %d images "
                    "\u2014 treating all as having no faces: %s",
                    type(exc).__name__,
                    len(safe_images),
                    exc,
                )
        return results

    #: A batch slower than this logs its own timing breakdown at INFO, without
    #: anyone having had to set PIXLSTASH_FEATURE_TIMING in advance. Well above
    #: a healthy batch (a hundred JPEGs run in ~1-2 s on a warm GPU) so an
    #: ordinary library never sees it, and well below the "is it stuck?"
    #: threshold of a person watching a log.
    SLOW_BATCH_LOG_S = 5.0

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

    @staticmethod
    def _read_video_frames(
        file_path: str,
    ) -> tuple[list[tuple[int, np.ndarray]], float]:
        """Return ``(frames, inv_scale)`` - the frames face detection samples.

        ``frames`` is ``(frame_index, bgr_frame)`` pairs, each reduced so its
        longest side is at most ``INFERENCE_MAX_SIDE`` (as stills are);
        ``inv_scale`` maps a reduced-frame coordinate back to source pixels and
        is one number per clip because every frame shares the clip's size.
        Frame 0 plus every ``max(1, frame_count // 3)``-th frame after it, read
        by seeking. Sequential decode was measured against this on real HEVC
        clips: ~25 % faster on clips under ~100 frames, but linear in clip
        length (12 s for a 14k-frame clip against 0.17 s seeking), and it
        returns different pixels on HEVC than the seek does. Seeking keeps the
        frames the detector has always received.
        """
        cap = cv2.VideoCapture(file_path)
        try:
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count < 1:
                logger.warning("No frames found in video: %s", file_path)
                return [], 1.0
            frames: list[tuple[int, np.ndarray]] = []
            inv_scale = 1.0

            def reduced(frame: np.ndarray) -> np.ndarray:
                nonlocal inv_scale
                h, w = frame.shape[:2]
                if max(h, w) <= FaceExtractionTask.INFERENCE_MAX_SIDE:
                    return frame
                scale = FaceExtractionTask.INFERENCE_MAX_SIDE / float(max(h, w))
                new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
                inv_scale = w / float(new_w)
                return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
            if ret and frame is not None:
                frames.append((0, reduced(frame)))
            step = max(1, frame_count // 3)
            for frame_index in range(step, frame_count, step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ret, frame = cap.read()
                if not ret or frame is None:
                    # DEBUG, not WARNING: CAP_PROP_FRAME_COUNT is an estimate
                    # for HEVC - a 47-frame iPhone clip reports 47 and cannot
                    # read frame 45 - so the last sample often lands past the
                    # end. The clip still gets its other frames.
                    logger.debug(
                        "Could not read frame %s of %s from video %s",
                        frame_index,
                        frame_count,
                        file_path,
                    )
                    continue
                frames.append((frame_index, reduced(frame)))
            return frames, inv_scale
        finally:
            cap.release()

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

        # Tag every face written in this batch with the pack that produced it so
        # the FACE_MODEL_REFRESH finder can detect stale embeddings on a pack
        # change. Read once per batch - the engine pack is immutable per run.
        model_pack = getattr(self._engine, "insightface_model_pack", DEFAULT_MODEL_PACK)

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
        # Retrieve the completed dict here (instant - _run_task already joined
        # the preload thread via _wait_for_preload).
        preloaded = self._preloaded_images

        # ── Batched detection + recognition ─────────────────────────────────
        # Run detection (per-image, detector ONNX batch=1) and recognition
        # (batched - all crops from all images in one ONNX call) up front.
        # This replaces N×(detector + recogniser + landmark + genderage) calls
        # with N detector calls + 1 recogniser call.
        runner = BatchedFaceRunner(self._insightface_app)
        # Build the set of resolved paths for the current chunk only.  The
        # preloaded dict contains ALL task images; without this filter, every
        # chunk would run run_batch() on the full task and get_feat() on all
        # crops - O(chunks × images) wasted work and proportionally higher
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
                                model_pack=model_pack,
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
                    preloaded_entry = preloaded.get(file_path)
                    if preloaded_entry is not None:
                        frames, inv_scale = preloaded_entry
                    else:
                        # Not preloaded (cancelled, or decode failed in the
                        # pool - already logged): read on the worker thread.
                        read_start = time.time()
                        frames, inv_scale = self._read_video_frames(file_path)
                        image_load_s += time.time() - read_start
                    first_frame = None
                    first_bboxes = []
                    if frames:
                        _infer_start = time.time()
                        per_frame_faces = runner.run_batch([f for _, f in frames])
                        inference_s += time.time() - _infer_start
                    else:
                        per_frame_faces = []
                    face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)
                    for (frame_index, frame), frame_faces in zip(
                        frames, per_frame_faces
                    ):
                        if frame_index == 0:
                            first_frame = frame
                        detected_faces_total += len(frame_faces)
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
                            if frame_index == 0:
                                # Loaded-frame space, matching first_frame.
                                first_bboxes.append(expanded_bbox)
                            # Scale bbox from loaded-frame space to source pixels.
                            if inv_scale != 1.0 and expanded_bbox:
                                expanded_bbox = [v * inv_scale for v in expanded_bbox]
                            face_objects.append(
                                Face(
                                    picture_id=pic.id,
                                    face_index=-1,
                                    bbox=expanded_bbox,
                                    character_id=None,
                                    frame_index=frame_index,
                                    features=features_bytes,
                                    model_pack=model_pack,
                                )
                            )
                    if first_frame is not None and first_bboxes:
                        pending_thumb_work.append(
                            (
                                pic.id,
                                pic.file_path,
                                first_frame,
                                first_bboxes,
                                inv_scale,
                            )
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
                    # Not a warning: most pictures have no face in them, and
                    # a warning per picture buried the real ones.
                    logger.debug("No faces found in %s (picture %s)", file_path, pic.id)
                    # Sentinel face - no bbox, face_index=-1
                    bulk_faces.append(
                        Face(
                            picture_id=pic.id,
                            face_index=-1,
                            character_id=None,
                            bbox=None,
                            model_pack=model_pack,
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

        # ── Square-crop rectangle update ──────────────────────────────────
        # The whole-frame AR bitmap is face-INDEPENDENT, so detecting faces does
        # not change the thumbnail file (written at import / by the finder). We
        # only recompute the face-weighted SQUARE-CROP rectangle in bitmap space -
        # pure geometry from the exif-corrected source dimensions and the detected
        # boxes, with no image re-encode or file write.
        if pending_thumb_work:
            _thumb_gen_start = time.time()
            for (
                pic_id,
                _src_path,
                img,
                bboxes_loaded,
                inv_scale,
            ) in pending_thumb_work:
                try:
                    loaded_h, loaded_w = img.shape[:2]
                except Exception as exc:
                    logger.debug(
                        "FaceExtractionTask: skipping square-crop update for "
                        "picture %s; unreadable image array: %s",
                        pic_id,
                        exc,
                    )
                    continue
                if loaded_w <= 0 or loaded_h <= 0:
                    continue
                # Recover exif-corrected source dims (the space the boxes live in)
                # from the loaded image and its inverse scale; the bitmap is a
                # uniform scale of the same source, so its aspect ratio matches.
                src_w = max(1, int(round(loaded_w * inv_scale)))
                src_h = max(1, int(round(loaded_h * inv_scale)))
                dims = ImageUtils.thumbnail_bitmap_size(src_w, src_h)
                if dims is None:
                    continue
                bmp_w, bmp_h = dims
                scale = bmp_w / float(loaded_w)
                faces_bitmap = [
                    [v * scale for v in b] for b in bboxes_loaded if b and len(b) == 4
                ]
                crop_x, crop_y, crop_side = FaceUtils.square_crop_rect(
                    bmp_w, bmp_h, faces_bitmap
                )
                bulk_thumbnail_crops.append(
                    (
                        pic_id,
                        {
                            "width": bmp_w,
                            "height": bmp_h,
                            "x": crop_x,
                            "y": crop_y,
                            "side": crop_side,
                        },
                    )
                )
            thumb_gen_s += time.time() - _thumb_gen_start

        elapsed = time.time() - batch_start
        # Always say why a slow batch was slow. The breakdown existed but only
        # behind an env var nobody sets before they need it, so the log of a
        # library that felt stalled showed a burst of sentinel warnings, minutes
        # of silence, and no way to tell whether that silence was work (a
        # hundred HEIC frames decoded, a video seeked three times) or the
        # planner idling between batches. One line per slow batch answers that,
        # and a fast batch stays quiet.
        if profile_enabled or elapsed >= self.SLOW_BATCH_LOG_S:
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
    ):
        """Submit the face rows and thumbnail crops to the writer; return the future.

        Called AFTER releasing the inference semaphore so that the SQLite
        commit does not block the next task from starting inference. The
        caller waits on the returned future before the task completes - see
        `_run_task` for why that wait is load-bearing.
        """
        if not bulk_faces and not bulk_thumbnail_crops:
            return None

        def bulk_write(session, faces, crops):
            for face in faces:
                session.add(face)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                logger.warning(
                    "Bulk face insert failed (IntegrityError) - skipping batch."
                )
                return

            for picture_id, crop in crops:
                picture = session.get(Picture, picture_id)
                if picture is None:
                    continue
                if crop:
                    picture.thumbnail_width = crop.get("width")
                    picture.thumbnail_height = crop.get("height")
                    picture.square_crop_x = crop.get("x")
                    picture.square_crop_y = crop.get("y")
                    picture.square_crop_side = crop.get("side")
                session.add(picture)
            try:
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.warning("Bulk thumbnail crop update failed: %s", exc)

        # Errors are logged inside bulk_write. HIGH priority so the task's own
        # completion is not queued behind the LOW writes of other stages.
        return self._db.submit_task(
            bulk_write, bulk_faces, bulk_thumbnail_crops, priority=DBPriority.HIGH
        )

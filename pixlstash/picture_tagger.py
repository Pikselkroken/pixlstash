#################################################################
# Adapted from Kohya_ss https://github.com/kohya-ss/sd-scripts/ #
# Under the Apache 2.0 License                                  #
# https://github.com/kohya-ss/sd-scripts/blob/main/LICENSE.md   #
#################################################################
from typing import Optional
import numpy as np
import os
import threading
import torch

from platformdirs import user_data_dir

from .pixl_logging import get_logger
from pixlstash.db_models.picture import Picture
from pixlstash.utils.model_utils import (
    env_int,
    env_float,
    clean_asset_name,
    trim_process_memory,
)
from pixlstash.utils.vram_utils import query_total_vram_mb, vram_limited_batch_cap
from pixlstash.utils.service.caption_utils import (
    merge_video_frame_tags,
    filter_texts,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.face_utils import FaceUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.tagger_plugins.clip_service import ClipService
from pixlstash.tagger_plugins.sbert import SBertService
from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
from pixlstash.tagger_plugins.wd14 import WD14Service
from pixlstash.tagger_plugins.florence2 import (
    Florence2Service,
    FLORENCE_BASE_VRAM_MB,
    FLORENCE_PER_IMAGE_VRAM_MB,
)

logger = get_logger(__name__)


MODEL_DIR = os.path.join(user_data_dir("pixlstash"), "downloaded_models")
MAX_CONCURRENT_IMAGES_GPU = env_int("PIXLSTASH_TAGGER_MAX_CONCURRENT_GPU", 64)
MAX_CONCURRENT_IMAGES_CPU = env_int("PIXLSTASH_TAGGER_MAX_CONCURRENT_CPU", 8)
DEFAULT_MAX_VRAM_GB = env_float("PIXLSTASH_MAX_VRAM_GB", None)

# Approximate VRAM footprints for non-tagging GPU pipelines
INSIGHTFACE_VRAM_MB = 400  # RetinaFace + ArcFace models via CUDA provider
# FLORENCE_BASE_VRAM_MB and FLORENCE_PER_IMAGE_VRAM_MB are imported from
# pixlstash.tagger_plugins.florence2 (defined there alongside the service).


class PictureTagger:
    """
    Generates natural captions using Florence-2.
    Also generates tags with WD14 and corrects them using the captions provided by Florence-2.
    Generates text embeddings using OpenCLIP.
    """

    FAST_CAPTIONS = False  # Class variable to control fast caption mode
    FORCE_CPU = False  # Class variable to control CPU inference

    def __init__(
        self,
        force_download=False,
        silent=True,
        device=None,
        image_root: str = None,
    ):
        logger.debug("Initializing PictureTagger...")
        self._silent = silent
        self._image_root = image_root
        self._model_init_lock = threading.Lock()
        self._models_ready = True
        self._keep_models_in_memory = True

        # Store device for both CLIP and ONNX
        if PictureTagger.FORCE_CPU:
            logger.warning("Forcing CPU inference for PictureTagger.")
            self._device = "cpu"
        else:
            if device is not None:
                self._device = device
            else:
                self._device = "cuda" if torch.cuda.is_available() else "cpu"

        if self._device == "cpu" and not PictureTagger.FORCE_CPU and not self._silent:
            if torch.cuda.is_available():
                logger.warning(
                    "PictureTagger initialising with CPU inference despite CUDA being available "
                    "(device was explicitly set to cpu)."
                )
            else:
                logger.warning(
                    "PictureTagger initialising with CPU inference (CUDA is not available)."
                )

        logger.debug(f"PictureTagger initialised with device: {self._device}")
        self._use_custom_tagger = True
        self._use_wd14_tagger = True
        self._custom_tagger_threshold_offset = 0.0
        self._max_vram_usage_mb: int | None = None

        self._clip_service = ClipService(device=self._device)
        self._sbert_service = SBertService(device=self._device)

        self._custom_service = PixlStashTaggerService(
            device=self._device,
            model_dir=MODEL_DIR,
            batch_size_fn=self._effective_custom_batch_size,
        )
        if self._custom_service.needs_download():
            self._custom_service.download()
        if not os.path.isfile(self._custom_service._model_path) or not os.path.isfile(
            self._custom_service._meta_path
        ):
            logger.warning(
                "Custom tagger not found at %s, skipping initialization.",
                self._custom_service._model_path,
            )
            self._use_custom_tagger = False
        else:
            logger.info(
                "Custom tagger loaded (version %d) from %s",
                self._custom_service.version(),
                self._custom_service._model_path,
            )

        self._wd14_service = WD14Service(
            device=self._device,
            model_dir=MODEL_DIR,
            batch_size_fn=self._effective_wd14_batch_size,
            silent=self._silent,
        )
        if self._wd14_service.needs_download() or force_download:
            self._wd14_service.download(force_download=force_download)

        # Initialize Florence-2 service for captioning (lazy-loaded on first use)
        logger.debug("Florence-2 captioning model is configured for lazy loading.")
        self._florence_service = Florence2Service(
            device=self._device,
            fast_captions=PictureTagger.FAST_CAPTIONS,
            force_cpu_fn=lambda: PictureTagger.FORCE_CPU,
            max_concurrent_fn=self.max_concurrent_images,
            vram_cap_fn=lambda base_mb, per_item_mb: self._vram_limited_batch_cap(
                base_mb, per_item_mb
            ),
        )

        self.set_max_vram_usage_gb(DEFAULT_MAX_VRAM_GB)

    def set_max_vram_usage_gb(self, max_vram_gb: float | None):
        if self._device != "cuda":
            self._max_vram_usage_mb = None
            logger.debug(
                "Ignoring tagger VRAM budget because inference device is %s.",
                self._device,
            )
            return

        if max_vram_gb is None:
            self._max_vram_usage_mb = None
            return
        try:
            requested_mb = int(float(max_vram_gb) * 1024)
        except Exception:
            self._max_vram_usage_mb = None
            return
        if requested_mb <= 0:
            self._max_vram_usage_mb = None
            return
        self._max_vram_usage_mb = requested_mb
        total_mb = query_total_vram_mb()
        if total_mb > 0 and requested_mb > total_mb:
            logger.warning(
                "Configured tagger VRAM budget %.2f GB exceeds detected GPU total %.2f GB; keeping configured budget as requested.",
                requested_mb / 1024.0,
                total_mb / 1024.0,
            )
        try:
            free_bytes, _ = torch.cuda.mem_get_info()
            free_gb = free_bytes / 1024**3
            free_str = f"{free_gb:.1f} GB free VRAM"
        except Exception:
            free_str = "VRAM unknown"
        try:
            gpu_name = torch.cuda.get_device_name(0)
        except Exception:
            gpu_name = "GPU"
        logger.info(
            "CUDA inference: %s, %s, budget %.2f GB",
            gpu_name,
            free_str,
            self._max_vram_usage_mb / 1024.0,
        )

    def _vram_limited_batch_cap(self, base_mb: int, per_item_mb: int) -> int:
        return vram_limited_batch_cap(
            self._max_vram_usage_mb,
            self._device,
            base_mb,
            per_item_mb,
        )

    def _effective_wd14_batch_size(self) -> int:
        max_concurrent = max(1, int(self.max_concurrent_images()))
        onnx_cap = self._wd14_service.batch_capacity()
        wd14_batch = min(max_concurrent, onnx_cap)
        if self._device == "cuda":
            wd14_batch = min(
                wd14_batch,
                self._vram_limited_batch_cap(base_mb=900, per_item_mb=220),
            )
        return max(1, int(wd14_batch))

    def _effective_custom_batch_size(self) -> int:
        # Use the same VRAM-budget cap as WD14 so that both taggers share a
        # single unified batch size.  WD14 (900 MB base + 220 MB/image) is the
        # more conservative bound, so any task sized for WD14 is safe for the
        # custom ConvNext tagger too.
        return self._effective_wd14_batch_size()

    def suggested_tag_task_size(self) -> int:
        # Use the tightest applicable VRAM cap across enabled taggers.
        # WD14 ONNX uses 900 MB base + 220 MB/image; the custom ConvNext
        # tagger uses 700 MB base + 90 MB/image.  When only the custom
        # tagger is active the WD14 cap is overly conservative and would
        # shrink batch sizes to ~18 on a 12 GB GPU instead of ~46.
        max_concurrent = max(1, int(self.max_concurrent_images()))
        if self._device == "cuda":
            wd14_enabled = getattr(self, "_use_wd14_tagger", True)
            custom_enabled = getattr(self, "_use_custom_tagger", False)
            if wd14_enabled:
                max_concurrent = min(
                    max_concurrent,
                    self._vram_limited_batch_cap(base_mb=900, per_item_mb=220),
                )
            if custom_enabled:
                max_concurrent = min(
                    max_concurrent,
                    self._vram_limited_batch_cap(base_mb=700, per_item_mb=90),
                )
        return max(1, max_concurrent)

    def estimate_task_vram_mb(self, image_count: int) -> int:
        image_count = max(1, int(image_count or 1))
        wd14_enabled = getattr(self, "_use_wd14_tagger", True)
        custom_enabled = getattr(self, "_use_custom_tagger", False)
        candidates = [1200]
        if wd14_enabled:
            wd14_batch = min(self._effective_wd14_batch_size(), image_count)
            candidates.append(900 + 220 * wd14_batch)
        if custom_enabled:
            custom_batch = min(self._effective_custom_batch_size(), image_count)
            candidates.append(700 + 90 * custom_batch)
        return int(max(candidates))

    def estimate_task_incremental_vram_mb(self, image_count: int) -> int:
        wd14_enabled = getattr(self, "_use_wd14_tagger", True)
        custom_enabled = getattr(self, "_use_custom_tagger", False)
        candidates = [256]
        if wd14_enabled:
            wd14_batch = min(
                self._effective_wd14_batch_size(),
                max(1, int(image_count or 1)),
            )
            candidates.append(220 * wd14_batch)
        if custom_enabled:
            custom_batch = min(
                self._effective_custom_batch_size(),
                max(1, int(image_count or 1)),
            )
            candidates.append(90 * custom_batch)
        return int(max(candidates))

    def suggested_image_embedding_batch_size(self) -> int:
        """VRAM-budget-constrained batch size for ImageEmbeddingTask CLIP inference."""
        # CLIP ViT-B-32: ~350 MB model (fp16), ~8 MB per image activation.
        # This is vastly cheaper than the tagger (220 MB/image) so a much
        # larger batch fits in the same VRAM budget.
        max_batch = 128
        if self._device == "cuda":
            max_batch = min(
                max_batch,
                self._vram_limited_batch_cap(base_mb=350, per_item_mb=8),
            )
        return max(1, max_batch)

    def estimate_image_embedding_vram_mb(self, image_count: int) -> int:
        """Incremental VRAM estimate for an ImageEmbeddingTask batch."""
        if self._device != "cuda":
            return 0
        batch = min(max(1, int(image_count or 1)), 512)
        return int(max(64, 8 * batch))

    def estimate_face_extraction_vram_mb(self) -> int:
        """Flat VRAM estimate for FaceExtractionTask (InsightFace model + inference)."""
        if self._device != "cuda":
            return 0
        return INSIGHTFACE_VRAM_MB

    def estimate_description_vram_mb(self, image_count: int) -> int:
        """Incremental VRAM estimate for a DescriptionTask batch.

        When Florence is already loaded in memory returns only the per-image
        activation scratch, avoiding a false-positive VRAM gate stall on warm
        runs (the full model footprint is already reflected in the nvidia-smi
        reading that the gate compares against).
        """
        if self._device != "cuda":
            return 0
        florence_batch = max(1, int(self._florence_service.description_batch_size()))
        batch = min(max(1, int(image_count or 1)), florence_batch)
        if self._florence_service.is_loaded():
            # Model already resident; only charge for per-image activation scratch.
            return int(FLORENCE_PER_IMAGE_VRAM_MB * batch)
        return int(FLORENCE_BASE_VRAM_MB + FLORENCE_PER_IMAGE_VRAM_MB * batch)

    def _resolve_picture_path(self, file_path: str) -> str:
        return ImageUtils.resolve_picture_path(self._image_root, file_path)

    def __enter__(self):
        logger.debug("PictureTagger.__enter__ called.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        import gc

        try:
            if hasattr(self, "_clip_service"):
                self._clip_service.unload()
                logger.debug("Released CLIP service models.")
            if hasattr(self, "_wd14_service"):
                self._wd14_service.unload()
                logger.debug("Released WD14 service models.")
            if hasattr(self, "_florence_service"):
                self._florence_service._model = None
                self._florence_service._processor = None
                self._florence_service._model_device = None
                logger.debug("Released Florence-2 service models.")
            if hasattr(self, "_sbert_service"):
                self._sbert_service.unload()
                logger.debug("Released SBERT service models.")
            if hasattr(self, "_custom_service"):
                self._custom_service.unload()
                logger.debug("Released custom tagger service models.")
        except Exception as cleanup_error:
            logger.warning(f"Exception during PictureTagger cleanup: {cleanup_error}")

        torch.cuda.empty_cache()
        gc.collect()
        trim_process_memory()
        self._models_ready = False
        logger.debug("PictureTagger.__exit__ called, all resources released.")

    def unload_tagger_session(self):
        """Release the WD14 ONNX inference session and its CUDA arena.

        ORT's BFC allocator holds the full activation workspace for the session
        lifetime.  Deleting the session object frees that CUDA memory so that
        subsequent GPU pipelines (embeddings, descriptions) start with a clean
        VRAM budget.  The session is rebuilt lazily the next time tagging runs.
        """
        if self._wd14_service.is_loaded():
            import gc

            self._wd14_service.unload()
            logger.info("PictureTagger: WD14 ONNX session unloaded (CUDA arena freed).")
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def aggressive_unload(self):
        logger.warning("PictureTagger.aggressive_unload() called, releasing models...")
        self.close()

    def safe_idle_unload(self):
        """
        Release non-captioning models during idle periods while keeping Florence loaded.

        This avoids expensive/fragile Florence unload-reload cycles during normal runtime,
        but still frees a significant amount of CPU/GPU memory from other models.
        """
        import gc

        logger.warning(
            "PictureTagger.safe_idle_unload() called, releasing non-captioning models..."
        )
        try:
            if hasattr(self, "_clip_service"):
                self._clip_service.unload()
                logger.debug("Released CLIP service models.")
            if hasattr(self, "_wd14_service"):
                self._wd14_service.unload()
                logger.debug("Released WD14 service models.")
            if hasattr(self, "_sbert_service"):
                self._sbert_service.unload()
                logger.debug("Released SBERT service models.")
            if hasattr(self, "_custom_service"):
                self._custom_service.unload()
                logger.debug("Released custom tagger service models.")
        except Exception as cleanup_error:
            logger.warning(
                "Exception during PictureTagger safe idle cleanup: %s", cleanup_error
            )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
        trim_process_memory()

        self._models_ready = self._florence_service.is_loaded()

    def _ensure_tagging_ready(self):
        with self._model_init_lock:
            if self._use_wd14_tagger:
                self._wd14_service.init()
            if self._use_custom_tagger and not self._custom_service.is_loaded():
                if not self._custom_service.init_or_cpu_fallback():
                    self._use_custom_tagger = False
            self._models_ready = True

    def _ensure_captioning_ready(self):
        if self._florence_service.is_loaded():
            return
        with self._model_init_lock:
            if not self._florence_service.is_loaded():
                self._florence_service.ensure_ready()
                self._models_ready = True

    def is_captioning_initialized(self) -> bool:
        return self._florence_service.is_loaded()

    @property
    def keep_models_in_memory(self) -> bool:
        return bool(getattr(self, "_keep_models_in_memory", True))

    def set_keep_models_in_memory(self, keep_models_in_memory: bool):
        self._keep_models_in_memory = bool(keep_models_in_memory)

    def set_wd14_tagger_enabled(self, enabled: bool):
        self._use_wd14_tagger = bool(enabled)

    def set_custom_tagger_enabled(self, enabled: bool):
        if bool(enabled) and not self._use_custom_tagger:
            # Only enable if the model files are actually present
            if os.path.isfile(self._custom_service._model_path) and os.path.isfile(
                self._custom_service._meta_path
            ):
                self._use_custom_tagger = True
        elif not bool(enabled):
            self._use_custom_tagger = False

    def set_wd14_threshold(self, threshold: float):
        self._wd14_service.set_threshold(threshold)

    def set_custom_tagger_threshold_offset(self, offset: float):
        self._custom_tagger_threshold_offset = float(offset)

    def loaded_model_state(self) -> dict:
        state = self._florence_service.state_info()
        state.update(
            {
                "clip_loaded": self._clip_service.is_loaded(),
                "wd14_onnx_loaded": self._wd14_service.is_loaded(),
                "sbert_loaded": self._sbert_service.is_loaded(),
                "custom_tagger_loaded": self._custom_service.is_loaded(),
                "keep_models_in_memory": self.keep_models_in_memory,
            }
        )
        return state

    def max_concurrent_images(self):
        if self._device == "cpu":
            return MAX_CONCURRENT_IMAGES_CPU
        return MAX_CONCURRENT_IMAGES_GPU

    def description_batch_size(self):
        return self._florence_service.description_batch_size()

    def custom_tagger_ready(self) -> bool:
        return bool(self._use_custom_tagger and self._custom_service.is_loaded())

    def custom_tagger_threshold_offset(self) -> float:
        return float(self._custom_tagger_threshold_offset)

    def custom_tagger_version(self) -> int:
        """Return the version integer from the loaded custom tagger meta.json."""
        return self._custom_service.version()

    def custom_tagger_meta_path(self) -> str:
        """Return the path to the custom tagger meta.json file."""
        return self._custom_service.meta_path

    def custom_tagger_image_size_full(self) -> int:
        return int(self._custom_service._image_size_full)

    def custom_tagger_image_size_quality_crop(self) -> int:
        return int(self._custom_service._image_size_quality_crop)

    def custom_tagger_batch_size(self) -> int:
        """Return the current effective batch size for the custom tagger."""
        return self._effective_custom_batch_size()

    @staticmethod
    def _expand_bbox_to_square(bbox, img_width, img_height, target_size):
        """Expand [x1, y1, x2, y2] outward from its center to a square of
        ``target_size`` pixels, clamped to image bounds.

        Args:
            bbox: [x1, y1, x2, y2] face bounding box.
            img_width: Image width in pixels.
            img_height: Image height in pixels.
            target_size: Desired square side length in pixels.

        Returns:
            Clamped [x1, y1, x2, y2] square region.
        """
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        half = target_size / 2.0
        nx1 = max(0, int(round(cx - half)))
        ny1 = max(0, int(round(cy - half)))
        nx2 = min(img_width, int(round(cx + half)))
        ny2 = min(img_height, int(round(cy + half)))
        return [nx1, ny1, nx2, ny2]

    def tag_quality_crops(
        self, items, stop_event=None, out_raw_scores: dict | None = None
    ):
        """Run the custom tagger on pre-cropped PIL images and return only
        quality-relevant tags.

        The crops should already be sized/centred on a face region at the
        custom tagger's native resolution so no additional downscaling of the
        full image is needed.

        Args:
            items: List of ``(key, PIL.Image)`` pairs.
            stop_event: Optional threading.Event to interrupt inference.
            out_raw_scores: When provided, the raw confidence scores for every
                label above ``min_confidence`` are written into this dict
                (``{key: {label: float}}``) during the same GPU pass — avoiding
                a separate ``score_quality_crops_raw`` call.

        Returns:
            Dict mapping key to list of quality tags that passed the whitelist
            filter.  Keys with no matching quality tags are omitted.
        """
        if not items:
            return {}
        if not self._custom_service.is_loaded():
            logger.debug("Custom tagger not available; skipping quality crop pass.")
            return {}
        return self._custom_service.tag_quality_crop_items(
            items,
            stop_event=stop_event,
            threshold_offset=self._custom_tagger_threshold_offset,
            out_raw_scores=out_raw_scores,
        )

    def score_images_custom(
        self,
        image_paths,
        preloaded_images=None,
        min_confidence: float = 0.05,
    ) -> dict[str, dict[str, float]]:
        """Return raw custom-tagger confidence scores for a list of images.

        Runs the custom (anomaly) tagger without applying the normal
        threshold, so that all labels with confidence >= ``min_confidence``
        are returned.  This is used by ``TagPredictionTask`` to populate the
        ``TagPrediction`` table.

        Args:
            image_paths: List of image file paths.
            preloaded_images: Optional dict of path → PIL image (avoids re-reading).
            min_confidence: Labels below this value are discarded.

        Returns:
            Dict mapping path to ``{label: confidence}`` for each image.
        """
        from PIL import Image

        if not self._use_custom_tagger:
            return {}
        self._ensure_tagging_ready()

        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        preloaded_map = preloaded_images or {}
        items = []
        for image_path in image_paths:
            path = str(image_path)
            ext = os.path.splitext(path)[1].lower()
            if ext in video_exts:
                frames = VideoUtils.extract_representative_video_frames(path, count=1)
                if not frames:
                    continue
                items.append((path, frames[0].convert("RGB")))
                continue
            try:
                img = preloaded_map.get(path)
                if img is None:
                    img = Image.open(path).convert("RGB")
                items.append((path, img))
            except Exception as exc:
                logger.error("Could not load %s for scoring: %s", path, exc)
        if not items:
            return {}
        return self._custom_service.score_items(
            items,
            min_confidence=min_confidence,
            image_size=self._custom_service._image_size_full,
        )

    def score_quality_crops_raw(
        self,
        items,
        stop_event=None,
        min_confidence: float = 0.05,
    ) -> dict[str, dict[str, float]]:
        """Return raw custom-tagger confidence scores for pre-cropped quality-crop images.

        Mirrors ``score_images_custom`` but uses the quality-crop image size and
        accepts pre-loaded PIL images so the caller controls how the crops are
        generated.  Intended for use by ``TagPredictionTask`` so that face-crop-
        detected tags receive a real confidence score rather than 0.0.

        Args:
            items: List of ``(key, PIL.Image)`` pairs (already cropped to face region).
            stop_event: Optional threading.Event to interrupt inference.
            min_confidence: Labels below this floor are discarded.

        Returns:
            Dict mapping key to ``{label: confidence}`` for each kept label.
        """
        if not items:
            return {}
        if not self._use_custom_tagger:
            return {}
        self._ensure_tagging_ready()
        return self._custom_service.score_items(
            items,
            stop_event=stop_event,
            min_confidence=min_confidence,
            image_size=self._custom_service._image_size_quality_crop,
        )

    def _tag_images_custom(
        self,
        image_paths,
        stop_event=None,
        preloaded_images=None,
        out_raw_scores: dict | None = None,
    ):
        from PIL import Image

        if not self._custom_service.is_loaded():
            logger.warning("Custom tagger not available; skipping custom tags.")
            return {}

        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        items = []
        for image_path in image_paths:
            if stop_event is not None and stop_event.is_set():
                logger.info("Tagging interrupted by stop event.")
                break
            path = str(image_path)
            ext = os.path.splitext(path)[1].lower()
            if ext in video_exts:
                # Use preloaded frame if the preloader already extracted it.
                if preloaded_images is not None and path in preloaded_images:
                    items.append((f"{path}#frame0", preloaded_images[path]))
                    continue
                frames = VideoUtils.extract_representative_video_frames(path, count=1)
                if not frames:
                    logger.error("No frames extracted from video: %s", path)
                    continue
                for idx, frame in enumerate(frames):
                    items.append((f"{path}#frame{idx}", frame))
                continue
            try:
                if preloaded_images is not None and path in preloaded_images:
                    image = preloaded_images[path]
                else:
                    image = Image.open(path).convert("RGB")
            except Exception as e:
                logger.error("Could not load image path: %s, error: %s", path, e)
                continue
            items.append((path, image))

        if not items:
            return {}

        if out_raw_scores is not None:
            tags_by_key, scores_by_key = self._custom_service.tag_and_score_items(
                items,
                stop_event=stop_event,
                threshold_offset=self._custom_tagger_threshold_offset,
                threshold=None,
                image_size=self._custom_service._image_size_full,
                pass_name="full_images",
            )
            # Merge video-frame raw scores back to the original path key.
            for key, scores in scores_by_key.items():
                orig = key.split("#frame")[0] if "#frame" in key else key
                existing = out_raw_scores.get(orig, {})
                for label, conf in scores.items():
                    if conf > existing.get(label, 0.0):
                        existing[label] = conf
                out_raw_scores[orig] = existing
        else:
            tags_by_key = self._custom_service.tag_items(
                items,
                stop_event=stop_event,
                threshold_offset=self._custom_tagger_threshold_offset,
                threshold=None,
                image_size=self._custom_service._image_size_full,
                pass_name="full_images",
            )
        return merge_video_frame_tags(tags_by_key)

    def tag_images(
        self,
        image_paths,
        stop_event=None,
        preloaded_images=None,
        _out_raw_custom_scores: dict | None = None,
    ):
        """
        Tag images using WD14 and optionally extend with the custom tagger.

        Args:
            image_paths (list of str): List of image file paths to be tagged.

        Returns:
            dict: A dictionary mapping image paths to their corresponding list of tags.
        """
        self._ensure_tagging_ready()

        preloaded_map = preloaded_images or {}

        wd14_results = {}
        if self._use_wd14_tagger:
            wd14_results = self._wd14_service.tag_images(
                image_paths, stop_event=stop_event, preloaded_map=preloaded_map
            )
        wd14_results = merge_video_frame_tags(wd14_results)

        if not self._use_custom_tagger:
            return wd14_results

        custom_results = self._tag_images_custom(
            image_paths,
            stop_event=stop_event,
            preloaded_images=preloaded_map,
            out_raw_scores=_out_raw_custom_scores,
        )

        combined_results = {}
        for path in set(wd14_results) | set(custom_results):
            combined_tags = set(wd14_results.get(path, []))
            combined_tags.update(custom_results.get(path, []))
            combined_results[path] = sorted(combined_tags)
        return combined_results

    def generate_description(self, picture):
        self._ensure_captioning_ready()
        logger.debug(
            f"generate_description: picture.file_path={getattr(picture, 'file_path', None)}"
        )
        picture_path = self._resolve_picture_path(getattr(picture, "file_path", None))
        florence_caption = self._florence_service.generate_caption(
            picture_path,
            _retry_on_cpu=False,
        )
        if florence_caption:
            logger.debug(
                f"Text embedding: using Florence-2 caption: {florence_caption}"
            )
        else:
            logger.error(
                "Florence captioning failed for %s",
                getattr(picture, "file_path", None),
            )
            raise RuntimeError("Florence captioning failed.")
        return florence_caption

    def generate_descriptions_batch(self, pictures: list[Picture]) -> dict[int, str]:
        if not pictures:
            return {}
        self._ensure_captioning_ready()

        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        results = {}
        batch_items = []

        for picture in pictures:
            picture_path = self._resolve_picture_path(
                getattr(picture, "file_path", None)
            )
            if not picture_path:
                results[picture.id] = None
                continue
            ext = os.path.splitext(picture_path)[1].lower()
            if ext in video_exts:
                results[picture.id] = self._florence_service.generate_caption(
                    picture_path, _retry_on_cpu=False
                )
            else:
                batch_items.append((picture.id, picture_path))

        batch_size = self._florence_service.description_batch_size()
        for idx in range(0, len(batch_items), batch_size):
            chunk = batch_items[idx : idx + batch_size]
            chunk_paths = [picture_path for _, picture_path in chunk]
            captions = self._florence_service.generate_captions_batch(chunk_paths)
            for picture_id, picture_path in chunk:
                results[picture_id] = captions.get(picture_path)

        return results

    @classmethod
    def _flatten_texts(cls, texts):
        flat = []

        characters = texts.get("characters") or []

        prefix = ""
        if characters:
            if len(characters) == 1:
                prefix = f"A picture of {characters[0]['name']}. "
            else:
                prefix = "A picture of "
                prefix += ", ".join([char["name"] for char in characters[:-1]])
                prefix += f" and {characters[-1]['name']}. "
            flat.append(prefix)

        if texts.get("description"):
            flat.append(str(texts["description"]))

        for char in characters:
            if char.get("description"):
                flat.append(str(char["description"]))

        comfyui = texts.get("comfyui") or {}
        if comfyui.get("positive_prompt"):
            flat.append(str(comfyui["positive_prompt"]))
        models = [clean_asset_name(m) for m in (comfyui.get("models") or []) if m]
        if models:
            flat.append(", ".join(models))
        loras = [clean_asset_name(lf) for lf in (comfyui.get("loras") or []) if lf]
        if loras:
            flat.append(", ".join(loras))

        return flat

    def generate_text_embedding(
        self, pictures: list[Picture] = None, query: str = None
    ):
        """
        Generate SBERT embeddings for the provided pictures or query text.
        Returns a list of embeddings matching the input order.
        """
        if pictures is None and query is None:
            raise ValueError("Either picture or query_string must be provided.")

        texts = []
        if query:
            texts.append(query.lower())
        else:
            for picture in pictures or []:
                text = picture.text_embedding_data()
                flat_text = PictureTagger._flatten_texts(text)
                filtered_text = filter_texts(flat_text)
                full_text = ". ".join(filtered_text).lower()
                texts.append(full_text)

        if not texts:
            return []

        return self._sbert_service.encode(texts)

    def generate_clip_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a CLIP text embedding for the provided query text.
        Returns a single embedding (np.ndarray) or None.
        """
        return self._clip_service.encode_text(query)

    def generate_facial_features(self, picture, face_bboxes):
        """
        Generate facial features for a list of face_bboxes in a picture.
        Returns a list of facial_features (np.ndarray or None) for each bbox.
        """
        import cv2
        from PIL import Image

        file_path = (
            picture.file_path if hasattr(picture, "file_path") else picture["file_path"]
        )
        file_path = self._resolve_picture_path(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        face_crops = []

        if ext in video_exts:
            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            cap.release()
            frame = frame if ret else None
            for bbox in face_bboxes:
                if frame is not None:
                    crop = FaceUtils.crop_face_from_frame(frame, bbox)
                    if crop is not None and isinstance(crop, np.ndarray):
                        crop = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                    face_crops.append(crop)
                else:
                    face_crops.append(None)
        else:
            for bbox in face_bboxes:
                face_crops.append(
                    FaceUtils.load_and_crop_square_image_with_face(file_path, bbox)
                )

        pic_desc = getattr(picture, "description", None) or file_path
        return self._clip_service.encode_image_crops(face_crops, pic_desc=pic_desc)

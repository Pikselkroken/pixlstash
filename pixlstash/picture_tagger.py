#################################################################
# Adapted from Kohya_ss https://github.com/kohya-ss/sd-scripts/ #
# Under the Apache 2.0 License                                  #
# https://github.com/kohya-ss/sd-scripts/blob/main/LICENSE.md   #
#################################################################
from typing import Optional
import open_clip
import csv
import json
import numpy as np
import onnxruntime as ort
import os
import platform
import re
import threading
import time
import torch
from torchvision import transforms

from tqdm import tqdm
from sentence_transformers import SentenceTransformer

from platformdirs import user_data_dir

from .pixl_logging import get_logger
from pixlstash.db_models.picture import Picture
from pixlstash.tag_naturaliser import TagNaturaliser
from pixlstash.image_loading_dataset_prepper import ImageLoadingDatasetPrepper
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.face_utils import FaceUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils

logger = get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        return max(1, value)
    except ValueError:
        logger.warning(
            "Invalid integer for %s=%r, using default=%s", name, raw, default
        )
        return default


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
        if value <= 0:
            return None
        return value
    except ValueError:
        logger.warning("Invalid float for %s=%r, using default=%s", name, raw, default)
        return default


def _from_pretrained_local_first(cls, model_name, **kwargs):
    """Load a HuggingFace model/processor from local cache when possible.

    Tries ``local_files_only=True`` first so no network requests are made
    when the model is already cached.  Falls back to a normal (online) load
    only on the first run, when the files aren't present yet.
    """
    try:
        return cls.from_pretrained(model_name, local_files_only=True, **kwargs)
    except OSError:
        logger.info("Downloading %s for the first time...", model_name)
        return cls.from_pretrained(model_name, **kwargs)


DEFAULT_WD14_TAGGER_REPO = "SmilingWolf/wd-convnext-tagger-v3"
FILES = ["keras_metadata.pb", "saved_model.pb", "selected_tags.csv"]
FILES_ONNX = ["model.onnx"]
SUB_DIR = "variables"
SUB_DIR_FILES = ["variables.data-00000-of-00001", "variables.index"]
CSV_FILE = FILES[-1]
MODEL_DIR = os.path.join(user_data_dir("pixlstash"), "downloaded_models")
BATCH_SIZE = 1
MAX_CONCURRENT_IMAGES_GPU = _env_int("PIXLSTASH_TAGGER_MAX_CONCURRENT_GPU", 64)
MAX_CONCURRENT_IMAGES_CPU = _env_int("PIXLSTASH_TAGGER_MAX_CONCURRENT_CPU", 8)
FLORENCE_BATCH_SIZE_GPU = _env_int("PIXLSTASH_FLORENCE_BATCH_GPU", 32)
FLORENCE_BATCH_SIZE_CPU = _env_int("PIXLSTASH_FLORENCE_BATCH_CPU", 2)
TAGGER_DATALOADER_TIMEOUT = 30
GENERAL_THRESHOLD = 0.8
UNDESIRED_TAGS = "solo, general, male_focus, meme, sensitive"
CAPTION_SEPARATOR = ", "
CUSTOM_TAGGER_HF_REPO = "PersonalJeebus/pixlvault-anomaly-tagger"
CUSTOM_TAGGER_FILENAME = "pixlstash-anomaly-tagger.safetensors"
CUSTOM_TAGGER_META_FILENAME = "pixlstash-anomaly-tagger_meta.json"
CUSTOM_TAGGER_PATH = os.path.join(MODEL_DIR, "pixlstash-anomaly-tagger.safetensors")
CUSTOM_TAGGER_META_PATH = os.path.join(MODEL_DIR, "pixlstash-anomaly-tagger_meta.json")
CUSTOM_TAGGER_THRESHOLD_FULL = 0.75
CUSTOM_TAGGER_IMAGE_SIZE_FULL = 448
CUSTOM_TAGGER_IMAGE_SIZE_QUALITY_CROP = 320
CUSTOM_TAGGER_BATCH = _env_int("PIXLSTASH_CUSTOM_TAGGER_BATCH", 16)
CLIP_MODEL_NAME = "ViT-B-32"
DEFAULT_MAX_VRAM_GB = _env_float("PIXLSTASH_MAX_VRAM_GB", None)
EXPECTED_CONCURRENT_TAG_TASKS = _env_int("PIXLSTASH_EXPECTED_TAG_TASKS", 2)

# Approximate VRAM footprints for non-tagging GPU pipelines
INSIGHTFACE_VRAM_MB = 400  # RetinaFace + ArcFace models via CUDA provider
FLORENCE_BASE_VRAM_MB = 500  # Florence-2-base model footprint
FLORENCE_PER_IMAGE_VRAM_MB = 150  # Activation scratch per image in a GPU mini-batch

# Tags that require close-up face crops to detect reliably at full-image resolution.
# These are collected from face-crop passes and merged into the picture's flat tag list.
QUALITY_CROP_TAG_WHITELIST = frozenset(
    {
        "pixelated",
        "blurry",
        "jpeg artifacts",
        "chromatic aberration",
        "scan artifacts",
        "film grain",
        "malformed teeth",
    }
)
CLIP_MODEL_WEIGHTS = "laion2b_s34b_b79k"


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
        model_location=os.path.join(
            MODEL_DIR, DEFAULT_WD14_TAGGER_REPO.replace("/", "_")
        ),
        force_download=False,
        silent=True,
        device=None,
        image_root: str = None,
    ):
        logger.debug("Initializing PictureTagger...")
        self._model_location = model_location
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

        if self._device == "cuda":
            providers = ort.get_available_providers()
            if "CUDAExecutionProvider" not in providers:
                # Only the WD14 ONNX tagger needs CUDAExecutionProvider; PyTorch
                # models (Florence, CLIP, SentenceTransformer) work fine without
                # it.  Log a note here but keep self._device as "cuda" — the ONNX
                # loader at model-init time already has its own provider fallback.
                logger.warning(
                    "CUDAExecutionProvider unavailable for onnxruntime "
                    "(WD14 tagger will use CPU; all PyTorch models still use CUDA). "
                    "Fix with: pip uninstall -y onnxruntime && pip install onnxruntime-gpu"
                )

        if self._device == "cpu" and not PictureTagger.FORCE_CPU:
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
        self._custom_tagger_path = CUSTOM_TAGGER_PATH
        self._custom_tagger_meta_path = CUSTOM_TAGGER_META_PATH
        self._use_custom_tagger = True
        self._custom_tagger_threshold_full = CUSTOM_TAGGER_THRESHOLD_FULL
        self._custom_tagger_image_size_full = CUSTOM_TAGGER_IMAGE_SIZE_FULL
        self._custom_tagger_image_size_quality_crop = (
            CUSTOM_TAGGER_IMAGE_SIZE_QUALITY_CROP
        )
        self._custom_tagger_batch = CUSTOM_TAGGER_BATCH
        self._custom_device = self._device
        self._max_vram_usage_mb: int | None = None

        self._ensure_model_files(force_download=force_download)

        # Defer heavy model initialization until first use.
        self.ort_sess = None
        self.input_name = None
        self._onnx_batch_capacity = 1
        self._rating_tags = None
        self._general_tags = None

        self._clip_model = None
        self._clip_preprocess = None
        self._clip_tokenizer = None
        self._clip_device = self._device

        self._custom_model = None
        self._custom_labels = None
        self._custom_label_to_idx = None
        self._custom_transform = None
        self._custom_transform_cache = {}
        if not os.path.isfile(self._custom_tagger_path) or not os.path.isfile(
            self._custom_tagger_meta_path
        ):
            self._download_custom_tagger()
        if not os.path.isfile(self._custom_tagger_path) or not os.path.isfile(
            self._custom_tagger_meta_path
        ):
            logger.warning(
                "Custom tagger not found at %s, skipping initialization.",
                self._custom_tagger_path,
            )
            self._use_custom_tagger = False

        self._tag_naturaliser = TagNaturaliser()

        # Initialize Florence-2 for captioning
        logger.debug("Florence-2 captioning model is configured for lazy loading.")
        self._florence_model = None
        self._florence_processor = None

        self._florence_device = None
        self._florence_model_name = "florence-community/Florence-2-base"
        self._last_florence_fallback_reason = None
        self._last_florence_fallback_at = None

        self._florence_max_tokens = 40 if PictureTagger.FAST_CAPTIONS else 120
        self._florence_batch_size = (
            FLORENCE_BATCH_SIZE_CPU
            if self._device == "cpu"
            else FLORENCE_BATCH_SIZE_GPU
        )
        self._expected_concurrent_tag_tasks = EXPECTED_CONCURRENT_TAG_TASKS
        self.set_max_vram_usage_gb(DEFAULT_MAX_VRAM_GB)

    @staticmethod
    def _query_total_vram_mb() -> int:
        try:
            import subprocess

            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            totals = []
            for line in output.splitlines():
                value = line.strip()
                if not value:
                    continue
                totals.append(int(float(value)))
            return sum(totals)
        except Exception:
            return 0

    @staticmethod
    def _query_process_vram_mb() -> int:
        try:
            import subprocess

            pid = os.getpid()
            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            used_mb = 0
            for line in output.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    line_pid = int(parts[0])
                    line_used_mb = int(float(parts[1]))
                except Exception:
                    continue
                if line_pid == pid:
                    used_mb += line_used_mb
            return used_mb
        except Exception:
            return 0

    def _runtime_vram_headroom_mb(self, reserve_mb: int = 128) -> int | None:
        budget_mb = self._max_vram_usage_mb
        if self._device != "cuda" or not budget_mb:
            return None
        used_mb = self._query_process_vram_mb()
        if used_mb <= 0:
            return None
        return max(0, int(budget_mb - used_mb - max(0, int(reserve_mb))))

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
        total_mb = self._query_total_vram_mb()
        if total_mb > 0:
            self._max_vram_usage_mb = max(1, min(requested_mb, total_mb))
        else:
            self._max_vram_usage_mb = requested_mb
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
        budget_mb = self._max_vram_usage_mb
        if self._device != "cuda" or not budget_mb:
            return 10_000
        expected_tasks = max(1, int(getattr(self, "_expected_concurrent_tag_tasks", 1)))
        reserve_mb = max(256, int(budget_mb * 0.20))
        distributable_mb = max(1, budget_mb - reserve_mb)
        task_budget_mb = max(1, int(distributable_mb / expected_tasks))
        if task_budget_mb <= base_mb:
            return 1
        return max(1, int((task_budget_mb - base_mb) / max(1, per_item_mb)))

    def _effective_wd14_batch_size(self) -> int:
        max_concurrent = max(1, int(self.max_concurrent_images()))
        onnx_cap = max(1, int(getattr(self, "_onnx_batch_capacity", 1)))
        wd14_batch = min(max_concurrent, onnx_cap)
        if self._device == "cuda":
            wd14_batch = min(
                wd14_batch,
                self._vram_limited_batch_cap(base_mb=900, per_item_mb=220),
            )
            runtime_headroom_mb = self._runtime_vram_headroom_mb(reserve_mb=128)
            if runtime_headroom_mb is not None:
                runtime_batch_cap = max(1, int(runtime_headroom_mb / 220))
                wd14_batch = min(wd14_batch, runtime_batch_cap)
        return max(1, int(wd14_batch))

    def _effective_custom_batch_size(self) -> int:
        custom_batch = max(1, int(self._custom_tagger_batch))
        if self._device == "cuda":
            custom_batch = min(
                custom_batch,
                self._vram_limited_batch_cap(base_mb=700, per_item_mb=180),
            )
            runtime_headroom_mb = self._runtime_vram_headroom_mb(reserve_mb=128)
            if runtime_headroom_mb is not None:
                runtime_batch_cap = max(1, int(runtime_headroom_mb / 180))
                custom_batch = min(custom_batch, runtime_batch_cap)
        wd14_batch = self._effective_wd14_batch_size()
        return max(1, int(min(custom_batch, wd14_batch)))

    def suggested_tag_task_size(self) -> int:
        wd14_batch = self._effective_wd14_batch_size()
        custom_batch = self._effective_custom_batch_size()
        return max(1, min(wd14_batch, custom_batch))

    def estimate_task_vram_mb(self, image_count: int) -> int:
        image_count = max(1, int(image_count or 1))
        wd14_batch = min(self._effective_wd14_batch_size(), image_count)
        custom_batch = min(self._effective_custom_batch_size(), image_count)
        wd14_estimate = 900 + 220 * wd14_batch
        custom_estimate = 700 + 180 * custom_batch
        return int(max(wd14_estimate, custom_estimate, 1200))

    def estimate_task_incremental_vram_mb(self, image_count: int) -> int:
        wd14_batch = min(
            self._effective_wd14_batch_size(),
            max(1, int(image_count or 1)),
        )
        custom_batch = min(
            self._effective_custom_batch_size(),
            max(1, int(image_count or 1)),
        )
        wd14_incremental = 220 * wd14_batch
        custom_incremental = 180 * custom_batch
        return int(max(256, wd14_incremental, custom_incremental))

    def suggested_image_embedding_batch_size(self) -> int:
        """VRAM-budget-constrained batch size for ImageEmbeddingTask CLIP inference."""
        max_batch = 32
        if self._device == "cuda":
            max_batch = min(
                max_batch,
                self._vram_limited_batch_cap(base_mb=900, per_item_mb=220),
            )
            runtime_headroom_mb = self._runtime_vram_headroom_mb(reserve_mb=128)
            if runtime_headroom_mb is not None:
                max_batch = min(max_batch, max(1, int(runtime_headroom_mb / 220)))
        return max(1, max_batch)

    def estimate_image_embedding_vram_mb(self, image_count: int) -> int:
        """Incremental VRAM estimate for an ImageEmbeddingTask batch."""
        if self._device != "cuda":
            return 0
        batch = min(max(1, int(image_count or 1)), 32)
        return int(max(128, 220 * batch))

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
        florence_batch = max(1, int(getattr(self, "_florence_batch_size", 4)))
        batch = min(max(1, int(image_count or 1)), florence_batch)
        if self._florence_model is not None:
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
        # Release ONNX/PyTorch resources here
        # For ONNX: self.session = None
        # For PyTorch: del self.model; torch.cuda.empty_cache()
        import gc

        # Explicitly delete all large model objects and set to None
        try:
            if hasattr(self, "_clip_model"):
                del self._clip_model
                self._clip_model = None
                logger.debug("Deleted _clip_model.")
            if hasattr(self, "ort_sess"):
                del self.ort_sess
                self.ort_sess = None
                logger.debug("Deleted ort_sess.")
            if hasattr(self, "_florence_model"):
                del self._florence_model
                self._florence_model = None
                logger.debug("Deleted _florence_model.")
            if hasattr(self, "_florence_processor"):
                del self._florence_processor
                self._florence_processor = None
                logger.debug("Deleted _florence_processor.")
            if hasattr(self, "_florence_device"):
                del self._florence_device
                self._florence_device = None
                logger.debug("Deleted _florence_device.")
            if hasattr(self, "_sbert_model"):
                del self._sbert_model
                self._sbert_model = None
                logger.debug("Deleted _sbert_model.")
            if hasattr(self, "_tag_naturaliser"):
                del self._tag_naturaliser
                self._tag_naturaliser = None
                logger.debug("Deleted _tag_naturaliser.")
            if hasattr(self, "_clip_preprocess"):
                del self._clip_preprocess
                self._clip_preprocess = None
                logger.debug("Deleted _clip_preprocess.")
            if hasattr(self, "_clip_tokenizer"):
                del self._clip_tokenizer
                self._clip_tokenizer = None
                logger.debug("Deleted _clip_tokenizer.")
            if hasattr(self, "_custom_model"):
                del self._custom_model
                self._custom_model = None
                logger.debug("Deleted _custom_model.")
            if hasattr(self, "_custom_labels"):
                del self._custom_labels
                self._custom_labels = None
                logger.debug("Deleted _custom_labels.")
            if hasattr(self, "_custom_label_to_idx"):
                del self._custom_label_to_idx
                self._custom_label_to_idx = None
                logger.debug("Deleted _custom_label_to_idx.")
            if hasattr(self, "_custom_transform"):
                del self._custom_transform
                self._custom_transform = None
                logger.debug("Deleted _custom_transform.")
        except Exception as cleanup_error:
            logger.warning(f"Exception during PictureTagger cleanup: {cleanup_error}")

        torch.cuda.empty_cache()
        gc.collect()
        self._trim_process_memory()
        self._models_ready = False
        logger.debug("PictureTagger.__exit__ called, all resources released.")

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
            if hasattr(self, "_clip_model"):
                del self._clip_model
                self._clip_model = None
                logger.debug("Deleted _clip_model.")
            if hasattr(self, "ort_sess"):
                del self.ort_sess
                self.ort_sess = None
                logger.debug("Deleted ort_sess.")
            if hasattr(self, "_sbert_model"):
                del self._sbert_model
                self._sbert_model = None
                logger.debug("Deleted _sbert_model.")
            if hasattr(self, "_clip_preprocess"):
                del self._clip_preprocess
                self._clip_preprocess = None
                logger.debug("Deleted _clip_preprocess.")
            if hasattr(self, "_clip_tokenizer"):
                del self._clip_tokenizer
                self._clip_tokenizer = None
                logger.debug("Deleted _clip_tokenizer.")
            if hasattr(self, "_custom_model"):
                del self._custom_model
                self._custom_model = None
                logger.debug("Deleted _custom_model.")
            if hasattr(self, "_custom_labels"):
                del self._custom_labels
                self._custom_labels = None
                logger.debug("Deleted _custom_labels.")
            if hasattr(self, "_custom_label_to_idx"):
                del self._custom_label_to_idx
                self._custom_label_to_idx = None
                logger.debug("Deleted _custom_label_to_idx.")
            if hasattr(self, "_custom_transform"):
                del self._custom_transform
                self._custom_transform = None
                logger.debug("Deleted _custom_transform.")
            if hasattr(self, "_custom_transform_cache"):
                del self._custom_transform_cache
                self._custom_transform_cache = None
                logger.debug("Deleted _custom_transform_cache.")
        except Exception as cleanup_error:
            logger.warning(
                "Exception during PictureTagger safe idle cleanup: %s", cleanup_error
            )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
        self._trim_process_memory()

        self._models_ready = bool(
            getattr(self, "_florence_model", None) is not None
            and getattr(self, "_florence_processor", None) is not None
        )

    @staticmethod
    def _trim_process_memory():
        """Best-effort RSS trim for Linux/glibc allocators."""
        if not platform.system().lower().startswith("linux"):
            return
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6")
            trim = getattr(libc, "malloc_trim", None)
            if trim is not None:
                trim(0)
        except Exception:
            pass

    def _init_clip_model(self):
        self._clip_model, _, self._clip_preprocess = (
            open_clip.create_model_and_transforms(
                CLIP_MODEL_NAME, pretrained=CLIP_MODEL_WEIGHTS
            )
        )
        self._clip_device = self._device
        self._clip_model = self._clip_model.to(self._clip_device)
        if self._clip_device == "cuda":
            self._clip_model = self._clip_model.half()
        self._clip_tokenizer = open_clip.get_tokenizer(CLIP_MODEL_NAME)

    def _ensure_clip_ready(self):
        if (
            getattr(self, "_clip_model", None) is not None
            and getattr(self, "_clip_preprocess", None) is not None
            and getattr(self, "_clip_tokenizer", None) is not None
        ):
            return
        with self._model_init_lock:
            if (
                getattr(self, "_clip_model", None) is None
                or getattr(self, "_clip_preprocess", None) is None
                or getattr(self, "_clip_tokenizer", None) is None
            ):
                self._init_clip_model()
                self._models_ready = True

    def _ensure_tagging_ready(self):
        with self._model_init_lock:
            if getattr(self, "ort_sess", None) is None:
                self._init_onnx_session()
            if (
                getattr(self, "_general_tags", None) is None
                or getattr(self, "_rating_tags", None) is None
            ):
                self._load_and_preprocess_tags()
            if self._use_custom_tagger:
                missing_custom = (
                    getattr(self, "_custom_model", None) is None
                    or getattr(self, "_custom_labels", None) is None
                    or getattr(self, "_custom_transform", None) is None
                )
                if missing_custom:
                    try:
                        self._init_custom_tagger()
                    except Exception as exc:
                        logger.warning(
                            "Custom tagger reinit failed; disabling custom tagger: %s",
                            exc,
                        )
                        self._use_custom_tagger = False
            self._models_ready = True

    def _ensure_captioning_ready(self):
        if (
            getattr(self, "_florence_model", None) is not None
            and getattr(self, "_florence_processor", None) is not None
        ):
            return
        with self._model_init_lock:
            if (
                getattr(self, "_florence_model", None) is None
                or getattr(self, "_florence_processor", None) is None
            ):
                self._init_florence_captioning()
                self._models_ready = True

    def is_captioning_initialized(self) -> bool:
        return bool(
            getattr(self, "_florence_model", None) is not None
            and getattr(self, "_florence_processor", None) is not None
        )

    @property
    def keep_models_in_memory(self) -> bool:
        return bool(getattr(self, "_keep_models_in_memory", True))

    def set_keep_models_in_memory(self, keep_models_in_memory: bool):
        self._keep_models_in_memory = bool(keep_models_in_memory)

    def loaded_model_state(self) -> dict:
        return {
            "florence_loaded": bool(
                getattr(self, "_florence_model", None) is not None
                and getattr(self, "_florence_processor", None) is not None
            ),
            "florence_fallback_reason": getattr(
                self, "_last_florence_fallback_reason", None
            ),
            "florence_fallback_at": getattr(self, "_last_florence_fallback_at", None),
            "clip_loaded": bool(getattr(self, "_clip_model", None) is not None),
            "wd14_onnx_loaded": bool(getattr(self, "ort_sess", None) is not None),
            "sbert_loaded": bool(getattr(self, "_sbert_model", None) is not None),
            "custom_tagger_loaded": bool(
                getattr(self, "_custom_model", None) is not None
                and getattr(self, "_custom_labels", None) is not None
                and getattr(self, "_custom_transform", None) is not None
            ),
            "keep_models_in_memory": self.keep_models_in_memory,
        }

    def max_concurrent_images(self):
        if self._device == "cpu":
            return MAX_CONCURRENT_IMAGES_CPU
        else:
            return MAX_CONCURRENT_IMAGES_GPU

    def description_batch_size(self):
        max_concurrent = max(1, int(self.max_concurrent_images()))
        florence_batch = max(1, int(getattr(self, "_florence_batch_size", 1)))
        base_batch = min(max_concurrent, florence_batch)
        if self._device == "cuda":
            base_batch = min(
                base_batch,
                self._vram_limited_batch_cap(
                    base_mb=FLORENCE_BASE_VRAM_MB,
                    per_item_mb=FLORENCE_PER_IMAGE_VRAM_MB,
                ),
            )
        return max(1, base_batch)

    def _init_florence_captioning(self):
        """
        Enable Florence-2 for natural language captioning instead of tag-based descriptions.
        This will download the model on first use (~900MB).
        """
        if self._florence_model is not None:
            logger.debug("Florence-2 already loaded")
            return

        try:
            logger.debug("Loading Florence-2 model for captioning...")
            import transformers

            # Check transformers version
            version = transformers.__version__
            logger.debug(f"Transformers version: {version}")

            # Check if device was explicitly set to CPU
            device_str = str(self._device)
            use_cpu = PictureTagger.FORCE_CPU or device_str == "cpu"

            if use_cpu:
                # Device explicitly set to CPU - respect that
                logger.debug(
                    "Device set to CPU, loading Florence-2 on CPU with FP32..."
                )
                self._load_florence_model(torch.device("cpu"), torch.float32)
                self._florence_batch_size = FLORENCE_BATCH_SIZE_CPU
                logger.debug("Florence-2 loaded successfully on CPU")
            elif torch.cuda.is_available():
                try:
                    logger.debug("Attempting to load Florence-2 on GPU with FP16...")
                    self._load_florence_model(torch.device("cuda"), torch.float16)
                    self._florence_batch_size = FLORENCE_BATCH_SIZE_GPU
                    logger.debug("Florence-2 loaded successfully on GPU (~500MB VRAM)")
                except Exception as gpu_error:
                    self._record_florence_fallback(
                        "init_gpu_load_failed",
                        gpu_error,
                    )
                    logger.warning(
                        f"GPU loading failed, falling back to CPU: {gpu_error}"
                    )
                    self._load_florence_model(torch.device("cpu"), torch.float32)
                    self._florence_batch_size = FLORENCE_BATCH_SIZE_CPU
                    logger.debug("Florence-2 loaded successfully on CPU")
            else:
                # No GPU available, use CPU
                logger.debug("No GPU available, loading Florence-2 on CPU with FP32...")
                device = (
                    self._device
                    if isinstance(self._device, torch.device)
                    else torch.device(self._device)
                )
                self._load_florence_model(device, torch.float32)
                self._florence_batch_size = FLORENCE_BATCH_SIZE_CPU
                logger.debug("Florence-2 loaded successfully on CPU")

        except Exception as e:
            logger.error(f"Failed to load Florence-2: {e}")
            logger.error("Try: pip install --upgrade transformers")

    def _load_florence_model(self, device, dtype):
        from transformers import Florence2Processor, Florence2ForConditionalGeneration

        if not isinstance(device, torch.device):
            device = torch.device(device)

        self._florence_processor = _from_pretrained_local_first(
            Florence2Processor,
            self._florence_model_name,
        )

        # Try SDPA first, fall back to eager if not supported
        attn_impl = "sdpa"
        try:
            self._florence_model = _from_pretrained_local_first(
                Florence2ForConditionalGeneration,
                self._florence_model_name,
                torch_dtype=dtype,
                attn_implementation=attn_impl,
            ).to(device)
        except (TypeError, AttributeError) as e:
            logger.debug(f"SDPA not supported, falling back to eager attention: {e}")
            attn_impl = "eager"
            self._florence_model = _from_pretrained_local_first(
                Florence2ForConditionalGeneration,
                self._florence_model_name,
                torch_dtype=dtype,
                attn_implementation=attn_impl,
            ).to(device)

        self._florence_model.eval()

        # Try to compile the model for better performance (PyTorch 2.0+)
        try:
            if hasattr(torch, "compile") and device.type == "cuda":
                logger.debug("Compiling Florence-2 model for better performance...")
                self._florence_model = torch.compile(
                    self._florence_model,
                    mode="reduce-overhead",  # Balance compilation time and performance
                )
                logger.debug("Model compilation successful")
        except Exception as compile_error:
            logger.warning(f"Model compilation failed (not critical): {compile_error}")

        self._florence_device = device

    def _record_florence_fallback(self, phase: str, error: Exception):
        reason = f"{phase}: {type(error).__name__}: {error}"
        self._last_florence_fallback_reason = reason
        self._last_florence_fallback_at = time.time()
        logger.warning("[FLORENCE_FALLBACK] %s", reason)

    def _reload_florence_on_cpu(self, cause: Exception = None):
        logger.warning(
            "Florence-2 GPU inference failed; attempting to reload on CPU..."
        )
        if cause is not None:
            self._record_florence_fallback("runtime_gpu_inference_failed", cause)
        try:
            self._florence_model = None
            self._florence_processor = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            self._load_florence_model(torch.device("cpu"), torch.float32)
            self._florence_batch_size = FLORENCE_BATCH_SIZE_CPU
            logger.debug("Florence-2 reloaded on CPU")
            return True
        except Exception as cpu_error:
            logger.error(
                f"Failed to reload Florence-2 on CPU: {cpu_error}", exc_info=True
            )
            return False

    def _generate_florence_caption(self, image_path, _retry_on_cpu=True):
        """
        Generate a natural language caption for an image using Florence-2.

        Args:
            image_path (str): Path to the image file

        Returns:
            str: Natural language caption
        """
        logger.debug(
            f"_generate_florence_caption called: image_path={image_path}, _retry_on_cpu={_retry_on_cpu}"
        )
        if self._florence_model is None:
            logger.error("Florence-2 model is not initialised")
            return None

        try:
            import os

            ext = os.path.splitext(image_path)[1].lower()
            video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
            from PIL import Image

            caption = None
            if ext in video_exts:
                from pixlstash.utils.image_processing.video_utils import VideoUtils

                frames = VideoUtils.extract_representative_video_frames(
                    image_path, count=3
                )
                for idx, pil_img in enumerate(frames):
                    # Resize large images to speed up processing
                    MAX_DIM = 512
                    if max(pil_img.size) > MAX_DIM:
                        aspect_ratio = pil_img.width / pil_img.height
                        if pil_img.width > pil_img.height:
                            new_width = MAX_DIM
                            new_height = int(MAX_DIM / aspect_ratio)
                        else:
                            new_height = MAX_DIM
                            new_width = int(MAX_DIM * aspect_ratio)
                        pil_img = pil_img.resize(
                            (new_width, new_height), Image.Resampling.LANCZOS
                        )
                        logger.debug(
                            f"Resised video frame to {new_width}x{new_height} for faster processing"
                        )
                    inputs = self._florence_processor(
                        text="<MORE_DETAILED_CAPTION>",
                        images=pil_img,
                        return_tensors="pt",
                    )
                    florence_device = getattr(self, "_florence_device", self._device)
                    target_dtype = (
                        self._florence_model.dtype
                        if hasattr(self._florence_model, "dtype")
                        else None
                    )
                    if target_dtype and target_dtype == torch.float16:
                        inputs = {
                            k: v.to(florence_device).half()
                            if torch.is_tensor(v) and v.dtype == torch.float32
                            else v.to(florence_device)
                            if torch.is_tensor(v)
                            else v
                            for k, v in inputs.items()
                        }
                    else:
                        inputs = {
                            k: v.to(florence_device) if torch.is_tensor(v) else v
                            for k, v in inputs.items()
                        }
                    logger.debug(f"Inputs moved to {florence_device}")
                    with torch.inference_mode():
                        generated_ids = self._florence_model.generate(
                            input_ids=inputs["input_ids"],
                            pixel_values=inputs["pixel_values"],
                            max_new_tokens=self._florence_max_tokens,
                            early_stopping=False,
                            do_sample=False,
                            num_beams=1,
                            pad_token_id=self._florence_processor.tokenizer.pad_token_id,
                        )
                    generated_text = self._florence_processor.batch_decode(
                        generated_ids, skip_special_tokens=False
                    )[0]
                    parsed = self._florence_processor.post_process_generation(
                        generated_text, task="<MORE_DETAILED_CAPTION>"
                    )
                    caption = parsed.get("<MORE_DETAILED_CAPTION>", "").strip()
                    # Ensure caption ends at last sentence-ending punctuation
                    last_punct = max([caption.rfind(p) for p in [".", "!", "?"]])
                    if last_punct != -1:
                        caption = caption[: last_punct + 1].strip()
                    if caption:
                        logger.debug(f"Florence-2 caption (frame {idx}): {caption}")
                        break
            else:
                image = Image.open(image_path).convert("RGB")
                MAX_DIM = 640
                if max(image.size) > MAX_DIM:
                    aspect_ratio = image.width / image.height
                    if image.width > image.height:
                        new_width = MAX_DIM
                        new_height = int(MAX_DIM / aspect_ratio)
                    else:
                        new_height = MAX_DIM
                        new_width = int(MAX_DIM * aspect_ratio)
                    image = image.resize(
                        (new_width, new_height), Image.Resampling.LANCZOS
                    )
                    logger.debug(
                        f"Resised image to {new_width}x{new_height} for faster processing"
                    )
                inputs = self._florence_processor(
                    text="<MORE_DETAILED_CAPTION>", images=image, return_tensors="pt"
                )
                florence_device = getattr(self, "_florence_device", self._device)
                target_dtype = (
                    self._florence_model.dtype
                    if hasattr(self._florence_model, "dtype")
                    else None
                )
                if target_dtype and target_dtype == torch.float16:
                    inputs = {
                        k: v.to(florence_device).half()
                        if torch.is_tensor(v) and v.dtype == torch.float32
                        else v.to(florence_device)
                        if torch.is_tensor(v)
                        else v
                        for k, v in inputs.items()
                    }
                else:
                    inputs = {
                        k: v.to(florence_device) if torch.is_tensor(v) else v
                        for k, v in inputs.items()
                    }
                logger.debug(f"Inputs moved to {florence_device}")
                with torch.inference_mode():
                    generated_ids = self._florence_model.generate(
                        input_ids=inputs["input_ids"],
                        pixel_values=inputs["pixel_values"],
                        max_new_tokens=self._florence_max_tokens,
                        early_stopping=False,
                        do_sample=False,
                        num_beams=1,
                        pad_token_id=self._florence_processor.tokenizer.pad_token_id,
                    )
                generated_text = self._florence_processor.batch_decode(
                    generated_ids, skip_special_tokens=False
                )[0]
                parsed = self._florence_processor.post_process_generation(
                    generated_text, task="<MORE_DETAILED_CAPTION>"
                )
                caption = parsed.get("<MORE_DETAILED_CAPTION>", "").strip()
                # Ensure caption ends at last sentence-ending punctuation
                last_punct = max([caption.rfind(p) for p in [".", "!", "?"]])
                if last_punct != -1:
                    caption = caption[: last_punct + 1].strip()
                if caption:
                    logger.debug(f"Florence-2 caption: {caption}")

            logger.debug(f"Final Florence-2 caption returned: {caption}")
            return caption

        except Exception as e:
            import traceback

            is_cuda_issue = "cuda" in str(e).lower()
            using_cuda = (
                getattr(self, "_florence_device", None) is not None
                and getattr(self._florence_device, "type", "") == "cuda"
            )

            if _retry_on_cpu and using_cuda and is_cuda_issue:
                logger.warning(
                    "Florence-2 captioning failed on GPU (%s); retrying on CPU.", e
                )
                if self._reload_florence_on_cpu(cause=e):
                    return self._generate_florence_caption(
                        image_path, _retry_on_cpu=False
                    )

            logger.error(f"Florence-2 captioning failed for {image_path}: {e}")
            logger.debug(traceback.format_exc())
            return None

    def _generate_florence_captions_batch(self, image_paths, _retry_on_cpu=True):
        logger.debug(
            "_generate_florence_captions_batch called: %d images", len(image_paths)
        )
        if self._florence_model is None:
            logger.error("Florence-2 model is not initialised")
            return {}

        try:
            from PIL import Image

            valid_items = []
            for image_path in image_paths:
                try:
                    image = Image.open(image_path).convert("RGB")
                    MAX_DIM = 640
                    if max(image.size) > MAX_DIM:
                        aspect_ratio = image.width / image.height
                        if image.width > image.height:
                            new_width = MAX_DIM
                            new_height = int(MAX_DIM / aspect_ratio)
                        else:
                            new_height = MAX_DIM
                            new_width = int(MAX_DIM * aspect_ratio)
                        image = image.resize(
                            (new_width, new_height), Image.Resampling.LANCZOS
                        )
                        logger.debug(
                            "Resised image to %dx%d for faster processing",
                            new_width,
                            new_height,
                        )
                    valid_items.append((image_path, image))
                except Exception as image_error:
                    logger.error(
                        "Florence-2 failed to load image for batch %s: %s",
                        image_path,
                        image_error,
                    )

            if not valid_items:
                return {}

            images = [image for _, image in valid_items]
            inputs = self._florence_processor(
                text=["<MORE_DETAILED_CAPTION>"] * len(images),
                images=images,
                return_tensors="pt",
                padding=True,
            )
            florence_device = getattr(self, "_florence_device", self._device)
            target_dtype = (
                self._florence_model.dtype
                if hasattr(self._florence_model, "dtype")
                else None
            )
            if target_dtype and target_dtype == torch.float16:
                inputs = {
                    k: v.to(florence_device).half()
                    if torch.is_tensor(v) and v.dtype == torch.float32
                    else v.to(florence_device)
                    if torch.is_tensor(v)
                    else v
                    for k, v in inputs.items()
                }
            else:
                inputs = {
                    k: v.to(florence_device) if torch.is_tensor(v) else v
                    for k, v in inputs.items()
                }
            logger.debug("Batch inputs moved to %s", florence_device)
            with torch.inference_mode():
                generated_ids = self._florence_model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=self._florence_max_tokens,
                    early_stopping=False,
                    do_sample=False,
                    num_beams=1,
                    pad_token_id=self._florence_processor.tokenizer.pad_token_id,
                )
            generated_texts = self._florence_processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )

            captions = {}
            for (image_path, _), generated_text in zip(valid_items, generated_texts):
                parsed = self._florence_processor.post_process_generation(
                    generated_text, task="<MORE_DETAILED_CAPTION>"
                )
                caption = parsed.get("<MORE_DETAILED_CAPTION>", "").strip()
                last_punct = max([caption.rfind(p) for p in [".", "!", "?"]])
                if last_punct != -1:
                    caption = caption[: last_punct + 1].strip()
                captions[image_path] = caption if caption else None
            return captions

        except Exception as e:
            import traceback

            is_cuda_issue = "cuda" in str(e).lower()
            using_cuda = (
                getattr(self, "_florence_device", None) is not None
                and getattr(self._florence_device, "type", "") == "cuda"
            )

            if _retry_on_cpu and using_cuda and is_cuda_issue:
                logger.warning(
                    "Florence-2 batch captioning failed on GPU (%s); retrying on CPU.",
                    e,
                )
                if self._reload_florence_on_cpu(cause=e):
                    return self._generate_florence_captions_batch(
                        image_paths, _retry_on_cpu=False
                    )

            logger.error("Florence-2 batch captioning failed: %s", e)
            logger.debug(traceback.format_exc())
            captions = {}
            for image_path in image_paths:
                captions[image_path] = self._generate_florence_caption(
                    image_path, _retry_on_cpu=False
                )
            return captions

    def _init_onnx_session(self):
        onnx_path = f"{self._model_location}/model.onnx"
        logger.debug("Running wd14 tagger with onnx")
        logger.debug(f"loading onnx model: {onnx_path}")
        if not os.path.exists(onnx_path):
            raise Exception(
                f"onnx model not found: {onnx_path}, please redownload the model with --force_download"
            )

        # Use CPU-only when device is set to "cpu" to coexist with LLMs and diffusion models
        if self._device == "cpu":
            logger.debug("initialising WD14 tagger with CPUExecutionProvider")
            self.ort_sess = ort.InferenceSession(
                onnx_path, providers=["CPUExecutionProvider"]
            )
        else:
            # Allow GPU providers when not explicitly set to CPU
            logger.debug(f"initialising WD14 tagger with device: {self._device}")
            if "OpenVINOExecutionProvider" in ort.get_available_providers():
                self.ort_sess = ort.InferenceSession(
                    onnx_path,
                    providers=["OpenVINOExecutionProvider"],
                    provider_options=[{"device_type": "GPU", "precision": "FP32"}],
                )
            else:
                self.ort_sess = ort.InferenceSession(
                    onnx_path,
                    providers=(
                        ["CUDAExecutionProvider"]
                        if "CUDAExecutionProvider" in ort.get_available_providers()
                        else ["ROCMExecutionProvider"]
                        if "ROCMExecutionProvider" in ort.get_available_providers()
                        else ["CPUExecutionProvider"]
                    ),
                )
        self.input_name = self.ort_sess.get_inputs()[0].name
        self._onnx_batch_capacity = self._resolve_onnx_batch_capacity()

    def _resolve_onnx_batch_capacity(self) -> int:
        if getattr(self, "ort_sess", None) is None:
            return 1
        try:
            input_meta = self.ort_sess.get_inputs()[0]
            input_shape = getattr(input_meta, "shape", None)
            if not input_shape:
                return 1
            batch_dim = input_shape[0]
            if isinstance(batch_dim, int):
                return max(1, int(batch_dim))
            if batch_dim is None or isinstance(batch_dim, str):
                return max(1, int(self.max_concurrent_images()))
        except Exception as exc:
            logger.warning("Could not resolve ONNX batch capacity: %s", exc)
        return 1

    def _build_custom_tagger_model(self, arch: str, num_labels: int):
        from torchvision.models import convnext_tiny, convnext_base

        if arch == "convnext_tiny":
            model = convnext_tiny(weights=None)
            in_features = model.classifier[2].in_features
            model.classifier[2] = torch.nn.Linear(in_features, num_labels)
            return model
        if arch == "convnext_base":
            model = convnext_base(weights=None)
            in_features = model.classifier[2].in_features
            model.classifier[2] = torch.nn.Linear(in_features, num_labels)
            return model
        raise ValueError(f"Unsupported custom tagger arch: {arch}")

    def _init_custom_tagger(self):
        if not os.path.exists(self._custom_tagger_path):
            raise FileNotFoundError(
                f"Custom tagger checkpoint not found: {self._custom_tagger_path}"
            )
        if not os.path.exists(self._custom_tagger_meta_path):
            raise FileNotFoundError(
                f"Custom tagger metadata not found: {self._custom_tagger_meta_path}"
            )
        with open(self._custom_tagger_meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        labels = meta.get("labels")
        arch = meta.get("arch", "convnext_base")
        if not labels:
            raise ValueError("Custom tagger metadata missing labels list.")
        from safetensors.torch import load_file

        state_dict = load_file(
            self._custom_tagger_path, device=str(self._custom_device)
        )
        self._custom_labels = labels
        self._custom_label_to_idx = {label: i for i, label in enumerate(labels)}
        self._custom_model = self._build_custom_tagger_model(arch, len(labels))
        self._custom_model.load_state_dict(state_dict)
        self._custom_model.to(self._custom_device)
        # Normalise to FP32: weights from safetensors may be FP16 while the
        # freshly-built classifier head stays FP32, creating mixed-precision
        # biases that cause "Input type (c10::Half) and bias type (float)" errors.
        self._custom_model.float()
        self._custom_model.eval()
        self._custom_transform_cache = {}
        self._custom_transform = self._build_custom_transform(
            self._custom_tagger_image_size_full
        )

    def _reload_custom_tagger_on_cpu(self) -> bool:
        logger.warning("Custom tagger GPU inference failed; reloading on CPU...")
        try:
            if hasattr(self, "_custom_model") and self._custom_model is not None:
                self._custom_model.to("cpu")
            self._custom_device = "cpu"
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.debug("Custom tagger reloaded on CPU")
            return True
        except Exception as cpu_error:
            logger.error(
                "Failed to reload custom tagger on CPU: %s",
                cpu_error,
                exc_info=True,
            )
            return False

    def _build_custom_transform(self, image_size: int):
        return transforms.Compose(
            [
                transforms.Resize((image_size, image_size)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225],
                ),
            ]
        )

    def _load_and_preprocess_tags(self):
        with open(
            os.path.join(self._model_location, CSV_FILE), "r", encoding="utf-8"
        ) as f:
            reader = csv.reader(f)
            line = [row for row in reader]
            header = line[0]  # tag_id,name,category,count
            rows = line[1:]
        assert (
            header[0] == "tag_id" and header[1] == "name" and header[2] == "category"
        ), f"unexpected csv format: {header}"

        self._rating_tags = [row[1] for row in rows[0:] if row[2] == "9"]
        self._general_tags = [row[1] for row in rows[0:] if row[2] == "0"]

    def _download_custom_tagger(self):
        """Download the custom anomaly tagger weights and metadata from HuggingFace if not present locally."""
        try:
            from huggingface_hub import hf_hub_download

            dest_dir = os.path.dirname(os.path.abspath(self._custom_tagger_path))
            os.makedirs(dest_dir, exist_ok=True)
            logger.info("Downloading custom tagger from %s ...", CUSTOM_TAGGER_HF_REPO)
            hf_hub_download(
                repo_id=CUSTOM_TAGGER_HF_REPO,
                filename=CUSTOM_TAGGER_FILENAME,
                local_dir=dest_dir,
            )
            hf_hub_download(
                repo_id=CUSTOM_TAGGER_HF_REPO,
                filename=CUSTOM_TAGGER_META_FILENAME,
                local_dir=dest_dir,
            )
            logger.info("Custom tagger downloaded to %s", self._custom_tagger_path)
        except Exception as e:
            logger.warning("Failed to download custom tagger: %s", e)

    def _ensure_model_files(self, force_download):
        # hf_hub_download

        # https://github.com/toriato/stable-diffusion-webui-wd14-tagger/issues/22
        # Check for the actual model files, not just the directory. The directory may
        # have been created by a previously failed download attempt, in which case we
        # still need to download the files.
        onnx_model_path = os.path.join(self._model_location, "model.onnx")
        tags_csv_path = os.path.join(self._model_location, "selected_tags.csv")
        files_present = os.path.exists(onnx_model_path) and os.path.exists(
            tags_csv_path
        )

        if not files_present or force_download:
            os.makedirs(self._model_location, exist_ok=True)
            logger.debug(
                f"downloading wd14 tagger model from hf_hub. id: {DEFAULT_WD14_TAGGER_REPO}"
            )
            # Always download ONNX model and selected_tags.csv
            from huggingface_hub import hf_hub_download

            # Download ONNX model
            logger.debug(f"Downloading ONNX model to {onnx_model_path}")
            hf_hub_download(
                repo_id=DEFAULT_WD14_TAGGER_REPO,
                filename="model.onnx",
                local_dir=self._model_location,
                force_download=True,
            )
            logger.debug(f"Downloading selected_tags.csv to {tags_csv_path}")
            hf_hub_download(
                repo_id=DEFAULT_WD14_TAGGER_REPO,
                filename="selected_tags.csv",
                local_dir=self._model_location,
                force_download=True,
            )

    def _collate_fn_remove_corrupted(self, batch):
        """Collate function that allows to remove corrupted examples in the
        dataloader. It expects that the dataloader returns 'None' when that occurs.
        The 'None's in the batch are removed.
        """
        # Filter out all the Nones (corrupted examples)
        batch = list(filter(lambda x: x is not None, batch))
        return batch

    def _run_batch(self, path_imgs, undesired_tags):
        imgs = np.array([im for _, im in path_imgs])
        try:
            probs = self.ort_sess.run(None, {self.input_name: imgs})[
                0
            ]  # onnx output numpy
        except Exception as e:
            logger.error(f"Error occurred while running ONNX model: {e}")
            logger.error(f"Images causing error: {[p for p, _ in path_imgs]}")
            return None

        probs = probs[: len(path_imgs)]
        result = {}
        for (image_path, _), prob in zip(path_imgs, probs):
            # Build all tags with their probabilities
            tag_probs = []
            # General tags
            for i, p in enumerate(prob[4 : 4 + len(self._general_tags)]):
                tag_name = self._general_tags[i]
                if p >= GENERAL_THRESHOLD and tag_name not in undesired_tags:
                    tag_probs.append((tag_name, p))
            # Sort all tags by probability
            all_tags_sorted = sorted(tag_probs, key=lambda x: x[1], reverse=True)
            combined_tags = [tag for tag, _ in all_tags_sorted]
            # Instead of writing to file, store tags in result dict
            result[image_path] = combined_tags
            logger.debug("")
            logger.debug(f"{image_path}:")
            logger.debug(f"\tTags: {combined_tags}")
        return result

    @staticmethod
    def _flatten_data_entry(data_entry):
        flat_data = []
        for item in data_entry:
            if isinstance(item, list):
                flat_data.extend(item)
            else:
                flat_data.append(item)
        return flat_data

    @staticmethod
    def _naturalize_tags(batch_result):
        # Naturalize tags for each image
        for k, tags in batch_result.items():
            tags = [TagNaturaliser.get_natural_tag(tag) for tag in tags]
            tags = [t for t in tags if t]
            batch_result[k] = tags
        return batch_result

    @staticmethod
    def _merge_video_frame_tags(frame_tags):
        merged_results = {}
        for path, tags in frame_tags.items():
            if "#frame" in path:
                base_path = path.split("#frame")[0]
                if base_path not in merged_results:
                    merged_results[base_path] = set()
                merged_results[base_path].update(tags)
            else:
                merged_results[path] = set(tags)
        # Convert sets back to sorted lists
        merged_results = {k: sorted(list(v)) for k, v in merged_results.items()}
        return merged_results

    @staticmethod
    def _filter_texts(texts):
        # Remove duplicates, empty strings, UUIDs, and date strings
        uuid_regex = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        date_regex = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")
        texts = [
            t
            for t in texts
            if t and not uuid_regex.match(t) and not date_regex.match(t)
        ]
        return texts

    def custom_tagger_ready(self) -> bool:
        return bool(
            self._use_custom_tagger
            and hasattr(self, "_custom_transform")
            and hasattr(self, "_custom_model")
            and hasattr(self, "_custom_labels")
        )

    def custom_tagger_threshold_full(self) -> float:
        return float(self._custom_tagger_threshold_full)

    def custom_tagger_image_size_full(self) -> int:
        return int(self._custom_tagger_image_size_full)

    def custom_tagger_image_size_quality_crop(self) -> int:
        return int(self._custom_tagger_image_size_quality_crop)

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

    def tag_quality_crops(self, items, stop_event=None):
        """Run the custom tagger on pre-cropped PIL images and return only
        quality-relevant tags.

        The crops should already be sized/centred on a face region at the
        custom tagger's native resolution so no additional downscaling of the
        full image is needed.

        Args:
            items: List of ``(key, PIL.Image)`` pairs.
            stop_event: Optional threading.Event to interrupt inference.

        Returns:
            Dict mapping key to list of quality tags that passed the whitelist
            filter.  Keys with no matching quality tags are omitted.
        """
        if not items:
            return {}
        if not hasattr(self, "_custom_model") or not hasattr(self, "_custom_labels"):
            logger.debug("Custom tagger not available; skipping quality crop pass.")
            return {}

        raw = self._tag_custom_items(
            items,
            stop_event=stop_event,
            threshold=self._custom_tagger_threshold_full,
            image_size=self._custom_tagger_image_size_quality_crop,
            pass_name="quality_crops",
        )
        return raw

    def _tag_custom_items(
        self,
        items,
        stop_event=None,
        threshold=None,
        image_size=None,
        pass_name: str = "full_images",
    ):
        if not items:
            return {}

        tag_threshold = (
            float(threshold)
            if threshold is not None and float(threshold) > 0
            else self._custom_tagger_threshold_full
        )
        if image_size is None:
            image_size = self._custom_tagger_image_size_full
        transform = self._custom_transform_cache.get(image_size)
        if transform is None:
            transform = self._build_custom_transform(image_size)
            self._custom_transform_cache[image_size] = transform

        logger.debug(
            "Performing custom tagging (%s) on %d items...",
            pass_name,
            len(items),
        )
        batch_size = self._effective_custom_batch_size()
        results = {}
        for batch_start in range(0, len(items), batch_size):
            if stop_event is not None and stop_event.is_set():
                logger.info("Tagging interrupted by stop event.")
                break
            batch = items[batch_start : batch_start + batch_size]
            batch_paths = []
            batch_tensors = []
            for path, image in batch:
                try:
                    batch_tensors.append(transform(image))
                    batch_paths.append(path)
                except Exception as e:
                    logger.error("Custom tagger failed to preprocess %s: %s", path, e)
            if not batch_tensors:
                continue
            inputs = torch.stack(batch_tensors)
            custom_device = getattr(self, "_custom_device", self._device)
            try:
                inputs = inputs.to(custom_device).float()
                with torch.inference_mode():
                    logits = self._custom_model(inputs)
                    probs = torch.sigmoid(logits).cpu().numpy()
            except Exception as exc:
                is_cuda_oom = isinstance(exc, torch.cuda.OutOfMemoryError) or (
                    "CUDA out of memory" in str(exc)
                )
                if is_cuda_oom and custom_device == "cuda":
                    logger.warning(
                        "Custom tagger CUDA OOM; falling back to CPU for this run."
                    )
                    if self._reload_custom_tagger_on_cpu():
                        logger.warning("Custom tagger is now running on CPU.")
                        inputs = inputs.to("cpu").float()
                        with torch.inference_mode():
                            logits = self._custom_model(inputs)
                            probs = torch.sigmoid(logits).cpu().numpy()
                    else:
                        logger.error("Custom tagger CPU fallback failed.")
                        break
                else:
                    logger.error("Custom tagger inference failed: %s", exc)
                    break
            for path, prob in zip(batch_paths, probs):
                tag_probs = []
                for label, p in zip(self._custom_labels, prob):
                    if p >= tag_threshold:
                        tag_probs.append((label, float(p)))
                all_tags_sorted = sorted(tag_probs, key=lambda x: x[1], reverse=True)
                results[path] = [tag for tag, _ in all_tags_sorted]

        return self._naturalize_tags(results)

    def _tag_images_custom(self, image_paths, stop_event=None, preloaded_images=None):
        from PIL import Image

        if not hasattr(self, "_custom_transform"):
            logger.warning("Custom tagger not initialised; skipping custom tags.")
            return {}
        if not hasattr(self, "_custom_model") or not hasattr(self, "_custom_labels"):
            logger.warning("Custom tagger model not available; skipping custom tags.")
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

        results = self._tag_custom_items(
            items,
            stop_event=stop_event,
            threshold=self._custom_tagger_threshold_full,
            image_size=self._custom_tagger_image_size_full,
            pass_name="full_images",
        )
        return self._merge_video_frame_tags(results)

    def tag_images(self, image_paths, stop_event=None, preloaded_images=None):
        """
        Tag images using WD14 and optionally extend with the custom tagger.

        Args:
            image_paths (list of str): List of image file paths to be tagged.

        Returns:
            dict: A dictionary mapping image paths to their corresponding list of tags.
        """
        undesired_tags = UNDESIRED_TAGS.split(CAPTION_SEPARATOR.strip())
        undesired_tags = set(
            [tag.strip() for tag in undesired_tags if tag.strip() != ""]
        )
        logger.debug("Removing tags: " + ", ".join(undesired_tags))

        self._ensure_tagging_ready()

        preloaded_map = preloaded_images or {}
        remaining_paths = [p for p in image_paths if str(p) not in preloaded_map]
        logger.debug(
            "[TAG_PRELOAD] total=%s preloaded_hits=%s dataloader_misses=%s",
            len(image_paths),
            len(image_paths) - len(remaining_paths),
            len(remaining_paths),
        )

        max_concurrent = self._effective_wd14_batch_size()
        onnx_batch_capacity = max(1, int(getattr(self, "_onnx_batch_capacity", 1)))
        inference_batch_size = min(max_concurrent, onnx_batch_capacity)

        logger.debug(
            "[TAG_BATCH] inference_batch_size=%s onnx_batch_capacity=%s max_concurrent=%s",
            inference_batch_size,
            onnx_batch_capacity,
            max_concurrent,
        )

        # On macOS, multiprocessing uses 'spawn' which requires pickling.
        # ONNX InferenceSession cannot be pickled, so disable workers on macOS.
        if platform.system() == "Darwin":
            worker_count = 0
        else:
            worker_count = min(
                max_concurrent, os.cpu_count() // 2 or 1, max(1, len(remaining_paths))
            )

        def run_tagging(data_loader):
            b_imgs_local = []
            results_local = {}
            tagging_failed_local = False
            for data_entry in tqdm(data_loader, smoothing=0.0, disable=self._silent):
                if stop_event is not None and stop_event.is_set():
                    logger.info("Tagging interrupted by stop event.")
                    break
                if tagging_failed_local:
                    break

                flat_data = self._flatten_data_entry(data_entry)

                for data in flat_data:
                    if stop_event is not None and stop_event.is_set():
                        logger.info("Tagging interrupted by stop event.")
                        tagging_failed_local = True
                        break
                    if data is None:
                        continue
                    image, image_path = data
                    b_imgs_local.append((image_path, image))
                    if len(b_imgs_local) >= inference_batch_size:
                        b_imgs_local = [
                            (str(image_path), image)
                            for image_path, image in b_imgs_local
                        ]
                        batch_result = self._run_batch(
                            b_imgs_local,
                            undesired_tags,
                        )
                        if batch_result is None:
                            logger.error(
                                "Tagging failed for batch: %s",
                                [p for p, _ in b_imgs_local],
                            )
                            tagging_failed_local = True
                            break

                        results_local.update(self._naturalize_tags(batch_result))
                        b_imgs_local.clear()
            return tagging_failed_local, b_imgs_local, results_local

        def run_preloaded_wd14(preloaded_map, results_local):
            if not preloaded_map:
                return
            wd14_batch = []
            for path in image_paths:
                loaded_img = preloaded_map.get(str(path))
                if loaded_img is None:
                    continue
                try:
                    prepared = ImageLoadingDatasetPrepper._preprocess_image(loaded_img)
                except Exception as exc:
                    logger.error(
                        "Could not preprocess preloaded image %s: %s", path, exc
                    )
                    continue
                wd14_batch.append((str(path), prepared))
                if len(wd14_batch) >= inference_batch_size:
                    batch_result = self._run_batch(wd14_batch, undesired_tags)
                    if batch_result is not None:
                        results_local.update(self._naturalize_tags(batch_result))
                    wd14_batch.clear()
            if wd14_batch:
                batch_result = self._run_batch(wd14_batch, undesired_tags)
                if batch_result is not None:
                    results_local.update(self._naturalize_tags(batch_result))

        b_imgs = []
        all_results = {}
        run_preloaded_wd14(preloaded_map, all_results)
        try:
            if remaining_paths:
                logger.debug(
                    "Starting tagger dataloader with worker count: %s and dataset size: %s",
                    worker_count,
                    len(remaining_paths),
                )
                dataset = ImageLoadingDatasetPrepper(remaining_paths)
                data = torch.utils.data.DataLoader(
                    dataset,
                    batch_size=inference_batch_size,
                    shuffle=False,
                    num_workers=worker_count,
                    collate_fn=self._collate_fn_remove_corrupted,
                    drop_last=False,
                    timeout=(TAGGER_DATALOADER_TIMEOUT if worker_count > 0 else 0),
                )
                _tagging_failed, b_imgs, dataloader_results = run_tagging(data)
                all_results.update(dataloader_results)
            else:
                _tagging_failed = False
        except RuntimeError as exc:
            logger.warning("Tagging dataloader stalled: %s", exc)
            if (
                worker_count > 0
                and remaining_paths
                and (stop_event is None or not stop_event.is_set())
            ):
                logger.warning(
                    "Retrying tagger dataloader with num_workers=0 for %s items",
                    len(remaining_paths),
                )
                dataset = ImageLoadingDatasetPrepper(remaining_paths)
                data = torch.utils.data.DataLoader(
                    dataset,
                    batch_size=inference_batch_size,
                    shuffle=False,
                    num_workers=0,
                    collate_fn=self._collate_fn_remove_corrupted,
                    drop_last=False,
                    timeout=0,
                )
                _tagging_failed, b_imgs, dataloader_results = run_tagging(data)
                all_results.update(dataloader_results)
            else:
                _tagging_failed = True

        if len(b_imgs) > 0 and not (stop_event is not None and stop_event.is_set()):
            b_imgs = [(str(image_path), image) for image_path, image in b_imgs]
            batch_result = self._run_batch(b_imgs, undesired_tags)
            if batch_result is None:
                logger.error(f"Tagging failed for batch: {[p for p, _ in b_imgs]}")
            else:
                for k, tags in batch_result.items():
                    tags = [TagNaturaliser.get_natural_tag(tag) for tag in tags]
                    tags = [t for t in tags if t]
                    batch_result[k] = tags
                all_results.update(batch_result)

        logger.debug(f"Completed tagging for {len(all_results)} images.")
        wd14_results = self._merge_video_frame_tags(all_results)
        if not self._use_custom_tagger:
            return wd14_results

        custom_results = self._tag_images_custom(
            image_paths,
            stop_event=stop_event,
            preloaded_images=preloaded_map,
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
        florence_caption = self._generate_florence_caption(
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

        from os import path as os_path

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
            ext = os_path.splitext(picture_path)[1].lower()
            if ext in video_exts:
                results[picture.id] = self._generate_florence_caption(
                    picture_path, _retry_on_cpu=False
                )
            else:
                batch_items.append((picture.id, picture_path))

        batch_size = max(1, int(self._florence_batch_size))
        for idx in range(0, len(batch_items), batch_size):
            chunk = batch_items[idx : idx + batch_size]
            chunk_paths = [picture_path for _, picture_path in chunk]
            captions = self._generate_florence_captions_batch(chunk_paths)
            for picture_id, picture_path in chunk:
                results[picture_id] = captions.get(picture_path)

        return results

    # Naive flatten
    @classmethod
    def _flatten_texts(cls, texts):
        flat = []

        characters = texts.get("characters") or []

        # Compose prefix
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
            full_text = query.lower()
            texts.append(full_text)
        else:
            for picture in pictures or []:
                text = picture.text_embedding_data()
                flat_text = PictureTagger._flatten_texts(text)
                filtered_text = self._filter_texts(flat_text)
                full_text = ". ".join(filtered_text)
                full_text = full_text.lower()
                texts.append(full_text)

        if not texts:
            return []

        # Generate text embedding using SBERT
        sbert_model = getattr(self, "_sbert_model", None)
        if sbert_model is None:
            try:
                sbert_model = SentenceTransformer(
                    "all-MiniLM-L6-v2", device=self._device, local_files_only=True
                )
            except OSError:
                logger.info("Downloading all-MiniLM-L6-v2 for the first time...")
                sbert_model = SentenceTransformer(
                    "all-MiniLM-L6-v2", device=self._device
                )
            self._sbert_model = sbert_model

        logger.debug(
            "Generating SBERT embeddings for %s texts on device: %s",
            len(texts),
            sbert_model.device,
        )
        text_embeddings = None
        try:
            text_embeddings = sbert_model.encode(texts, show_progress_bar=False)
            logger.debug("Done generating SBERT embeddings.")
        except RuntimeError as e:
            if "CUDA" in str(e):
                logger.warning(
                    f"SBERT embedding failed on CUDA: {e}. Falling back to CPU."
                )
                try:
                    sbert_model = SentenceTransformer(
                        "all-MiniLM-L6-v2", device="cpu", local_files_only=True
                    )
                except OSError:
                    sbert_model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
                self._sbert_model = sbert_model
                logger.info("Falling back to CPU for SBERT embeddings.")
                text_embeddings = sbert_model.encode(texts, show_progress_bar=False)
            else:
                logger.error(f"Failed to generate text embedding: {e}")
                raise

        embeddings_array = np.asarray(text_embeddings)
        return [embeddings_array[i] for i in range(len(texts))]

    def generate_clip_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a CLIP text embedding for the provided query text.
        Returns a single embedding (np.ndarray) or None.
        """
        if not query:
            return None

        self._ensure_clip_ready()

        import torch

        try:
            if not hasattr(self, "_clip_model") or self._clip_model is None:
                logger.warning(
                    "PictureTagger: CLIP model not available for text embedding."
                )
                return None

            with torch.no_grad():
                text = self._clip_tokenizer([query]).to(self._clip_device)
                text_features = self._clip_model.encode_text(text)
                text_features /= text_features.norm(dim=-1, keepdim=True)
                return text_features.cpu().numpy()[0]
        except Exception as e:
            logger.error(f"PictureTagger: Failed to generate CLIP text embedding: {e}")
            return None

    def generate_facial_features(self, picture, face_bboxes):
        """
        Generate facial features for a list of face_bboxes in a picture.
        Returns a list of facial_features (np.ndarray or None) for each bbox.
        """
        import os
        import torch
        from PIL import Image

        self._ensure_clip_ready()

        file_path = (
            picture.file_path if hasattr(picture, "file_path") else picture["file_path"]
        )
        file_path = self._resolve_picture_path(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        facial_features_list = []
        face_crops = []

        # Load image or first frame for all crops
        if ext in video_exts:
            import cv2

            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                frame = None
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
                crop = FaceUtils.load_and_crop_square_image_with_face(file_path, bbox)
                face_crops.append(crop)

        # Try to get a human-friendly description for logging
        pic_desc = getattr(picture, "description", None)
        if not pic_desc:
            pic_desc = file_path

        for i, crop in enumerate(face_crops):
            if crop is None:
                logger.warning(
                    f"Face crop is None for picture '{pic_desc}', bbox={face_bboxes[i]}"
                )
                facial_features_list.append(None)
                continue
            logger.debug(
                f"Face crop type for picture '{pic_desc}', bbox={face_bboxes[i]}: {type(crop)}"
            )
            if hasattr(crop, "size"):
                logger.debug(f"Face crop size: {crop.size}")
            try:
                img_input = (
                    self._clip_preprocess(crop).unsqueeze(0).to(self._clip_device)
                )
                with torch.no_grad():
                    features = self._clip_model.encode_image(img_input).cpu().numpy()[0]
                logger.debug(
                    f"Extracted features for picture '{pic_desc}', bbox={face_bboxes[i]}: {features[:5]}... (shape: {features.shape})"
                )
                facial_features_list.append(features)
            except RuntimeError as e:
                logger.error(
                    f"RuntimeError for picture '{pic_desc}', bbox={face_bboxes[i]}: {e}"
                )
                if (
                    ("CUDA out of memory" in str(e))
                    or ("not compatible" in str(e))
                    or ("CUDA error" in str(e))
                ):
                    self._clip_device = "cpu"
                    self._clip_model = self._clip_model.to(self._clip_device)
                    try:
                        img_input = (
                            self._clip_preprocess(crop)
                            .unsqueeze(0)
                            .to(self._clip_device)
                        )
                        with torch.no_grad():
                            features = (
                                self._clip_model.encode_image(img_input)
                                .cpu()
                                .numpy()[0]
                            )
                        logger.debug(
                            f"Extracted features (CPU fallback) for picture '{pic_desc}', bbox={face_bboxes[i]}: {features[:5]}... (shape: {features.shape})"
                        )
                        facial_features_list.append(features)
                    except Exception as e2:
                        logger.error(
                            f"CPU fallback failed for picture '{pic_desc}', bbox={face_bboxes[i]}: {e2}"
                        )
                        facial_features_list.append(None)
                else:
                    facial_features_list.append(None)
            except Exception as e:
                logger.error(
                    f"Exception for picture '{pic_desc}', bbox={face_bboxes[i]}: {e}"
                )
                facial_features_list.append(None)
        return facial_features_list

    def preprocess_query_words(self, words, top_k=3):
        """
        Preprocess a list of query words for semantic search:
        - Expand with up to top_k ranked synonyms for each word (≥4 letters, single-word only)
        - Add 'woman' if 'she' or 'her' is present, 'man' if 'he' or 'him' is present
        Returns a list of deduplicated, relevant words.
        """
        # Remove prepositions and filter words
        prepositions = {
            "about",
            "above",
            "across",
            "after",
            "against",
            "along",
            "among",
            "around",
            "at",
            "before",
            "behind",
            "below",
            "beneath",
            "beside",
            "between",
            "beyond",
            "but",
            "by",
            "concerning",
            "despite",
            "down",
            "during",
            "except",
            "for",
            "from",
            "in",
            "inside",
            "into",
            "like",
            "near",
            "of",
            "off",
            "on",
            "onto",
            "out",
            "outside",
            "over",
            "past",
            "regarding",
            "since",
            "through",
            "throughout",
            "to",
            "toward",
            "under",
            "underneath",
            "until",
            "up",
            "upon",
            "with",
            "within",
            "without",
            "have",
            "were",
        }
        cleaned = []
        lower_words = [w.lower() for w in words]
        for word in lower_words:
            if word == "she" or word == "her":
                cleaned.append("woman")
            elif word == "he" or word == "him":
                cleaned.append("man")
            elif len(word) > 3 and word not in prepositions:
                cleaned.append(word)

        return cleaned

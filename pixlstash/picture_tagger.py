#################################################################
# Adapted from Kohya_ss https://github.com/kohya-ss/sd-scripts/ #
# Under the Apache 2.0 License                                  #
# https://github.com/kohya-ss/sd-scripts/blob/main/LICENSE.md   #
#################################################################
import os
import torch

from platformdirs import user_data_dir

from .pixl_logging import get_logger
from pixlstash.inference.vram_budget import VramBudget
from pixlstash.inference.model_lifecycle import ModelLifecycleManager
from pixlstash.inference.engine import InferenceEngine
from pixlstash.inference.workflows.text_embedding import TextEmbeddingWorkflow
from pixlstash.inference.workflows.face_embedding import FaceEmbeddingWorkflow
from pixlstash.inference.workflows.description import DescriptionWorkflow
from pixlstash.inference.workflows.tagging import TaggingWorkflow
from pixlstash.utils.image_processing.face_utils import expand_bbox_to_square
from pixlstash.tagger_plugins.clip_service import ClipService
from pixlstash.tagger_plugins.sbert import SBertService
from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
from pixlstash.tagger_plugins.wd14 import WD14Service
from pixlstash.tagger_plugins.florence2 import (
    Florence2Service,
)

logger = get_logger(__name__)


MODEL_DIR = os.path.join(user_data_dir("pixlstash"), "downloaded_models")
MAX_CONCURRENT_IMAGES_GPU = 64
MAX_CONCURRENT_IMAGES_CPU = 8
DEFAULT_MAX_VRAM_GB: float | None = None



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
            vram_cap_fn=lambda base_mb, per_item_mb: self._engine.vram_budget.limited_batch_cap(
                base_mb, per_item_mb
            ),
        )

        _vram_budget = VramBudget(self._device)
        _lifecycle = ModelLifecycleManager(self._device)
        self._engine = InferenceEngine(
            device=self._device,
            clip_service=self._clip_service,
            sbert_service=self._sbert_service,
            wd14_service=self._wd14_service,
            custom_service=self._custom_service,
            florence_service=self._florence_service,
            vram_budget=_vram_budget,
            lifecycle=_lifecycle,
            force_cpu=(self._device != "cuda"),
        )

        self.set_max_vram_usage_gb(DEFAULT_MAX_VRAM_GB)

    def set_max_vram_usage_gb(self, max_vram_gb: float | None):
        self._engine.vram_budget.set_budget_gb(max_vram_gb)

    def suggested_tag_task_size(self) -> int:
        """Delegate to :meth:`TaggingWorkflow.suggested_task_size`."""
        return self.tagging_workflow.suggested_task_size()

    def estimate_task_vram_mb(self, image_count: int) -> int:
        """Delegate to :meth:`TaggingWorkflow.estimated_vram_mb`."""
        return self.tagging_workflow.estimated_vram_mb(image_count)

    def estimate_task_incremental_vram_mb(self, image_count: int) -> int:
        """Delegate to :meth:`TaggingWorkflow.estimated_incremental_vram_mb`."""
        return self.tagging_workflow.estimated_incremental_vram_mb(image_count)

    def suggested_image_embedding_batch_size(self) -> int:
        """Delegate to :meth:`ClipEmbeddingWorkflow.suggested_batch_size`."""
        return self.clip_embedding_workflow.suggested_batch_size()

    def estimate_image_embedding_vram_mb(self, image_count: int) -> int:
        """Delegate to :meth:`ClipEmbeddingWorkflow.estimated_vram_mb`."""
        return self.clip_embedding_workflow.estimated_vram_mb(image_count)

    def estimate_face_extraction_vram_mb(self) -> int:
        """Delegate to :meth:`FaceEmbeddingWorkflow.estimated_vram_mb`."""
        return self.face_embedding_workflow.estimated_vram_mb()

    def __enter__(self):
        logger.debug("PictureTagger.__enter__ called.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self._engine.lifecycle.aggressive_unload(
            clip_service=self._clip_service,
            wd14_service=self._wd14_service,
            sbert_service=self._sbert_service,
            custom_service=self._custom_service,
            florence_service=self._florence_service,
        )
        self._models_ready = False
        logger.debug("PictureTagger.__exit__ called, all resources released.")

    def unload_tagger_session(self):
        """Release the WD14 ONNX inference session and its CUDA arena.

        ORT's BFC allocator holds the full activation workspace for the session
        lifetime.  Deleting the session object frees that CUDA memory so that
        subsequent GPU pipelines (embeddings, descriptions) start with a clean
        VRAM budget.  The session is rebuilt lazily the next time tagging runs.
        """
        self._engine.lifecycle.unload_wd14_session(self._wd14_service)

    def aggressive_unload(self):
        logger.warning("PictureTagger.aggressive_unload() called, releasing models...")
        self.close()

    def safe_idle_unload(self):
        """
        Release non-captioning models during idle periods while keeping Florence loaded.

        This avoids expensive/fragile Florence unload-reload cycles during normal runtime,
        but still frees a significant amount of CPU/GPU memory from other models.
        """
        self._engine.lifecycle.safe_idle_unload(
            clip_service=self._clip_service,
            wd14_service=self._wd14_service,
            sbert_service=self._sbert_service,
            custom_service=self._custom_service,
        )
        self._models_ready = self._florence_service.is_loaded()

    def _ensure_tagging_ready(self):
        success = self._engine.lifecycle.ensure_tagging_ready(
            self._wd14_service,
            self._custom_service,
            self._use_wd14_tagger,
            self._use_custom_tagger,
        )
        if not success:
            self._use_custom_tagger = False
        self._models_ready = True

    def _ensure_captioning_ready(self):
        self._engine.lifecycle.ensure_captioning_ready(self._florence_service)
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

        .. deprecated::
            Import and call
            :func:`pixlstash.utils.image_processing.face_utils.expand_bbox_to_square`
            directly instead.
        """
        return expand_bbox_to_square(bbox, img_width, img_height, target_size)

    def ensure_clip_ready(self) -> None:
        """Load the CLIP model if not already loaded."""
        self._clip_service.ensure_ready()

    @property
    def force_cpu(self) -> bool:
        """Return ``True`` when this tagger is configured to use CPU-only inference."""
        return self._engine.force_cpu

    @property
    def clip_service(self) -> ClipService:
        """The underlying :class:`ClipService` instance."""
        return self._clip_service

    @property
    def text_embedding_workflow(self) -> TextEmbeddingWorkflow:
        """A :class:`TextEmbeddingWorkflow` bound to this tagger's engine."""
        return TextEmbeddingWorkflow(engine=self._engine)

    @property
    def face_embedding_workflow(self) -> FaceEmbeddingWorkflow:
        """A :class:`FaceEmbeddingWorkflow` bound to this tagger's engine."""
        return FaceEmbeddingWorkflow(engine=self._engine)

    @property
    def description_workflow(self) -> DescriptionWorkflow:
        """A :class:`DescriptionWorkflow` bound to this tagger's engine."""
        return DescriptionWorkflow(engine=self._engine, image_root=self._image_root)

    @property
    def clip_embedding_workflow(self) -> "ClipEmbeddingWorkflow":
        """A :class:`ClipEmbeddingWorkflow` bound to this tagger's engine."""
        from pixlstash.inference.workflows.clip_embedding import ClipEmbeddingWorkflow

        return ClipEmbeddingWorkflow(engine=self._engine)

    @property
    def tagging_workflow(self) -> TaggingWorkflow:
        """A :class:`TaggingWorkflow` bound to this tagger's current configuration."""
        return TaggingWorkflow(
            engine=self._engine,
            use_wd14=self._use_wd14_tagger,
            use_custom=self._use_custom_tagger,
            threshold_offset=self._custom_tagger_threshold_offset,
        )



"""Model lifecycle management: load ordering, idle unload, CUDA cleanup."""

from __future__ import annotations

import gc
import threading

import torch

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.model_utils import trim_process_memory

logger = get_logger(__name__)


class ModelLifecycleManager:
    """Manages the init lock, ensure-ready policies, and unload strategies
    across a set of inference services.

    Owns the single ``threading.Lock`` that serialises concurrent model
    initialisation so two tasks starting simultaneously cannot both try to
    load the same model weights.

    The key policy encoded here is that Florence-2 stays resident across
    ``safe_idle_unload`` because its reload is expensive and fragile.
    CLIP, WD14, SBERT, and the custom tagger are released on idle.

    Args:
        device: Inference device (``"cuda"`` or ``"cpu"``).
    """

    def __init__(self, device: str) -> None:
        self._device = device
        self._init_lock = threading.Lock()

    @property
    def init_lock(self) -> threading.Lock:
        """The single initialisation lock shared by all services."""
        return self._init_lock

    def ensure_tagging_ready(
        self,
        wd14_service,
        custom_service,
        use_wd14: bool,
        use_custom: bool,
    ) -> bool:
        """Load WD14 and/or the custom tagger under the init lock.

        Args:
            wd14_service: :class:`WD14Service` instance.
            custom_service: :class:`PixlStashTaggerService` instance.
            use_wd14: Whether WD14 should be loaded.
            use_custom: Whether the custom tagger should be loaded.

        Returns:
            ``True`` on success; ``False`` if the custom tagger failed to load
            (caller should set ``use_custom_tagger = False``).
        """
        custom_failed = False
        with self._init_lock:
            if use_wd14:
                wd14_service.init()
            if use_custom and not custom_service.is_loaded():
                if not custom_service.init_or_cpu_fallback():
                    custom_failed = True
        return not custom_failed

    def ensure_captioning_ready(self, florence_service) -> None:
        """Load Florence-2 under the init lock if not already loaded.

        Args:
            florence_service: :class:`Florence2Service` instance.
        """
        if florence_service.is_loaded():
            return
        with self._init_lock:
            if not florence_service.is_loaded():
                florence_service.ensure_ready()

    def aggressive_unload(
        self,
        clip_service=None,
        wd14_service=None,
        sbert_service=None,
        custom_service=None,
        florence_service=None,
    ) -> None:
        """Unload all models and release all GPU/CPU memory.

        Args:
            clip_service: Optional :class:`ClipService` to unload.
            wd14_service: Optional :class:`WD14Service` to unload.
            sbert_service: Optional :class:`SBertService` to unload.
            custom_service: Optional :class:`PixlStashTaggerService` to unload.
            florence_service: Optional :class:`Florence2Service` to unload.
        """
        logger.warning("ModelLifecycleManager.aggressive_unload() called.")
        try:
            if clip_service is not None:
                clip_service.unload()
            if wd14_service is not None:
                wd14_service.unload()
            if sbert_service is not None:
                sbert_service.unload()
            if custom_service is not None:
                custom_service.unload()
            if florence_service is not None:
                florence_service._model = None
                florence_service._processor = None
                florence_service._model_device = None
        except Exception as exc:
            logger.warning("Exception during aggressive unload: %s", exc)

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
        trim_process_memory()

    def safe_idle_unload(
        self,
        clip_service=None,
        wd14_service=None,
        sbert_service=None,
        custom_service=None,
    ) -> None:
        """Release non-captioning models during idle periods.

        Florence-2 is intentionally kept resident because reloading it is
        expensive and can be fragile on some CUDA setups.  CLIP, WD14,
        SBERT, and the custom tagger are released.

        Args:
            clip_service: Optional :class:`ClipService` to unload.
            wd14_service: Optional :class:`WD14Service` to unload.
            sbert_service: Optional :class:`SBertService` to unload.
            custom_service: Optional :class:`PixlStashTaggerService` to unload.
        """
        logger.warning(
            "ModelLifecycleManager.safe_idle_unload() called, releasing non-captioning models."
        )
        try:
            if clip_service is not None:
                clip_service.unload()
                logger.debug("Released CLIP service models.")
            if wd14_service is not None:
                wd14_service.unload()
                logger.debug("Released WD14 service models.")
            if sbert_service is not None:
                sbert_service.unload()
                logger.debug("Released SBERT service models.")
            if custom_service is not None:
                custom_service.unload()
                logger.debug("Released custom tagger service models.")
        except Exception as exc:
            logger.warning("Exception during safe idle unload: %s", exc)

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
        trim_process_memory()

    def unload_wd14_session(self, wd14_service) -> None:
        """Release the WD14 ONNX inference session and its CUDA memory arena.

        ORT's BFC allocator holds the full activation workspace for the session
        lifetime.  Deleting the session object frees that CUDA memory so that
        subsequent GPU pipelines (embeddings, descriptions) start with a clean
        VRAM budget.  The session is rebuilt lazily on next tagging.

        Args:
            wd14_service: :class:`WD14Service` instance.
        """
        if wd14_service.is_loaded():
            wd14_service.unload()
            logger.info(
                "ModelLifecycleManager: WD14 ONNX session unloaded (CUDA arena freed)."
            )
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

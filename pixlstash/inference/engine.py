"""InferenceEngine: DI root holding the service registry, VRAM budget, and lifecycle."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pixlstash.inference.vram_budget import VramBudget
from pixlstash.inference.model_lifecycle import ModelLifecycleManager
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.tagger_plugins.clip_service import ClipService
    from pixlstash.tagger_plugins.sbert import SBertService
    from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
    from pixlstash.tagger_plugins.wd14 import WD14Service
    from pixlstash.tagger_plugins.florence2 import Florence2Service
    from pixlstash.inference.workflows.tagging import TaggingWorkflow
    from pixlstash.inference.workflows.description import DescriptionWorkflow
    from pixlstash.inference.workflows.text_embedding import TextEmbeddingWorkflow
    from pixlstash.inference.workflows.face_embedding import FaceEmbeddingWorkflow
    from pixlstash.inference.workflows.clip_embedding import ClipEmbeddingWorkflow

logger = get_logger(__name__)

_MAX_CONCURRENT_GPU = 64
_MAX_CONCURRENT_CPU = 8


class InferenceEngine:
    """Dependency-injection root for one worker-process inference context.

    Holds the five service instances, the :class:`VramBudget`, and the
    :class:`ModelLifecycleManager`.  Workflow objects receive this engine
    via constructor injection to access services and configuration.

    Use :meth:`create` to construct a fully-wired engine in one call.

    Args:
        device: Inference device string (``"cuda"`` or ``"cpu"``).
        clip_service: :class:`ClipService` instance (lazy-loaded on first use).
        sbert_service: :class:`SBertService` instance.
        wd14_service: :class:`WD14Service` instance.
        pixlstash_tagger_service: :class:`PixlStashTaggerService` instance.
        florence_service: :class:`Florence2Service` instance (may be ``None``
            during construction; set before use).
        vram_budget: Pre-configured :class:`VramBudget` for this engine.
        lifecycle: :class:`ModelLifecycleManager` for this engine.
        force_cpu: When ``True`` all inference is forced onto the CPU.
        image_root: Filesystem root for picture storage (used by workflows).
        keep_models_in_memory: When ``False`` models are unloaded during idle
            periods.
        insightface_model_pack: Name of the InsightFace model pack used by the
            face pipeline (e.g. ``"buffalo_l"`` or ``"auraface"``).
        wd14_enabled: Whether the WD14 tagger is active.
        pixlstash_tagger_enabled: Whether the PixlStash tagger is active.
        pixlstash_tagger_threshold_offset: Score threshold adjustment for the
            PixlStash tagger.
        tagger_settings: Full plugin settings dict (takes precedence over the
            per-tagger flags when provided).
    """

    def __init__(
        self,
        device: str,
        clip_service: "ClipService",
        sbert_service: "SBertService",
        wd14_service: "WD14Service",
        pixlstash_tagger_service: "PixlStashTaggerService",
        florence_service: "Florence2Service | None",
        vram_budget: VramBudget,
        lifecycle: ModelLifecycleManager,
        force_cpu: bool = False,
        image_root: str | None = None,
        keep_models_in_memory: bool = True,
        insightface_model_pack: str = "buffalo_l",
        wd14_enabled: bool = True,
        pixlstash_tagger_enabled: bool = True,
        pixlstash_tagger_threshold_offset: float = 0.0,
        tagger_settings: dict | None = None,
    ) -> None:
        self.device = device
        self.clip_service = clip_service
        self.sbert_service = sbert_service
        self.wd14_service = wd14_service
        self.pixlstash_tagger_service = pixlstash_tagger_service
        self.florence_service = florence_service
        self.vram_budget = vram_budget
        self.lifecycle = lifecycle
        self.force_cpu = force_cpu
        self.image_root = image_root
        self._keep_models_in_memory = keep_models_in_memory
        self.insightface_model_pack = insightface_model_pack
        # tagger_settings is the authoritative config; the per-tagger flags are
        # kept for backward compat but derived from settings when settings are set.
        if tagger_settings is not None:
            self._tagger_settings: dict = tagger_settings
        else:
            # Build a minimal settings dict from the legacy flags.
            self._tagger_settings = {
                "active_description_plugin": "florence2",
                "active_tag_plugin": "pixlstash_tagger",
                "plugins": {
                    "wd14": {
                        "enabled": bool(wd14_enabled),
                        "params": {"threshold": 0.85},
                    },
                    "pixlstash_tagger": {
                        "enabled": bool(pixlstash_tagger_enabled),
                        "params": {
                            "threshold_offset": float(pixlstash_tagger_threshold_offset)
                        },
                    },
                    "florence2": {
                        "params": {"max_new_tokens": 120, "fast_mode": False},
                    },
                },
            }
        # Legacy private flags — kept so existing setters don't break.
        self._wd14_enabled = bool(wd14_enabled)
        self._pixlstash_tagger_enabled = bool(pixlstash_tagger_enabled)
        self._pixlstash_tagger_threshold_offset = float(
            pixlstash_tagger_threshold_offset
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def keep_models_in_memory(self) -> bool:
        """Whether models are kept loaded between inference runs."""
        return self._keep_models_in_memory

    @property
    def tagger_settings(self) -> dict:
        """Full tagger plugin settings dict."""
        return self._tagger_settings

    @property
    def wd14_enabled(self) -> bool:
        """Whether the WD14 tagger is currently enabled."""
        try:
            return bool(
                self._tagger_settings["plugins"]["wd14"].get(
                    "enabled", self._wd14_enabled
                )
            )
        except (KeyError, TypeError):
            return self._wd14_enabled

    @property
    def pixlstash_tagger_enabled(self) -> bool:
        """Whether the PixlStash tagger is currently enabled."""
        try:
            return bool(
                self._tagger_settings["plugins"]["pixlstash_tagger"].get(
                    "enabled", self._pixlstash_tagger_enabled
                )
            )
        except (KeyError, TypeError):
            return self._pixlstash_tagger_enabled

    # ------------------------------------------------------------------
    # Workflow accessors
    # ------------------------------------------------------------------

    @property
    def tagging_workflow(self) -> "TaggingWorkflow":
        """Return a :class:`TaggingWorkflow` bound to the current engine config."""
        from pixlstash.inference.workflows.tagging import TaggingWorkflow

        plugins = self._tagger_settings.get("plugins", {})
        pixl_cfg = plugins.get("pixlstash_tagger", {})
        active = (
            self._tagger_settings.get("active_tag_plugin", "pixlstash_tagger")
            or "pixlstash_tagger"
        )
        return TaggingWorkflow(
            engine=self,
            use_wd14=(active == "wd14"),
            use_pixlstash_tagger=(active == "pixlstash_tagger"),
            threshold_offset=float(
                pixl_cfg.get("params", {}).get(
                    "threshold_offset", self._pixlstash_tagger_threshold_offset
                )
            ),
            tagger_settings=self._tagger_settings,
        )

    @property
    def description_workflow(self) -> "DescriptionWorkflow":
        """Return a :class:`DescriptionWorkflow` bound to this engine."""
        from pixlstash.inference.workflows.description import DescriptionWorkflow

        return DescriptionWorkflow(engine=self, image_root=self.image_root)

    @property
    def text_embedding_workflow(self) -> "TextEmbeddingWorkflow":
        """Return a :class:`TextEmbeddingWorkflow` bound to this engine."""
        from pixlstash.inference.workflows.text_embedding import TextEmbeddingWorkflow

        return TextEmbeddingWorkflow(engine=self)

    @property
    def face_embedding_workflow(self) -> "FaceEmbeddingWorkflow":
        """Return a :class:`FaceEmbeddingWorkflow` bound to this engine."""
        from pixlstash.inference.workflows.face_embedding import FaceEmbeddingWorkflow

        return FaceEmbeddingWorkflow(engine=self)

    @property
    def clip_embedding_workflow(self) -> "ClipEmbeddingWorkflow":
        """Return a :class:`ClipEmbeddingWorkflow` bound to this engine."""
        from pixlstash.inference.workflows.clip_embedding import ClipEmbeddingWorkflow

        return ClipEmbeddingWorkflow(engine=self)

    # ------------------------------------------------------------------
    # Configuration setters
    # ------------------------------------------------------------------

    def set_max_vram_usage_gb(self, max_vram_gb: float | None) -> None:
        """Update the VRAM budget cap."""
        self.vram_budget.set_budget_gb(max_vram_gb)

    def set_keep_models_in_memory(self, value: bool) -> None:
        """Control whether models stay loaded between inference runs."""
        self._keep_models_in_memory = bool(value)

    def set_tagger_settings(self, settings: dict) -> None:
        """Replace the full tagger plugin settings dict.

        Also keeps the legacy per-tagger flags in sync so that existing code
        reading ``engine.wd14_enabled`` / ``engine.pixlstash_tagger_enabled``
        continues to work without changes.
        """
        self._tagger_settings = settings
        plugins = settings.get("plugins", {})
        wd14_cfg = plugins.get("wd14", {})
        pixl_cfg = plugins.get("pixlstash_tagger", {})
        self._wd14_enabled = bool(wd14_cfg.get("enabled", False))
        self._pixlstash_tagger_enabled = bool(pixl_cfg.get("enabled", False))
        self._pixlstash_tagger_threshold_offset = float(
            pixl_cfg.get("params", {}).get("threshold_offset", 0.0)
        )
        # Keep the WD14 service threshold in sync.
        wd14_threshold = wd14_cfg.get("params", {}).get("threshold")
        if wd14_threshold is not None:
            self.wd14_service.set_threshold(float(wd14_threshold))

    def set_wd14_tagger_enabled(self, enabled: bool) -> None:
        """Enable or disable the WD14 tagger."""
        self._wd14_enabled = bool(enabled)
        try:
            self._tagger_settings["plugins"]["wd14"]["enabled"] = self._wd14_enabled
        except (KeyError, TypeError):
            logger.warning(
                "Failed to update tagger_settings when setting WD14 enabled=%s",
                enabled,
            )

    def set_pixlstash_tagger_enabled(self, enabled: bool) -> None:
        """Enable or disable the PixlStash tagger (only if model files exist)."""
        if bool(enabled) and not self._pixlstash_tagger_enabled:
            if os.path.isfile(
                self.pixlstash_tagger_service._model_path
            ) and os.path.isfile(self.pixlstash_tagger_service._meta_path):
                self._pixlstash_tagger_enabled = True
        elif not bool(enabled):
            self._pixlstash_tagger_enabled = False
        try:
            self._tagger_settings["plugins"]["pixlstash_tagger"]["enabled"] = (
                self._pixlstash_tagger_enabled
            )
        except (KeyError, TypeError):
            logger.warning(
                "Failed to update tagger_settings when setting PixlStash tagger enabled=%s",
                enabled,
            )

    def set_wd14_threshold(self, threshold: float) -> None:
        """Update the WD14 inference threshold."""
        self.wd14_service.set_threshold(threshold)
        try:
            self._tagger_settings["plugins"]["wd14"]["params"]["threshold"] = float(
                threshold
            )
        except (KeyError, TypeError):
            logger.warning(
                "Failed to update tagger_settings when setting WD14 threshold=%s",
                threshold,
            )

    def set_pixlstash_tagger_threshold_offset(self, offset: float) -> None:
        """Update the PixlStash tagger score threshold offset."""
        self._pixlstash_tagger_threshold_offset = float(offset)
        try:
            self._tagger_settings["plugins"]["pixlstash_tagger"]["params"][
                "threshold_offset"
            ] = float(offset)
        except (KeyError, TypeError):
            logger.warning(
                "Failed to update tagger_settings when setting PixlStash tagger threshold_offset=%s",
                offset,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Unload all models and release GPU/CPU memory."""
        self.lifecycle.aggressive_unload(
            clip_service=self.clip_service,
            wd14_service=self.wd14_service,
            sbert_service=self.sbert_service,
            pixlstash_tagger_service=self.pixlstash_tagger_service,
            florence_service=self.florence_service,
        )

    def aggressive_unload(self) -> None:
        """Alias for :meth:`close`."""
        self.close()

    def safe_idle_unload(self) -> None:
        """Release non-captioning models during idle periods."""
        self.lifecycle.safe_idle_unload(
            clip_service=self.clip_service,
            wd14_service=self.wd14_service,
            sbert_service=self.sbert_service,
            pixlstash_tagger_service=self.pixlstash_tagger_service,
        )

    def unload_tagger_session(self) -> None:
        """Release the WD14 ONNX inference session to free VRAM."""
        self.lifecycle.unload_wd14_session(self.wd14_service)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def max_concurrent_images(self) -> int:
        """Return the maximum number of images to process concurrently."""
        if self.device == "cpu":
            return _MAX_CONCURRENT_CPU
        return _MAX_CONCURRENT_GPU

    def description_batch_size(self) -> int:
        """Return the current Florence-2 description batch size."""
        return self.florence_service.description_batch_size()

    def pixlstash_tagger_version(self) -> int:
        """Return the version integer from the PixlStash tagger meta.json."""
        return self.pixlstash_tagger_service.version()

    def pixlstash_tagger_batch_size(self) -> int:
        """Return the effective batch size for the PixlStash tagger."""
        return self._effective_pixlstash_tagger_batch_size()

    def pixlstash_tagger_ready(self) -> bool:
        """Return ``True`` if the PixlStash tagger is enabled and loaded."""
        return bool(
            self._pixlstash_tagger_enabled and self.pixlstash_tagger_service.is_loaded()
        )

    def ensure_pixlstash_tagger_ready(self) -> bool:
        """Load the PixlStash anomaly tagger on demand (idempotent).

        ``safe_idle_unload`` releases the tagger between tagging runs, so
        interactive consumers (the review anomaly-region endpoint) must be able
        to bring it back without waiting for a tagging task to keep it resident.
        Downloads the model first if its files are missing, then loads it under
        the shared init lock via the lifecycle manager.

        Returns:
            ``True`` if the tagger is enabled and ready for inference, ``False``
            if it is disabled or failed to load.
        """
        if not self._pixlstash_tagger_enabled:
            return False
        service = self.pixlstash_tagger_service
        if service.is_loaded():
            return True
        if service.needs_download():
            service.download()
        return self.lifecycle.ensure_tagging_ready(
            self.wd14_service,
            service,
            use_wd14=False,
            use_pixlstash_tagger=True,
        )

    def ensure_clip_ready(self) -> None:
        """Load the CLIP model if not already loaded."""
        self.clip_service.ensure_ready()

    def _ensure_captioning_ready(self) -> None:
        """Load Florence-2 if not already loaded."""
        self.lifecycle.ensure_captioning_ready(self.florence_service)

    def detect_objects(self, image_paths: list, prompt: str | None = None) -> dict:
        """Run Florence-2 object detection / phrase grounding on a batch.

        Loads Florence-2 if needed (shared with captioning) and delegates to
        :meth:`~pixlstash.tagger_plugins.florence2.Florence2Service.detect_objects`.

        Args:
            image_paths: Still-image file paths to detect objects in.
            prompt: Optional phrase to ground; empty/None → dense ``<OD>``.

        Returns:
            ``{path: [(label, [x1, y1, x2, y2], score_or_None), ...]}``.
        """
        self.lifecycle.ensure_captioning_ready(self.florence_service)
        return self.florence_service.detect_objects(image_paths, prompt=prompt)

    def is_captioning_initialized(self) -> bool:
        """Return ``True`` if Florence-2 is currently loaded."""
        return self.florence_service.is_loaded()

    def _effective_pixlstash_tagger_batch_size(self) -> int:
        """Return the VRAM-constrained batch size for the PixlStash tagger."""
        return self.tagging_workflow.effective_pixlstash_tagger_batch_size()

    def _effective_wd14_batch_size(self) -> int:
        """Return the VRAM-constrained batch size for WD14."""
        return self.tagging_workflow.effective_wd14_batch_size()

    def loaded_model_state(self) -> dict:
        """Return a dict snapshot of which models are currently loaded."""
        state = self.florence_service.state_info()
        state.update(
            {
                "clip_loaded": self.clip_service.is_loaded(),
                "wd14_onnx_loaded": self.wd14_service.is_loaded(),
                "sbert_loaded": self.sbert_service.is_loaded(),
                "pixlstash_tagger_loaded": self.pixlstash_tagger_service.is_loaded(),
                "keep_models_in_memory": self._keep_models_in_memory,
            }
        )
        return state

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        device: str | None = None,
        image_root: str | None = None,
        force_cpu: bool = False,
        fast_captions: bool = False,
        max_vram_gb: float | None = None,
        wd14_enabled: bool = True,
        pixlstash_tagger_enabled: bool = True,
        wd14_threshold: float | None = None,
        pixlstash_tagger_threshold_offset: float = 0.0,
        keep_models_in_memory: bool = True,
        insightface_model_pack: str = "buffalo_l",
        tagger_settings: dict | None = None,
    ) -> "InferenceEngine":
        """Construct a fully-wired :class:`InferenceEngine`.

        Args:
            device: Inference device (``"cuda"`` or ``"cpu"``).  Auto-detected
                when ``None``.
            image_root: Filesystem root for picture storage.
            force_cpu: When ``True`` forces CPU inference regardless of CUDA
                availability.
            fast_captions: When ``True`` enables fast (lower-quality) caption
                mode in Florence-2.
            max_vram_gb: Optional VRAM budget cap in gigabytes.
            wd14_enabled: Whether to enable WD14 tagging.
            pixlstash_tagger_enabled: Whether to enable the PixlStash tagger.
            wd14_threshold: Optional override for the WD14 inference threshold.
            pixlstash_tagger_threshold_offset: Score threshold adjustment for
                the PixlStash tagger.
            keep_models_in_memory: Whether to keep models loaded between runs.
            insightface_model_pack: Name of the InsightFace model pack used by
                the face pipeline (e.g. ``"buffalo_l"`` or ``"auraface"``).

        Returns:
            A fully constructed :class:`InferenceEngine` ready for use.
        """
        import torch
        from platformdirs import user_data_dir
        from pixlstash.tagger_plugins.clip_service import ClipService
        from pixlstash.tagger_plugins.sbert import SBertService
        from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
        from pixlstash.tagger_plugins.wd14 import WD14Service
        from pixlstash.tagger_plugins.florence2 import Florence2Service

        model_dir = os.path.join(user_data_dir("pixlstash"), "downloaded_models")

        if force_cpu:
            device = "cpu"
        elif device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        clip_service = ClipService(device=device)
        sbert_service = SBertService(device=device)

        # Mutable cell so batch_size_fn closures can reference the engine
        # before it is constructed.
        _engine_cell: list[InferenceEngine | None] = [None]

        pixlstash_tagger_service = PixlStashTaggerService(
            device=device,
            model_dir=model_dir,
            batch_size_fn=lambda: (
                _engine_cell[0]._effective_pixlstash_tagger_batch_size()
                if _engine_cell[0] is not None
                else 1
            ),
        )
        if pixlstash_tagger_service.needs_download():
            pixlstash_tagger_service.download()
        if not os.path.isfile(
            pixlstash_tagger_service._model_path
        ) or not os.path.isfile(pixlstash_tagger_service._meta_path):
            logger.warning(
                "PixlStash tagger not found at %s, disabling.",
                pixlstash_tagger_service._model_path,
            )
            pixlstash_tagger_enabled = False
        else:
            logger.info(
                "PixlStash tagger loaded (version %d) from %s",
                pixlstash_tagger_service.version(),
                pixlstash_tagger_service._model_path,
            )

        wd14_service = WD14Service(
            device=device,
            model_dir=model_dir,
            batch_size_fn=lambda: (
                _engine_cell[0]._effective_wd14_batch_size()
                if _engine_cell[0] is not None
                else 1
            ),
            silent=True,
        )
        if wd14_service.needs_download():
            # Best-effort, mirroring the PixlStash tagger above: a transient
            # model-download failure (e.g. a HuggingFace 429) must disable WD14
            # tagging, not propagate out of engine creation and fail the
            # caller. Tagging is async/best-effort, so a network blip here must
            # never fail a user's import.
            try:
                wd14_service.download()
            except Exception as exc:
                logger.warning("WD14 tagger download failed (%s), disabling.", exc)
        if wd14_service.needs_download():
            logger.warning("WD14 tagger model not found in %s, disabling.", model_dir)
            wd14_enabled = False

        vram_budget = VramBudget(device)
        vram_budget.set_budget_gb(max_vram_gb)
        lifecycle = ModelLifecycleManager(device)

        # Create engine without florence_service first (chicken-and-egg: Florence
        # needs closures over the engine, so the engine must exist first).
        engine = cls(
            device=device,
            clip_service=clip_service,
            sbert_service=sbert_service,
            wd14_service=wd14_service,
            pixlstash_tagger_service=pixlstash_tagger_service,
            florence_service=None,
            vram_budget=vram_budget,
            lifecycle=lifecycle,
            force_cpu=force_cpu or (device == "cpu"),
            image_root=image_root,
            keep_models_in_memory=keep_models_in_memory,
            insightface_model_pack=insightface_model_pack,
            wd14_enabled=wd14_enabled,
            pixlstash_tagger_enabled=pixlstash_tagger_enabled,
            pixlstash_tagger_threshold_offset=pixlstash_tagger_threshold_offset,
            tagger_settings=tagger_settings,
        )
        _engine_cell[0] = engine

        florence_service = Florence2Service(
            device=device,
            fast_captions=fast_captions,
            force_cpu_fn=lambda: engine.force_cpu,
            max_concurrent_fn=engine.max_concurrent_images,
            vram_cap_fn=lambda base_mb, per_item_mb: (
                engine.vram_budget.limited_batch_cap(base_mb, per_item_mb)
            ),
        )
        engine.florence_service = florence_service

        if wd14_threshold is not None:
            wd14_service.set_threshold(wd14_threshold)

        return engine

"""Tagging workflow: WD14 and PixlStash-tagger inference for batch picture tagging."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.service.caption_utils import merge_video_frame_tags

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine

logger = get_logger(__name__)

_VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"})
_MAX_CONCURRENT_GPU = 64
_MAX_CONCURRENT_CPU = 8


class TaggingWorkflow:
    """Runs WD14 and/or PixlStash-tagger inference against a batch of images.

    This workflow wraps the two tag models (WD14 ONNX and the PixlStash
    anomaly tagger) behind a unified API.  Lifecycle management
    (loading / unloading models) is delegated to the engine's
    :class:`~pixlstash.inference.model_lifecycle.ModelLifecycleManager`.

    Args:
        engine: The :class:`~pixlstash.inference.engine.InferenceEngine` that
            holds the already-constructed service instances.
        use_wd14: Whether WD14 inference is enabled.
        use_pixlstash_tagger: Whether the PixlStash tagger is enabled.
        threshold_offset: Score threshold adjustment applied to the PixlStash
            tagger at inference time (positive values raise the bar).
    """

    def __init__(
        self,
        engine: "InferenceEngine",
        use_wd14: bool,
        use_pixlstash_tagger: bool,
        threshold_offset: float = 0.0,
        tagger_settings: dict | None = None,
    ) -> None:
        self._engine = engine
        self._use_wd14 = use_wd14
        self._use_pixlstash_tagger = use_pixlstash_tagger
        self._threshold_offset = threshold_offset
        self._tagger_settings = tagger_settings or {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_wd14_enabled(self) -> bool:
        """Whether WD14 inference is active for this workflow instance."""
        return self._use_wd14

    @property
    def is_pixlstash_tagger_enabled(self) -> bool:
        """Whether the PixlStash tagger is active for this workflow instance."""
        return self._use_pixlstash_tagger

    def pixlstash_tagger_image_size_quality_crop(self) -> int:
        """Return the quality-crop image size expected by the PixlStash tagger."""
        return int(self._engine.pixlstash_tagger_service._image_size_quality_crop)

    # ------------------------------------------------------------------
    # Public inference methods
    # ------------------------------------------------------------------

    def tag_images(
        self,
        image_paths,
        stop_event=None,
        preloaded_images=None,
        out_raw_pixlstash_scores: dict | None = None,
        engine_override: str | None = None,
    ) -> dict[str, list[str]]:
        """Tag a batch of images using the active tag plugin.

        The active plugin is read from ``tagger_settings['active_tag_plugin']``
        unless *engine_override* is supplied.  When neither is set, ``'pixlstash_tagger'``
        is used as a fallback.  Passing ``engine_override=''`` or setting
        ``active_tag_plugin`` to ``None`` returns an empty dict (no tagging).

        Args:
            image_paths: Sequence of absolute image/video file paths.
            stop_event: Optional :class:`threading.Event` to interrupt inference.
            preloaded_images: Optional ``{path: PIL.Image}`` map to skip
                re-loading images from disk.
            out_raw_pixlstash_scores: When provided, per-label confidence scores
                from the PixlStash tagger's full-image pass are written here
                (``{path: {label: float}}``).  Only populated when the active
                plugin is ``'pixlstash_tagger'``.
            engine_override: If given, run this specific plugin instead of the
                configured ``active_tag_plugin``.

        Returns:
            ``{path: [tag, ...]}`` mapping.
        """
        active = (
            engine_override
            if engine_override is not None
            else self._tagger_settings.get("active_tag_plugin") or "pixlstash_tagger"
        )

        if not active:
            return {}

        preloaded_map = preloaded_images or {}

        if active == "wd14":
            self._engine.lifecycle.ensure_tagging_ready(
                self._engine.wd14_service,
                self._engine.pixlstash_tagger_service,
                True,
                False,
            )
            results = self._engine.wd14_service.tag_images(
                image_paths,
                stop_event=stop_event,
                preloaded_map=preloaded_map,
            )
            return merge_video_frame_tags(results)

        if active == "pixlstash_tagger":
            success = self._engine.lifecycle.ensure_tagging_ready(
                self._engine.wd14_service,
                self._engine.pixlstash_tagger_service,
                False,
                True,
            )
            if not success:
                logger.warning(
                    "[TaggingWorkflow] PixlStash tagger failed to load; returning empty results."
                )
                return {}
            return self._tag_images_custom(
                image_paths,
                stop_event=stop_event,
                preloaded_images=preloaded_map,
                out_raw_scores=out_raw_pixlstash_scores,
            )

        return self._tag_images_single_plugin(
            active, image_paths, stop_event=stop_event
        )

    def tag_quality_crops(
        self,
        items,
        stop_event=None,
        out_raw_scores: dict | None = None,
    ) -> dict:
        """Run the custom tagger on pre-cropped face/quality images.

        The crops should already be sized and centred on a face region at the
        custom tagger's native quality-crop resolution.

        Args:
            items: List of ``(key, PIL.Image)`` pairs.
            stop_event: Optional :class:`threading.Event` to interrupt.
            out_raw_scores: If provided, per-label confidence scores are
                written into this dict during the same GPU pass.

        Returns:
            ``{key: [quality_tag, ...]}`` — keys with no matching whitelist
            tags are omitted.
        """
        if not items:
            return {}
        if not self._engine.pixlstash_tagger_service.is_loaded():
            logger.debug("PixlStash tagger not loaded; skipping quality crop pass.")
            return {}
        return self._engine.pixlstash_tagger_service.tag_quality_crop_items(
            items,
            stop_event=stop_event,
            threshold_offset=self._threshold_offset,
            out_raw_scores=out_raw_scores,
        )

    def score_images_custom(
        self,
        image_paths,
        preloaded_images=None,
        min_confidence: float = 0.05,
    ) -> dict[str, dict[str, float]]:
        """Return raw custom-tagger confidence scores for a list of images.

        Unlike :meth:`tag_images`, this method bypasses the normal threshold
        so that all labels with confidence ≥ *min_confidence* are returned.
        Used by :class:`~pixlstash.tasks.tag_task.TagTask` to populate
        the ``TagPrediction`` table inline during the tagging pass.

        Args:
            image_paths: Sequence of absolute image/video file paths.
            preloaded_images: Optional ``{path: PIL.Image}`` map.
            min_confidence: Labels below this value are discarded.

        Returns:
            ``{path: {label: confidence}}`` for each image.
        """
        from PIL import Image

        if not self._use_pixlstash_tagger:
            return {}
        self._ensure_ready()

        preloaded_map = preloaded_images or {}
        items = []
        for image_path in image_paths:
            path = str(image_path)
            ext = os.path.splitext(path)[1].lower()
            if ext in _VIDEO_EXTS:
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
        return self._engine.pixlstash_tagger_service.score_items(
            items,
            min_confidence=min_confidence,
            image_size=self._engine.pixlstash_tagger_service._image_size_full,
        )

    def score_quality_crops_raw(
        self,
        items,
        stop_event=None,
        min_confidence: float = 0.05,
    ) -> dict[str, dict[str, float]]:
        """Return raw custom-tagger confidence scores for quality-crop images.

        Mirrors :meth:`score_images_custom` but operates on pre-loaded face
        crops at quality-crop resolution.  Used by
        :class:`~pixlstash.tasks.tag_task.TagTask` to give
        quality-crop-detected tags a real confidence score.

        Args:
            items: List of ``(key, PIL.Image)`` pairs already cropped.
            stop_event: Optional :class:`threading.Event` to interrupt.
            min_confidence: Labels below this floor are discarded.

        Returns:
            ``{key: {label: confidence}}`` for each kept label.
        """
        if not items:
            return {}
        if not self._use_pixlstash_tagger:
            return {}
        self._ensure_ready()
        return self._engine.pixlstash_tagger_service.score_items(
            items,
            stop_event=stop_event,
            min_confidence=min_confidence,
            image_size=self._engine.pixlstash_tagger_service._image_size_quality_crop,
        )

    # ------------------------------------------------------------------
    # Private helpers

    # Built-in plugins handled by this workflow's dedicated code paths.
    # Any other plugin in the registry that supports_tags will go through
    # _tag_images_single_plugin().
    _BUILTIN_TAG_PLUGIN_NAMES = frozenset({"wd14", "pixlstash_tagger"})

    def _tag_images_single_plugin(
        self,
        plugin_name: str,
        image_paths,
        stop_event=None,
    ) -> dict[str, list[str]]:
        """Dispatch tagging to a single named third-party plugin.

        Args:
            plugin_name: The registered plugin name (e.g. ``'joycaption'``).
            image_paths: Sequence of absolute image/video file paths.
            stop_event: Optional :class:`threading.Event` to interrupt.

        Returns:
            ``{path: [tag, ...]}`` or an empty dict on failure.
        """
        from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

        mgr = get_tagger_plugin_manager()
        plugin = mgr.get_plugin(plugin_name)
        if plugin is None or not plugin.supports_tags:
            logger.warning(
                "[TaggingWorkflow] active_tag_plugin %r not found or does not support tags; "
                "returning empty results.",
                plugin_name,
            )
            return {}

        plugins_cfg = self._tagger_settings.get("plugins", {})
        cfg = plugins_cfg.get(plugin_name, {})
        params = {**plugin.default_params(), **cfg.get("params", {})}

        try:
            if hasattr(plugin, "setup"):
                plugin.setup(self._engine.device)
            plugin.init(params)
            raw = plugin.tag_images(
                image_paths,
                parameters=params,
                stop_event=stop_event,
            )
        except Exception:
            logger.exception(
                "[TaggingWorkflow] Plugin %r failed during tag_images; returning empty results.",
                plugin_name,
            )
            return {}

        combined: dict[str, list[str]] = {}
        for path, tag_results in raw.items():
            path_str = str(path)
            combined[path_str] = sorted(tr.tag for tr in tag_results)
        return combined

    def _tag_images_extra_plugins(
        self,
        image_paths,
        stop_event=None,
    ) -> dict[str, list[str]]:
        """Call any enabled tag-capable plugin that isn't WD14 or PixlStash tagger.

        .. deprecated::
            The multi-plugin path is no longer used by :meth:`tag_images`.
            Kept for any direct callers during the transition period.

        Returns a merged ``{path: [tag, ...]}`` dict.
        """
        from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

        mgr = get_tagger_plugin_manager()
        plugins_cfg = self._tagger_settings.get("plugins", {})
        combined: dict[str, list[str]] = {}

        extra_candidates = [
            (p.name, p)
            for p in mgr.get_all_plugins()
            if p.supports_tags and p.name not in self._BUILTIN_TAG_PLUGIN_NAMES
        ]

        for plugin_name, plugin in extra_candidates:
            cfg = plugins_cfg.get(plugin_name, {})
            enabled = cfg.get("enabled", False)
            if not enabled:
                continue
            params = {
                **plugin.default_params(),
                **cfg.get("params", {}),
            }
            try:
                if hasattr(plugin, "setup"):
                    plugin.setup(self._engine.device)
                plugin.init(params)
                raw = plugin.tag_images(
                    image_paths,
                    parameters=params,
                    stop_event=stop_event,
                )
                for path, tag_results in raw.items():
                    path_str = str(path)
                    existing = set(combined.get(path_str, []))
                    existing.update(tr.tag for tr in tag_results)
                    combined[path_str] = sorted(existing)
            except Exception:
                logger.exception("Extra tag plugin %r failed; skipping.", plugin_name)

        return combined

    # ------------------------------------------------------------------

    def _ensure_ready(self) -> bool:
        """Ensure tagging models are loaded.

        Returns:
            ``True`` when the PixlStash tagger is usable after this call,
            ``False`` if loading failed (caller should skip PixlStash tagger inference).
        """
        success = self._engine.lifecycle.ensure_tagging_ready(
            self._engine.wd14_service,
            self._engine.pixlstash_tagger_service,
            self._use_wd14,
            self._use_pixlstash_tagger,
        )
        if not success:
            return False
        return self._use_pixlstash_tagger

    def _tag_images_custom(
        self,
        image_paths,
        stop_event=None,
        preloaded_images=None,
        out_raw_scores: dict | None = None,
    ) -> dict[str, list[str]]:
        """Run the custom tagger on full images and return per-image tag lists."""
        from PIL import Image

        if not self._engine.pixlstash_tagger_service.is_loaded():
            logger.warning(
                "PixlStash tagger not available; skipping PixlStash tagger tags."
            )
            return {}

        preloaded_map = preloaded_images or {}
        items = []
        for image_path in image_paths:
            if stop_event is not None and stop_event.is_set():
                logger.info("Tagging interrupted by stop event.")
                break
            path = str(image_path)
            ext = os.path.splitext(path)[1].lower()
            if ext in _VIDEO_EXTS:
                if path in preloaded_map:
                    items.append((f"{path}#frame0", preloaded_map[path]))
                    continue
                frames = VideoUtils.extract_representative_video_frames(path, count=1)
                if not frames:
                    logger.error("No frames extracted from video: %s", path)
                    continue
                for idx, frame in enumerate(frames):
                    items.append((f"{path}#frame{idx}", frame))
                continue
            try:
                img = preloaded_map.get(path)
                if img is None:
                    img = Image.open(path).convert("RGB")
            except Exception as exc:
                logger.error("Could not load image %s: %s", path, exc)
                continue
            items.append((path, img))

        if not items:
            return {}

        if out_raw_scores is not None:
            tags_by_key, scores_by_key = (
                self._engine.pixlstash_tagger_service.tag_and_score_items(
                    items,
                    stop_event=stop_event,
                    threshold_offset=self._threshold_offset,
                    threshold=None,
                    image_size=self._engine.pixlstash_tagger_service._image_size_full,
                    pass_name="full_images",
                )
            )
            for key, scores in scores_by_key.items():
                orig = key.split("#frame")[0] if "#frame" in key else key
                existing = out_raw_scores.get(orig, {})
                for label, conf in scores.items():
                    if conf > existing.get(label, 0.0):
                        existing[label] = conf
                out_raw_scores[orig] = existing
        else:
            tags_by_key = self._engine.pixlstash_tagger_service.tag_items(
                items,
                stop_event=stop_event,
                threshold_offset=self._threshold_offset,
                threshold=None,
                image_size=self._engine.pixlstash_tagger_service._image_size_full,
                pass_name="full_images",
            )

        return merge_video_frame_tags(tags_by_key)

    # ──── VRAM / batch-sizing ────────────────────────────────────────────────

    def _max_concurrent_images(self) -> int:
        """Maximum image concurrency determined by device type."""
        if self._engine.device == "cuda":
            return _MAX_CONCURRENT_GPU
        return _MAX_CONCURRENT_CPU

    def _vram_limited_batch_cap(self, base_mb: int, per_item_mb: int) -> int:
        """Delegate VRAM-based batch cap to the engine's budget."""
        return self._engine.vram_budget.limited_batch_cap(base_mb, per_item_mb)

    def effective_wd14_batch_size(self) -> int:
        """Effective WD14 batch size subject to ONNX capacity and VRAM budget."""
        max_concurrent = max(1, int(self._max_concurrent_images()))
        onnx_cap = self._engine.wd14_service.batch_capacity()
        wd14_batch = min(max_concurrent, onnx_cap)
        if self._engine.device == "cuda":
            wd14_batch = min(
                wd14_batch,
                self._vram_limited_batch_cap(base_mb=900, per_item_mb=220),
            )
        return max(1, int(wd14_batch))

    def effective_pixlstash_tagger_batch_size(self) -> int:
        # WD14 is the conservative bound; both taggers share the same limit.
        return self.effective_wd14_batch_size()

    def suggested_task_size(self) -> int:
        """VRAM-budget-aware batch size for a TagTask run.

        Uses the tightest cap across enabled taggers.  When only the custom
        tagger is active the WD14 cap would be overly conservative.  Extra
        plugins (e.g. JoyCaption) that report a preferred batch size via
        ``effective_batch_size()`` are also respected so that the task manager
        shows incremental progress instead of a single jump at task completion.
        """
        max_concurrent = max(1, int(self._max_concurrent_images()))
        if self._engine.device == "cuda":
            if self._use_wd14:
                max_concurrent = min(
                    max_concurrent,
                    self._vram_limited_batch_cap(base_mb=900, per_item_mb=220),
                )
            if self._use_pixlstash_tagger:
                max_concurrent = min(
                    max_concurrent,
                    self._vram_limited_batch_cap(base_mb=700, per_item_mb=90),
                )
        # Cap by any extra plugin's preferred batch size so each TagTask covers
        # exactly one inference batch, giving fine-grained progress updates.
        try:
            from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

            mgr = get_tagger_plugin_manager()
            plugins_cfg = self._tagger_settings.get("plugins", {})
            for plugin in mgr.get_all_plugins():
                if not plugin.supports_tags:
                    continue
                if plugin.name in self._BUILTIN_TAG_PLUGIN_NAMES:
                    continue
                cfg = plugins_cfg.get(plugin.name, {})
                if not cfg.get("enabled", False):
                    continue
                params = {**plugin.default_params(), **cfg.get("params", {})}
                plugin_batch = plugin.effective_batch_size(params)
                if plugin_batch > 1:
                    max_concurrent = min(max_concurrent, plugin_batch)
        except Exception:
            logger.exception(
                "Failed to query extra tag plugins for batch sizing; ignoring."
            )
        return max(1, max_concurrent)

    def estimated_vram_mb(self, image_count: int) -> int:
        """Total VRAM estimate (including model base) for *image_count* images."""
        image_count = max(1, int(image_count or 1))
        candidates = [1200]
        if self._use_wd14:
            wd14_batch = min(self.effective_wd14_batch_size(), image_count)
            candidates.append(900 + 220 * wd14_batch)
        if self._use_pixlstash_tagger:
            custom_batch = min(
                self.effective_pixlstash_tagger_batch_size(), image_count
            )
            candidates.append(700 + 90 * custom_batch)
        return int(max(candidates))

    def estimated_incremental_vram_mb(self, image_count: int) -> int:
        """Incremental VRAM estimate (activations only) for *image_count* images."""
        candidates = [256]
        if self._use_wd14:
            wd14_batch = min(
                self.effective_wd14_batch_size(),
                max(1, int(image_count or 1)),
            )
            candidates.append(220 * wd14_batch)
        if self._use_pixlstash_tagger:
            custom_batch = min(
                self.effective_pixlstash_tagger_batch_size(),
                max(1, int(image_count or 1)),
            )
            candidates.append(90 * custom_batch)
        return int(max(candidates))

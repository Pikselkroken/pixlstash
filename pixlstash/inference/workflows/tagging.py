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
    ) -> None:
        self._engine = engine
        self._use_wd14 = use_wd14
        self._use_pixlstash_tagger = use_pixlstash_tagger
        self._threshold_offset = threshold_offset

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
    ) -> dict[str, list[str]]:
        """Tag a batch of images with WD14 and/or the custom tagger.

        Args:
            image_paths: Sequence of absolute image/video file paths.
            stop_event: Optional :class:`threading.Event` to interrupt inference.
            preloaded_images: Optional ``{path: PIL.Image}`` map to skip
                re-loading images from disk.
            out_raw_pixlstash_scores: When provided, per-label confidence scores
                from the PixlStash tagger's full-image pass are written here
                (``{path: {label: float}}``).

        Returns:
            ``{path: [tag, ...]}`` mapping with combined WD14 + custom tags.
        """
        use_pixlstash_tagger = self._ensure_ready()

        preloaded_map = preloaded_images or {}

        wd14_results = {}
        if self._use_wd14:
            wd14_results = self._engine.wd14_service.tag_images(
                image_paths,
                stop_event=stop_event,
                preloaded_map=preloaded_map,
            )
        wd14_results = merge_video_frame_tags(wd14_results)

        if not use_pixlstash_tagger:
            return wd14_results

        pixlstash_results = self._tag_images_custom(
            image_paths,
            stop_event=stop_event,
            preloaded_images=preloaded_map,
            out_raw_scores=out_raw_pixlstash_scores,
        )

        combined = {}
        for path in set(wd14_results) | set(pixlstash_results):
            tags = set(wd14_results.get(path, []))
            tags.update(pixlstash_results.get(path, []))
            combined[path] = sorted(tags)
        return combined

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
        Used by :class:`~pixlstash.tasks.tag_prediction_task.TagPredictionTask`
        to populate the ``TagPrediction`` table.

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
        :class:`~pixlstash.tasks.tag_prediction_task.TagPredictionTask` to
        give quality-crop-detected tags a real confidence score.

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
            logger.warning("PixlStash tagger not available; skipping PixlStash tagger tags.")
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
            tags_by_key, scores_by_key = self._engine.pixlstash_tagger_service.tag_and_score_items(
                items,
                stop_event=stop_event,
                threshold_offset=self._threshold_offset,
                threshold=None,
                image_size=self._engine.pixlstash_tagger_service._image_size_full,
                pass_name="full_images",
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

    def _effective_wd14_batch_size(self) -> int:
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

    def _effective_pixlstash_tagger_batch_size(self) -> int:
        # WD14 is the conservative bound; both taggers share the same limit.
        return self._effective_wd14_batch_size()

    def suggested_task_size(self) -> int:
        """VRAM-budget-aware batch size for a TagTask run.

        Uses the tightest cap across enabled taggers.  When only the custom
        tagger is active the WD14 cap would be overly conservative.
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
        return max(1, max_concurrent)

    def estimated_vram_mb(self, image_count: int) -> int:
        """Total VRAM estimate (including model base) for *image_count* images."""
        image_count = max(1, int(image_count or 1))
        candidates = [1200]
        if self._use_wd14:
            wd14_batch = min(self._effective_wd14_batch_size(), image_count)
            candidates.append(900 + 220 * wd14_batch)
        if self._use_pixlstash_tagger:
            custom_batch = min(self._effective_pixlstash_tagger_batch_size(), image_count)
            candidates.append(700 + 90 * custom_batch)
        return int(max(candidates))

    def estimated_incremental_vram_mb(self, image_count: int) -> int:
        """Incremental VRAM estimate (activations only) for *image_count* images."""
        candidates = [256]
        if self._use_wd14:
            wd14_batch = min(
                self._effective_wd14_batch_size(),
                max(1, int(image_count or 1)),
            )
            candidates.append(220 * wd14_batch)
        if self._use_pixlstash_tagger:
            custom_batch = min(
                self._effective_pixlstash_tagger_batch_size(),
                max(1, int(image_count or 1)),
            )
            candidates.append(90 * custom_batch)
        return int(max(candidates))

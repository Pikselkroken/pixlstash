"""Description workflow: batch caption generation via Florence-2."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.florence2 import FLORENCE_PER_IMAGE_VRAM_MB
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.vram_utils import is_vram_oom

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine

logger = get_logger(__name__)

_VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"})


class DescriptionWorkflow:
    """Generates textual captions for images and video thumbnails.

    Uses Florence-2 for both single-image and batched captioning.  The
    lifecycle (loading / unloading the model) is delegated to the engine's
    :class:`~pixlstash.inference.model_lifecycle.ModelLifecycleManager`.

    Args:
        engine: The :class:`~pixlstash.inference.engine.InferenceEngine` that
            holds the already-constructed service instances.
        image_root: Absolute path prefix used to resolve relative picture
            ``file_path`` values.  May be ``None`` when pictures store
            absolute paths already.
    """

    def __init__(self, engine: "InferenceEngine", image_root: str | None) -> None:
        self._engine = engine
        self._image_root = image_root

    def generate_batch(
        self,
        pictures: list,
        engine_override: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict[int, str | None]:
        """Generate captions for a batch of picture-like objects.

        Dispatches to the active description plugin (from tagger_settings).
        Defaults to Florence-2 when no other plugin is configured.

        Args:
            pictures: Sequence of ORM ``Picture`` objects (or any object that
                exposes ``id`` and ``file_path``).
            engine_override: If supplied, use this plugin instead of
                ``active_description_plugin`` for this batch.
            stop_event: Optional :class:`threading.Event` the caller sets to
                cancel.  It is checked between images and handed to the plugin,
                so a cancel stops the batch instead of running it out.  The
                event belongs to the caller - ``DescriptionTask`` passes its own
                - because the workflow object running a batch is not always the
                one the task was constructed with (CPU spillover builds a fresh
                one on every access).

        Returns:
            A ``{picture_id: caption_str}`` mapping.  Missing or failed
            captions are stored as ``None``.  An empty dict is returned when
            *pictures* is empty or the batch was cancelled before it began.
        """
        if stop_event is not None and stop_event.is_set():
            return {}

        if not pictures:
            return {}

        active = (
            engine_override
            if engine_override is not None
            else self._engine.tagger_settings.get(
                "active_description_plugin", "florence2"
            )
        )
        logger.info(
            "[DescriptionWorkflow] active_description_plugin=%r; known plugins: %s",
            active,
            list(self._engine.tagger_settings.get("plugins", {}).keys()),
        )

        if active and active != "florence2":
            explicit = engine_override is not None
            return self._generate_batch_plugin(
                pictures, active, explicit=explicit, stop_event=stop_event
            )

        return self._generate_batch_florence(pictures, stop_event=stop_event)

    def _generate_batch_plugin(
        self,
        pictures: list,
        plugin_name: str,
        explicit: bool = False,
        stop_event: threading.Event | None = None,
    ) -> dict[int, str | None]:
        """Dispatch description generation to a named TaggerPlugin."""
        from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

        mgr = get_tagger_plugin_manager()
        plugin = mgr.get_plugin(plugin_name)
        logger.info(
            "[DescriptionWorkflow] dispatching to plugin %r (found=%s, supports_descriptions=%s)",
            plugin_name,
            plugin is not None,
            plugin.supports_descriptions if plugin else "n/a",
        )
        if plugin is None or not plugin.supports_descriptions:
            logger.warning(
                "Active description plugin %r not found or does not support descriptions; "
                "falling back to Florence-2.",
                plugin_name,
            )
            return self._generate_batch_florence(pictures, stop_event=stop_event)

        plugins_cfg = self._engine.tagger_settings.get("plugins", {})
        cfg = plugins_cfg.get(plugin_name, {})
        params = {**plugin.default_params(), **cfg.get("params", {})}

        try:
            if hasattr(plugin, "setup"):
                plugin.setup(self._engine.device)
            plugin.init(params)
        except Exception as exc:
            if is_vram_oom(exc):
                # Transient: the card was full this second, not this plugin is
                # broken. Let it out so the task retries the batch instead of
                # clearing every caption in it (BaseTask.run).
                logger.warning(
                    "Description plugin %r ran out of GPU memory while loading: %s",
                    plugin_name,
                    exc,
                )
                raise
            logger.exception(
                "Failed to initialise description plugin %r; %s.",
                plugin_name,
                "description will be cleared (not falling back to Florence-2 because plugin was explicitly requested)"
                if explicit
                else "falling back to Florence-2",
            )
            if explicit:
                return {}
            return self._generate_batch_florence(pictures, stop_event=stop_event)

        image_paths = []
        path_to_id: dict[str, int] = {}
        results: dict[int, str | None] = {}

        for picture in pictures:
            picture_path = ImageUtils.resolve_picture_path(
                self._image_root, getattr(picture, "file_path", None)
            )
            if not picture_path:
                results[picture.id] = None
                continue
            image_paths.append(picture_path)
            path_to_id[picture_path] = picture.id

        try:
            captions = plugin.generate_descriptions(
                image_paths, parameters=params, stop_event=stop_event
            )
            for path, caption in captions.items():
                pic_id = path_to_id.get(str(path))
                if pic_id is not None:
                    results[pic_id] = caption
        except Exception as exc:
            if is_vram_oom(exc):
                logger.warning(
                    "Description plugin %r ran out of GPU memory while captioning: %s",
                    plugin_name,
                    exc,
                )
                raise
            logger.exception(
                "Description plugin %r raised during generation; results may be partial.",
                plugin_name,
            )

        return results

    def _generate_batch_florence(
        self, pictures: list, stop_event: threading.Event | None = None
    ) -> dict[int, str | None]:
        """Caption a batch using Florence-2 (original implementation)."""
        if not pictures:
            return {}

        self._engine.ensure_captioning_ready()

        results: dict[int, str | None] = {}
        batch_items: list[tuple[int, str]] = []

        for picture in pictures:
            # Videos are captioned one at a time in this loop, and a video is
            # the slowest single item there is - so the check belongs here and
            # not only on the still-image chunks below.
            if stop_event is not None and stop_event.is_set():
                break
            picture_path = ImageUtils.resolve_picture_path(
                self._image_root, getattr(picture, "file_path", None)
            )
            if not picture_path:
                results[picture.id] = None
                continue
            ext = os.path.splitext(picture_path)[1].lower()
            if ext in _VIDEO_EXTS:
                results[picture.id] = self._engine.florence_service.generate_caption(
                    picture_path, _retry_on_cpu=False
                )
            else:
                batch_items.append((picture.id, picture_path))

        batch_size = self._engine.florence_service.description_batch_size()
        for idx in range(0, len(batch_items), batch_size):
            if stop_event is not None and stop_event.is_set():
                break
            chunk = batch_items[idx : idx + batch_size]
            chunk_paths = [picture_path for _, picture_path in chunk]
            captions = self._engine.florence_service.generate_captions_batch(
                chunk_paths, stop_event=stop_event
            )
            for picture_id, picture_path in chunk:
                results[picture_id] = captions.get(picture_path)

        return results

    def estimate_vram_mb(self, image_count: int, plugin_name: str | None = None) -> int:
        """Estimate incremental VRAM (in MB) required to caption *image_count* images.

        The estimate follows the batch's dispatch: when a plugin other than
        Florence-2 will run it, that plugin is asked. Charging the Florence
        figure for a run that never loads Florence lets the scheduler start a
        second model alongside the plugin's and OOM.

        When Florence is already resident in GPU memory only the per-image
        activation scratch is charged, avoiding a false-positive VRAM gate
        stall on warm runs. The cold-start charge follows the *configured*
        checkpoint (``base`` ~900 MB, ``large-ft`` ~2.6 GB, issue #512) - a
        constant pinned to base would under-count and spill on large-ft.

        Args:
            image_count: Number of images to be captioned.
            plugin_name: Plugin the batch will be dispatched to, matching
                ``generate_batch``'s ``engine_override``. ``None`` means the
                configured ``active_description_plugin``.

        Returns:
            Estimated VRAM in MB, or ``0`` on non-CUDA devices.
        """
        if self._engine.device != "cuda":
            return 0
        active = (
            plugin_name
            if plugin_name is not None
            else self._engine.tagger_settings.get(
                "active_description_plugin", "florence2"
            )
        )
        if active and active != "florence2":
            estimate = self._plugin_estimate_vram_mb(active, image_count)
            if estimate > 0:
                return estimate
        service = self._engine.florence_service
        # The gate runs before the load, so charge the variant that is about to
        # be loaded, not whichever one happens to be resident.
        service.set_model_variant(self._engine.florence_model_variant)
        florence_batch = max(1, int(service.description_batch_size()))
        batch = min(max(1, int(image_count or 1)), florence_batch)
        if service.is_loaded():
            return int(FLORENCE_PER_IMAGE_VRAM_MB * batch)
        return int(service.base_vram_mb + FLORENCE_PER_IMAGE_VRAM_MB * batch)

    def _plugin_estimate_vram_mb(self, plugin_name: str, image_count: int) -> int:
        """Ask *plugin_name* what a batch of *image_count* images will cost.

        Returns ``0``, meaning "charge the Florence-2 estimate instead", in two
        different situations. For a plugin that is missing or cannot caption
        that is simply correct: ``generate_batch`` falls back to Florence-2, so
        Florence is what loads. For a plugin that returns 0 or raises it is
        a deliberate charge for the wrong model, because that plugin *does*
        run. 0 is ambiguous at this seam - it is the base class default, and
        also its documented "CPU-only" value, and the two cannot be told apart
        - so the fallback is kept for both: the host cannot invent a figure for
        a model it knows nothing about, and for the CPU-only reading the error
        is a harmless over-charge. ``TaggerPlugin.estimated_vram_mb`` tells
        authors to charge for a cold start rather than rely on this (#967).
        """
        from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

        try:
            plugin = get_tagger_plugin_manager().get_plugin(plugin_name)
            if plugin is None or not plugin.supports_descriptions:
                return 0
            cfg = self._engine.tagger_settings.get("plugins", {}).get(plugin_name, {})
            params = {**plugin.default_params(), **(cfg.get("params") or {})}
            # Capped at the plugin's own batch size for the same reason the
            # Florence path caps at its own: only one batch is resident at once.
            batch = min(
                max(1, int(image_count or 1)),
                max(1, int(plugin.effective_batch_size(params))),
            )
            return max(0, int(plugin.estimated_vram_mb(batch, params)))
        except Exception as exc:
            logger.warning(
                "Description plugin %r could not estimate VRAM; falling back to "
                "the Florence-2 estimate: %s",
                plugin_name,
                exc,
            )
            return 0

"""Custom (anomaly) tagger service for PixlStash.

Wraps the PixlStash anomaly-detection ConvNext model that generates quality
and content tags beyond what WD14 covers.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable

import torch
from torchvision import transforms

from pixlstash.tagger_plugins.base import TagResult, TaggerPlugin
from pixlstash.utils.service.caption_utils import naturalize_tags, sanitise_tag

logger = logging.getLogger(__name__)

PIXLSTASH_TAGGER_HF_REPO = "PersonalJeebus/pixlvault-anomaly-tagger"
PIXLSTASH_TAGGER_FILENAME = "pixlstash-anomaly-tagger.safetensors"
PIXLSTASH_TAGGER_META_FILENAME = "pixlstash-anomaly-tagger_meta.json"
PIXLSTASH_TAGGER_REV_FILENAME = "pixlstash-anomaly-tagger.revision"
# Pin a specific HuggingFace git commit SHA so the model is re-downloaded
# whenever this value is updated, even if the local file already exists.
# Set to "main" to always use the latest commit on the default branch.
PIXLSTASH_TAGGER_REVISION = "d456616956954587e1a8c2d31c60c72f89a4ac3d"
PIXLSTASH_TAGGER_DEFAULT_THRESHOLD = 0.50
PIXLSTASH_TAGGER_LABEL_THRESHOLD_BIAS = 0.0

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
PIXLSTASH_TAGGER_IMAGE_SIZE_FULL = 448
PIXLSTASH_TAGGER_IMAGE_SIZE_QUALITY_CROP = 320


class PixlStashTaggerService:
    """Service that manages the PixlStash anomaly/quality tagger model lifecycle.

    Handles model downloading, initialisation, CPU fallback on OOM, and batch
    inference for tag-only, joint tag+score, and score-only passes.

    Args:
        device: Initial inference device ("cuda" or "cpu").
        model_dir: Directory where model files are stored. Paths to the
            checkpoint, meta.json, and revision sidecar are constructed
            internally from ``model_dir`` and this class's filename constants.
        batch_size_fn: Callable that returns the current batch size cap.
    """

    def __init__(
        self,
        device: str,
        model_dir: str,
        batch_size_fn: Callable[[], int],
    ) -> None:
        self._device = device
        self._model_path = os.path.join(model_dir, PIXLSTASH_TAGGER_FILENAME)
        self._meta_path = os.path.join(model_dir, PIXLSTASH_TAGGER_META_FILENAME)
        self._rev_path = os.path.join(model_dir, PIXLSTASH_TAGGER_REV_FILENAME)
        self._image_size_full = PIXLSTASH_TAGGER_IMAGE_SIZE_FULL
        self._image_size_quality_crop = PIXLSTASH_TAGGER_IMAGE_SIZE_QUALITY_CROP
        self._batch_size_fn = batch_size_fn
        # Model state — populated by init()
        self._model = None
        self._labels: list | None = None
        self._label_to_idx: dict | None = None
        self._label_thresholds: dict[str, float] = {}
        self._transform = None
        self._transform_cache: dict[int, transforms.Compose] = {}
        self._dtype = torch.float32

    # ------------------------------------------------------------------ #
    # State queries
    # ------------------------------------------------------------------ #

    @property
    def meta_path(self) -> str:
        """Return the path to the meta.json file for this tagger."""
        return self._meta_path

    def is_loaded(self) -> bool:
        """Return True when the model is ready for inference."""
        return (
            self._model is not None
            and self._labels is not None
            and self._transform is not None
        )

    def version(self) -> int:
        """Return the version integer from the loaded PixlStash tagger meta.json.

        Returns:
            Version integer, or 0 if the file is absent or lacks a version field.
        """
        if not os.path.isfile(self._meta_path):
            logger.warning(
                "PixlStash tagger meta.json not found at %s; using version 0",
                self._meta_path,
            )
            return 0
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            version = meta.get("version")
            if version is not None:
                return int(version)
        except Exception:
            logger.debug(
                "PixlStash tagger meta.json has no 'version' field; using version 0"
            )
        return 0

    def needs_download(self) -> bool:
        """Return True if the model or meta file must be (re-)downloaded.

        Re-download is required when:
        - The model or meta file is missing, OR
        - The revision sidecar is absent or records a different revision than
          ``PIXLSTASH_TAGGER_REVISION``, indicating the pinned version has changed.
        """
        if not os.path.isfile(self._model_path) or not os.path.isfile(self._meta_path):
            return True
        # Only enforce revision check for explicit commit SHAs.
        if PIXLSTASH_TAGGER_REVISION == "main":
            return False
        if not os.path.isfile(self._rev_path):
            return True
        try:
            with open(self._rev_path, "r", encoding="utf-8") as f:
                cached_rev = f.read().strip()
            return cached_rev != PIXLSTASH_TAGGER_REVISION
        except OSError:
            return True

    # ------------------------------------------------------------------ #
    # Setup / teardown
    # ------------------------------------------------------------------ #

    def download(self) -> None:
        """Download the model weights and metadata from HuggingFace.

        Always passes ``revision=PIXLSTASH_TAGGER_REVISION`` to pin the download
        to a specific git commit SHA.  After a successful download the resolved
        revision is written to a sidecar file for future staleness checks.
        """
        try:
            from huggingface_hub import hf_hub_download

            dest_dir = os.path.dirname(os.path.abspath(self._model_path))
            os.makedirs(dest_dir, exist_ok=True)
            logger.info(
                "Downloading PixlStash tagger (revision=%s) from %s ...",
                PIXLSTASH_TAGGER_REVISION,
                PIXLSTASH_TAGGER_HF_REPO,
            )
            hf_hub_download(
                repo_id=PIXLSTASH_TAGGER_HF_REPO,
                filename=PIXLSTASH_TAGGER_FILENAME,
                local_dir=dest_dir,
                revision=PIXLSTASH_TAGGER_REVISION,
                force_download=False,
            )
            hf_hub_download(
                repo_id=PIXLSTASH_TAGGER_HF_REPO,
                filename=PIXLSTASH_TAGGER_META_FILENAME,
                local_dir=dest_dir,
                revision=PIXLSTASH_TAGGER_REVISION,
                force_download=False,
            )
            try:
                with open(self._rev_path, "w", encoding="utf-8") as f:
                    f.write(PIXLSTASH_TAGGER_REVISION)
            except OSError as rev_err:
                logger.warning("Could not write revision sidecar: %s", rev_err)
            logger.info("PixlStash tagger downloaded to %s", self._model_path)
        except Exception as e:
            logger.warning("Failed to download PixlStash tagger: %s", e)

    def init(self) -> None:
        """Load the model checkpoint and metadata from disk into memory."""
        if not os.path.exists(self._model_path):
            raise FileNotFoundError(
                f"PixlStash tagger checkpoint not found: {self._model_path}"
            )
        if not os.path.exists(self._meta_path):
            raise FileNotFoundError(
                f"PixlStash tagger metadata not found: {self._meta_path}"
            )
        with open(self._meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        labels = meta.get("labels")
        arch = meta.get("arch", "convnext_base")
        if not labels:
            raise ValueError("PixlStash tagger metadata missing labels list.")
        from safetensors.torch import load_file

        state_dict = load_file(self._model_path, device=str(self._device))
        self._labels = labels
        self._label_to_idx = {label: i for i, label in enumerate(labels)}
        self._label_thresholds = {
            k: float(v) for k, v in meta.get("label_thresholds", {}).items()
        }
        if self._label_thresholds:
            logger.debug(
                "Loaded per-label thresholds for %d labels from meta.json",
                len(self._label_thresholds),
            )
        self._model = self._build_model(arch, len(labels))
        # Normalise dtype first: safetensors weights may be FP16 while the
        # freshly-built classifier head is FP32.  Cast everything to FP32,
        # load the state dict (now a consistent dtype), then promote to FP16
        # on CUDA for faster inference.  CPU always stays FP32.
        self._model.float()
        self._model.load_state_dict(state_dict)
        self._model.to(self._device)
        if str(self._device) == "cuda":
            self._model.half()
            self._dtype = torch.float16
        else:
            self._dtype = torch.float32
        self._model.eval()
        self._transform_cache = {}
        self._transform = self._build_transform(self._image_size_full)

    def init_or_cpu_fallback(self) -> bool:
        """Load the model, falling back to CPU on OOM.

        Returns:
            True if the model is successfully loaded, False if loading
            failed on both GPU and CPU.
        """
        if self.is_loaded():
            return True
        try:
            self.init()
            return True
        except Exception as exc:
            is_oom = isinstance(exc, torch.cuda.OutOfMemoryError) or (
                "out of memory" in str(exc).lower()
            )
            if is_oom and self._device != "cpu":
                logger.warning(
                    "PixlStash tagger GPU load failed (OOM); retrying on CPU: %s", exc
                )
                self._device = "cpu"
                try:
                    self.init()
                    logger.info("PixlStash tagger loaded on CPU successfully.")
                    return True
                except Exception as cpu_exc:
                    logger.warning(
                        "PixlStash tagger CPU fallback also failed; disabling: %s",
                        cpu_exc,
                    )
                    return False
            logger.warning(
                "PixlStash tagger reinit failed; disabling PixlStash tagger: %s", exc
            )
            return False

    def reload_on_cpu(self) -> bool:
        """Move the already-loaded model to CPU, freeing GPU memory.

        Returns:
            True on success, False if the move failed.
        """
        logger.warning("PixlStash tagger GPU inference failed; reloading on CPU...")
        try:
            if self._model is not None:
                self._model.float()
                self._model.to("cpu")
            self._device = "cpu"
            self._dtype = torch.float32
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.debug("PixlStash tagger reloaded on CPU")
            return True
        except Exception as cpu_error:
            logger.error(
                "Failed to reload PixlStash tagger on CPU: %s",
                cpu_error,
                exc_info=True,
            )
            return False

    def unload(self) -> None:
        """Release model memory and clear the transform cache."""
        self._model = None
        self._labels = None
        self._label_to_idx = None
        self._transform = None
        self._transform_cache = {}

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #

    def tag_items(
        self,
        items,
        stop_event=None,
        threshold_offset: float = 0.0,
        threshold=None,
        image_size: int | None = None,
        pass_name: str = "full_images",
    ) -> dict:
        """Run the PixlStash tagger and return thresholded tags.

        Args:
            items: List of ``(key, PIL.Image)`` pairs.
            stop_event: Optional threading.Event to interrupt inference.
            threshold_offset: Offset added to each label's base threshold.
            threshold: Override the default threshold base value.
            image_size: Override image size for the transform.
            pass_name: Logging label for debug output.

        Returns:
            Dict mapping key to list of tags above threshold, naturalized.
        """
        if not items:
            return {}
        if self._model is None or self._labels is None:
            logger.warning("PixlStash tagger model is None; skipping tag_items.")
            return {}

        tag_threshold = (
            float(threshold)
            if threshold is not None and float(threshold) > 0
            else PIXLSTASH_TAGGER_DEFAULT_THRESHOLD
        )
        if image_size is None:
            image_size = self._image_size_full
        transform = self._get_transform(image_size)

        logger.debug(
            "Performing custom tagging (%s) on %d items...",
            pass_name,
            len(items),
        )
        batch_size = self._batch_size_fn()
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
                    logger.error(
                        "PixlStash tagger failed to preprocess %s: %s", path, e
                    )
            if not batch_tensors:
                continue
            inputs = torch.stack(batch_tensors)
            device = self._device
            try:
                inputs = inputs.to(device=device, dtype=self._dtype)
                with torch.inference_mode():
                    logits = self._model(inputs)
                    probs = torch.sigmoid(logits).cpu().numpy()
            except Exception as exc:
                is_cuda_oom = isinstance(exc, torch.cuda.OutOfMemoryError) or (
                    "CUDA out of memory" in str(exc)
                )
                if is_cuda_oom and device == "cuda":
                    logger.warning(
                        "PixlStash tagger CUDA OOM; falling back to CPU for this run."
                    )
                    if self.reload_on_cpu():
                        logger.warning("PixlStash tagger is now running on CPU.")
                        inputs = inputs.to(device="cpu", dtype=torch.float32)
                        with torch.inference_mode():
                            logits = self._model(inputs)
                            probs = torch.sigmoid(logits).cpu().numpy()
                    else:
                        logger.error("PixlStash tagger CPU fallback failed.")
                        break
                else:
                    logger.error("PixlStash tagger inference failed: %s", exc)
                    break
            for path, prob in zip(batch_paths, probs):
                tag_probs = []
                for label, p in zip(self._labels, prob):
                    base = self._label_thresholds.get(label, tag_threshold)
                    per_label_threshold = max(0.01, base + threshold_offset)
                    if p >= per_label_threshold:
                        tag_probs.append((label, float(p)))
                all_tags_sorted = sorted(tag_probs, key=lambda x: x[1], reverse=True)
                results[path] = [tag for tag, _ in all_tags_sorted]

        return naturalize_tags(results)

    def tag_and_score_items(
        self,
        items,
        stop_event=None,
        threshold_offset: float = 0.0,
        threshold=None,
        image_size: int | None = None,
        pass_name: str = "full_images",
        min_confidence: float = 0.05,
    ) -> tuple:
        """Run the PixlStash tagger once and return both thresholded tags and raw scores.

        Identical to calling ``tag_items`` followed by ``score_items`` on the
        same batch, but runs the GPU forward pass only once.

        Args:
            items: List of ``(key, PIL.Image)`` pairs.
            stop_event: Optional threading.Event to interrupt.
            threshold_offset: Offset added to each label's base threshold.
            threshold: Override per-label threshold base value.
            image_size: Override image size for the transform.
            pass_name: Logging label for debug output.
            min_confidence: Floor for raw scores returned in the scores dict.

        Returns:
            Tuple ``(tags_by_key, scores_by_key)`` where:
            - ``tags_by_key``: ``{key: [tag, ...]}`` thresholded and naturalized.
            - ``scores_by_key``: ``{key: {natural_label: float}}`` raw scores.
        """
        if not items:
            return {}, {}
        if self._model is None or self._labels is None:
            logger.warning(
                "PixlStash tagger model is None; skipping tag_and_score_items."
            )
            return {}, {}

        tag_threshold = (
            float(threshold)
            if threshold is not None and float(threshold) > 0
            else PIXLSTASH_TAGGER_DEFAULT_THRESHOLD
        )
        if image_size is None:
            image_size = self._image_size_full
        transform = self._get_transform(image_size)

        logger.debug(
            "Performing custom tagging+scoring (%s) on %d items...",
            pass_name,
            len(items),
        )
        batch_size = self._batch_size_fn()
        tags_results: dict = {}
        scores_results: dict = {}

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
                    logger.error(
                        "PixlStash tagger failed to preprocess %s: %s", path, e
                    )
            if not batch_tensors:
                continue
            inputs = torch.stack(batch_tensors)
            device = self._device
            dtype = self._dtype
            try:
                inputs = inputs.to(device=device, dtype=dtype)
                with torch.inference_mode():
                    logits = self._model(inputs)
                    probs = torch.sigmoid(logits).float().cpu().numpy()
            except Exception as exc:
                is_cuda_oom = isinstance(exc, torch.cuda.OutOfMemoryError) or (
                    "CUDA out of memory" in str(exc)
                )
                if is_cuda_oom and device == "cuda":
                    logger.warning(
                        "PixlStash tagger CUDA OOM; falling back to CPU for this run."
                    )
                    if self.reload_on_cpu():
                        logger.warning("PixlStash tagger is now running on CPU.")
                        self._dtype = torch.float32
                        inputs = inputs.to(device="cpu", dtype=torch.float32)
                        with torch.inference_mode():
                            logits = self._model(inputs)
                            probs = torch.sigmoid(logits).float().cpu().numpy()
                    else:
                        logger.error("PixlStash tagger CPU fallback failed.")
                        break
                else:
                    logger.error("PixlStash tagger inference failed: %s", exc)
                    break
            for path, prob in zip(batch_paths, probs):
                tag_probs = []
                scores: dict = {}
                for label, p in zip(self._labels, prob):
                    p_f = float(p)
                    base = self._label_thresholds.get(label, tag_threshold)
                    per_label_threshold = max(0.01, base + threshold_offset)
                    if p_f >= per_label_threshold:
                        tag_probs.append((label, p_f))
                    if p_f >= min_confidence:
                        natural = sanitise_tag(label)
                        if natural:
                            scores[natural] = p_f
                all_tags_sorted = sorted(tag_probs, key=lambda x: x[1], reverse=True)
                tags_results[path] = [tag for tag, _ in all_tags_sorted]
                scores_results[path] = scores

        return naturalize_tags(tags_results), scores_results

    def tag_quality_crop_items(
        self,
        items,
        stop_event=None,
        threshold_offset: float = 0.0,
        out_raw_scores: dict | None = None,
    ) -> dict:
        """Run the PixlStash tagger on quality-crop images and return only whitelist tags.

        Filters results to ``QUALITY_CROP_TAG_WHITELIST`` so that non-quality
        tags detected on zoomed-in face crops do not leak into the Tag table.

        Args:
            items: List of ``(key, PIL.Image)`` pairs (pre-cropped face regions).
            stop_event: Optional threading.Event to interrupt inference.
            threshold_offset: Offset added to each label's base threshold.
            out_raw_scores: When provided, raw confidence scores for every label
                above ``min_confidence`` are merged into this dict
                (``{key: {label: float}}``) during the same GPU pass.

        Returns:
            Dict mapping key to list of whitelist-filtered quality tags.
            Keys with no matching quality tags are omitted.
        """
        if not items:
            return {}
        if out_raw_scores is not None:
            raw, scores_by_key = self.tag_and_score_items(
                items,
                stop_event=stop_event,
                threshold_offset=threshold_offset,
                threshold=None,
                image_size=self._image_size_quality_crop,
                pass_name="quality_crops",
            )
            out_raw_scores.update(scores_by_key)
        else:
            raw = self.tag_items(
                items,
                stop_event=stop_event,
                threshold_offset=threshold_offset,
                threshold=None,
                image_size=self._image_size_quality_crop,
                pass_name="quality_crops",
            )
        filtered = {}
        for key, tags in raw.items():
            quality_tags = [t for t in tags if t in QUALITY_CROP_TAG_WHITELIST]
            if quality_tags:
                filtered[key] = quality_tags
        return filtered

    def score_items(
        self,
        items,
        stop_event=None,
        image_size: int | None = None,
        min_confidence: float = 0.05,
    ) -> dict[str, dict[str, float]]:
        """Run the PixlStash tagger and return raw sigmoid scores for each label.

        Unlike ``tag_items``, this method applies no threshold and returns all
        labels whose confidence is >= ``min_confidence`` so that the full
        probability distribution is available for writing to the
        ``TagPrediction`` table.

        Args:
            items: List of ``(path, PIL.Image)`` pairs.
            stop_event: Optional threading.Event to interrupt inference.
            image_size: Override image size for the transform.
            min_confidence: Discard labels below this floor to save storage.

        Returns:
            Dict mapping path to ``{label: confidence}`` for each kept label.
        """
        if not items:
            return {}
        if self._model is None or self._labels is None:
            return {}
        if image_size is None:
            image_size = self._image_size_full
        transform = self._get_transform(image_size)

        batch_size = self._batch_size_fn()
        results: dict[str, dict[str, float]] = {}
        for batch_start in range(0, len(items), batch_size):
            if stop_event is not None and stop_event.is_set():
                break
            batch = items[batch_start : batch_start + batch_size]
            batch_paths = []
            batch_tensors = []
            for path, image in batch:
                try:
                    batch_tensors.append(transform(image))
                    batch_paths.append(path)
                except Exception as exc:
                    logger.error("Custom scorer failed to preprocess %s: %s", path, exc)
            if not batch_tensors:
                continue
            inputs = torch.stack(batch_tensors)
            device = self._device
            try:
                inputs = inputs.to(device=device, dtype=self._dtype)
                with torch.inference_mode():
                    logits = self._model(inputs)
                    probs = torch.sigmoid(logits).cpu().numpy()
            except Exception as exc:
                is_cuda_oom = isinstance(exc, torch.cuda.OutOfMemoryError) or (
                    "CUDA out of memory" in str(exc)
                )
                if is_cuda_oom and device == "cuda":
                    logger.warning(
                        "Custom scorer CUDA OOM; falling back to CPU for this batch."
                    )
                    if self.reload_on_cpu():
                        inputs = inputs.to(device="cpu", dtype=torch.float32)
                        with torch.inference_mode():
                            logits = self._model(inputs)
                            probs = torch.sigmoid(logits).cpu().numpy()
                    else:
                        logger.error("Custom scorer CPU fallback failed.")
                        break
                else:
                    logger.error("Custom scorer inference failed: %s", exc)
                    break
            for path, prob in zip(batch_paths, probs):
                scores: dict[str, float] = {}
                for label, p in zip(self._labels, prob):
                    p_f = float(p)
                    if p_f >= min_confidence:
                        natural = sanitise_tag(label)
                        if natural:
                            scores[natural] = p_f
                results[path] = scores
        return results

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _build_model(self, arch: str, num_labels: int):
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
        raise ValueError(f"Unsupported PixlStash tagger arch: {arch}")

    def _build_transform(self, image_size: int) -> transforms.Compose:
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

    def _get_transform(self, image_size: int) -> transforms.Compose:
        transform = self._transform_cache.get(image_size)
        if transform is None:
            transform = self._build_transform(image_size)
            self._transform_cache[image_size] = transform
        return transform


class PixlStashTaggerPlugin(TaggerPlugin):
    """TaggerPlugin wrapper around :class:`PixlStashTaggerService`.

    Attributes:
        name: Plugin identifier used in ``tagger_settings``.
        display_name: Human-readable label shown in the UI.
        description: Short description.
        supports_tags: Produces anomaly/quality tags.
        supports_descriptions: Does not produce captions.
        requires_download: Model must be downloaded on first use.
    """

    name: str = "pixlstash_tagger"
    display_name: str = "PixlStash Tagger"
    description: str = "Custom anomaly/quality tagger trained for PixlStash — detects blur, artefacts, and content-specific tags."
    supports_tags: bool = True
    supports_descriptions: bool = False
    requires_download: bool = True
    default_enabled: bool = True

    def __init__(self) -> None:
        self._service: PixlStashTaggerService | None = None

    # ------------------------------------------------------------------
    # Infrastructure binding
    # ------------------------------------------------------------------

    def setup(
        self,
        device: str,
        model_dir: str,
        batch_size_fn,
    ) -> None:
        """Create the underlying :class:`PixlStashTaggerService`.

        Must be called before any other method.

        Args:
            device: Inference device string (``"cuda"`` or ``"cpu"``).
            model_dir: Root directory for downloaded model files.
            batch_size_fn: Zero-argument callable returning the effective
                inference batch size.
        """
        self._service = PixlStashTaggerService(
            device=device,
            model_dir=model_dir,
            batch_size_fn=batch_size_fn,
        )

    @property
    def service(self) -> PixlStashTaggerService:
        """Return the underlying :class:`PixlStashTaggerService` (raises if not set up)."""
        if self._service is None:
            raise RuntimeError("PixlStashTaggerPlugin.setup() has not been called")
        return self._service

    def bind_service(self, service: PixlStashTaggerService) -> None:
        """Bind an existing :class:`PixlStashTaggerService` instance.

        Used by the :class:`~pixlstash.vault.Vault` to share the engine's
        service with the plugin registry so that ``is_loaded()`` reflects the
        true model state.

        Args:
            service: The already-constructed service to attach.
        """
        self._service = service

    # ------------------------------------------------------------------
    # TaggerPlugin interface
    # ------------------------------------------------------------------

    def parameter_schema(self) -> list:
        """Return parameter definitions for the PixlStash tagger."""
        return [
            {
                "name": "threshold_offset",
                "label": "Threshold offset",
                "type": "number",
                "default": PIXLSTASH_TAGGER_LABEL_THRESHOLD_BIAS,
                "min": -0.5,
                "max": 0.5,
                "step": 0.01,
                "description": (
                    "Offset added to each label's base threshold. "
                    "Positive values raise the bar; negative values lower it."
                ),
            },
        ]

    def default_params(self) -> dict:
        """Return ``{name: default}`` from ``parameter_schema``."""
        return {f["name"]: f["default"] for f in self.parameter_schema()}

    def plugin_schema(self) -> dict:
        """Return JSON-serialisable metadata for this plugin."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "supports_tags": self.supports_tags,
            "supports_descriptions": self.supports_descriptions,
            "requires_download": self.requires_download,
            "parameters": self.parameter_schema(),
            "downloaded_artifacts": self.list_downloaded_artifacts(),
            "is_loaded": self.is_loaded(),
        }

    def needs_download(self, parameters=None) -> bool:
        """Return ``True`` if model files are absent or stale."""
        return self.service.needs_download()

    def download(self, parameters=None, progress_callback=None) -> None:
        """Download the PixlStash tagger model from HuggingFace."""
        self.service.download()

    def init(self, parameters: dict) -> None:
        """Load the model checkpoint (idempotent)."""
        self.service.init()

    def unload(self) -> None:
        """Release the model from memory."""
        self.service.unload()

    def is_loaded(self) -> bool:
        """Return ``True`` if the model is ready for inference."""
        if self._service is None:
            return False
        return self._service.is_loaded()

    def list_downloaded_artifacts(self) -> list:
        """Return empty list — PixlStash tagger has a single non-deletable artifact."""
        return []

    def estimated_vram_mb(self, image_count: int, parameters=None) -> int:
        """Return 0 — VRAM is modest and managed by the service internally."""
        return 0

    def effective_batch_size(self, parameters=None) -> int:
        """Return the effective inference batch size."""
        if self._service is None:
            return 1
        return max(1, int(self._service._batch_size_fn()))

    def tag_images(
        self,
        image_paths: list,
        parameters: dict,
        preloaded: dict | None = None,
        stop_event=None,
        out_raw_scores: dict | None = None,
    ) -> dict:
        """Run the PixlStash tagger and return ``{path: [TagResult, ...]}``

        Calls :meth:`PixlStashTaggerService.tag_and_score_items` so that raw
        per-label confidence scores are collected in the same GPU pass.
        Confidence scores are included in each :class:`~pixlstash.tagger_plugins.base.TagResult`.

        Args:
            image_paths: Ordered list of absolute image/video paths.
            parameters: Plugin parameters (uses ``threshold_offset``).
            preloaded: Optional ``{path: PIL.Image}`` map.
            stop_event: Optional :class:`threading.Event` to interrupt.
            out_raw_scores: When provided, raw ``{path: {label: float}}``
                confidence scores are written here.

        Returns:
            ``{path: [TagResult, ...]}`` for each processed image.
        """
        from PIL import Image as PILImage
        from pixlstash.utils.image_processing.video_utils import VideoUtils

        threshold_offset = float(
            parameters.get("threshold_offset", PIXLSTASH_TAGGER_LABEL_THRESHOLD_BIAS)
        )
        preloaded_map = preloaded or {}

        _VIDEO_EXTS = frozenset(
            {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        )
        items = []
        for path in image_paths:
            path_str = str(path)
            ext = os.path.splitext(path_str)[1].lower()
            img = preloaded_map.get(path_str)
            if img is None:
                if ext in _VIDEO_EXTS:
                    frames = VideoUtils.extract_representative_video_frames(
                        path_str, count=1
                    )
                    if not frames:
                        continue
                    img = frames[0].convert("RGB")
                else:
                    try:
                        img = PILImage.open(path_str).convert("RGB")
                    except Exception as exc:
                        logger.warning(
                            "Could not load %s for tagging: %s", path_str, exc
                        )
                        continue
            items.append((path_str, img))

        if not items:
            return {}

        tags_by_path, scores_by_path = self.service.tag_and_score_items(
            items,
            stop_event=stop_event,
            threshold_offset=threshold_offset,
        )
        if out_raw_scores is not None:
            out_raw_scores.update(scores_by_path)

        # Build TagResult list, including confidence for labels that have scores.
        result = {}
        for path_str, tags in tags_by_path.items():
            scores = scores_by_path.get(path_str, {})
            result[path_str] = [
                TagResult(tag=t, confidence=scores.get(t)) for t in tags
            ]
        return result

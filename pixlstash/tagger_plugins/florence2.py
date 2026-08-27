"""Florence-2 captioning service, extracted from picture_tagger.py."""

from __future__ import annotations

import os
import threading
import time
import traceback
from typing import TYPE_CHECKING, Callable, Optional

from PIL import Image

if TYPE_CHECKING:  # annotations only - see the function-local import note below
    import torch

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.base import TaggerPlugin
from pixlstash.utils.model_utils import from_pretrained_local_first
from pixlstash.utils.image_processing.video_utils import VideoUtils

# ML imports (torch / torchvision) are deliberately FUNCTION-LOCAL throughout
# this module. They cost seconds to import, and this module sits on the API
# server's import path - so importing them at module scope would make server
# startup and every single test pay that cost before doing any work.

logger = get_logger(__name__)

FLORENCE_BATCH_SIZE_GPU = 32
FLORENCE_BATCH_SIZE_CPU = 2
FLORENCE_BASE_VRAM_MB = 900  # Florence-2-base model footprint (fp16 on GPU)
FLORENCE_LARGE_FT_VRAM_MB = 2600  # Florence-2-large-ft footprint (0.77B, fp16 on GPU)
FLORENCE_PER_IMAGE_VRAM_MB = 40  # Activation scratch per image in a GPU mini-batch
FLORENCE_MODEL_REVISION = "00921df66db728a9ceb750f5eca43e5c203a2051"

# Selectable Florence-2 checkpoints. One setting drives BOTH captioning and
# object detection (Segment) - the service is shared, and loading two variants
# side by side would double the VRAM for no benefit (issue #512). Every entry
# pins a revision: an unpinned HuggingFace ref is a silent supply-chain change.
# `vram_mb` is the model footprint the VRAM gate charges before a batch runs;
# it MUST follow the chosen variant or the gate under-counts and we spill.
FLORENCE_MODEL_VARIANTS: dict[str, dict] = {
    "base": {
        "model": "florence-community/Florence-2-base",
        "revision": FLORENCE_MODEL_REVISION,
        "vram_mb": FLORENCE_BASE_VRAM_MB,
        "label": "Base (0.23B, ~900 MB)",
    },
    "large-ft": {
        "model": "florence-community/Florence-2-large-ft",
        "revision": "26b734a54fdfbf9c398351eedfabb7f27fc470b7",
        "vram_mb": FLORENCE_LARGE_FT_VRAM_MB,
        "label": "Large fine-tuned (0.77B, ~2.6 GB)",
    },
}
DEFAULT_FLORENCE_VARIANT = "base"

_VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"})


def _resize_to_max_dim(image: Image.Image, max_dim: int) -> Image.Image:
    """Return *image* resized so its longest side is at most *max_dim* pixels."""
    if max(image.size) <= max_dim:
        return image
    aspect_ratio = image.width / image.height
    if image.width >= image.height:
        new_width = max_dim
        new_height = max(1, int(max_dim / aspect_ratio))
    else:
        new_height = max_dim
        new_width = max(1, int(max_dim * aspect_ratio))
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


def _truncate_at_sentence(caption: str) -> str:
    """Trim *caption* at the last sentence-ending punctuation mark."""
    last_punct = max(caption.rfind(p) for p in (".", "!", "?"))
    if last_punct != -1:
        return caption[: last_punct + 1].strip()
    return caption


def _move_inputs_to_device(inputs: dict, device, dtype) -> dict:
    """Move a HuggingFace processor output dict to *device*/*dtype*."""
    import torch

    return {
        k: (
            v.to(device=device, dtype=dtype)
            if torch.is_tensor(v) and v.is_floating_point()
            else v.to(device)
            if torch.is_tensor(v)
            else v
        )
        for k, v in inputs.items()
    }


class Florence2Service:
    """Self-contained Florence-2 captioning service.

    Attributes:
        _device: Inference device string passed at construction time.
        _force_cpu_fn: Callable returning True when CPU-only inference is required.
        _max_concurrent_fn: Callable returning the max concurrent image count.
        _vram_cap_fn: Callable(base_mb, per_item_mb) returning VRAM-capped batch size.
        _model: Loaded Florence-2 model or None.
        _processor: Loaded Florence-2 processor or None.
        _model_device: torch.device the model is currently resident on.
        _dtype: torch dtype the model is loaded with.
        _model_variant: Key into :data:`FLORENCE_MODEL_VARIANTS`.
        _model_name: HuggingFace model identifier.
        _model_revision: Pinned HuggingFace revision for ``_model_name``.
        _base_vram_mb: Model footprint in MB for the active variant.
        _batch_size: Active batch size for GPU inference.
        _max_tokens: Maximum new tokens per generated caption.
        _last_fallback_reason: Description of the last GPU-to-CPU fallback.
        _last_fallback_at: Unix timestamp of the last GPU-to-CPU fallback.
    """

    def __init__(
        self,
        device: str,
        fast_captions: bool = False,
        force_cpu_fn: Optional[Callable[[], bool]] = None,
        max_concurrent_fn: Optional[Callable[[], int]] = None,
        vram_cap_fn: Optional[Callable[[int, int], int]] = None,
    ):
        self._device = device
        self._force_cpu_fn = force_cpu_fn or (lambda: False)
        self._max_concurrent_fn = max_concurrent_fn or (
            lambda: (
                FLORENCE_BATCH_SIZE_CPU if device == "cpu" else FLORENCE_BATCH_SIZE_GPU
            )
        )
        self._vram_cap_fn = vram_cap_fn or (lambda base_mb, per_item_mb: 32)

        self._model = None
        self._processor = None
        self._model_device = None
        self._dtype = None
        # Serialises loading against unloading. ``aggressive_unload`` runs from
        # the idle sweep and from shutdown, neither of which knows a load is in
        # flight, and dropping the model mid-load frees device memory the
        # loader is still writing into. See test_model_unload_race.py.
        self._load_lock = threading.RLock()
        self._model_variant = DEFAULT_FLORENCE_VARIANT
        variant = FLORENCE_MODEL_VARIANTS[DEFAULT_FLORENCE_VARIANT]
        self._model_name = variant["model"]
        self._model_revision = variant["revision"]
        self._base_vram_mb = variant["vram_mb"]
        self._batch_size = (
            FLORENCE_BATCH_SIZE_CPU if device == "cpu" else FLORENCE_BATCH_SIZE_GPU
        )
        self._max_tokens = 40 if fast_captions else 120
        self._last_fallback_reason: Optional[str] = None
        self._last_fallback_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_variant(self) -> str:
        """Return the active Florence-2 variant key (see FLORENCE_MODEL_VARIANTS)."""
        return self._model_variant

    @property
    def base_vram_mb(self) -> int:
        """Return the active variant's model footprint in MB.

        The VRAM gate charges this before a batch runs, so it has to track the
        selected checkpoint rather than staying pinned to base (issue #512).
        """
        return self._base_vram_mb

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_model_variant(self, variant: str) -> None:
        """Select the Florence-2 checkpoint used for captions and detection.

        Switching variants while a model is resident unloads it so the next
        :meth:`ensure_ready` picks up the new checkpoint; an unknown key is
        logged and ignored rather than left to fail deep inside a HuggingFace
        download.

        Args:
            variant: A key of :data:`FLORENCE_MODEL_VARIANTS`.
        """
        key = str(variant or "").strip() or DEFAULT_FLORENCE_VARIANT
        spec = FLORENCE_MODEL_VARIANTS.get(key)
        if spec is None:
            logger.warning(
                "Unknown Florence-2 model variant %r; keeping %r. Known variants: %s",
                variant,
                self._model_variant,
                sorted(FLORENCE_MODEL_VARIANTS),
            )
            return
        if key == self._model_variant:
            return
        was_loaded = self.is_loaded()
        logger.info(
            "Switching Florence-2 variant %r -> %r (%s); loaded=%s",
            self._model_variant,
            key,
            spec["model"],
            was_loaded,
        )
        self._model_variant = key
        self._model_name = spec["model"]
        self._model_revision = spec["revision"]
        self._base_vram_mb = spec["vram_mb"]
        if was_loaded:
            # Drop the resident checkpoint; ensure_ready() reloads the new one.
            self._model = None
            self._processor = None
            self._model_device = None
            self._dtype = None

    def is_loaded(self) -> bool:
        """Return True if the model and processor are both loaded."""
        return self._model is not None and self._processor is not None

    def ensure_ready(self) -> None:
        """Load Florence-2 if not already loaded (idempotent)."""
        with self._load_lock:
            if self.is_loaded():
                return
            self._init()

    def unload(self) -> None:
        """Release the model and processor, waiting for any in-flight load.

        The plugin used to clear ``_model``/``_processor`` from outside, which
        could land in the middle of :meth:`_init`. Freeing device memory the
        loader is still writing into crashes the process, so the drop belongs
        here, under the same lock the load takes.
        """
        with self._load_lock:
            self._model = None
            self._processor = None

    def description_batch_size(self) -> int:
        """Return the VRAM-constrained batch size for caption generation."""
        max_concurrent = max(1, int(self._max_concurrent_fn()))
        base_batch = min(max_concurrent, max(1, int(self._batch_size)))
        if self._device == "cuda":
            base_batch = min(
                base_batch,
                self._vram_cap_fn(self._base_vram_mb, FLORENCE_PER_IMAGE_VRAM_MB),
            )
        return max(1, base_batch)

    def state_info(self) -> dict:
        """Return a dict of observable service state for diagnostics."""
        return {
            "florence_loaded": self.is_loaded(),
            "florence_variant": self._model_variant,
            "florence_model": self._model_name,
            "florence_fallback_reason": self._last_fallback_reason,
            "florence_fallback_at": self._last_fallback_at,
        }

    def generate_caption(
        self, image_path: str, _retry_on_cpu: bool = True
    ) -> Optional[str]:
        """Generate a natural language caption for a single image or video file.

        There is no ``stop_event`` here: one caption is a single inference with
        nothing to interrupt part-way, so cancellation is the caller's check
        before it asks for the next one.

        Args:
            image_path: Path to the image or video file.
            _retry_on_cpu: When True, retry on CPU if a CUDA error occurs.

        Returns:
            Caption string, or None on failure.
        """
        logger.debug(
            "_generate_florence_caption called: image_path=%s, _retry_on_cpu=%s",
            image_path,
            _retry_on_cpu,
        )
        if self._model is None:
            logger.error("Florence-2 model is not initialised")
            return None

        try:
            ext = os.path.splitext(image_path)[1].lower()
            caption = None
            if ext in _VIDEO_EXTS:
                frames = VideoUtils.extract_representative_video_frames(
                    image_path, count=3
                )
                for idx, pil_img in enumerate(frames):
                    pil_img = _resize_to_max_dim(pil_img, max_dim=512)
                    caption = self._infer_single(pil_img)
                    if caption:
                        logger.debug("Florence-2 caption (frame %d): %s", idx, caption)
                        break
            else:
                image = Image.open(image_path).convert("RGB")
                image = _resize_to_max_dim(image, max_dim=640)
                caption = self._infer_single(image)
                if caption:
                    logger.debug("Florence-2 caption: %s", caption)

            logger.debug("Final Florence-2 caption returned: %s", caption)
            return caption

        except Exception as e:
            if _retry_on_cpu and self._is_cuda_error(e):
                logger.warning(
                    "Florence-2 captioning failed on GPU (%s); retrying on CPU.", e
                )
                if self._reload_on_cpu(cause=e):
                    return self.generate_caption(image_path, _retry_on_cpu=False)

            logger.error("Florence-2 captioning failed for %s: %s", image_path, e)
            logger.debug(traceback.format_exc())
            return None

    def generate_captions_batch(
        self,
        image_paths: list,
        _retry_on_cpu: bool = True,
        stop_event: threading.Event | None = None,
    ) -> dict:
        """Generate captions for a batch of still images.

        Args:
            image_paths: List of file paths (non-video only).
            _retry_on_cpu: When True, retry on CPU if a CUDA error occurs.
            stop_event: Optional :class:`threading.Event` to interrupt
                inference mid-batch.

        Returns:
            Dict mapping file path → caption string (or None on failure).
        """
        import torch

        logger.debug(
            "_generate_florence_captions_batch called: %d images", len(image_paths)
        )
        if self._model is None:
            logger.error("Florence-2 model is not initialised")
            return {}

        try:
            valid_items = []
            for image_path in image_paths:
                if stop_event and stop_event.is_set():
                    logger.debug(
                        "Florence-2 generate captions batch stop-event reached, ending early"
                    )
                    return {}
                try:
                    image = Image.open(image_path).convert("RGB")
                    image = _resize_to_max_dim(image, max_dim=640)
                    valid_items.append((image_path, image))
                except Exception as image_error:
                    logger.error(
                        "Florence-2 failed to load image for batch %s: %s",
                        image_path,
                        image_error,
                    )

            if not valid_items:
                return {}

            images = [img for _, img in valid_items]
            inputs = self._processor(
                text=["<MORE_DETAILED_CAPTION>"] * len(images),
                images=images,
                return_tensors="pt",
                padding=True,
            )
            inputs = _move_inputs_to_device(inputs, self._model_device, self._dtype)
            logger.debug("Batch inputs moved to %s", self._model_device)

            if stop_event and stop_event.is_set():
                logger.debug(
                    "Florence-2 generate captions batch stop-event reached, ending early"
                )
                return {}

            with torch.inference_mode():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=self._max_tokens,
                    early_stopping=False,
                    do_sample=False,
                    num_beams=1,
                    pad_token_id=self._processor.tokenizer.pad_token_id,
                )
            generated_texts = self._processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )

            captions = {}
            for (image_path, _), generated_text in zip(valid_items, generated_texts):
                captions[image_path] = self._parse_caption(generated_text)
            return captions

        except Exception as e:
            if _retry_on_cpu and self._is_cuda_error(e):
                logger.warning(
                    "Florence-2 batch captioning failed on GPU (%s); retrying on CPU.",
                    e,
                )
                if self._reload_on_cpu(cause=e):
                    return self.generate_captions_batch(
                        image_paths, _retry_on_cpu=False, stop_event=stop_event
                    )

            logger.error("Florence-2 batch captioning failed: %s", e)
            logger.debug(traceback.format_exc())
            captions = {}
            for image_path in image_paths:
                if stop_event and stop_event.is_set():
                    logger.debug(
                        "Florence-2 per-image fallback stop-event reached, ending early"
                    )
                    break
                captions[image_path] = self.generate_caption(
                    image_path, _retry_on_cpu=False
                )
            return captions

    def detect_objects(
        self,
        image_paths: list,
        prompt: Optional[str] = None,
        max_new_tokens: int = 1024,
        max_dim: int = 1024,
        _retry_on_cpu: bool = True,
    ) -> dict:
        """Detect objects in a batch of still images.

        Runs Florence-2 dense object detection (``<OD>`` when *prompt* is empty)
        or open-vocabulary detection
        (``<OPEN_VOCABULARY_DETECTION>`` when a *prompt* phrase is supplied)
        and returns labelled bounding boxes.

        Coordinates are returned in **original picture pixels** as ``xyxy``.
        Florence emits scale-invariant normalised bins (0-1000), so passing each
        image's *original* size to ``post_process_generation`` dequantises
        straight back to original pixels - independent of the ``max_dim`` resize
        we feed the model for speed (the resize preserves aspect ratio, and the
        bins are per-axis fractions).

        Args:
            image_paths: List of still-image file paths (non-video).
            prompt: Optional phrase to ground. Empty/None → dense ``<OD>``.
            max_new_tokens: Generation cap; detection token strings are long.
            max_dim: Longest side (px) each image is resized to before inference.
            _retry_on_cpu: When True, retry once on CPU after a CUDA error.

        Returns:
            ``{path: [(label, [x1, y1, x2, y2], score_or_None), ...]}``.  Paths
            that fail to load are omitted; ``score`` is ``None`` for detectors
            (like Florence ``<OD>``/grounding) that emit no per-box confidence.
        """
        import torch

        if self._model is None:
            logger.error("Florence-2 model is not initialised")
            return {}

        phrase = (prompt or "").strip()
        if phrase:
            # Open-vocabulary detection is the right task for a bare class-style
            # prompt ("dog", "license plate"): it does one-to-one detection of
            # the named object. <CAPTION_TO_PHRASE_GROUNDING> expects a full
            # caption-like sentence and grounds noun phrases within it, which
            # gives inconsistent results when fed a single label.
            task_token = "<OPEN_VOCABULARY_DETECTION>"
            text_prompt = f"{task_token}{phrase}"
        else:
            task_token = "<OD>"
            text_prompt = task_token

        try:
            # (path, fed_image, (orig_w, orig_h)) - fed_image is the resized
            # copy handed to the processor; orig size drives box dequantisation.
            valid_items = []
            for image_path in image_paths:
                try:
                    image = Image.open(image_path).convert("RGB")
                    orig_size = (image.width, image.height)
                    fed_image = _resize_to_max_dim(image, max_dim=max_dim)
                    valid_items.append((image_path, fed_image, orig_size))
                except Exception as image_error:
                    logger.error(
                        "Florence-2 failed to load image for detection %s: %s",
                        image_path,
                        image_error,
                    )

            if not valid_items:
                return {}

            images = [img for _, img, _ in valid_items]
            inputs = self._processor(
                text=[text_prompt] * len(images),
                images=images,
                return_tensors="pt",
                padding=True,
            )
            inputs = _move_inputs_to_device(inputs, self._model_device, self._dtype)

            with torch.inference_mode():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=max_new_tokens,
                    early_stopping=False,
                    do_sample=False,
                    # Beam search (Florence-2's reference default) over greedy:
                    # detection is emitted as a sequence of location tokens, and
                    # a single greedy path drops/duplicates boxes far more than
                    # beams do. Keep do_sample=False so runs stay repeatable.
                    num_beams=3,
                    pad_token_id=self._processor.tokenizer.pad_token_id,
                )
            generated_texts = self._processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )

            detections: dict = {}
            for (image_path, _, orig_size), generated_text in zip(
                valid_items, generated_texts
            ):
                detections[image_path] = self._parse_detections(
                    generated_text, task_token, orig_size
                )
            return detections

        except Exception as e:
            if _retry_on_cpu and self._is_cuda_error(e):
                logger.warning(
                    "Florence-2 detection failed on GPU (%s); retrying on CPU.", e
                )
                if self._reload_on_cpu(cause=e):
                    return self.detect_objects(
                        image_paths,
                        prompt=prompt,
                        max_new_tokens=max_new_tokens,
                        max_dim=max_dim,
                        _retry_on_cpu=False,
                    )

            logger.error("Florence-2 detection failed: %s", e)
            logger.debug(traceback.format_exc())
            return {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Load Florence-2 onto the best available device."""
        import torch

        try:
            import transformers

            logger.debug("Loading Florence-2 model for captioning...")
            logger.debug("Transformers version: %s", transformers.__version__)

            requested_device = (
                self._device
                if isinstance(self._device, torch.device)
                else torch.device(self._device)
            )
            use_cpu = self._force_cpu_fn() or requested_device.type == "cpu"

            if use_cpu:
                logger.debug(
                    "Device set to CPU, loading Florence-2 on CPU with FP32..."
                )
                self._load_model(torch.device("cpu"), torch.float32)
                self._batch_size = FLORENCE_BATCH_SIZE_CPU
                logger.debug("Florence-2 loaded successfully on CPU")
            elif requested_device.type == "cuda" and torch.cuda.is_available():
                try:
                    logger.debug("Attempting to load Florence-2 on GPU with FP16...")
                    self._load_model(torch.device("cuda"), torch.float16)
                    self._batch_size = FLORENCE_BATCH_SIZE_GPU
                    logger.debug("Florence-2 loaded successfully on GPU (~500MB VRAM)")
                except Exception as gpu_error:
                    self._record_fallback("init_gpu_load_failed", gpu_error)
                    logger.warning(
                        "GPU loading failed, falling back to CPU: %s", gpu_error
                    )
                    self._load_model(torch.device("cpu"), torch.float32)
                    self._batch_size = FLORENCE_BATCH_SIZE_CPU
                    logger.debug("Florence-2 loaded successfully on CPU")
            elif requested_device.type == "cuda":
                unavailable = RuntimeError(
                    "CUDA was explicitly requested but torch.cuda.is_available() is false"
                )
                self._record_fallback("cuda_unavailable", unavailable)
                logger.warning(
                    "CUDA was explicitly requested but is unavailable; loading "
                    "Florence-2 on CPU with FP32"
                )
                self._load_model(torch.device("cpu"), torch.float32)
                self._batch_size = FLORENCE_BATCH_SIZE_CPU
                logger.debug("Florence-2 loaded successfully on CPU")
            else:
                # Preserve explicitly supported non-CUDA accelerators rather
                # than silently changing their device. CUDA is special-cased
                # above because availability has a first-class torch probe.
                self._load_model(requested_device, torch.float32)
                self._batch_size = FLORENCE_BATCH_SIZE_CPU

        except Exception as e:
            logger.error("Failed to load Florence-2: %s", e)
            logger.error("Try: pip install --upgrade transformers")

    def _load_model(self, device: torch.device, dtype) -> None:
        import torch

        from transformers import Florence2Processor, Florence2ForConditionalGeneration

        if not isinstance(device, torch.device):
            device = torch.device(device)

        # device_map routes loading through Accelerate, which correctly handles
        # Florence-2's tied weights (lm_head / embed_tokens) and places all
        # tensors on the target device during from_pretrained - no post-load
        # .to() call is needed, eliminating "Cannot copy out of meta tensor".
        device_map = str(device)

        self._processor = from_pretrained_local_first(
            Florence2Processor,
            self._model_name,
            revision=self._model_revision,
        )

        for attn_impl in ("sdpa", "eager"):
            try:
                model = from_pretrained_local_first(
                    Florence2ForConditionalGeneration,
                    self._model_name,
                    torch_dtype=dtype,
                    device_map=device_map,
                    attn_implementation=attn_impl,
                    revision=self._model_revision,
                )
                break
            except (TypeError, AttributeError, NotImplementedError) as e:
                if attn_impl == "eager":
                    raise
                logger.debug(
                    "SDPA not supported, falling back to eager attention: %s", e
                )

        # lm_head and embed_tokens are tied weights absent from the checkpoint.
        # Accelerate leaves them on the meta device after dispatch; tie_weights()
        # resolves their references to the already-materialised shared embedding.
        model.tie_weights()
        model.eval()

        self._model = model
        self._model_device = device
        self._dtype = dtype

    def _record_fallback(self, phase: str, error: Exception) -> None:
        reason = f"{phase}: {type(error).__name__}: {error}"
        self._last_fallback_reason = reason
        self._last_fallback_at = time.time()
        logger.warning("[FLORENCE_FALLBACK] %s", reason)

    def _reload_on_cpu(self, cause: Optional[Exception] = None) -> bool:
        import torch

        logger.warning(
            "Florence-2 GPU inference failed; attempting to reload on CPU..."
        )
        if cause is not None:
            self._record_fallback("runtime_gpu_inference_failed", cause)
        try:
            self._model = None
            self._processor = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            self._load_model(torch.device("cpu"), torch.float32)
            self._batch_size = FLORENCE_BATCH_SIZE_CPU
            logger.debug("Florence-2 reloaded on CPU")
            return True
        except Exception as cpu_error:
            logger.error(
                "Failed to reload Florence-2 on CPU: %s", cpu_error, exc_info=True
            )
            return False

    def _infer_single(self, image: Image.Image) -> Optional[str]:
        """Run inference on a single PIL image and return the caption."""
        import torch

        inputs = self._processor(
            text="<MORE_DETAILED_CAPTION>",
            images=image,
            return_tensors="pt",
        )
        inputs = _move_inputs_to_device(inputs, self._model_device, self._dtype)
        logger.debug("Inputs moved to %s", self._model_device)

        with torch.inference_mode():
            generated_ids = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=self._max_tokens,
                early_stopping=False,
                do_sample=False,
                num_beams=1,
                pad_token_id=self._processor.tokenizer.pad_token_id,
            )
        generated_text = self._processor.batch_decode(
            generated_ids, skip_special_tokens=False
        )[0]
        return self._parse_caption(generated_text)

    def _parse_caption(self, generated_text: str) -> Optional[str]:
        """Post-process a raw generated text into a clean caption string."""
        parsed = self._processor.post_process_generation(
            generated_text, task="<MORE_DETAILED_CAPTION>"
        )
        caption = parsed.get("<MORE_DETAILED_CAPTION>", "").strip()
        if not caption:
            return None
        return _truncate_at_sentence(caption)

    def _parse_detections(self, generated_text: str, task_token: str, image_size):
        """Parse Florence detection output into ``[(label, xyxy_px, score|None)]``.

        Args:
            generated_text: Raw decoded model output.
            task_token: ``"<OD>"`` or ``"<OPEN_VOCABULARY_DETECTION>"`` - the
                key ``post_process_generation`` returns results under.
            image_size: ``(width, height)`` of the original picture in pixels;
                Florence's normalised bins dequantise directly to these pixels.

        Returns:
            List of ``(label, [x1, y1, x2, y2], score_or_None)``. Boxes are
            clamped to image bounds (CLAUDE.md) and degenerate boxes dropped.
        """
        width, height = image_size
        parsed = self._processor.post_process_generation(
            generated_text, task=task_token, image_size=(width, height)
        )
        result = parsed.get(task_token, {}) or {}
        bboxes = result.get("bboxes", []) or []
        # <OD>/grounding return labels under "labels"; open-vocabulary detection
        # returns them under "bboxes_labels". Accept either so all three task
        # tokens parse through this one path.
        labels = result.get("labels") or result.get("bboxes_labels") or []
        scores = result.get("scores", None)

        detections = []
        for idx, (bbox, label) in enumerate(zip(bboxes, labels)):
            if not bbox or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = bbox
            x1 = max(0, min(int(round(x1)), width))
            y1 = max(0, min(int(round(y1)), height))
            x2 = max(0, min(int(round(x2)), width))
            y2 = max(0, min(int(round(y2)), height))
            if x2 <= x1 or y2 <= y1:
                continue
            score = None
            if scores is not None and idx < len(scores):
                try:
                    score = float(scores[idx])
                except (TypeError, ValueError):
                    score = None
            detections.append((str(label).strip(), [x1, y1, x2, y2], score))
        return detections

    def _is_cuda_error(self, error: Exception) -> bool:
        import torch

        if (
            self._model_device is None
            or getattr(self._model_device, "type", "") != "cuda"
        ):
            return False
        # PyTorch's typed OOM deliberately does not promise the word "cuda" in
        # its message. Type identity is the stable signal; the string fallback
        # retains compatibility with provider/runtime errors raised outside
        # PyTorch's own exception hierarchy.
        oom_type = getattr(torch, "OutOfMemoryError", None)
        cuda_error_type = getattr(torch.cuda, "CudaError", None)
        typed_cuda_errors = tuple(
            error_type
            for error_type in (oom_type, cuda_error_type)
            if isinstance(error_type, type)
        )
        if typed_cuda_errors and isinstance(error, typed_cuda_errors):
            return True
        message = str(error).lower()
        return "cuda" in message or "cudnn" in message or "cublas" in message


class Florence2Plugin(TaggerPlugin):
    """TaggerPlugin wrapper around :class:`Florence2Service`.

    Attributes:
        name: Plugin identifier used in ``tagger_settings``.
        display_name: Human-readable label shown in the UI.
        description: Short description.
        author, license, models: Header fields, see :class:`TaggerPlugin`.
        supports_tags: Florence-2 does not produce tags.
        supports_descriptions: Florence-2 generates captions.
        requires_download: Model must be downloaded on first use.
    """

    name: str = "florence2"
    display_name: str = "Florence-2"
    description: str = (
        "Microsoft Florence-2 - generates natural-language image descriptions. "
        "The selected checkpoint also drives the Segment action."
    )
    author: str = "Gaute Lindkvist <lindkvis@gmail.com>"
    license: str = "GPL-3.0-only"
    # One entry per selectable checkpoint (FLORENCE_MODEL_VARIANTS) - the user
    # picks which one is downloaded.
    models: list[dict[str, str]] = [
        {"name": "florence-community/Florence-2-base", "license": "MIT"},
        {"name": "florence-community/Florence-2-large-ft", "license": "MIT"},
    ]
    supports_tags: bool = False
    supports_descriptions: bool = True
    requires_download: bool = True

    def __init__(self) -> None:
        self._service: Florence2Service | None = None

    # ------------------------------------------------------------------
    # Infrastructure binding
    # ------------------------------------------------------------------

    def setup(
        self,
        device: str,
        fast_captions: bool = False,
        force_cpu_fn=None,
        max_concurrent_fn=None,
        vram_cap_fn=None,
    ) -> None:
        """Create the underlying :class:`Florence2Service`.

        Must be called before any other method.

        Args:
            device: Inference device string (``"cuda"`` or ``"cpu"``).
            fast_captions: When ``True`` enables lower-quality / faster mode.
            force_cpu_fn: Zero-argument callable returning ``True`` when CPU
                inference should be forced regardless of ``device``.
            max_concurrent_fn: Zero-argument callable returning the max
                concurrent image count.
            vram_cap_fn: Callable ``(base_mb, per_item_mb) -> int`` returning
                a VRAM-capped batch size.
        """
        self._service = Florence2Service(
            device=device,
            fast_captions=fast_captions,
            force_cpu_fn=force_cpu_fn,
            max_concurrent_fn=max_concurrent_fn,
            vram_cap_fn=vram_cap_fn,
        )

    @property
    def service(self) -> Florence2Service:
        """Return the underlying :class:`Florence2Service` (raises if not set up)."""
        if self._service is None:
            raise RuntimeError("Florence2Plugin.setup() has not been called")
        return self._service

    def bind_service(self, service: Florence2Service) -> None:
        """Bind an existing :class:`Florence2Service` instance.

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
        """Return parameter definitions for Florence-2."""
        return [
            {
                "name": "max_new_tokens",
                "label": "Max new tokens",
                "type": "integer",
                "default": 120,
                "min": 16,
                "max": 512,
                "step": 8,
                "description": "Maximum number of tokens to generate per caption.",
            },
            {
                "name": "fast_mode",
                "label": "Fast mode",
                "type": "boolean",
                "default": False,
                "description": "Use a shorter prompt for faster, less detailed captions.",
            },
            {
                "name": "model_variant",
                "label": "Florence-2 model",
                "type": "select",
                "default": DEFAULT_FLORENCE_VARIANT,
                "options": [
                    {"value": key, "label": spec["label"]}
                    for key, spec in FLORENCE_MODEL_VARIANTS.items()
                ],
                "description": (
                    "Larger checkpoints find smaller objects and write richer "
                    "descriptions, at more VRAM. This one model does both the "
                    "descriptions and the Segment action."
                ),
            },
        ]

    def default_params(self) -> dict:
        """Return ``{name: default}`` from ``parameter_schema``."""
        return {f["name"]: f["default"] for f in self.parameter_schema()}

    def needs_download(self, parameters=None) -> bool:
        """Return ``True`` - Florence-2 is always downloaded on first use."""
        # Florence-2 uses HuggingFace's automatic caching; the service handles
        # download lazily inside ensure_ready().  We report False here so the
        # workflow doesn't gate on an explicit download step.
        return False

    def download(self, parameters=None, progress_callback=None) -> None:
        """No-op - Florence-2 downloads automatically inside ensure_ready()."""

    def init(self, parameters: dict) -> None:
        """Apply parameters and load the model (idempotent).

        Applies ``model_variant`` before ``max_new_tokens`` so a variant switch
        unloads the previous checkpoint before the reload below.

        Args:
            parameters: Plugin parameters (uses ``model_variant`` and
                ``max_new_tokens``).
        """
        self.service.set_model_variant(
            parameters.get("model_variant", DEFAULT_FLORENCE_VARIANT)
        )
        max_tokens = int(parameters.get("max_new_tokens", 120))
        self.service._max_tokens = max_tokens
        self.service.ensure_ready()

    def unload(self) -> None:
        """Unload Florence-2 from memory."""
        if self._service is not None:
            self._service.unload()

    def is_loaded(self) -> bool:
        """Return ``True`` if Florence-2 is loaded."""
        if self._service is None:
            return False
        return self._service.is_loaded()

    def list_downloaded_artifacts(self) -> list:
        """Return empty list - Florence-2 uses HF cache, not a named artifact."""
        return []

    def estimated_vram_mb(self, image_count: int, parameters=None) -> int:
        """Estimate VRAM required for captioning *image_count* images.

        Args:
            image_count: Number of images to caption.
            parameters: Unused.

        Returns:
            Estimated VRAM in MB.
        """
        if self._service is None:
            return 0
        svc = self._service
        if svc._model_device is None or str(svc._model_device) == "cpu":
            return 0
        batch = min(max(1, image_count), svc.description_batch_size())
        if svc.is_loaded():
            return int(FLORENCE_PER_IMAGE_VRAM_MB * batch)
        return int(svc.base_vram_mb + FLORENCE_PER_IMAGE_VRAM_MB * batch)

    def effective_batch_size(self, parameters=None) -> int:
        """Return the VRAM-constrained batch size."""
        if self._service is None:
            return 1
        return max(1, self._service.description_batch_size())

    def generate_descriptions(
        self,
        image_paths: list,
        parameters: dict,
        stop_event: threading.Event | None = None,
    ) -> dict:
        """Generate captions for a batch of image/video paths.

        Args:
            image_paths: Ordered list of absolute image/video paths.
            parameters: Plugin parameters (uses ``max_new_tokens``).
            stop_event: Optional :class:`threading.Event`.  Checked before each
                video and before each still-image chunk, so a cancel returns
                the captions produced so far rather than running the batch out.

        Returns:
            ``{path: caption_str}`` - value is ``None`` on per-image failure.
            A cancelled batch simply omits the paths it never reached.
        """
        _VIDEO_EXTS = frozenset(
            {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        )

        max_tokens = int(parameters.get("max_new_tokens", 120))
        self.service._max_tokens = max_tokens

        results: dict[str, str | None] = {}
        batch_items: list[str] = []

        for path in image_paths:
            if stop_event and stop_event.is_set():
                logger.debug(
                    "Florence-2 generate descriptions stop-event reached, ending early."
                )
                return results
            path_str = str(path)
            ext = os.path.splitext(path_str)[1].lower()
            if ext in _VIDEO_EXTS:
                results[path_str] = self.service.generate_caption(
                    path_str, _retry_on_cpu=False
                )
            else:
                batch_items.append(path_str)

        batch_size = self.service.description_batch_size()
        for idx in range(0, len(batch_items), batch_size):
            if stop_event and stop_event.is_set():
                logger.debug(
                    "Florence-2 generate descriptions stop-event reached, ending early."
                )
                return results
            chunk = batch_items[idx : idx + batch_size]
            captions = self.service.generate_captions_batch(
                chunk, stop_event=stop_event
            )
            for path_str in chunk:
                results[path_str] = captions.get(path_str)

        return results

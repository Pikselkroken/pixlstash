"""Florence-2 captioning service, extracted from picture_tagger.py."""

import os
import time
import traceback
from typing import Callable, Optional

import torch
from PIL import Image

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.base import TaggerPlugin
from pixlstash.utils.model_utils import from_pretrained_local_first
from pixlstash.utils.image_processing.video_utils import VideoUtils

logger = get_logger(__name__)

FLORENCE_BATCH_SIZE_GPU = 32
FLORENCE_BATCH_SIZE_CPU = 2
FLORENCE_BASE_VRAM_MB = 900  # Florence-2-base model footprint (fp16 on GPU)
FLORENCE_PER_IMAGE_VRAM_MB = 40  # Activation scratch per image in a GPU mini-batch
FLORENCE_MODEL_REVISION = "00921df66db728a9ceb750f5eca43e5c203a2051"

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
        _model_name: HuggingFace model identifier.
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
        self._model_name = "florence-community/Florence-2-base"
        self._batch_size = (
            FLORENCE_BATCH_SIZE_CPU if device == "cpu" else FLORENCE_BATCH_SIZE_GPU
        )
        self._max_tokens = 40 if fast_captions else 120
        self._last_fallback_reason: Optional[str] = None
        self._last_fallback_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        """Return True if the model and processor are both loaded."""
        return self._model is not None and self._processor is not None

    def ensure_ready(self) -> None:
        """Load Florence-2 if not already loaded (idempotent)."""
        if self.is_loaded():
            return
        self._init()

    def description_batch_size(self) -> int:
        """Return the VRAM-constrained batch size for caption generation."""
        max_concurrent = max(1, int(self._max_concurrent_fn()))
        base_batch = min(max_concurrent, max(1, int(self._batch_size)))
        if self._device == "cuda":
            base_batch = min(
                base_batch,
                self._vram_cap_fn(FLORENCE_BASE_VRAM_MB, FLORENCE_PER_IMAGE_VRAM_MB),
            )
        return max(1, base_batch)

    def state_info(self) -> dict:
        """Return a dict of observable service state for diagnostics."""
        return {
            "florence_loaded": self.is_loaded(),
            "florence_fallback_reason": self._last_fallback_reason,
            "florence_fallback_at": self._last_fallback_at,
        }

    def generate_caption(
        self, image_path: str, _retry_on_cpu: bool = True
    ) -> Optional[str]:
        """Generate a natural language caption for a single image or video file.

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
        self, image_paths: list, _retry_on_cpu: bool = True
    ) -> dict:
        """Generate captions for a batch of still images.

        Args:
            image_paths: List of file paths (non-video only).
            _retry_on_cpu: When True, retry on CPU if a CUDA error occurs.

        Returns:
            Dict mapping file path → caption string (or None on failure).
        """
        logger.debug(
            "_generate_florence_captions_batch called: %d images", len(image_paths)
        )
        if self._model is None:
            logger.error("Florence-2 model is not initialised")
            return {}

        try:
            valid_items = []
            for image_path in image_paths:
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
                        image_paths, _retry_on_cpu=False
                    )

            logger.error("Florence-2 batch captioning failed: %s", e)
            logger.debug(traceback.format_exc())
            return {
                image_path: self.generate_caption(image_path, _retry_on_cpu=False)
                for image_path in image_paths
            }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Load Florence-2 onto the best available device."""
        try:
            import transformers

            logger.debug("Loading Florence-2 model for captioning...")
            logger.debug("Transformers version: %s", transformers.__version__)

            use_cpu = self._force_cpu_fn() or self._device == "cpu"

            if use_cpu:
                logger.debug(
                    "Device set to CPU, loading Florence-2 on CPU with FP32..."
                )
                self._load_model(torch.device("cpu"), torch.float32)
                self._batch_size = FLORENCE_BATCH_SIZE_CPU
                logger.debug("Florence-2 loaded successfully on CPU")
            elif torch.cuda.is_available():
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
            else:
                logger.debug("No GPU available, loading Florence-2 on CPU with FP32...")
                device = (
                    self._device
                    if isinstance(self._device, torch.device)
                    else torch.device(self._device)
                )
                self._load_model(device, torch.float32)
                self._batch_size = FLORENCE_BATCH_SIZE_CPU
                logger.debug("Florence-2 loaded successfully on CPU")

        except Exception as e:
            logger.error("Failed to load Florence-2: %s", e)
            logger.error("Try: pip install --upgrade transformers")

    def _load_model(self, device: torch.device, dtype) -> None:
        from transformers import Florence2Processor, Florence2ForConditionalGeneration

        if not isinstance(device, torch.device):
            device = torch.device(device)

        # device_map routes loading through Accelerate, which correctly handles
        # Florence-2's tied weights (lm_head / embed_tokens) and places all
        # tensors on the target device during from_pretrained — no post-load
        # .to() call is needed, eliminating "Cannot copy out of meta tensor".
        device_map = str(device)

        self._processor = from_pretrained_local_first(
            Florence2Processor,
            self._model_name,
            revision=FLORENCE_MODEL_REVISION,
        )

        for attn_impl in ("sdpa", "eager"):
            try:
                model = from_pretrained_local_first(
                    Florence2ForConditionalGeneration,
                    self._model_name,
                    torch_dtype=dtype,
                    device_map=device_map,
                    attn_implementation=attn_impl,
                    revision=FLORENCE_MODEL_REVISION,
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

    def _is_cuda_error(self, error: Exception) -> bool:
        return (
            self._model_device is not None
            and getattr(self._model_device, "type", "") == "cuda"
            and "cuda" in str(error).lower()
        )


class Florence2Plugin(TaggerPlugin):
    """TaggerPlugin wrapper around :class:`Florence2Service`.

    Attributes:
        name: Plugin identifier used in ``tagger_settings``.
        display_name: Human-readable label shown in the UI.
        description: Short description.
        supports_tags: Florence-2 does not produce tags.
        supports_descriptions: Florence-2 generates captions.
        requires_download: Model must be downloaded on first use.
    """

    name: str = "florence2"
    display_name: str = "Florence-2"
    description: str = (
        "Microsoft Florence-2-base — generates natural-language image descriptions."
    )
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
        """Return ``True`` — Florence-2 is always downloaded on first use."""
        # Florence-2 uses HuggingFace's automatic caching; the service handles
        # download lazily inside ensure_ready().  We report False here so the
        # workflow doesn't gate on an explicit download step.
        return False

    def download(self, parameters=None, progress_callback=None) -> None:
        """No-op — Florence-2 downloads automatically inside ensure_ready()."""

    def init(self, parameters: dict) -> None:
        """Apply parameters and load the model (idempotent).

        Updates ``max_new_tokens`` on the service and then loads the model.

        Args:
            parameters: Plugin parameters (uses ``max_new_tokens``).
        """
        max_tokens = int(parameters.get("max_new_tokens", 120))
        self.service._max_tokens = max_tokens
        self.service.ensure_ready()

    def unload(self) -> None:
        """Unload Florence-2 from memory."""
        if self._service is not None:
            self._service._model = None
            self._service._processor = None

    def is_loaded(self) -> bool:
        """Return ``True`` if Florence-2 is loaded."""
        if self._service is None:
            return False
        return self._service.is_loaded()

    def list_downloaded_artifacts(self) -> list:
        """Return empty list — Florence-2 uses HF cache, not a named artifact."""
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
        return int(FLORENCE_BASE_VRAM_MB + FLORENCE_PER_IMAGE_VRAM_MB * batch)

    def effective_batch_size(self, parameters=None) -> int:
        """Return the VRAM-constrained batch size."""
        if self._service is None:
            return 1
        return max(1, self._service.description_batch_size())

    def generate_descriptions(
        self,
        image_paths: list,
        parameters: dict,
        stop_event=None,
    ) -> dict:
        """Generate captions for a batch of image/video paths.

        Args:
            image_paths: Ordered list of absolute image/video paths.
            parameters: Plugin parameters (uses ``max_new_tokens``).
            stop_event: Not used by Florence-2 (kept for interface compatibility).

        Returns:
            ``{path: caption_str}`` — value is ``None`` on per-image failure.
        """
        _VIDEO_EXTS = frozenset(
            {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
        )

        max_tokens = int(parameters.get("max_new_tokens", 120))
        self.service._max_tokens = max_tokens

        results: dict[str, str | None] = {}
        batch_items: list[str] = []

        for path in image_paths:
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
            chunk = batch_items[idx : idx + batch_size]
            captions = self.service.generate_captions_batch(chunk)
            for path_str in chunk:
                results[path_str] = captions.get(path_str)

        return results

"""JoyCaption tagger/captioner plugin (Milestone 1.3b)."""

from __future__ import annotations

import os
import time
import traceback
from typing import Any, Optional

import torch
from PIL import Image

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.base import TagResult, TaggerPlugin
from pixlstash.utils.model_utils import from_pretrained_local_first
from pixlstash.utils.service.caption_utils import sanitise_tag

logger = get_logger(__name__)

_MODEL_NAME = "fancyfeast/llama-joycaption-beta-one-hf-llava"
_MODEL_REVISION = "ebf414ea497a020da0f82df3913e5b6cb8e9663a"  # Pinned; avoids a HF network round-trip on every load.

_DEFAULT_DESCRIPTION_PROMPT = "Write a long detailed description for this image."
_DEFAULT_TAG_PROMPT = (
    "Generate only comma-separated Danbooru tags. "
    "Only add general tags. "
    "Include counts (1girl), appearance, clothing, accessories, pose, "
    "expression, actions, background. "
    "Use precise Danbooru syntax. No extra text."
)

_BASE_VRAM_MB = 8_000  # NF4/INT8 quantised footprint
_PER_IMAGE_VRAM_MB = 512

_VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"})


def _resize_to_max_dim(image: Image.Image, max_dim: int) -> Image.Image:
    """Return *image* resized so its longest side is at most *max_dim* pixels."""
    if max(image.size) <= max_dim:
        return image
    ratio = max_dim / max(image.size)
    new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
    return image.resize(new_size, Image.LANCZOS)


class JoyCaptionService:
    """Self-contained JoyCaption inference service.

    Attributes:
        _device: Inference device string.
        _precision: One of ``"nf4"``, ``"int8"``, ``"bf16"``, ``"fp16"``.
        _model: Loaded LLaVA model or None.
        _processor: Loaded processor or None.
        _model_device: Device the model is resident on.
    """

    def __init__(self, device: str, precision: str = "nf4") -> None:
        self._device = device
        self._precision = precision
        self._model = None
        self._processor = None
        self._model_device: Optional[torch.device] = None

    def is_loaded(self) -> bool:
        """Return True if the model and processor are both in memory."""
        return self._model is not None and self._processor is not None

    def ensure_ready(self, precision: str | None = None) -> None:
        """Load the model if not already loaded, re-loading on precision change."""
        if precision is not None and precision != self._precision:
            self.unload()
            self._precision = precision
        if self.is_loaded():
            return
        self._init()

    def unload(self) -> None:
        """Release model and processor from memory."""
        self._model = None
        self._processor = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def generate_caption(
        self,
        image: Image.Image,
        prompt: str,
        parameters: dict[str, Any],
    ) -> Optional[str]:
        """Generate text for a single PIL image using *prompt*.

        Args:
            image: Input image (RGB).
            prompt: User-role prompt sent to the model.
            parameters: Plugin parameters (temperature, top_k, top_p,
                max_new_tokens, suppress_tokens).

        Returns:
            Generated text string, or None on failure.
        """
        if self._model is None or self._processor is None:
            logger.error("JoyCaption model not loaded — call ensure_ready() first")
            return None
        try:
            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful image analysis assistant.",
                },
                {"role": "user", "content": "<image>\n" + prompt},
            ]
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = self._processor(
                text=text,
                images=[image],
                return_tensors="pt",
            )
            # pixel_values must be cast to the model's compute dtype (the
            # processor may output them as uint8 / Byte which the model rejects).
            # Integer tensors for token IDs are left as-is.
            _compute_dtype = (
                torch.float32
                if str(self._model_device) == "cpu"
                else torch.bfloat16
                if self._precision in ("nf4", "int8", "bf16")
                else torch.float16
            )
            inputs_moved = {}
            for k, v in inputs.items():
                if not hasattr(v, "to"):
                    inputs_moved[k] = v
                elif k == "pixel_values":
                    inputs_moved[k] = v.to(
                        device=self._model_device, dtype=_compute_dtype
                    )
                else:
                    inputs_moved[k] = v.to(self._model_device)
            inputs = inputs_moved
            logger.debug(
                "[JoyCaption] inputs dtypes: %s",
                {k: v.dtype for k, v in inputs.items() if hasattr(v, "dtype")},
            )

            temperature = float(parameters.get("temperature", 0.6))
            top_k = int(parameters.get("top_k", 0))
            top_p = float(parameters.get("top_p", 0.9))
            max_new_tokens = int(parameters.get("max_new_tokens", 256))
            suppress_tokens = _parse_suppress_tokens(
                parameters.get("suppress_tokens", "")
            )

            gen_kwargs: dict[str, Any] = {
                "max_new_tokens": max_new_tokens,
                "do_sample": temperature > 0,
                "temperature": temperature if temperature > 0 else 1.0,
            }
            if top_k > 0:
                gen_kwargs["top_k"] = top_k
            if top_p < 1.0:
                gen_kwargs["top_p"] = top_p
            if suppress_tokens:
                gen_kwargs["suppress_tokens"] = suppress_tokens

            with torch.inference_mode():
                output_ids = self._model.generate(
                    **inputs,
                    **gen_kwargs,
                )

            input_len = inputs["input_ids"].shape[1]
            new_ids = output_ids[0, input_len:]
            text = self._processor.decode(new_ids, skip_special_tokens=True)
            return text.strip() or None
        except Exception as exc:
            logger.error("JoyCaption inference error: %s", exc)
            logger.error(traceback.format_exc())
            return None

    def generate_captions_batch(
        self,
        images: list[Image.Image],
        prompt: str,
        parameters: dict[str, Any],
    ) -> list[Optional[str]]:
        """Generate captions for a batch of images in a single model.generate() call.

        Uses left-padding so all samples in the batch share the same input
        length, allowing the new-token slice ``output[:, input_len:]`` to be
        applied uniformly across all items.

        Args:
            images: RGB PIL images to caption.
            prompt: Shared user-role prompt (same for every image in the batch).
            parameters: Plugin parameters (temperature, top_k, top_p,
                max_new_tokens, suppress_tokens).

        Returns:
            List of generated strings (same length as *images*); ``None`` for
            any image that failed.
        """
        if not images:
            return []
        if self._model is None or self._processor is None:
            logger.error("JoyCaption model not loaded — call ensure_ready() first")
            return [None] * len(images)
        try:
            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful image analysis assistant.",
                },
                {"role": "user", "content": "<image>\n" + prompt},
            ]
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            texts = [text] * len(images)

            # Decoder-only models require *left* padding so that generated
            # tokens are not mixed with pad tokens on the right.
            orig_padding_side = self._processor.tokenizer.padding_side
            self._processor.tokenizer.padding_side = "left"
            try:
                inputs = self._processor(
                    text=texts,
                    images=images,
                    return_tensors="pt",
                    padding=True,
                )
            finally:
                self._processor.tokenizer.padding_side = orig_padding_side

            _compute_dtype = (
                torch.float32
                if str(self._model_device) == "cpu"
                else torch.bfloat16
                if self._precision in ("nf4", "int8", "bf16")
                else torch.float16
            )
            inputs_moved = {}
            for k, v in inputs.items():
                if not hasattr(v, "to"):
                    inputs_moved[k] = v
                elif k == "pixel_values":
                    inputs_moved[k] = v.to(
                        device=self._model_device, dtype=_compute_dtype
                    )
                else:
                    inputs_moved[k] = v.to(self._model_device)
            inputs = inputs_moved

            temperature = float(parameters.get("temperature", 0.6))
            top_k = int(parameters.get("top_k", 0))
            top_p = float(parameters.get("top_p", 0.9))
            max_new_tokens = int(parameters.get("max_new_tokens", 256))
            suppress_tokens = _parse_suppress_tokens(
                parameters.get("suppress_tokens", "")
            )

            gen_kwargs: dict[str, Any] = {
                "max_new_tokens": max_new_tokens,
                "do_sample": temperature > 0,
                "temperature": temperature if temperature > 0 else 1.0,
            }
            if top_k > 0:
                gen_kwargs["top_k"] = top_k
            if top_p < 1.0:
                gen_kwargs["top_p"] = top_p
            if suppress_tokens:
                gen_kwargs["suppress_tokens"] = suppress_tokens

            with torch.inference_mode():
                output_ids = self._model.generate(**inputs, **gen_kwargs)

            # All inputs share the same padded length due to left-padding.
            input_len = inputs["input_ids"].shape[1]
            results = []
            for i in range(len(images)):
                new_ids = output_ids[i, input_len:]
                decoded = self._processor.decode(new_ids, skip_special_tokens=True)
                results.append(decoded.strip() or None)
            return results
        except Exception as exc:
            logger.error("JoyCaption batch inference error: %s", exc)
            logger.error(traceback.format_exc())
            return [None] * len(images)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Load the JoyCaption model with the configured precision."""

        t_start = time.perf_counter()
        try:
            # bitsandbytes is required for NF4/INT8 quantisation.
            if self._precision in ("nf4", "int8"):
                import bitsandbytes  # noqa: F401 — validates availability

            from transformers import (
                AutoProcessor,
                BitsAndBytesConfig,
                LlavaForConditionalGeneration,
            )

            logger.info(
                "[JoyCaption] Loading model %s (precision=%s, device=%s) …",
                _MODEL_NAME,
                self._precision,
                self._device,
            )

            quantization_config = None
            torch_dtype = None
            device_map: str | None = None

            use_cpu = self._device == "cpu"

            if use_cpu:
                torch_dtype = torch.float32
                device_map = "cpu"
            elif self._precision == "nf4":
                # Skip the vision tower and projector — bitsandbytes incorrectly
                # quantizes SigLIP's NonDynamicallyQuantizableLinear layers to
                # uint8.  The skip-module names must be full dotted prefixes so
                # that should_convert_module's re.match check reaches all child
                # linear layers (e.g. "model.vision_tower" matches
                # "model.vision_tower.vision_model.head.attention.out_proj").
                _skip_modules = ["model.vision_tower", "model.multi_modal_projector"]
                quantization_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    llm_int8_skip_modules=_skip_modules,
                )
                device_map = "auto"
            elif self._precision == "int8":
                _skip_modules = ["model.vision_tower", "model.multi_modal_projector"]
                quantization_config = BitsAndBytesConfig(
                    load_in_8bit=True,
                    llm_int8_skip_modules=_skip_modules,
                )
                device_map = "auto"
            elif self._precision == "bf16":
                torch_dtype = torch.bfloat16
                device_map = "auto"
            else:  # fp16
                torch_dtype = torch.float16
                device_map = "auto"

            t_proc = time.perf_counter()
            self._processor = from_pretrained_local_first(
                AutoProcessor,
                _MODEL_NAME,
                revision=_MODEL_REVISION,
            )
            logger.info(
                "[JoyCaption] Processor loaded in %.1fs",
                time.perf_counter() - t_proc,
            )

            # LLaMA tokenizers have no pad token by default; set it to EOS so
            # that batched inference with padding=True works correctly.
            _tok = getattr(self._processor, "tokenizer", None)
            if _tok is not None and _tok.pad_token is None:
                _tok.pad_token = _tok.eos_token
                logger.debug(
                    "[JoyCaption] Set tokenizer pad_token = eos_token for batch padding"
                )

            # PyTorch's F.interpolate does not support LANCZOS — patch the
            # image processor to use BICUBIC so inference doesn't crash.
            _ip = getattr(self._processor, "image_processor", None)
            if _ip is not None and hasattr(_ip, "resample"):
                from PIL.Image import Resampling

                if _ip.resample == Resampling.LANCZOS:
                    _ip.resample = Resampling.BICUBIC
                    logger.debug(
                        "[JoyCaption] Patched image processor resample: LANCZOS → BICUBIC"
                    )

            load_kwargs: dict[str, Any] = {
                "device_map": device_map,
                "revision": _MODEL_REVISION,
            }
            if quantization_config is not None:
                load_kwargs["quantization_config"] = quantization_config
            if torch_dtype is not None:
                load_kwargs["torch_dtype"] = torch_dtype

            t_weights = time.perf_counter()
            model = from_pretrained_local_first(
                LlavaForConditionalGeneration,
                _MODEL_NAME,
                **load_kwargs,
            )
            logger.info(
                "[JoyCaption] Weights loaded in %.1fs",
                time.perf_counter() - t_weights,
            )
            model.eval()

            # The vision tower and projector are excluded from quantization
            # (to avoid the SigLIP uint8 bug) so we must cast them explicitly
            # to the compute dtype.  In this version of transformers,
            # vision_tower and multi_modal_projector live under model.model.
            if self._precision in ("nf4", "int8"):
                _vision_dtype = torch.bfloat16
                _inner = getattr(model, "model", model)
                _vt = getattr(_inner, "vision_tower", None)
                _proj = getattr(_inner, "multi_modal_projector", None)
                if _vt is not None:
                    _vt.to(dtype=_vision_dtype)
                if _proj is not None:
                    _proj.to(dtype=_vision_dtype)

            self._model = model
            self._model_device = (
                torch.device("cpu")
                if use_cpu
                else torch.device("cuda" if torch.cuda.is_available() else "cpu")
            )
            logger.info(
                "[JoyCaption] Model loaded successfully on %s (precision=%s) — total load time %.1fs",
                self._model_device,
                self._precision,
                time.perf_counter() - t_start,
            )
            if str(self._model_device) == "cpu":
                logger.warning(
                    "[JoyCaption] Running on CPU — inference will be very slow (~100s/image). "
                    "Set default_device=cuda in server-config.json to use the GPU."
                )

        except Exception as exc:
            self._model = None
            self._processor = None
            logger.error("Failed to load JoyCaption: %s", exc)
            raise


def _parse_suppress_tokens(value: Any) -> list[int]:
    """Parse a comma-separated integer string or list into list[int]."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                logger.warning("Invalid token ID in suppress_tokens list: %s", item)
        return result
    try:
        return [int(tok.strip()) for tok in str(value).split(",") if tok.strip()]
    except (TypeError, ValueError):
        logger.warning("Invalid token ID in suppress_tokens list: %s", value)
        return []


class JoyCaptionPlugin(TaggerPlugin):
    """TaggerPlugin for fancyfeast/llama-joycaption-beta-one-hf-llava.

    Supports both tag generation and natural-language description generation.
    Uses ``bitsandbytes`` for NF4/INT8 quantisation; the import is lazy so a
    broken ``bitsandbytes`` install only disables this plugin and does not
    prevent the rest of the application from starting.  ``bitsandbytes`` is
    installed as a regular dependency and supports Linux, macOS, and Windows.

    Attributes:
        name: Plugin identifier used in ``tagger_settings``.
        display_name: Human-readable label shown in the UI.
        description: Short description.
        supports_tags: JoyCaption can produce Danbooru-style tags.
        supports_descriptions: JoyCaption can generate captions.
        requires_download: Model must be downloaded on first use.
        default_enabled: Disabled by default.
    """

    name: str = "joycaption"
    display_name: str = "JoyCaption"
    description: str = (
        "LLaVA-style LLM captioner — generates detailed descriptions and "
        "Danbooru tags. Requires ~8 GB VRAM (NF4). Requires bitsandbytes."
    )
    supports_tags: bool = True
    supports_descriptions: bool = True
    requires_download: bool = True
    default_enabled: bool = False

    def __init__(self) -> None:
        self._service: JoyCaptionService | None = None

    # ------------------------------------------------------------------
    # Infrastructure binding
    # ------------------------------------------------------------------

    def setup(self, device: str) -> None:
        """Create the underlying :class:`JoyCaptionService`, or reuse the existing one.

        Calling this method repeatedly is safe: if a service already exists for
        the same device the existing instance — and any model loaded into it —
        is preserved.  A new service is only created when the device changes (in
        which case the old model is unloaded automatically via garbage collection).

        Args:
            device: Inference device string (``"cuda"`` or ``"cpu"``).
        """
        if self._service is not None and self._service._device == device:
            return
        self._service = JoyCaptionService(device=device)

    @property
    def service(self) -> JoyCaptionService:
        """Return the underlying :class:`JoyCaptionService` (raises if not set up)."""
        if self._service is None:
            raise RuntimeError("JoyCaptionPlugin.setup() has not been called")
        return self._service

    # ------------------------------------------------------------------
    # TaggerPlugin interface
    # ------------------------------------------------------------------

    def parameter_schema(self) -> list[dict[str, Any]]:
        """Return parameter definitions for JoyCaption."""
        return [
            {
                "name": "precision",
                "label": "Precision",
                "type": "select",
                "default": "nf4",
                "description": (
                    "Quantisation precision. NF4/INT8 use bitsandbytes and "
                    "require less VRAM; BF16/FP16 require more but are faster."
                ),
                "options": [
                    {"value": "nf4", "label": "NF4 (~5 GB VRAM)"},
                    {"value": "int8", "label": "INT8 (~8 GB VRAM)"},
                    {"value": "bf16", "label": "BF16 (~16 GB VRAM)"},
                    {"value": "fp16", "label": "FP16 (~16 GB VRAM)"},
                ],
            },
            {
                "name": "temperature",
                "label": "Temperature",
                "type": "number",
                "default": 0.1,
                "min": 0.0,
                "max": 2.0,
                "step": 0.05,
                "description": "Sampling temperature. 0 = greedy decoding.",
            },
            {
                "name": "top_k",
                "label": "Top-K",
                "type": "integer",
                "default": 0,
                "min": 0,
                "max": 200,
                "step": 1,
                "description": "Top-K sampling. 0 = disabled.",
            },
            {
                "name": "top_p",
                "label": "Top-P (nucleus)",
                "type": "number",
                "default": 0.3,
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "description": "Nucleus sampling probability. 1.0 = disabled.",
            },
            {
                "name": "max_new_tokens",
                "label": "Max new tokens (descriptions)",
                "type": "integer",
                "default": 256,
                "min": 32,
                "max": 1024,
                "step": 32,
                "description": "Maximum tokens to generate for descriptions.",
            },
            {
                "name": "max_new_tokens_tags",
                "label": "Max new tokens (tags)",
                "type": "integer",
                "default": 512,
                "min": 32,
                "max": 1024,
                "step": 32,
                "description": "Maximum tokens to generate for tag output. A typical full Danbooru tag list needs 400–600 tokens; increase if tags are cut off alphabetically mid-list.",
            },
            {
                "name": "tag_batch_size",
                "label": "Tag batch size",
                "type": "integer",
                "default": 4,
                "min": 1,
                "max": 16,
                "step": 1,
                "description": "Number of images to process in one GPU batch during tagging. Higher = faster but more VRAM.",
            },
            {
                "name": "suppress_tokens",
                "label": "Suppress tokens",
                "type": "csv-int",
                "default": "",
                "description": (
                    "Comma-separated token IDs to suppress during generation. "
                    "Leave blank to suppress nothing."
                ),
            },
            {
                "name": "description_prompt",
                "label": "Description prompt",
                "type": "textarea",
                "default": _DEFAULT_DESCRIPTION_PROMPT,
                "description": (
                    "System prompt used when generating image descriptions."
                ),
            },
            {
                "name": "tag_prompt",
                "label": "Tag prompt",
                "type": "textarea",
                "default": _DEFAULT_TAG_PROMPT,
                "description": (
                    "System prompt used when generating Danbooru-style tags."
                ),
            },
        ]

    def needs_download(self, parameters: dict[str, Any] | None = None) -> bool:
        """Return ``True`` if the base model is not yet in the HF cache."""
        try:
            from huggingface_hub import try_to_load_from_cache  # type: ignore[import]

            result = try_to_load_from_cache(
                _MODEL_NAME, "config.json", revision=_MODEL_REVISION
            )
            return result is None
        except Exception:
            return True

    def download(
        self,
        parameters: dict[str, Any] | None = None,
        progress_callback=None,
    ) -> None:
        """Download JoyCaption model files into the HuggingFace cache.

        Args:
            parameters: Unused (all precisions share the same base repo).
            progress_callback: Optional progress reporting callback.
        """
        try:
            from huggingface_hub import snapshot_download  # type: ignore[import]

            logger.info("[JoyCaption] Starting download of %s …", _MODEL_NAME)
            snapshot_download(
                _MODEL_NAME,
                revision=_MODEL_REVISION,
                ignore_patterns=["*.msgpack", "*.h5", "flax_model*"],
            )
            logger.info("[JoyCaption] Download complete: %s", _MODEL_NAME)
        except Exception as exc:
            logger.error("[JoyCaption] Download failed: %s", exc)
            raise

    def init(self, parameters: dict[str, Any]) -> None:
        """Load the model into memory with the requested precision.

        Args:
            parameters: Plugin parameters (uses ``precision``).
        """
        precision = str(parameters.get("precision", "nf4"))
        already_loaded = self.is_loaded()
        logger.info(
            "[JoyCaption] init() called — precision=%s, already_loaded=%s",
            precision,
            already_loaded,
        )
        self.service.ensure_ready(precision=precision)
        logger.info("[JoyCaption] init() complete — loaded=%s", self.is_loaded())

    def unload(self) -> None:
        """Unload JoyCaption from memory."""
        if self._service is not None:
            self._service.unload()

    def is_loaded(self) -> bool:
        """Return ``True`` if the model is in memory."""
        if self._service is None:
            return False
        return self._service.is_loaded()

    def list_downloaded_artifacts(self) -> list[dict[str, Any]]:
        """Return one entry per downloaded artifact in the HF cache."""
        try:
            from huggingface_hub import scan_cache_dir  # type: ignore[import]

            cache_info = scan_cache_dir()
            for repo in cache_info.repos:
                if repo.repo_id == _MODEL_NAME:
                    return [
                        {
                            "id": "base",
                            "label": "Base model",
                            "size_bytes": repo.size_on_disk,
                        }
                    ]
        except Exception:
            logger.error("Failed to list JoyCaption artifacts from cache")
        return []

    def delete_artifact(self, artifact_id: str) -> None:
        """Delete the downloaded base model from the HF cache.

        Args:
            artifact_id: Must be ``"base"`` (the only supported artifact).
        """
        if artifact_id != "base":
            raise ValueError(f"Unknown JoyCaption artifact: {artifact_id!r}")
        try:
            from huggingface_hub import scan_cache_dir  # type: ignore[import]

            cache_info = scan_cache_dir()
            delete_strategy = cache_info.delete_revisions(
                *[
                    rev.commit_hash
                    for repo in cache_info.repos
                    if repo.repo_id == _MODEL_NAME
                    for rev in repo.revisions
                ]
            )
            delete_strategy.execute()
            logger.debug("JoyCaption model deleted from cache.")
        except Exception as exc:
            logger.error("Failed to delete JoyCaption artifact: %s", exc)
            raise

    def estimated_vram_mb(
        self, image_count: int, parameters: dict[str, Any] | None = None
    ) -> int:
        """Estimate peak VRAM for processing *image_count* images.

        Args:
            image_count: Number of images.
            parameters: Plugin parameters (uses ``precision``).

        Returns:
            Estimated VRAM in MB.
        """
        if self._service is None or self._service._model_device is None:
            return 0
        if str(self._service._model_device) == "cpu":
            return 0
        return _BASE_VRAM_MB + _PER_IMAGE_VRAM_MB * max(1, image_count)

    def effective_batch_size(self, parameters: dict[str, Any] | None = None) -> int:
        """Return the GPU inference batch size (``tag_batch_size`` parameter)."""
        if parameters:
            return max(1, int(parameters.get("tag_batch_size", 4)))
        return 4

    def tag_images(
        self,
        image_paths: list,
        parameters: dict[str, Any],
        preloaded=None,
        stop_event=None,
    ) -> dict[str, list[TagResult]]:
        """Generate Danbooru-style tags for each image.

        Images are processed in GPU batches (controlled by ``tag_batch_size``)
        for significantly higher throughput than one-at-a-time inference.
        LLM output has no per-tag confidence score; all ``TagResult`` entries
        carry ``confidence=None``.

        Args:
            image_paths: Absolute paths to image files.
            parameters: Plugin parameters.
            preloaded: Unused.
            stop_event: If set, processing stops after the current batch.

        Returns:
            ``{path: list[TagResult]}`` — empty list on per-image failure.
        """
        tag_prompt = str(parameters.get("tag_prompt", _DEFAULT_TAG_PROMPT))
        tag_batch_size = max(1, int(parameters.get("tag_batch_size", 4)))
        # Use a tag-specific token budget; fall back to the shared max_new_tokens.
        max_new_tokens_tags = int(
            parameters.get("max_new_tokens_tags")
            or parameters.get("max_new_tokens", 512)
        )
        tag_params = {**parameters, "max_new_tokens": max_new_tokens_tags}

        results: dict[str, list[TagResult]] = {}
        logger.debug(
            "[JoyCaption] tag_images() — %d image(s), batch_size=%d, max_new_tokens=%d",
            len(image_paths),
            tag_batch_size,
            max_new_tokens_tags,
        )

        batch_paths: list[str] = []
        batch_images: list[Image.Image] = []

        def _flush_batch() -> None:
            if not batch_paths:
                return
            captions = self.service.generate_captions_batch(
                batch_images, tag_prompt, tag_params
            )
            for path_str, raw in zip(batch_paths, captions):
                tags = _parse_tags(raw) if raw else []
                logger.debug("[JoyCaption] Tagged %s → %d tag(s)", path_str, len(tags))
                results[path_str] = tags
            batch_paths.clear()
            batch_images.clear()

        for path in image_paths:
            if stop_event is not None and stop_event.is_set():
                break
            path_str = str(path)
            ext = os.path.splitext(path_str)[1].lower()
            if ext in _VIDEO_EXTS:
                results[path_str] = []
                continue
            try:
                image = Image.open(path_str).convert("RGB")
                image = _resize_to_max_dim(image, max_dim=512)
                batch_paths.append(path_str)
                batch_images.append(image)
                if len(batch_paths) >= tag_batch_size:
                    _flush_batch()
            except Exception as exc:
                logger.warning(
                    "[JoyCaption] Failed to load image %s: %s", path_str, exc
                )
                results[path_str] = []

        _flush_batch()  # process any remaining images

        logger.debug("[JoyCaption] tag_images() complete — %d results", len(results))
        return results

    def generate_descriptions(
        self,
        image_paths: list,
        parameters: dict[str, Any],
        stop_event=None,
    ) -> dict[str, Optional[str]]:
        """Generate natural-language descriptions for each image.

        Args:
            image_paths: Absolute paths to image/video files.
            parameters: Plugin parameters.
            stop_event: If set, processing stops after the current image.

        Returns:
            ``{path: caption_str}`` — value is ``None`` on failure.
        """
        desc_prompt = str(
            parameters.get("description_prompt", _DEFAULT_DESCRIPTION_PROMPT)
        )
        results: dict[str, Optional[str]] = {}
        logger.debug(
            "[JoyCaption] generate_descriptions() — %d image(s)", len(image_paths)
        )

        for path in image_paths:
            if stop_event is not None and stop_event.is_set():
                break
            path_str = str(path)
            ext = os.path.splitext(path_str)[1].lower()
            if ext in _VIDEO_EXTS:
                results[path_str] = None
                continue
            try:
                image = Image.open(path_str).convert("RGB")
                image = _resize_to_max_dim(image, max_dim=640)
                caption = self.service.generate_caption(image, desc_prompt, parameters)
                logger.debug(
                    "[JoyCaption] Described %s → %s",
                    path_str,
                    repr(caption[:80]) if caption else None,
                )
                results[path_str] = caption
            except Exception as exc:
                logger.warning(
                    "[JoyCaption] Description failed for %s: %s", path_str, exc
                )
                results[path_str] = None

        logger.debug(
            "[JoyCaption] generate_descriptions() complete — %d results", len(results)
        )
        return results


def _parse_tags(raw: str) -> list[TagResult]:
    """Split comma-separated LLM output into TagResult list.

    Applies ``sanitise_tag`` to normalise each token.  Tags without any
    alphabetic characters after sanitisation are discarded.  All ``TagResult``
    entries carry ``confidence=None`` because LLM decoding does not produce
    per-token probabilities in the generation pipeline.

    Args:
        raw: Raw comma-separated text from the model.

    Returns:
        Deduplicated, sanitised list of TagResult objects.
    """
    seen: set[str] = set()
    results: list[TagResult] = []
    for token in raw.split(","):
        cleaned = sanitise_tag(token.strip())
        if not cleaned or not any(c.isalpha() for c in cleaned):
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(TagResult(tag=cleaned, confidence=None))
    return results

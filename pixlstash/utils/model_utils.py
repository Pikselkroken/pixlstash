"""Utility helpers for loading and configuring ML models."""

import os
import platform
from contextlib import contextmanager

try:
    from transformers import logging as transformers_logging
except Exception:  # pragma: no cover - optional dependency behaviour
    transformers_logging = None

from sentence_transformers import SentenceTransformer

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable, clamping to >= 1."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        return max(1, value)
    except ValueError:
        logger.warning(
            "Invalid integer for %s=%r, using default=%s", name, raw, default
        )
        return default


def env_float(name: str, default: float | None) -> float | None:
    """Read a positive float from an environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
        if value <= 0:
            return None
        return value
    except ValueError:
        logger.warning("Invalid float for %s=%r, using default=%s", name, raw, default)
        return default


def from_pretrained_local_first(cls, model_name, **kwargs):
    """Load a HuggingFace model/processor from local cache when possible.

    Tries ``local_files_only=True`` first so no network requests are made
    when the model is already cached.  Falls back to a normal (online) load
    only on the first run, when the files aren't present yet.
    """
    try:
        return cls.from_pretrained(model_name, local_files_only=True, **kwargs)
    except OSError:
        logger.info("Downloading %s for the first time...", model_name)
        return cls.from_pretrained(model_name, **kwargs)


@contextmanager
def quiet_transformers_load_report():
    """Temporarily suppress non-critical Transformers load-report warnings.

    Some HF model loads (notably all-MiniLM-L6-v2) can emit a benign
    "UNEXPECTED embeddings.position_ids" load report. Keep hard errors while
    muting that warning noise during model initialization.
    """
    if transformers_logging is None:
        yield
        return

    previous = transformers_logging.get_verbosity()
    try:
        transformers_logging.set_verbosity_error()
        yield
    finally:
        transformers_logging.set_verbosity(previous)


def load_sentence_transformer(*args, **kwargs) -> SentenceTransformer:
    """Load a SentenceTransformer model, suppressing benign load warnings."""
    with quiet_transformers_load_report():
        return SentenceTransformer(*args, **kwargs)


def clean_asset_name(filename: str) -> str:
    """Strip file extension and replace underscores/hyphens with spaces.

    Used to produce human-readable model and LoRA names for text embedding.
    Example: 'z_image_turbo_bf16.safetensors' -> 'z image turbo bf16'
    """
    name = os.path.basename(filename or "")
    name = os.path.splitext(name)[0]
    name = name.replace("_", " ").replace("-", " ")
    return name.strip()


def trim_process_memory() -> None:
    """Best-effort RSS trim for Linux/glibc allocators."""
    if not platform.system().lower().startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        trim = getattr(libc, "malloc_trim", None)
        if trim is not None:
            trim(0)
    except Exception as exc:
        logger.debug("malloc_trim call failed: %s", exc)

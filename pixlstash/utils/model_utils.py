"""Utility helpers for loading and configuring ML models."""

from __future__ import annotations

import os
import platform
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def _transformers_logging():
    """Return the Transformers ``logging`` module, or ``None`` if unavailable.

    Imported on demand rather than at module scope: ``transformers`` (and the
    ``sentence_transformers`` stack below it) costs seconds to import and is
    only needed once a model is actually loaded. Importing it here would make
    every consumer of this module — including the API server and the whole test
    suite — pay for it at startup.
    """
    try:
        from transformers import logging as transformers_logging
    except Exception as exc:  # pragma: no cover - optional dependency behaviour
        logger.debug(
            "Transformers logging unavailable (%s); model load reports stay unmuted.",
            exc,
        )
        return None
    return transformers_logging


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
    transformers_logging = _transformers_logging()
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
    # Local import: see _transformers_logging() for why the ML stack is not
    # imported at module scope.
    from sentence_transformers import SentenceTransformer

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

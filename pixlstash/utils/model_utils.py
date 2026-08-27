"""Utility helpers for loading and configuring ML models."""

from __future__ import annotations

import os
import platform
import re
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
    every consumer of this module - including the API server and the whole test
    suite - pay for it at startup.
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

    Note:
        This feeds sentence embeddings (``inference/workflows/text_embedding``),
        so its output is baked into stored vectors. Changing it would silently
        invalidate every embedding built from ComfyUI metadata. The shelf's
        display name therefore layers on top in :func:`derive_model_name`
        instead of altering this.
    """
    name = os.path.basename(filename or "")
    name = os.path.splitext(name)[0]
    name = name.replace("_", " ").replace("-", " ")
    return name.strip()


# Trailing tokens that record where in a training run a checkpoint was saved.
# `JimmyVehicle_000002750` and `ohwx_woman-step00004500` are one subject each, not
# a subject called "JimmyVehicle 000002750".
#
# The bare-digit rule needs five digits or more on purpose: ai-toolkit
# zero-pads its step counts, while a genuine version suffix is short. So
# `000002750` goes and the `2` in `portrait mix v2` stays.
_TRAINING_SUFFIX_RE = re.compile(
    r"^(?:step\d+|epoch\d+|\d+ep|\d{5,})$",
    re.IGNORECASE,
)


def derive_model_name(filename: str) -> str:
    """Return a display name for a model file that never said what it is called.

    Builds on :func:`clean_asset_name` and additionally drops trailing training
    bookkeeping, because the step and epoch are parsed into their own fields and
    repeating them in the name turns six checkpoints of one run into six
    unrelated-looking rows.

    This is a *derived* name and the caller must treat it as one: the shelf
    stores ``display_name`` as NULL and computes this at render, so
    ``WHERE display_name IS NULL`` stays an exact "nobody has named this" queue
    and a guess is never mistaken for a choice.

    Args:
        filename: File name or path.

    Returns:
        A human-readable name, or ``""`` when nothing survives. Callers decide
        what an empty result looks like; the shelf falls back to the raw
        filename and marks the row as carrying the file's own name.

    Examples:
        >>> derive_model_name("JimmyVehicle_000002750.safetensors")
        'JimmyVehicle'
        >>> derive_model_name("ohwx_woman-step00004500.safetensors")
        'ohwx woman'
        >>> derive_model_name("portrait_mix_v2.safetensors")
        'portrait mix v2'
    """
    tokens = clean_asset_name(filename).split()
    while tokens and _TRAINING_SUFFIX_RE.match(tokens[-1]):
        tokens.pop()
    return " ".join(tokens)


# A trailing version token. Unlike a training suffix this is a *person's*
# revision of a subject rather than a point inside one run: `Foxglove_v2` is a
# second attempt at Foxglove, trained separately, and `Foxglove_000000500` is a
# checkpoint of one attempt. Both belong on one shelf row, which is why the
# stack detector groups on the name with this token removed.
#
# Only an explicit `v<digits>` counts, optionally with one decimal (`v2.1`,
# which Civitai-style names use). A bare trailing `2` is deliberately NOT a
# version: `JimmyVehicle` beside `JimmyVehicle2` is the ambiguous prefix case that
# needs counter-evidence, and reading it as a version here would silently merge
# two unrelated subjects.
#
# ``re.ASCII`` is not decoration. Python's ``\d`` matches every Unicode decimal
# - `v٢` would parse as version 2 - while JavaScript's does not, and
# `modelVersion` in `frontend/src/utils/modelShelf.js` mirrors this rule. Two
# halves that disagree about what a version is would put a member under a
# version the server never assigned it.
_VERSION_SUFFIX_RE = re.compile(r"^v(\d+)(?:\.(\d+))?$", re.IGNORECASE | re.ASCII)


def split_model_version(filename: str) -> tuple[str, str | None]:
    """Split a derived name into its subject and its trailing version token.

    Runs on top of :func:`derive_model_name`, so training bookkeeping is already
    gone by the time the version is looked for and ``Foxglove_v2_000000500``
    answers the same as ``Foxglove_v2``.

    Args:
        filename: File name or path.

    Returns:
        ``(subject, version)``, the version **exactly as the file wrote it** or
        ``None`` when the name carries no version token. Case is preserved
        because this token is put back into a stack's name, and folding it would
        silently rename every ``_V2`` run on the shelf to ``v2``. Comparison is
        never done on this string - :func:`version_sort_key` parses it, and that
        is what makes the case irrelevant everywhere it matters.

    Examples:
        >>> split_model_version("Foxglove_v2.safetensors")
        ('Foxglove', 'v2')
        >>> split_model_version("Foxglove_V2.1_000000500.safetensors")
        ('Foxglove', 'V2.1')
        >>> split_model_version("Foxglove.safetensors")
        ('Foxglove', None)
    """
    tokens = derive_model_name(filename).split()
    if tokens and _VERSION_SUFFIX_RE.match(tokens[-1]):
        return " ".join(tokens[:-1]), tokens[-1]
    return " ".join(tokens), None


def version_sort_key(version: str | None) -> tuple[int, int]:
    """Order two version tokens, newest highest.

    An unversioned file reads as ``v1``: ``Foxglove`` exists before
    ``Foxglove_v2`` does, so it is the first version rather than an unknown one,
    and treating it as unknown would make the cover of a two-version stack a
    coin toss.

    Args:
        version: A token from :func:`split_model_version`, or ``None``.

    Returns:
        ``(major, minor)``, comparable with ``<``.
    """
    match = _VERSION_SUFFIX_RE.match(version or "")
    if not match:
        return (1, 0)
    return (int(match.group(1)), int(match.group(2) or 0))


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

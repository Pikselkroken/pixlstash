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


# The precision a file was stored at, written into its name.
#
# `clean_asset_name` has already split on `_` and `-` by the time any of this
# runs, so the vocabulary is TOKEN-LEVEL: `Q4_K_M` arrives as three tokens.
#
# Two separate rules, not one list, because the two families have different
# safety profiles. A GGUF level is `k`, `s`, `m`, `l`, `0` or `1` - ordinary
# tokens that appear in real names (`sdxl_1_0_fp16`, `sd_xl_base_1`) - so they
# are only ever eaten as the tail of an explicit `q<n>` head. The rest
# (`scaled`, `awq`, `gptq`, `fast`) are distinctive enough to pop next to any
# real quant token, and never on their own.
#
# ``re.ASCII`` for the same reason `_VERSION_SUFFIX_RE` carries it: Python's
# ``\d`` matches every Unicode decimal and JavaScript's does not, and
# `frontend/src/utils/modelShelf.js` mirrors this rule token for token.
# ``iq`` as well as ``q``: the I-quant family (``IQ3_M``, ``IQ4_XS``,
# ``IQ2_XXS``) is most of what city96 publishes for Flux and Qwen-Image, which
# is the image-model GGUF this feature is actually for. ``xs``/``xl``/``xxs``/
# ``nl`` are levels for the same reason ``k``/``s``/``m`` are.
_GGUF_HEAD_RE = re.compile(r"^i?q\d+$", re.IGNORECASE | re.ASCII)
_GGUF_LEVELS = frozenset({"k", "s", "m", "l", "xs", "xl", "xxs", "nl", "0", "1"})

# Filename spelling -> canonical id. The refinement wins where both are
# present (`fp8_e4m3fn` is `fp8_e4m3`), which the rightmost-token rule in
# :func:`_split_quant` gets for free: real names put the refinement last.
_QUANT_TOKENS = {
    "fp32": "fp32",
    "f32": "fp32",
    "fp16": "fp16",
    "f16": "fp16",
    "bf16": "bf16",
    "fp8": "fp8",
    "f8": "fp8",
    "e4m3": "fp8_e4m3",
    "e4m3fn": "fp8_e4m3",
    "e5m2": "fp8_e5m2",
    "nvfp4": "nvfp4",
    "fp4": "fp4",
    "nf4": "nf4",
    "int8": "int8",
    "i8": "int8",
    "int4": "int4",
    "i4": "int4",
}

# Popped only when a real quant token is popped with them. On their own they
# are somebody's model name.
_QUANT_MODIFIERS = frozenset({"scaled", "awq", "gptq", "fast"})

# Spellings that are not already the canonical id. The safetensors header
# names a dtype (`f16`, `f8_e4m3`, `i32`); a filename names a precision
# (`fp16`); one card must not read `f16` where the next reads `FP16` for the
# same thing, so both sources fold through :func:`canonical_quant`.
_QUANT_FOLDS = {
    "f32": "fp32",
    "f16": "fp16",
    "f8": "fp8",
    "f8_e4m3": "fp8_e4m3",
    "f8_e4m3fn": "fp8_e4m3",
    "f8_e5m2": "fp8_e5m2",
    "i4": "int4",
    "i8": "int8",
    "i16": "int16",
    "i32": "int32",
    "i64": "int64",
    "u8": "uint8",
    "u16": "uint16",
    "u32": "uint32",
    "u64": "uint64",
}


def canonical_quant(raw: str | None) -> str | None:
    """Fold one source's spelling of a precision into the id clients render.

    Both sources go through here: :func:`quant_from_filename` and
    ``adapter_header.quant_from_header``, whose answer is a **safetensors
    dtype** (``f16``, ``f8_e4m3``, ``i32``) rather than the precision a person
    writes into a filename (``fp16``). Folding one and not the other is how one
    card comes to read ``f16`` where the next reads ``FP16``.

    An unrecognised string folds to itself, lowercased. That is deliberate:
    ``mixed`` is a real header answer, a GGUF level (``q4_k_m``) *is* the name
    a reader recognises, and a dtype nothing here has seen is better shown
    verbatim than swallowed.

    Args:
        raw: A dtype, a filename postfix, or ``None``.

    Returns:
        The canonical id, or ``None`` when *raw* says nothing.

    Examples:
        >>> canonical_quant("F8_E4M3")
        'fp8_e4m3'
        >>> canonical_quant("bf16")
        'bf16'
        >>> canonical_quant("mixed")
        'mixed'
    """
    key = (raw or "").strip().casefold()
    if not key:
        return None
    return _QUANT_FOLDS.get(key, key)


def _split_quant(tokens: list[str]) -> tuple[list[str], str | None]:
    """Split a token list into its name and the quant postfix on the end.

    The one parser both :func:`derive_model_name` and
    :func:`quant_from_filename` run, so the name a row shows and the badge
    beside it can never disagree about where the name ended.

    **Pure**: *tokens* is never mutated, on any branch. The JS mirror cannot
    mutate its argument at all, and a helper whose side effect depends on which
    branch ran is the kind of difference the parity tests would not catch.

    Args:
        tokens: ``clean_asset_name(...).split()``.

    Returns:
        ``(tokens with the postfix removed, canonical id or None)``. The list
        is a new one, equal to *tokens* when nothing was recognised.
    """
    # The GGUF tail first: `q<n>` plus up to two level tokens, longest match
    # first so `Q4 K M` beats the bare `Q4` inside it.
    for width in (3, 2, 1):
        if len(tokens) < width or not _GGUF_HEAD_RE.match(tokens[-width]):
            continue
        levels = tokens[len(tokens) - width + 1 :]
        if all(level.casefold() in _GGUF_LEVELS for level in levels):
            return tokens[:-width], "_".join(t.casefold() for t in tokens[-width:])

    kept = list(tokens)
    popped: list[str] = []
    while kept and (
        kept[-1].casefold() in _QUANT_TOKENS or kept[-1].casefold() in _QUANT_MODIFIERS
    ):
        popped.append(kept.pop())
    # The guard the whole safety of this rests on. `scaled` and `fast` are
    # ordinary tokens in real model names, and may only be eaten when a genuine
    # quant token was eaten with them: without this,
    # `some_model_scaled.safetensors` silently becomes `some model`.
    quant = next(
        (_QUANT_TOKENS[t.casefold()] for t in popped if t.casefold() in _QUANT_TOKENS),
        None,
    )
    if quant is None:
        return list(tokens), None
    return kept, quant


def quant_from_filename(filename: str) -> str | None:
    """Return the precision a model's *filename* says it was stored at.

    The second of the two sources behind the shelf's ``quant`` column, and the
    only one a ``.gguf`` file has: the header answers for ``.safetensors`` and
    is authoritative where it does, so this fills the gap rather than competing
    with it.

    Args:
        filename: File name or path.

    Returns:
        A canonical id (see :func:`canonical_quant`), or ``None`` when the name
        carries no quant postfix - which is most names.

    Examples:
        >>> quant_from_filename("z_image_turbo_bf16.safetensors")
        'bf16'
        >>> quant_from_filename("t5xxl_fp8_e4m3fn.safetensors")
        'fp8_e4m3'
        >>> quant_from_filename("flux1-dev-Q4_K_M.gguf")
        'q4_k_m'
        >>> quant_from_filename("some_model_scaled.safetensors") is None
        True
    """
    return _split_quant(clean_asset_name(filename).split())[1]


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

    Builds on :func:`clean_asset_name` and additionally drops the trailing
    quant postfix and then any training bookkeeping, because both are parsed
    into their own fields and repeating them in the name turns six checkpoints
    of one run into six unrelated-looking rows, and puts the precision in the
    place a person reads the model's identity.

    Quant first, then training: real names put the quant last
    (``model-step00004500-fp16``). The reverse (``model_fp16_step500``) is not
    a convention anybody uses and is deliberately not handled.

    **The precision is dropped from the NAME, never lost.** Two quant variants
    of one model collapse to one name here, and what keeps them apart is the
    badge the shelf and the workflow card draw from
    :func:`quant_from_filename` (or, for a ``.safetensors``, the header's own
    answer). A caller that strips the name without showing the badge has made
    two rows read identically.

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
        >>> derive_model_name("t5xxl_fp8_e4m3fn.safetensors")
        't5xxl'
        >>> derive_model_name("flux1-dev-Q4_K_M.gguf")
        'flux1 dev'
    """
    tokens, _quant = _split_quant(clean_asset_name(filename).split())
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

"""Read what a ``.safetensors`` adapter says about itself, without loading it.

The model shelf (v1.10) has to describe a file the user just dropped on it, and
the file may be 800 MB. A safetensors file starts with an 8-byte little-endian
header length followed by that many bytes of JSON, so everything the shelf needs
is in the first few hundred kilobytes and the tensor payload is never touched.
That is why this reads the header by hand instead of using the ``safetensors``
package: the package is only present transitively (via transformers) and its
reader is built to load tensors, which is the one thing we must not do here.

**These files are untrusted.** They arrive by download, so the header length and
the JSON inside it are attacker-controlled. Everything below is bounded and
every parse failure degrades to "we could not tell" rather than raising into the
import path: a file we cannot describe is still a file the user wants on the
shelf, just one they will have to name themselves.

What the wild actually looks like, measured against real files on 2026-08-07:

* ai-toolkit output carries ``ss_base_model_version``, ``ss_output_name``,
  ``ss_tag_frequency``, plus ``software`` and ``training_info``.
* A LoRA downloaded from a model site routinely carries **nothing** but
  ``format`` — one measured at 819 MB and 448 tensors with a single metadata
  key. Empty metadata is the normal case, not an edge case.

Which is why :func:`detect_adapter_kind` reads *tensor names* rather than
metadata: it is the only signal present on every file regardless of provenance.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# The safetensors spec caps the header at 100 MB. We cap far lower: a header is
# tensor names plus a small metadata dict, and the largest seen in practice is
# well under a megabyte. The point of the cap is that the length prefix comes
# from the file, so an 8-byte field can otherwise ask us to allocate 16 EiB.
_MAX_HEADER_BYTES = 16 * 1024 * 1024

# A file shorter than this cannot contain a length prefix and a header.
_MIN_FILE_BYTES = 8

# Tensor-name suffixes that identify the adapter algorithm. Order matters:
# DoRA is LoRA plus a magnitude vector, so its tensors include lora_A/lora_B
# *and* dora_scale, and the more specific marker has to win. Verified against
# real LoRA files; the LyCORIS markers come from the formats' own conventions
# and want a real file pointed at them before this enum is treated as closed.
_KIND_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dora", ("dora_scale",)),
    ("lokr", ("lokr_w1", "lokr_w2", "lokr_w1_a", "lokr_w2_a")),
    ("loha", ("hada_w1_a", "hada_w2_a", "hada_w1_b", "hada_w2_b")),
    ("oft", ("oft_blocks", "oft_diag")),
    ("lora", ("lora_A", "lora_B", "lora_up", "lora_down")),
)

_MARKER_RE = re.compile(
    r"\.("
    + "|".join(
        sorted({m for _, ms in _KIND_MARKERS for m in ms}, key=len, reverse=True)
    )
    + r")(?:\.|$)"
)

KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class AdapterInfo:
    """What a ``.safetensors`` file says about itself.

    Every field except ``kind`` and ``tensor_count`` is optional, because the
    common downloaded file carries no metadata at all. A ``None`` here means
    "the file did not say", never "the value is empty" — the shelf shows those
    differently, since the first is a prompt to fill something in.

    Attributes:
        kind: Adapter algorithm from tensor names, or ``"unknown"``.
        tensor_count: Number of tensors, excluding the metadata entry.
        base_model: Trainer-reported base model. **Free text** (``zimage``,
            ``krea2``, ``minimax_h3`` seen in the wild), not a closed set.
        trigger_words: Tags recovered from ``ss_tag_frequency``.
        display_name: Trainer-reported name, if any.
        training_step: Step the checkpoint was saved at.
        training_epoch: Epoch the checkpoint was saved at.
        trained_by: Producing software, e.g. ``"ai-toolkit 0.9.11"``.
        has_metadata: Whether the file carried a ``__metadata__`` block with
            anything beyond the mandatory ``format`` key.
    """

    kind: str
    tensor_count: int
    base_model: Optional[str] = None
    trigger_words: list[str] = field(default_factory=list)
    display_name: Optional[str] = None
    training_step: Optional[int] = None
    training_epoch: Optional[int] = None
    trained_by: Optional[str] = None
    has_metadata: bool = False


def read_safetensors_header(path: str) -> Optional[dict]:
    """Return the parsed JSON header of a safetensors file, or ``None``.

    Reads the 8-byte little-endian length prefix and exactly that many bytes.
    The tensor payload after the header is never read.

    Args:
        path: Path to the ``.safetensors`` file.

    Returns:
        The decoded header dict, or ``None`` when the file is unreadable, too
        short, declares an implausible header length, or does not contain JSON.
    """
    try:
        with open(path, "rb") as handle:
            prefix = handle.read(_MIN_FILE_BYTES)
            if len(prefix) < _MIN_FILE_BYTES:
                logger.warning(
                    "Not a safetensors file (only %d bytes, need at least %d): %s",
                    len(prefix),
                    _MIN_FILE_BYTES,
                    path,
                )
                return None
            (header_len,) = struct.unpack("<Q", prefix)
            if header_len == 0 or header_len > _MAX_HEADER_BYTES:
                logger.warning(
                    "Refusing safetensors header of %d bytes (cap %d), file %s. "
                    "The length prefix is file-controlled, so this is either a "
                    "corrupt file or one built to make us allocate.",
                    header_len,
                    _MAX_HEADER_BYTES,
                    path,
                )
                return None
            raw = handle.read(header_len)
        if len(raw) < header_len:
            logger.warning(
                "Truncated safetensors header in %s: declared %d bytes, got %d.",
                path,
                header_len,
                len(raw),
            )
            return None
        header = json.loads(raw)
    except (OSError, struct.error) as exc:
        logger.warning("Could not read safetensors header from %s: %s", path, exc)
        return None
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning(
            "safetensors header in %s is not valid JSON: %s. Treating the file "
            "as undescribed rather than rejecting it.",
            path,
            exc,
        )
        return None
    if not isinstance(header, dict):
        logger.warning(
            "safetensors header in %s decoded to %s, expected an object.",
            path,
            type(header).__name__,
        )
        return None
    return header


def detect_adapter_kind(tensor_names) -> str:
    """Return the adapter algorithm implied by *tensor_names*.

    This is the only identification that works on every file: a model site
    download often strips all metadata, but it cannot strip the tensor names
    without breaking the file.

    Args:
        tensor_names: Iterable of tensor keys from the header.

    Returns:
        One of ``"dora"``, ``"lokr"``, ``"loha"``, ``"oft"``, ``"lora"``, or
        ``"unknown"``. DoRA wins over LoRA where both markers appear, because
        DoRA carries LoRA's tensors plus its own.
    """
    found: set[str] = set()
    for name in tensor_names:
        if not isinstance(name, str):
            continue
        match = _MARKER_RE.search(name)
        if match:
            found.add(match.group(1))
    if not found:
        return KIND_UNKNOWN
    for kind, markers in _KIND_MARKERS:
        if found.intersection(markers):
            return kind
    return KIND_UNKNOWN


def _trigger_words_from_tag_frequency(raw) -> list[str]:
    """Pull tag names out of kohya/ai-toolkit's ``ss_tag_frequency``.

    The value is ``{dataset_dir: {tag: count}}``, e.g.
    ``{"1_jimmycarr": {"jimmycarr": 1}}``. The dataset directory name is an
    artefact of how the trainer was invoked, so only the inner tags are used,
    ordered by descending count so the actual trigger leads.

    Args:
        raw: The metadata value, a JSON string or an already-decoded dict.

    Returns:
        Tag names, most frequent first. Empty when the value is unusable.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            logger.warning("ss_tag_frequency is not valid JSON (%s); ignoring.", exc)
            return []
    if not isinstance(raw, dict):
        return []
    counts: dict[str, int] = {}
    for tags in raw.values():
        if not isinstance(tags, dict):
            continue
        for tag, count in tags.items():
            if not isinstance(tag, str) or not tag.strip():
                continue
            try:
                counts[tag] = counts.get(tag, 0) + int(count)
            except (TypeError, ValueError):
                counts.setdefault(tag, 0)
    return sorted(counts, key=lambda tag: (-counts[tag], tag))


def _decode_json_object(raw, label: str) -> dict:
    """Return *raw* as a dict, decoding a JSON string if needed.

    Args:
        raw: A dict, or a JSON string holding one.
        label: Metadata key name, for the log line when it will not decode.

    Returns:
        The dict, or ``{}`` when the value is missing or unusable.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError as exc:
            logger.warning(
                "Metadata key %r is not valid JSON (%s); ignoring.", label, exc
            )
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _coerce_int(value) -> Optional[int]:
    """Return *value* as an int, or ``None`` if it is not one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def describe_adapter(path: str) -> Optional[AdapterInfo]:
    """Describe a ``.safetensors`` adapter from its header alone.

    Args:
        path: Path to the file.

    Returns:
        An :class:`AdapterInfo`, or ``None`` when the header cannot be read at
        all. A readable file with no metadata returns a populated ``kind`` and
        ``tensor_count`` with everything else unset, which is the common case
        for a downloaded adapter.
    """
    header = read_safetensors_header(path)
    if header is None:
        return None

    metadata = header.get("__metadata__")
    if not isinstance(metadata, dict):
        metadata = {}
    tensor_names = [key for key in header if key != "__metadata__"]

    software = _decode_json_object(metadata.get("software"), "software")
    trained_by = None
    if software.get("name"):
        version = software.get("version")
        trained_by = (
            f"{software['name']} {version}".strip()
            if version
            else str(software["name"])
        )

    training = _decode_json_object(metadata.get("training_info"), "training_info")

    base_model = metadata.get("ss_base_model_version") or None
    display_name = metadata.get("ss_output_name") or metadata.get("name") or None

    # `format` is mandatory and says nothing about the model, so a header
    # carrying only that is "no metadata" as far as the shelf is concerned.
    informative = {key for key in metadata if key != "format"}

    return AdapterInfo(
        kind=detect_adapter_kind(tensor_names),
        tensor_count=len(tensor_names),
        base_model=str(base_model) if base_model else None,
        trigger_words=_trigger_words_from_tag_frequency(
            metadata.get("ss_tag_frequency")
        ),
        display_name=str(display_name) if display_name else None,
        training_step=_coerce_int(training.get("step")),
        training_epoch=_coerce_int(training.get("epoch")),
        trained_by=trained_by,
        has_metadata=bool(informative),
    )

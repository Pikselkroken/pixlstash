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
  ``format`` - one measured at 819 MB and 448 tensors with a single metadata
  key. Empty metadata is the normal case, not an edge case.

Which is why :func:`detect_adapter_kind` reads *tensor names* rather than
metadata: it is the only signal present on every file regardless of provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
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

# Longest metadata string kept. Names and base-model declarations are tens of
# characters; this only stops a hostile header filling a column.
_MAX_METADATA_TEXT = 256

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

# What the file is, as opposed to which algorithm an adapter uses.
FILE_ADAPTER = "adapter"
FILE_CHECKPOINT = "checkpoint"
FILE_UNKNOWN = "unknown"

# What PixlStash downloaded for itself: a tagger, a captioner, a scorer, a face
# pack. Never produced by `classify_model_file` - these rows are DECLARED by
# `services/builtin_models.py`, because we chose to download them and therefore
# know what they are without reading a header (half of them are ONNX or `.pt`,
# which the scanner does not even yield). The role goes in `model.kind`, which
# already holds free text, so this vocabulary stays four values wide.
FILE_ENGINE = "engine"

# The two support roles a generation graph loads beside a diffusion model. They
# are not checkpoints and they are not adapters, and until they had names of
# their own the shelf had nowhere to put them: measured against a real shelf,
# every VAE and CLIP fell below `_CHECKPOINT_MIN_PARAMS` and read as `unknown`,
# while every T5/UMT5/Qwen/Gemma text encoder cleared it and read as
# `checkpoint`. That is 131 GB of encoders counted as base models on one
# machine, and it is why "what can I delete" had no answer.
FILE_VAE = "vae"
FILE_TEXT_ENCODER = "text_encoder"

# What a directory NAMES the files inside it, normalised by `_normalise_folder`.
#
# ComfyUI files models by role (`models/vae`, `models/text_encoders`,
# `models/clip`) and the launchers that front it mirror the layout, so the role
# is sitting in the path of nearly every file on a real shelf. That is a better
# signal than anything the header carries here: a VAE and a text encoder are
# ordinary tensor bundles with no marker to find, and their parameter counts
# straddle the checkpoint threshold from both sides.
#
# Only genuinely different words belong here - the normaliser already folds
# spacing and case, so a `text_encoders` entry covers `TextEncoders` too.
#
# `unet` and `diffusion_models` hold a bare diffusion model, which is what a
# Flux or Wan graph loads where an SD graph loads a checkpoint, so they name
# `checkpoint`. A `.safetensors` there usually clears the parameter count
# anyway; a `.gguf` has no count to clear and would otherwise read `unknown`.
#
# Deliberately absent, each for its own reason:
#
# * `loras` / `lora` - an adapter is asserted from tensor markers, which is
#   positive evidence no directory can improve on. Trusting the folder here
#   would actively break the case that already works: an all-in-one checkpoint
#   dropped in a LoRA folder is caught today by its parameter count.
# * `approxvae` - TAESD previews and latent interposers. They live beside real
#   autoencoders and are not one, so treating them as VAEs would offer the wrong
#   file as a companion.
# * `clipvision` - an image encoder. It shares a prefix with `clip` and does a
#   different job, which is exactly the confusion worth not shipping.
_ROLE_FOLDERS: dict[str, str] = {
    "vae": FILE_VAE,
    "vaes": FILE_VAE,
    "clip": FILE_TEXT_ENCODER,
    "textencoder": FILE_TEXT_ENCODER,
    "textencoders": FILE_TEXT_ENCODER,
    "unet": FILE_CHECKPOINT,
    "unets": FILE_CHECKPOINT,
    "diffusionmodels": FILE_CHECKPOINT,
}

# Parameter count above which a marker-free file is a base checkpoint rather
# than an adapter we failed to recognise.
#
# Adapters are low-rank deltas and checkpoints are whole models, so the two
# separate by about an order of magnitude: a rank-32 adapter runs to tens of
# millions of parameters, while SDXL is ~2.6 B and Flux ~12 B. The threshold
# sits well above the largest plausible adapter (a high-rank adapter on a large
# base can reach the low hundreds of millions) and well below the smallest
# plausible checkpoint, so the band between them returns FILE_UNKNOWN rather
# than guessing. The caller resolves that band with the folder's declared kind,
# which is a user-visible and user-correctable prior rather than a heuristic.
#
# Parameter count, not file size: size is confounded in both directions, since
# quantisation shrinks a checkpoint and a high rank inflates an adapter.
_CHECKPOINT_MIN_PARAMS = 1_000_000_000

# Share of the parameters one dtype must hold for `quant` to name it. Below
# this the file is `mixed`, which is a real answer rather than a failure.
_QUANT_MAJORITY = 0.5

QUANT_MIXED = "mixed"

# CLIP's tokenizer vocabulary, which is what tells a CLIP text encoder's token
# table from any other embedding of the same width.
_CLIP_VOCAB = 49408
_CLIP_WIDTHS = {768: "clip_l", 1024: "clip_h", 1280: "clip_g"}

# T5's and UMT5's vocabularies. Only the XXL width is named: it is the one
# every current image and video model loads, and a smaller T5 is not a
# support file anybody has on a shelf.
_T5_VOCABS = {32128: "t5", 256384: "umt5"}
_T5_XXL_WIDTH = 4096


@dataclass(frozen=True)
class AdapterInfo:
    """What a ``.safetensors`` file says about itself.

    Every field except ``kind`` and ``tensor_count`` is optional, because the
    common downloaded file carries no metadata at all. A ``None`` here means
    "the file did not say", never "the value is empty" - the shelf shows those
    differently, since the first is a prompt to fill something in.

    Attributes:
        kind: Adapter algorithm from tensor names, or ``"unknown"``. Only
            meaningful when ``is_adapter`` is true.
        is_adapter: Whether adapter tensor markers were found. This is *proven*
            from names the file cannot strip without breaking, so an
            unrecognised LyCORIS variant reads as "an adapter whose kind we do
            not know" rather than being mistaken for a checkpoint.
        file_kind: What the file is: ``"adapter"``, ``"vae"``,
            ``"text_encoder"``, ``"checkpoint"`` or ``"unknown"``. Never
            guesses checkpoint from the absence of markers alone; see
            :func:`classify_model_file`.
        param_count: Total parameters, summed from the tensor shapes already in
            the header. Exact and free, unlike file size which quantisation and
            rank both confound.
        tensor_count: Number of tensors, excluding the metadata entry.
        base_model: Trainer-reported base model. **Free text** (``zimage``,
            ``krea2``, ``minimax_h3`` seen in the wild), not a closed set.
        trigger_words: Tags recovered from ``ss_tag_frequency``.
        display_name: Trainer-reported name, if any: ``ss_output_name``,
            ``name``, then the SAI ``modelspec.title``.
        architecture: The SAI ``modelspec.architecture`` declaration
            (``stable-diffusion-xl-v1-base/lora``), verbatim. Evidence for
            :func:`~pixlstash.utils.known_base_models.identify`, never shown.
        trained_on: kohya's ``ss_sd_model_name``, the checkpoint the file was
            trained against. A **filename** (``animagineXLV31_v31.safetensors``),
            so it is matched as one.
        training_step: Step the checkpoint was saved at.
        training_epoch: Epoch the checkpoint was saved at.
        trained_by: Producing software, e.g. ``"ai-toolkit 0.9.11"``.
        has_metadata: Whether the file carried a ``__metadata__`` block with
            anything beyond the mandatory ``format`` key.
        family: The architecture the tensors show, for the support files
            whose layout says it (see :func:`family_from_header`), else
            ``None``.
        quant: The dtype holding most of the parameters, or ``"mixed"``.
        weights_id: Digest of the tensor names and shapes, dtype excluded, so
            two clean casts of one model share it.
    """

    kind: str
    tensor_count: int
    is_adapter: bool = False
    file_kind: str = FILE_UNKNOWN
    param_count: int = 0
    base_model: Optional[str] = None
    trigger_words: list[str] = field(default_factory=list)
    display_name: Optional[str] = None
    architecture: Optional[str] = None
    trained_on: Optional[str] = None
    training_step: Optional[int] = None
    training_epoch: Optional[int] = None
    trained_by: Optional[str] = None
    has_metadata: bool = False
    family: Optional[str] = None
    quant: Optional[str] = None
    weights_id: Optional[str] = None


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


def has_adapter_markers(tensor_names) -> bool:
    """Return whether *tensor_names* contain any known adapter marker.

    Separate from :func:`detect_adapter_kind` on purpose. That function answers
    "which algorithm", and returns ``"unknown"`` both for a file with no markers
    at all and for one whose markers we do not recognise. Those are different
    facts and the shelf needs them apart: the first may be a checkpoint, the
    second is definitely an adapter.

    Args:
        tensor_names: Iterable of tensor keys from the header.

    Returns:
        True when at least one tensor name carries an adapter marker.
    """
    for name in tensor_names:
        if isinstance(name, str) and _MARKER_RE.search(name):
            return True
    return False


def count_parameters(header: dict) -> int:
    """Return the total parameter count implied by a safetensors *header*.

    Each tensor entry carries its ``shape``, so the count is exact and costs no
    extra I/O: the header has already been read. Entries whose shape is missing
    or malformed contribute nothing rather than raising, because this runs in
    the import path and a file we cannot measure is still a file the user wants.

    Args:
        header: Parsed safetensors header.

    Returns:
        Sum over tensors of the product of each shape, or 0 when nothing is
        measurable. A scalar tensor (empty shape) counts as one parameter.
    """
    return sum(_shape_size(entry) for _name, entry in _tensor_entries(header))


def _tensor_entries(header: dict):
    """Yield ``(name, entry)`` for every tensor in *header*, metadata skipped."""
    for key, entry in header.items():
        if key != "__metadata__" and isinstance(entry, dict):
            yield key, entry


def _valid_shape(entry) -> Optional[list[int]]:
    """Return the entry's shape when every dimension is a non-negative int.

    *entry* comes from an untrusted header, so anything but a dict is ``None``.
    """
    if not isinstance(entry, dict):
        return None
    shape = entry.get("shape")
    if not isinstance(shape, list):
        return None
    for dim in shape:
        if not isinstance(dim, int) or isinstance(dim, bool) or dim < 0:
            return None
    return shape


def _shape_size(entry: dict) -> int:
    """Parameters in one tensor entry, 0 when its shape is unusable."""
    shape = _valid_shape(entry)
    if shape is None:
        return 0
    count = 1
    for dim in shape:
        count *= dim
    return count


def quant_from_header(header: dict) -> Optional[str]:
    """Return the dtype most of the parameters are stored at.

    **Weighted by parameter count, not by tensor count.** A model with 300 fp32
    norm tensors and 200 fp8 weight tensors is fp8, and counting tensors gets
    that backwards. When no dtype holds a majority the answer is ``"mixed"``.

    The filename is never consulted: ``fp8_e4m3fn_scaled`` and ``nvfp4_awq``
    are real names, and the column code reasons over must come from the header.
    The limit is the header's: an AWQ-style pack stores its weights in ``I32``
    or ``U8`` containers, and that container is what this reports.

    Args:
        header: Parsed safetensors header.

    Returns:
        The dtype lowercased (``f16``, ``bf16``, ``f8_e4m3``, ``i32`` …),
        ``"mixed"``, or ``None`` when no tensor declares a usable dtype and
        shape.
    """
    by_dtype: dict[str, int] = {}
    for _name, entry in _tensor_entries(header):
        dtype = entry.get("dtype")
        if isinstance(dtype, str) and dtype:
            key = dtype.lower()
            by_dtype[key] = by_dtype.get(key, 0) + _shape_size(entry)
    total = sum(by_dtype.values())
    if not total:
        return None
    dtype, count = max(by_dtype.items(), key=lambda item: item[1])
    return dtype if count > total * _QUANT_MAJORITY else QUANT_MIXED


def weights_id_from_header(header: dict) -> Optional[str]:
    """Return a digest that is the same for one model at any precision.

    SHA-256 over the sorted ``(tensor name, shape)`` pairs, **dtype excluded**,
    so two clean casts of one model share it where ``sha256`` differs by
    construction. A repack does not: ``umt5_xxl_fp8_e4m3fn_scaled`` carries
    scale tensors its bf16 twin lacks, so the two get different ids. That is
    correct (they are not interchangeable), and it means this groups some quant
    sets rather than all of them.

    Args:
        header: Parsed safetensors header.

    Returns:
        Lowercase hex, or ``None`` for a header with no tensors.
    """
    pairs = sorted(
        (name, _valid_shape(entry) or []) for name, entry in _tensor_entries(header)
    )
    if not pairs:
        return None
    blob = json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def family_from_header(header: dict) -> Optional[str]:
    """Return the architecture a support file's tensors show, or ``None``.

    For the files that carry no ``base_model`` - every downloaded VAE and text
    encoder, which are the files the question "can I delete this" is about.
    Each rule reads one tensor at the **top level** of the file, never under a
    prefix: a full checkpoint bakes a VAE in as ``first_stage_model.decoder…``
    and its encoders under ``conditioner.embedders…``, and matching those would
    file a whole SDXL checkpoint as a VAE.

    * ``decoder.conv_in.weight`` takes the latent channels in: ``vae_4ch`` is
      the SD 1.5 / SDXL autoencoder, ``vae_16ch`` the FLUX / SD 3.5 one.
    * A CLIP token table (49,408 rows) gives the width: ``clip_l`` (768),
      ``clip_h`` (1024), ``clip_g`` (1280).
    * A T5 or UMT5 token table at width 4096: ``t5_xxl`` / ``umt5_xxl``.

    The vocabulary is architecture, not compatibility, and deliberately not the
    base-model families of :mod:`pixlstash.utils.known_base_models`: a 4-channel
    VAE serves SD 1.5 and SDXL alike, so naming it after either would be a
    guess. Unrecognised layouts (Qwen and Gemma encoders, 3D video VAEs) return
    ``None``, which says nothing rather than something wrong.

    Args:
        header: Parsed safetensors header.

    Returns:
        One of the tokens above, or ``None``.
    """
    conv_in = _valid_shape(header.get("decoder.conv_in.weight"))
    if conv_in is not None and len(conv_in) == 4:
        return f"vae_{conv_in[1]}ch"
    for name in (
        "text_model.embeddings.token_embedding.weight",
        "token_embedding.weight",
    ):
        table = _valid_shape(header.get(name))
        if table is not None and len(table) == 2 and table[0] == _CLIP_VOCAB:
            return _CLIP_WIDTHS.get(table[1])
    for name in ("shared.weight", "encoder.embed_tokens.weight"):
        table = _valid_shape(header.get(name))
        if (
            table is not None
            and len(table) == 2
            and table[0] in _T5_VOCABS
            and table[1] == _T5_XXL_WIDTH
        ):
            return f"{_T5_VOCABS[table[0]]}_xxl"
    return None


def _normalise_folder(name: str) -> str:
    """Fold a directory name to its letters and digits, lowercased.

    ``TextEncoders``, ``text_encoders`` and ``text-encoders`` are one layout
    spelled three ways, so :data:`_ROLE_FOLDERS` carries the word once.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def role_from_folder(path: str) -> Optional[str]:
    """Return the ``file_kind`` the file's own directory names, or ``None``.

    Only the directory the file sits in is consulted, never an ancestor: a
    registered folder may itself be called ``vae`` (someone can register
    ``ComfyUI/models/vae`` directly) and it may equally be a whole models tree
    with ``vae/`` inside it. Both cases put the role one level up from the file,
    and nothing else in the path is evidence about a particular file.

    Args:
        path: Path to the model file. May be relative or absolute.

    Returns:
        ``"vae"``, ``"text_encoder"``, ``"checkpoint"``, or ``None`` when the
        directory names no role we recognise - which is the answer for a flat folder of mixed
        downloads and must never be read as "not a VAE".
    """
    folder = os.path.basename(os.path.dirname(path))
    if not folder:
        return None
    return _ROLE_FOLDERS.get(_normalise_folder(folder))


def classify_model_file(tensor_names, param_count: int, path: str = "") -> str:
    """Return what a file *is*, from its tensor names, parameter count and location.

    The rule is deliberately asymmetric, and the order below is the ranking of
    how much each signal can be trusted:

    1. **Adapter markers**, when present, settle it. They are positive evidence
       the file cannot strip without breaking, so nothing overrides them - not
       a parameter count and not a directory. This is what keeps an all-in-one
       checkpoint dropped in someone's ``Lora/`` folder from being filed as a
       LoRA.
    2. **The directory**, when it names a role. A VAE and a text encoder carry
       no marker to find, so the layout the user (or their launcher) already
       maintains is the best evidence there is. It outranks the parameter count
       because the parameter count is *wrong* for both: they sit either side of
       a threshold that was only ever meant to separate adapters from base
       models. ``unet/`` and ``diffusion_models/`` name ``checkpoint``, which
       is what files a ``.gguf`` (no parameter count at all) as a base model.
    3. **The parameter count**, for a marker-free file in a folder that says
       nothing. A count no adapter reaches asserts checkpoint.

    Everything else is ``"unknown"``, which the shelf shows as unknown and lets
    the user correct. ``unknown`` must never be rendered or stored as
    checkpoint: a marker-free file too small to be a base model, outside a
    folder that says otherwise, is most likely an adapter format we have not
    met yet.

    Args:
        tensor_names: Iterable of tensor keys from the header.
        param_count: Total parameters, from :func:`count_parameters`.
        path: Where the file lives. Optional - omitting it drops rule 2 and
            leaves the pre-existing behaviour, which is what a caller holding
            only a header should get.

    Returns:
        ``"adapter"``, ``"vae"``, ``"text_encoder"``, ``"checkpoint"`` or
        ``"unknown"``.
    """
    if has_adapter_markers(tensor_names):
        return FILE_ADAPTER
    role = role_from_folder(path) if path else None
    if role is not None:
        return role
    if param_count >= _CHECKPOINT_MIN_PARAMS:
        return FILE_CHECKPOINT
    return FILE_UNKNOWN


def _trigger_words_from_tag_frequency(raw) -> list[str]:
    """Pull tag names out of kohya/ai-toolkit's ``ss_tag_frequency``.

    The value is ``{dataset_dir: {tag: count}}``, e.g.
    ``{"1_jimmyvehicle": {"jimmyvehicle": 1}}``. The dataset directory name is an
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


def _metadata_text(metadata: dict, *keys: str) -> Optional[str]:
    """The first of *keys* holding a non-blank value, as bounded text.

    The value comes from an untrusted header, so it is coerced to ``str`` and
    cut at :data:`_MAX_METADATA_TEXT` rather than trusted to be a short string.
    """
    for key in keys:
        value = metadata.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text[:_MAX_METADATA_TEXT]
    return None


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

    base_model = _metadata_text(metadata, "ss_base_model_version")
    display_name = _metadata_text(metadata, "ss_output_name", "name", "modelspec.title")

    # `format` is mandatory and says nothing about the model, so a header
    # carrying only that is "no metadata" as far as the shelf is concerned.
    informative = {key for key in metadata if key != "format"}

    param_count = count_parameters(header)

    return AdapterInfo(
        kind=detect_adapter_kind(tensor_names),
        tensor_count=len(tensor_names),
        is_adapter=has_adapter_markers(tensor_names),
        file_kind=classify_model_file(tensor_names, param_count, path),
        param_count=param_count,
        base_model=base_model,
        trigger_words=_trigger_words_from_tag_frequency(
            metadata.get("ss_tag_frequency")
        ),
        display_name=display_name,
        architecture=_metadata_text(metadata, "modelspec.architecture"),
        trained_on=_metadata_text(metadata, "ss_sd_model_name"),
        training_step=_coerce_int(training.get("step")),
        training_epoch=_coerce_int(training.get("epoch")),
        trained_by=trained_by,
        has_metadata=bool(informative),
        family=family_from_header(header),
        quant=quant_from_header(header),
        weights_id=weights_id_from_header(header),
    )

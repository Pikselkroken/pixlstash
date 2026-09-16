"""A1111 generation data as a recipe: the same three keys a ComfyUI graph gets.

A picture from Stable Diffusion web UI (A1111) or one of its forks (Forge,
reForge, SD.Next) carries a text, not a graph -- a PNG's ``parameters`` chunk,
or a JPEG's or WebP's EXIF ``UserComment``::

    a castle on a hill <lora:example-style:0.8>
    Negative prompt: blurry
    Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 123, Size: 512x768,
    Model hash: 0123456789, Model: sd_xl_base_1.0, Version: v1.10.1

So this module builds the graph that text describes and hands it to
:mod:`pixlstash.services.workflow_hash` already reduced, which is what puts an
A1111 picture on the same topology, recipe and instance tables as a ComfyUI one.
It builds :class:`ReducedNode` directly rather than an API graph because every
field is classified HERE, by name, into the four buckets the hash spec uses:

* **structure** - what shapes the graph: a LoRA or embedding in use, hires fix,
  a refiner, an img2img source. Each is a node.
* **asset** - which file: ``Model``, ``VAE``, ``Refiner``, LoRA and embedding
  names, any field named for a model (``Hires upscaler``, ``ADetailer model``),
  and the checkpoint and embedding hashes as ``*_sha256`` widgets.
* **parameter** - everything else, the prompts included, **and every field this
  module has never heard of**. Misreading a parameter as structure shatters
  recipes, which the spec calls unrecoverable; the opposite over-groups, which a
  later ``hash_version`` can split.
* **volatile** - the seeds and the web UI version, which vary between re-rolls
  of one instance, and the hash summaries that repeat what the asset widgets
  already say. A credential-named field is dropped outright, as in a graph.

**Known ceiling:** a model named inside a compound value (ControlNet's
``"Module: canny, Model: control_x [hash], Weight: 1"``) stays in that value,
so it reaches the instance document and forgetting the model does not.

**A short hash is stored as it is and resolved when read**
(:func:`pixlstash.services.workflow_hash.digests_with_prefix`). A1111's
``Model hash`` is the first 10 hex digits of the file's sha256, so it names a
shelf model when exactly one digest starts with it and is only a filename match,
the unverified tier, when none or several do. Resolving at scan time instead
would give two pictures of one model different recipes depending on whether the
model was on the shelf yet. The 8-digit hash of 2022 builds is not a sha256
prefix at all and is dropped, leaving the name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import (
    DIGEST_PREFIX_RE,
    MODEL_EXTENSIONS,
    SECRET_FIELD_RE,
    ReducedNode,
    normalized_filename,
    structural_widget_value,
)

logger = get_logger(__name__)

# A1111's own infotext grammar (modules/infotext_utils.py): ``Key: value`` pairs
# separated by commas, a value optionally a JSON-quoted string.
_PARAM_RE = re.compile(r'\s*(\w[\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')

# ``<lora:name:weight>``; ``lyco`` is the LyCORIS extension's spelling of it.
_EXTRA_NETWORK_RE = re.compile(r"<(lora|lyco):([^:>]+)((?::[^>]*)?)>", re.IGNORECASE)

# `name [hash]`, how A1111 writes a refiner or a hires checkpoint.
_NAME_WITH_HASH_RE = re.compile(r"^(.*?)\s*\[([0-9a-fA-F]+)\]$")

# A field that names a model file: ``Hires upscaler``, ADetailer's ``ADetailer
# model 2nd``, Forge's ``Module 1``, ``AddNet Model 1``. Its value is an asset, so
# the name is kept where forgetting a model reaches it rather than in the
# instance document.
_MODEL_FIELD_RE = re.compile(
    r"(^|\s)(model|checkpoint|upscaler|vae|module|lora)(\s+(\d+|\d*(st|nd|rd|th)))?$",
    re.IGNORECASE,
)

# The regex above is quadratic on a long line with no pairs in it: measured,
# 19,000 characters costs 0.82 s and 10,000 costs 0.22 s, against 0.02 s for a
# realistic ADetailer-heavy 3,000. Migration 0119 re-opens every picture that
# has no keys, so the ceiling is set where a real line never reaches it.
# ponytail: a longer genuine line is read as no A1111 data; raise if one shows.
_MAX_FIELDS_LINE = 8_000

_VOLATILE_FIELDS = frozenset(
    {
        "Seed",
        "Variation seed",
        "Seed resize from",
        "Version",
        # Summaries of names and hashes the asset nodes already carry. The
        # embedding entries are read into those nodes, not kept here.
        "Hashes",
        "TI hashes",
        # Not a sha256 prefix: for a .safetensors LoRA, A1111 hashes the tensor
        # data after the header (Civitai's AutoV3), so it could never resolve
        # and every LoRA on the shelf would read as a model ghost.
        "Lora hashes",
    }
)

# Asset fields of the checkpoint node, and the fields that carry their hashes.
_CHECKPOINT_ASSETS = {"Model": "ckpt_name", "VAE": "vae_name"}
_CHECKPOINT_HASHES = {"Model hash": "ckpt_sha256", "VAE hash": "vae_sha256"}


@dataclass(frozen=True)
class A1111Recipe:
    """One picture's A1111 generation data, reduced.

    Attributes:
        nodes: The graph, ready for ``graph_key`` and the hub writer.
        seed: The ``Seed`` field as text, or ``None`` when it is absent or not
            an integer. Text for the reason ``generation.seed`` is.
    """

    nodes: dict[str, ReducedNode]
    seed: Optional[str]


def find_a1111_parameters(metadata: Optional[dict]) -> Optional[str]:
    """The picture's A1111 generation text, or ``None`` if it has none.

    A PNG's ``parameters`` chunk, else a JPEG's or WebP's EXIF ``UserComment``,
    which is where A1111 puts the same text when the format has no text chunk.
    """
    metadata = metadata or {}
    for section, key in (("png", "parameters"), ("exif", "UserComment")):
        value = (metadata.get(section) or {}).get(key)
        if isinstance(value, str):
            return value
    return None


def parse_infotext(text: str) -> Optional[tuple[str, str, dict[str, str]]]:
    """Split infotext into ``(prompt, negative_prompt, fields)``.

    Returns ``None`` for text that is not A1111's: some line must hold a
    ``Steps`` field, which every A1111 generation writes. Fooocus also uses a
    ``parameters`` chunk, for JSON, and that is not matched.

    **The fields line is the LAST one that parses**, not simply the last line.
    A1111 escapes newlines so its own fields line always is the last, but a tool
    that re-saves an image and appends a line of its own would otherwise cost
    the picture its whole recipe -- and silently, since the scan marks it read.
    """
    if text.lstrip().startswith("{"):
        return None
    lines = text.strip().split("\n")
    for index in range(len(lines) - 1, -1, -1):
        fields = _parse_fields(lines[index])
        if fields is None:
            continue
        prompt: list[str] = []
        negative: list[str] = []
        target = prompt
        for line in lines[:index]:
            if line.startswith("Negative prompt:"):
                target = negative
                line = line[len("Negative prompt:") :]
            target.append(line.strip())
        return "\n".join(prompt).strip(), "\n".join(negative).strip(), fields
    return None


def _parse_fields(line: str) -> Optional[dict[str, str]]:
    """One line as A1111's ``Key: value`` pairs, or ``None`` if it is not that.

    **First occurrence wins.** A1111 quotes any value holding a comma, so a
    repeated key means something else wrote the text and an unquoted compound
    value (a ControlNet setup) has been shredded into pairs of its own. A1111
    writes ``Model`` before anything an extension adds, so keeping the first is
    what stops such a value replacing the checkpoint -- which would put a
    filename and a digest belonging to two different models in one recipe.
    """
    if "Steps:" not in line:
        return None
    if len(line) > _MAX_FIELDS_LINE:
        logger.info(
            "Skipped A1111 data whose fields line is %d characters long.", len(line)
        )
        return None
    fields: dict[str, str] = {}
    for key, value in _PARAM_RE.findall(line):
        if len(value) > 1 and value[0] == '"' and value[-1] == '"':
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                logger.debug("Kept A1111 field %r quoted: %s", key, exc)
        fields.setdefault(key, value.strip() if isinstance(value, str) else str(value))
    return fields if "Steps" in fields else None


def reduce_a1111(metadata: Optional[dict]) -> Optional[A1111Recipe]:
    """The picture's A1111 generation data as a reduced graph, or ``None``."""
    text = find_a1111_parameters(metadata)
    parsed = parse_infotext(text) if text else None
    if parsed is None:
        return None
    prompt, negative, fields = parsed

    nodes: dict[str, ReducedNode] = {}
    checkpoint: dict[str, Any] = {}
    for field, widget in _CHECKPOINT_ASSETS.items():
        if fields.get(field):
            checkpoint[widget] = _asset_name(fields[field])
    for field, widget in _CHECKPOINT_HASHES.items():
        checkpoint[widget] = _short_hash(fields.get(field))
    nodes["checkpoint"] = _node("A1111Checkpoint", checkpoint, checkpoint, {})
    model = "checkpoint"

    for field in [f for f in fields if SECRET_FIELD_RE.search(_widget_name(f))]:
        # The same defense in depth a ComfyUI widget gets: the instance document
        # is kept, so a credential-named field never reaches it. Debug, because
        # A1111's own `Token merging ratio` matches on every such picture. It is
        # then absent from the instance too, so two pictures that differ only in
        # it share one -- the recoverable direction, and the same trade the
        # ComfyUI rule makes.
        logger.debug(
            "Dropping A1111 field %r: it matches the credential pattern.", field
        )
        del fields[field]

    # A LoRA tag can sit in any prompt, the hires and ADetailer ones included, so
    # every value is searched and stripped. LoRAs chain in name order, so the
    # order they were typed in is not structure.
    strengths: dict[str, list[str]] = {}
    for value in [prompt, negative, *fields.values()]:
        for _, name, weight in _EXTRA_NETWORK_RE.findall(value):
            asset = _asset_name(name)
            if asset:
                strengths.setdefault(asset, []).append(weight.lstrip(":"))
    prompt = _EXTRA_NETWORK_RE.sub("", prompt).strip()
    negative = _EXTRA_NETWORK_RE.sub("", negative).strip()
    fields = {k: _EXTRA_NETWORK_RE.sub("", v).strip() for k, v in fields.items()}
    for index, name in enumerate(sorted(strengths)):
        node_id = f"lora_{index}"
        nodes[node_id] = _node(
            "A1111Lora",
            {"lora_name": name, "strength": ",".join(sorted(strengths[name]))},
            {"lora_name": name},
            {"model": model},
        )
        model = node_id

    for name in ("positive", "negative"):
        nodes[name] = _node(
            "A1111Prompt",
            {"text": prompt if name == "positive" else negative},
            {},
            {"clip": model},
        )
    for index, (name, digest) in enumerate(
        sorted(_name_hash_list(fields.get("TI hashes")).items())
    ):
        assets = {"embedding_name": name, "embedding_sha256": _short_hash(digest)}
        nodes[f"embedding_{index}"] = _node(
            "A1111Embedding", assets, assets, {"positive": "positive"}
        )

    # `First pass size` is how hires fix was written before its fields were named.
    hires = {
        k: v
        for k, v in fields.items()
        if k.startswith("Hires ") or k == "First pass size"
    }
    sampler_inputs = {"model": model, "positive": "positive", "negative": "negative"}
    if "Denoising strength" in fields and not hires:
        # img2img: the source picture is not in the infotext, only that one was used.
        nodes["source"] = _node("A1111SourceImage", {}, {}, {})
        sampler_inputs["latent_image"] = "source"
    if fields.get("Refiner"):
        nodes["refiner"] = _node(
            "A1111Refiner", *_checkpoint_widgets(fields["Refiner"]), {}
        )
        sampler_inputs["refiner"] = "refiner"

    consumed = set(_CHECKPOINT_ASSETS) | set(_CHECKPOINT_HASHES) | {"Refiner"}
    sampler = _widgets(
        {k: v for k, v in fields.items() if k not in hires and k not in consumed}
    )
    nodes["sampler"] = _node("A1111Sampler", *sampler, sampler_inputs)
    if hires:
        widgets, assets = _widgets(
            {k: v for k, v in hires.items() if k != "Hires checkpoint"}
        )
        if hires.get("Hires checkpoint"):
            ckpt, ckpt_assets = _checkpoint_widgets(hires["Hires checkpoint"])
            widgets.update(ckpt)
            assets.update(ckpt_assets)
        nodes["hires"] = _node("A1111HiresFix", widgets, assets, {"samples": "sampler"})

    seed = fields.get("Seed", "").strip()
    return A1111Recipe(nodes=nodes, seed=seed if seed.isdigit() else None)


def _widgets(fields: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fields as ``(widgets, assets)``: parameters, and the model names among them."""
    widgets: dict[str, Any] = {}
    assets: dict[str, Any] = {}
    for field, value in fields.items():
        if field in _VOLATILE_FIELDS:
            continue
        widget = _widget_name(field)
        widgets[widget] = value
        if value and _MODEL_FIELD_RE.search(field):
            match = _NAME_WITH_HASH_RE.match(value)
            assets[widget] = widgets[widget] = match.group(1) if match else value
        # And by the rule a ComfyUI widget of unknown meaning gets: a value
        # with a model extension (ADetailer's `face_yolov8n.pt`).
        elif structural_widget_value(widget, value) is not None:
            assets[widget] = value
    return widgets, assets


def _checkpoint_widgets(value: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """``name [hash]`` as a checkpoint's name and hash widgets, both assets."""
    match = _NAME_WITH_HASH_RE.match(value)
    name, digest = match.groups() if match else (value, None)
    assets = {"ckpt_name": _asset_name(name), "ckpt_sha256": _short_hash(digest)}
    return assets, assets


def _node(
    class_type: str,
    widgets: dict[str, Any],
    assets: dict[str, Any],
    inputs: dict[str, str],
) -> ReducedNode:
    """A reduced node whose buckets were decided by the caller.

    ``assets`` names the widgets that are assets; every other widget is a
    parameter, and ``None`` is volatile (absent from the instance). An asset
    value of ``None`` (no hash written) is left out entirely, so a picture
    whose hash is missing keys like one from a build that never wrote it.

    **A parameter is not even named in the recipe**, unlike a ComfyUI widget.
    A1111 leaves a field out when it holds its default (``Clip skip: 2``
    appears, ``Clip skip: 1`` does not), so a field's presence is a value and
    keying on it would fork the recipe on a parameter.
    """
    kept = {k: v for k, v in widgets.items() if not (k in assets and v is None)}
    return ReducedNode(
        class_type=class_type,
        widgets=tuple(
            sorted((k, normalized_filename(v)) for k, v in kept.items() if k in assets)
        ),
        inputs=tuple(sorted((name, source, 0) for name, source in inputs.items())),
        instance_widgets=tuple(sorted(kept.items(), key=lambda kv: kv[0])),
    )


def _asset_name(name: str) -> Optional[str]:
    """A model name as a filename the shelf would record.

    A1111 names a checkpoint and a LoRA without its extension. ``.safetensors``
    is assumed, because it is the only kind the shelf holds, so a match against
    a shelf model's filename and the privacy tools that forget a model's name
    both work on it.
    """
    # ponytail: a .ckpt or .pt model is recorded as .safetensors too. It reads as
    # a model ghost (the shelf cannot hold it anyway), and a shelf file of the
    # same stem counts it in the unverified by-filename tier.
    name = normalized_filename(name.strip())
    if not name:
        # `Refiner: [abcdef0123]`, a hash with no name. Appending the extension
        # would invent an asset called `.safetensors`, which then reads as a
        # model ghost and is offered for forgetting.
        return None
    return name if name.endswith(MODEL_EXTENSIONS) else name + ".safetensors"


def _short_hash(value: Optional[str]) -> Optional[str]:
    """A hash worth keeping as a sha256 prefix, lowercased, else ``None``."""
    value = (value or "").strip().lower()
    return value if DIGEST_PREFIX_RE.match(value) else None


def _name_hash_list(value: Optional[str]) -> dict[str, str]:
    """``"a: 0123abcd, b: 4567ef01"`` as ``{"a.safetensors": "0123abcd", ...}``."""
    pairs: dict[str, str] = {}
    for entry in (value or "").split(","):
        name, _, digest = entry.rpartition(":")
        asset = _asset_name(name) if name.strip() else None
        if asset:
            pairs[asset] = digest.strip()
    return pairs


def _widget_name(field: str) -> str:
    """``CFG scale`` as ``cfg_scale``."""
    return re.sub(r"[^0-9a-z]+", "_", field.lower()).strip("_")

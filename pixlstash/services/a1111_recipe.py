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

**A model named inside a compound value is an asset too** (#1375). ControlNet
writes its whole setup into one field (``"Module: canny, Model: control_x
[hash], Weight: 1"``), where the field is not named for a model and the name
inside carries no extension, so neither rule above sees it. Its ``Model:``
sub-fields are taken out of the value into asset widgets of their own, which is
what keeps the name out of the instance document -- forgetting a model is a row
delete and never rewrites a stored document. Only the fields
:data:`_COMPOUND_MODEL_FIELD_RE` names are read this way; any other compound
keeps its name, which is the old behaviour and the safe direction --
``X Values`` on a checkpoint-name sweep and Tiled Diffusion's ``Upscaler`` key
are the two that matter, the second having ControlNet's shape but values
(``Latent``, ``None``) that are as often not files. A name holding a comma is
truncated at it, because A1111's grammar is comma-separated and the extension
has written a value its own re-import misparses too.

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
    MAX_FILENAME_LENGTH,
    MODEL_EXTENSIONS,
    SECRET_FIELD_RE,
    ReducedNode,
    normalized_filename,
    structural_widget_value,
)

logger = get_logger(__name__)

# A1111's own infotext grammar (modules/infotext_utils.py): ``Key: value`` pairs
# separated by commas, a value optionally a JSON-quoted string.
#
# **The key's run is bounded, and that is what makes this linear.** An
# unbounded ``[\w \-/]+`` before the literal ``:`` backtracks over every
# length of every run that has no colon after it, which is quadratic in the
# line: measured on this module's own ceiling, 8,000 characters of
# ``"xSteps: 20," + "a" * 7989`` cost 0.14 s and 32,000 cost 2.34 s. Bounded at
# 63, the same inputs cost 0.0024 s and 0.011 s - linear, and no longer a
# multiplier on anything. A1111's longest real key is around 19 characters
# (``Denoising strength``, ``ADetailer model 2nd``), so nothing genuine is near
# the bound; a key longer than it reads as prose rather than as a field, which
# is the right answer for a line that is not A1111's.
_PARAM_RE = re.compile(r'\s*(\w[\w \-/]{0,62}):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')

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

# The fields whose value is a compound written by an extension, one `Key:
# value` list inside one field, with a model named in the middle of it:
# ``ControlNet 0: "Module: canny, Model: control_x [hash], Weight: 1"``.
#
# **A NAMED SET, never "any field that is not prose".** A1111 fields hold
# prose in more places than `carries_prose` knows about -- the X/Y/Z plot
# script writes prompt fragments into `X Values`, sd-dynamic-prompts writes
# the raw template into `Template` -- and reading `model: someone` in one of
# those as a filename would file a person's words as a `workflow_recipe_asset`
# row, which is hub-wide, permanent, and offered on the ghost screen as a
# model. That is a worse leak than the one this closes, and in the direction
# the spec calls unrecoverable, so a field earns its place here by being known
# to write a model into a compound.
_COMPOUND_MODEL_FIELD_RE = re.compile(r"^controlnet\b", re.IGNORECASE)

# Which of `_MODEL_FIELD_RE`'s keywords name a FILE, and so take the filename
# spelling every other asset here gets. `upscaler` and `module` are left out:
# `Hires upscaler: Latent` and ControlNet's `Module: canny` name a method, and
# `latent.safetensors` would be a model ghost for a model that never existed.
_MODEL_FILE_KEYWORDS = frozenset({"model", "checkpoint", "vae", "lora"})

# A ``Model:`` sub-field inside such a value, at a comma boundary so a key of
# another name (``Model hash:``, ``Base Model:``) is left where it is.
_NESTED_MODEL_RE = re.compile(r"(?:^|,)\s*model:\s*([^,]*)", re.IGNORECASE)

# What an extension writes for "no model chosen". Naming it an asset would file
# a row called `none.safetensors` and offer the owner a ghost to forget.
_NO_MODEL = frozenset({"", "none"})

# A ceiling on the line handed to the regex. It was set when that regex was
# quadratic, on an extrapolation that undercounted (8,000 characters measured
# 0.14 s here, not the ~0.04 s the old comment's datapoints suggest); the
# bounded key run above has since made the cost linear, so this is now a bound
# on absurdity rather than the thing holding the line. Migration 0119 re-opens
# every picture that has no keys, so it is set where a real line never reaches
# it.
# ponytail: a longer genuine line is read as no A1111 data; raise if one shows.
_MAX_FIELDS_LINE = 8_000

# How many lines from the end are searched for that fields line. Belt and
# braces, not the cost control it was: with a linear regex the whole search is
# linear in the text. It stays because a future change to the grammar above
# should not be able to reintroduce a multiplier silently. See `parse_infotext`
# for why the tail is where the fields line is.
_MAX_FIELDS_LINES = 32

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
            an integer. Text for the reason ``generation.seed`` is - but an
            integer ``int()`` itself accepts, which ``str.isdigit()`` alone
            does not promise: it is true of ``"\u00b2"`` and of a 4,301-digit
            run, both of which ``int()`` refuses (CPython's integer-string
            limit). A caller converting the text would have turned a crafted
            ``parameters`` chunk into a 500.
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
    # Only the tail is searched. The grammar loses nothing by it: A1111 escapes
    # newlines, so its fields line IS the last, and this backwards search
    # exists only for a tool that re-saved the file and appended a few of its
    # own. It is a second bound rather than the cost control it was - the
    # regex above is linear now - kept so that a change to the grammar cannot
    # quietly turn a line count back into a multiplier. This module gained a
    # request-path caller in v1.12 B5 (`GET /comfyui/pictures/{id}/recipe`,
    # reachable with a share token), which is what makes that worth a bound at
    # all; it had only a background pass before.
    # ponytail: a genuine fields line further back than this reads as no A1111
    # data; raise the bound if one shows up.
    for index in range(len(lines) - 1, max(len(lines) - _MAX_FIELDS_LINES, 0) - 1, -1):
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

    return A1111Recipe(nodes=nodes, seed=_integer_text(fields.get("Seed")))


def _integer_text(value: Optional[str]) -> Optional[str]:
    """*value* if it is an integer ``int()`` accepts, else ``None``.

    ``str.isdigit()`` is not that test. It is true of ``"\u00b2"`` and of a
    digit run past CPython's 4,300-character integer-string limit, and both
    raise in ``int()`` - so a consumer that converts the text got a 500 out of
    a crafted ``parameters`` chunk. Asking ``int()`` itself is the only way to
    promise what the field's docstring promises.
    """
    text = (value or "").strip()
    try:
        int(text)
    except ValueError:
        if text:
            logger.debug("Dropped A1111 seed %r: it is not an integer.", text[:32])
        return None
    return text


def _widgets(fields: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fields as ``(widgets, assets)``: parameters, and the model names among them."""
    widgets: dict[str, Any] = {}
    assets: dict[str, Any] = {}
    for field, value in fields.items():
        if field in _VOLATILE_FIELDS:
            continue
        widget = _widget_name(field)
        widgets[widget] = value
        if value and (model_field := _MODEL_FIELD_RE.search(field)):
            widgets[widget] = name = _named_model(value)
            if name.lower() in _NO_MODEL:
                # `ControlNet Model: None`. A model called `none` is a row on
                # the ghost screen offering to forget a name of nothing.
                continue
            # **One model, one spelling.** `_asset_name` is how a checkpoint, a
            # LoRA, a refiner, an embedding and a model lifted out of a
            # compound are all written, and this branch was the only holdout:
            # `ControlNet Model: x` filed `x` where `ControlNet 0: "... Model:
            # x ..."` files `x.safetensors`, two rows for one file, so
            # forgetting one missed the other. Only the keywords that name a
            # FILE are normalized: `upscaler` and ControlNet's `module` name a
            # method as often as a file (`Latent`, `canny`), and giving those
            # an extension would invent a model ghost for something that was
            # never a model.
            if model_field.group(2).lower() in _MODEL_FILE_KEYWORDS:
                name = _asset_name(name) or name
            assets[widget] = widgets[widget] = name
        # And by the rule a ComfyUI widget of unknown meaning gets: a value
        # with a model extension (ADetailer's `face_yolov8n.pt`).
        elif structural_widget_value(widget, value) is not None:
            assets[widget] = value
        elif _COMPOUND_MODEL_FIELD_RE.match(field):
            # A compound value names its model inside itself (#1375).
            widgets[widget], nested = _nested_model_names(value)
            for index, name in enumerate(nested):
                # `controlnet_0_model`, then `_model_2`: a second model in one
                # value is forgotten on its own row, not folded into the first.
                key = f"{widget}_model" + (f"_{index + 1}" if index else "")
                assets[key] = widgets[key] = name
    return widgets, assets


def _named_model(value: str) -> str:
    """``name [hash]`` as the name; anything else unchanged."""
    match = _NAME_WITH_HASH_RE.match(value)
    return (match.group(1) if match else value).strip()


def _nested_model_names(value: str) -> tuple[str, list[str]]:
    """A compound value without its ``Model:`` sub-fields, and the names they held.

    Called for :data:`_COMPOUND_MODEL_FIELD_RE` fields only.
    ``"Module: canny, Model: control_x [d14c016b], Weight: 1"`` becomes
    ``("Module: canny, Weight: 1", ["control_x.safetensors"])``. The caller
    files each name as an asset, so the stored document names it by reference
    like any other model and forgetting the model -- a row delete in
    ``workflow_recipe_asset``, with no stored graph rewritten -- reaches it.
    Leaving it in the value is what made that partly untrue (#1375).
    """
    names: list[str] = []

    def take(match: re.Match) -> str:
        name = _named_model(match.group(1).strip())
        # BOTH halves of the backstop `structural_widget_value` puts under a
        # widget of unknown meaning: a filename is one path component, so it
        # holds no newline and no more than 255 bytes. The newline half is not
        # theoretical here -- A1111 escapes a real newline into its one-line
        # infotext and `_parse_fields` unescapes it through `json.loads`, so a
        # compound can carry one, and prose is exactly what it would file as a
        # model name. Measured on the name, not on the whole sub-field: the
        # `[hash]` suffix is not part of it.
        refused = (
            name.lower() in _NO_MODEL or "\n" in name or len(name) > MAX_FILENAME_LENGTH
        )
        asset = None if refused else _asset_name(name)
        if asset is None:
            # One refusal, not two: `Model: None`, `Model: [d14c016b]` (a hash
            # with no name, as for `Refiner`) and a name no filename could be
            # all leave the sub-field exactly where it was, which is the old
            # behaviour rather than an invented asset.
            logger.debug(
                "Left a `Model:` sub-field where it was: %r names no model file.",
                name[:64],
            )
            return match.group(0)
        names.append(asset)
        return ""

    # A value that named no model is returned untouched: the strip below is for
    # the comma a removed sub-field leaves behind, not a rule about values.
    stripped = _NESTED_MODEL_RE.sub(take, value)
    return (stripped.strip(" ,") if names else value), names


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

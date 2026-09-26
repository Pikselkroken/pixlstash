"""Recipe replay: pre-flighting an embedded ComfyUI prompt graph (Remix v1.9).

"Recipe mode" replays the API-format ``prompt`` chunk a generated image carries -
the graph the ComfyUI server actually executed - against the user's *current*
ComfyUI. That install may have moved on: a custom node pack uninstalled, a
checkpoint renamed, a LoRA deleted. Submitting blind produces an opaque 400 from
``POST /prompt``; pre-flighting against ``GET /object_info`` lets us say which
node class or which model file is missing before the user waits.

Two rules govern everything here:

- **Report honestly, never guess.** A check we cannot make (ComfyUI unreachable,
  a widget whose options ComfyUI does not enumerate) is reported as *unchecked*,
  not as *passing* and not as *missing*. A spurious "missing model" is worse than
  no check at all, because it blocks a run that would have worked.
- **Pre-flight is advisory, not authoritative.** ``POST /prompt``'s structured
  ``node_errors`` remains the backstop; ComfyUI is the only thing that truly
  knows whether a graph will validate.
"""

from __future__ import annotations

import math
import random
import re
from copy import deepcopy
from typing import Any, Optional

import requests

from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import MODEL_EXTENSIONS, is_link

logger = get_logger(__name__)

OBJECT_INFO_TIMEOUT_S = 15.0

# ComfyUI's own seed ceiling for the core sampler nodes. Note this is 64-bit,
# NOT the 32-bit limit the t2i endpoint validates against: the shipped
# Flux2-Klein-Image-Edit template ships noise_seed 432262096973502, which a
# 32-bit check would reject as invalid on our own built-in.
MAX_SEED_64 = 2**64 - 1

# Depth guard for following a seed link through passthrough primitives.
_MAX_SEED_LINK_DEPTH = 4

# Classes that merely *carry* an int and hand it to a real consumer. They are
# never scanned directly (see detect_seed_targets) because their
# control_after_generate flag is unconditional and would otherwise make us
# randomize width/height primitives.
SEED_PASSTHROUGH_CLASSES = frozenset({"PrimitiveInt", "SeedNode", "Seed"})

# Nodes that end a graph with an image PixlStash can end up owning: either a
# written file it collects from ComfyUI's history, or a ComfyUI-PixlStash saver
# that uploads into the vault itself. A graph with none of these produces no
# importable output no matter how long it runs.
SAVE_IMAGE_CLASSES = frozenset(
    {"SaveImage", "SaveImageWebsocket", "PixlStashPictureSaver"}
)

# Fields naming a file in ComfyUI's *input* directory rather than a model.
# Kept separate from MODEL_FILENAME_FIELDS: ComfyUI validates these by file
# existence (their VALIDATE_INPUTS suppresses combo checking entirely), the fix
# is a re-upload rather than a download, and calling one a "missing model"
# sends the user hunting for something to install.
INPUT_IMAGE_FIELDS: dict[str, tuple[str, ...]] = {
    "LoadImage": ("image",),
    "LoadImageMask": ("image",),
    "LoadImageOutput": ("image",),
}

# The input a core LoRA loader names its file in, and the ones a
# ComfyUI-PixlStash loader names it by digest in. Together they are the whole
# rule for detect_lora_targets - see its docstring for why this is a field
# name and not a class list. The numbered form is how a stacker spells its
# second and third slot (`lora_name_2`), and each of those is a slot of its own.
LORA_FILENAME_FIELD_RE = re.compile(r"^lora_name(_\d+)?$")
LORA_DIGEST_FIELDS = ("adapter_sha256", "lora_sha256")
# The same names as a PATTERN, for the different question "is this widget a
# LoRA slot at all?" - which `workflow_identity.is_lora_widget` asks of a
# widget it already has, and which has to cover the numbered spelling because
# `workflow_hash.SHA256_FIELD_RE` keys one as an asset. A digest slot missed
# here lands in the card key with no mark check, so swapping a character LoRA
# forks the workflow into a new card - the error `guess_mark`'s
# precision-beats-recall rule exists to prevent.
#
# Deliberately NOT used by `detect_lora_targets` above, which wants ONE digest
# slot per node whatever the pack spelled it, and says so.
LORA_DIGEST_FIELD_RE = re.compile(r"^(adapter|lora)_sha256(_\d+)?$")

# How hard a LoRA slot is applied, reported beside it. ``strength`` is the
# model-only loaders' single widget, so it fills ``model`` when the two-widget
# spelling is absent rather than becoming a third key nobody reads.
_LORA_STRENGTH_FIELDS = (("model", "strength_model"), ("clip", "strength_clip"))

# The ComfyUI-PixlStash loader: LoraLoader's signature with the file named by
# digest, so it can go where ComfyUI does not have the file by name (#1376).
PIXLSTASH_ADAPTER_LOADER = "PixlStashAdapterLoader"

# Loader input fields that hold a model FILE NAME, keyed by the node's own
# `class_type`. Checks are filename-level only: we compare the graph's value
# against the combo list ComfyUI advertises for that field. Anything not listed
# here is simply not checked - this map is deliberately conservative, because a
# false "missing" is worse than a missed check.
MODEL_FILENAME_FIELDS: dict[str, tuple[str, ...]] = {
    "CheckpointLoaderSimple": ("ckpt_name",),
    "CheckpointLoader": ("ckpt_name", "config_name"),
    "CheckpointLoaderNF4": ("ckpt_name",),
    "UNETLoader": ("unet_name",),
    "UnetLoaderGGUF": ("unet_name",),
    "UNETLoaderGGUF": ("unet_name",),
    "DiffusersLoader": ("model_path",),
    "LoraLoader": ("lora_name",),
    "LoRALoader": ("lora_name",),
    "LoraLoaderModelOnly": ("lora_name",),
    "LoRALoaderModelOnly": ("lora_name",),
    "LoraLoaderGGUF": ("lora_name",),
    "VAELoader": ("vae_name",),
    "CLIPLoader": ("clip_name",),
    "DualCLIPLoader": ("clip_name1", "clip_name2"),
    "TripleCLIPLoader": ("clip_name1", "clip_name2", "clip_name3"),
    "CLIPVisionLoader": ("clip_name",),
    "ControlNetLoader": ("control_net_name",),
    "DiffControlNetLoader": ("control_net_name",),
    "StyleModelLoader": ("style_model_name",),
    "GLIGENLoader": ("gligen_name",),
    "UpscaleModelLoader": ("model_name",),
    "HypernetworkLoader": ("hypernetwork_name",),
    "PhotoMakerLoader": ("photomaker_model_name",),
    # The GGUF pack's CLIP loader, sibling of `UnetLoaderGGUF` above.
    "CLIPLoaderGGUF": ("clip_name",),
}

# ComfyUI-MultiGPU wraps a loader to add device placement and names the wrapper
# after it: `UNETLoaderDisTorch2MultiGPU` is `UNETLoader` plus placement inputs.
# Every suffixed class whose base is in the map above keeps the base's field
# names (23 of 23 on a live install, #1440), so the base's fields are used. A
# suffix on a base the map does not know resolves to nothing: the rule extends
# the map and can never invent a field. Longest suffix first, so the earliest
# match strips the whole of it.
_MULTIGPU_SUFFIX_RE = re.compile(r"(?:DisTorch2MultiGPU|DisTorchMultiGPU|MultiGPU)$")


def model_filename_fields(class_type: str) -> tuple[str, ...]:
    """The fields of *class_type* that hold a model file name, or ``()``.

    :data:`MODEL_FILENAME_FIELDS`, plus a ComfyUI-MultiGPU wrapper resolved to
    the loader it wraps. Every reader of the map goes through this, so the
    pre-flight, the parameters and a card's model chips agree on which loaders
    they can read.
    """
    fields = MODEL_FILENAME_FIELDS.get(class_type)
    if fields is not None:
        return fields
    base = _MULTIGPU_SUFFIX_RE.sub("", class_type)
    if base and base != class_type:
        return MODEL_FILENAME_FIELDS.get(base, ())
    return ()


def fetch_object_info(base_url: str) -> dict:
    """Return ComfyUI's ``GET /object_info`` map, keyed by node class name.

    Args:
        base_url: The ComfyUI base URL, without a trailing slash.

    Returns:
        The parsed ``{class_name: node_spec}`` mapping.

    Raises:
        RuntimeError: When ComfyUI is unreachable or answers with something
            that is not a JSON object. The caller turns this into an
            *unchecked* pre-flight rather than a failure.
    """
    url = f"{base_url}/object_info"
    try:
        response = requests.get(url, timeout=OBJECT_INFO_TIMEOUT_S)
    except requests.RequestException as exc:
        logger.warning("ComfyUI object_info request failed (%s): %s", url, exc)
        raise RuntimeError(f"Could not reach ComfyUI at {base_url}") from exc
    if response.status_code >= 300:
        detail = (response.text or "").strip()[:200]
        logger.warning(
            "ComfyUI object_info failed: url=%s status=%s detail=%s",
            url,
            response.status_code,
            detail,
        )
        raise RuntimeError(f"ComfyUI answered {response.status_code} for /object_info")
    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("ComfyUI object_info returned invalid JSON from %s", url)
        raise RuntimeError("ComfyUI returned invalid JSON for /object_info") from exc
    if not isinstance(payload, dict):
        logger.warning(
            "ComfyUI object_info returned %s, expected an object",
            type(payload).__name__,
        )
        raise RuntimeError("ComfyUI returned an unexpected /object_info shape")
    return payload


def find_input_spec(node_spec: Any, field: str) -> tuple[Any, dict] | None:
    """Return ``(type_field, opts)`` for *field* in an ``object_info`` node spec.

    ``object_info[class]["input"]` splits into ``required`` / ``optional`` /
    ``hidden`` groups; only the first two carry values a graph sets.

    Returns:
        The raw spec pair, or ``None`` when the field is not declared.
    """
    if not isinstance(node_spec, dict):
        return None
    inputs = node_spec.get("input")
    if not isinstance(inputs, dict):
        return None
    for group in ("required", "optional"):
        group_spec = inputs.get(group)
        if not isinstance(group_spec, dict) or field not in group_spec:
            continue
        entry = group_spec[field]
        if not isinstance(entry, (list, tuple)) or not entry:
            return None
        opts = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
        return entry[0], opts
    return None


def _combo_options(node_spec: Any, field: str) -> list[str] | None:
    """Return the enumerated string options for *field*, or ``None``.

    ComfyUI serialises a combo widget in **two** shapes, and both are live in a
    current install - ``UpscaleModelLoader`` is already on the second:

    - **V1** ``[["a.safetensors", "b.safetensors"], {opts}]`` - the option list
      *is* the type field.
    - **V3** ``["COMBO", {"options": [...], ...}]`` - the list moved into opts.

    This mirrors ComfyUI's own branch in ``execution.py`` (``isinstance(
    input_type, list) or input_type == io.Combo.io_type``). Reading only the V1
    shape would silently stop checking every V3-migrated loader.

    ``None`` means "not enumerable", and is deliberately returned for:

    - a plain type name (``"INT"``, ``"MODEL"``) - not a filename at all;
    - a ``remote`` combo, whose options ComfyUI leaves empty in ``object_info``
      and fills from a URL at runtime, so the embedded list proves nothing;
    - an empty list, which ComfyUI emits both for "nothing installed" and for
      lists it populates lazily. Treating that as "everything is missing" would
      flag a whole graph on a healthy server.
    """
    found = find_input_spec(node_spec, field)
    if found is None:
        return None
    type_field, opts = found
    if opts.get("remote"):
        # Lazily fetched by the frontend; the embedded list is not the truth.
        return None
    if isinstance(type_field, (list, tuple)):
        options = type_field  # V1
    elif type_field == "COMBO":
        options = opts.get("options")  # V3
        if not isinstance(options, (list, tuple)):
            return None
    else:
        return None
    values = [opt for opt in options if isinstance(opt, str)]
    return values or None


def listed_options(
    object_info: dict | None, class_type: str, field: str
) -> list[str] | None:
    """What ComfyUI lists for one loader field, or ``None`` when it cannot say.

    ``None`` for no ``object_info``, a class it does not declare, or a field it
    does not enumerate (see :func:`_combo_options`): each means "unchecked",
    never "empty".
    """
    if object_info is None:
        return None
    return _combo_options(object_info.get(class_type), field)


def _normalize_filename(value: str) -> str:
    """Return *value* with path separators unified.

    ComfyUI builds combo entries with ``os.path.relpath``, so the same model
    lists as ``SDXL\\base.safetensors`` on a Windows host and
    ``SDXL/base.safetensors`` on Linux. A recipe generated on one and replayed
    on the other would otherwise read as a missing model that is right there.
    """
    return value.replace("\\", "/")


def _matching_option(value: str, options: list[str]) -> str | None:
    """The advertised option *value* names, in **ComfyUI's own spelling**.

    :func:`_match_option` answers "is this loadable" and normalizes separators to
    do it, which is right for a check and not enough for a patch: a caller about
    to write the value into a graph has to write the string this install actually
    lists, because ComfyUI compares exactly. Case is not folded, for the same
    reason ``_match_option`` reports a case-only difference as a miss.

    Returns ``None`` when no option matches, so a caller can loop candidates.
    """
    normalized = _normalize_filename(value)
    for option in options:
        if _normalize_filename(option) == normalized:
            return option
    return None


def _match_option(value: str, options: list[str]) -> str | None:
    """Return ``None`` if *value* is present, else a note on the near-miss.

    Separator differences are not a mismatch (see :func:`_normalize_filename`).
    A case-only difference IS a real failure on a case-sensitive host - ComfyUI
    compares exactly - but saying "present under a different case" is far more
    actionable than "missing", so it is reported as its own kind of miss.
    """
    normalized = _normalize_filename(value)
    normalized_options = {_normalize_filename(opt): opt for opt in options}
    if normalized in normalized_options:
        return None
    lowered = {key.lower(): key for key in normalized_options}
    candidate = lowered.get(normalized.lower())
    if candidate is not None:
        return f"present under a different case: {normalized_options[candidate]}"
    return "not available on this ComfyUI"


def collect_node_classes(prompt_graph: dict) -> list[str]:
    """Return the distinct ``class_type`` names *prompt_graph* would execute.

    This is a **security** disclosure, not a statistic. The graph is authored by
    whoever made the image file, not by the owner replaying it, and PixlStash's
    premise is importing images from elsewhere. What that graph can do on the
    owner's ComfyUI is bounded only by which node packs are installed, so the
    owner - the only trust anchor in the loop - has to be able to see *which*
    node classes will run before approving the run. A node *count* does not
    answer that question; the class list does.

    Sorted case-insensitively for a stable, scannable list: the graph's own key
    order is ComfyUI's internal node ids and carries no meaning for a reader.

    Args:
        prompt_graph: The API-format graph, sanitized or raw.

    Returns:
        The distinct class names, sorted. Empty for a junk or empty graph.
    """
    classes: set[str] = set()
    for node in (prompt_graph or {}).values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type:
            classes.add(class_type)
    return sorted(classes, key=lambda name: (name.lower(), name))


def preflight_prompt(prompt_graph: dict, object_info: dict) -> dict:
    """Check *prompt_graph* against *object_info* and report what is missing.

    Four checks, each deliberately narrow, each reported in its own bucket so
    the UI can say *what kind* of thing is wrong:

    1. **Node classes** (``missing_node_classes``) - a ``class_type`` that is
       not a key of ``object_info`` cannot run. This one is exact.
    2. **Model filenames** (``missing_models``) - for the loader fields in
       :data:`MODEL_FILENAME_FIELDS`, a literal string value absent from
       ComfyUI's advertised combo list. Node references (``[node_id, slot]``)
       are skipped: computed at run time, not filenames. Non-enumerable fields
       are skipped and counted in ``unchecked_fields``, so a mostly-skipped
       check cannot masquerade as a clean bill of health. A model-shaped
       value (a model file extension) on a loader the map does not cover is
       counted in ``unchecked_models`` for the same reason.
    3. **Input images** (``missing_input_images``) - a recipe's ``LoadImage``
       names whatever sat in *that* ComfyUI's ``input/`` directory when the
       image was generated, which is usually gone. This is a separate bucket
       because it is a different problem with a different fix, and because
       ComfyUI itself validates it by file existence rather than against the
       combo list. Never reported as a "missing model".
    4. **Output nodes** (``has_save_image``) - a graph with nothing that writes
       an image runs to completion and imports nothing. Catching it here saves
       the user the full generation wait for an empty result.

    Args:
        prompt_graph: The API-format graph.
        object_info: The map from :func:`fetch_object_info`.

    Returns:
        A dict with the four buckets above plus ``ok`` (True only when all
        three missing-lists are empty), ``checked``, ``unchecked_fields`` and
        ``unchecked_models``.
    """
    missing_classes: list[str] = []
    missing_models: list[dict] = []
    missing_input_images: list[dict] = []
    unchecked_fields = 0
    unchecked_models = 0
    seen_classes: set[str] = set()
    has_save_image = False

    for node_id, node in (prompt_graph or {}).items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            continue
        if class_type in SAVE_IMAGE_CLASSES:
            has_save_image = True

        if class_type not in object_info:
            if class_type not in seen_classes:
                seen_classes.add(class_type)
                missing_classes.append(class_type)
            # Without a spec there is nothing to check its filenames against.
            # Reporting its inputs too would turn one missing node pack into a
            # page of scary findings.
            continue

        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        if class_type in INPUT_IMAGE_FIELDS:
            for field in INPUT_IMAGE_FIELDS[class_type]:
                value = inputs.get(field)
                if not isinstance(value, str) or not value:
                    continue
                options = _combo_options(object_info.get(class_type), field)
                if options is None:
                    unchecked_fields += 1
                    continue
                if _match_option(value, options) is not None:
                    missing_input_images.append(
                        {
                            "node_id": str(node_id),
                            "class_type": class_type,
                            "field": field,
                            "value": value,
                        }
                    )
            continue

        fields = list(model_filename_fields(class_type))
        # Stackers spell their extra slots `lora_name_2`, `lora_name_3`, …;
        # unlike the core loader, their class gives no fixed field list. Read
        # the actual graph inputs so the pre-flight and the later bypass see
        # the same missing adapters.
        fields.extend(
            field
            for field in inputs
            if LORA_FILENAME_FIELD_RE.match(str(field)) and field not in fields
        )
        for field in fields:
            value = inputs.get(field)
            if not isinstance(value, str) or not value:
                # Missing, or wired from another node - not a literal filename.
                continue
            options = _combo_options(object_info.get(class_type), field)
            if options is None:
                unchecked_fields += 1
                continue
            note = _match_option(value, options)
            if note is not None:
                missing_models.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "field": field,
                        "value": value,
                        "note": note,
                    }
                )
        # A model-shaped value on a loader the map does not cover was never
        # looked at. Counted, so "no missing models" is read against how many
        # models went unchecked rather than as a clean bill of health.
        unchecked_models += sum(
            1
            for field, value in inputs.items()
            if field not in fields
            and isinstance(value, str)
            and value.lower().endswith(MODEL_EXTENSIONS)
        )

    return {
        "ok": not missing_classes and not missing_models and not missing_input_images,
        "checked": True,
        "missing_node_classes": missing_classes,
        "missing_models": missing_models,
        "missing_input_images": missing_input_images,
        "has_save_image": has_save_image,
        "unchecked_fields": unchecked_fields,
        "unchecked_models": unchecked_models,
    }


def advertised_model_names(object_info: dict) -> set[str]:
    """Every model filename this ComfyUI says it can load, normalized.

    The question "does that ComfyUI read this file" answered by the only party
    who knows: the combo lists ComfyUI publishes for the loader fields in
    :data:`MODEL_FILENAME_FIELDS`. PixlStash holds a ComfyUI **URL** and no path
    to its ``models/`` tree, so comparing registered folders against it is not
    available - and would be the wrong answer anyway, because what matters is
    what the install can load, symlinks, ``extra_model_paths.yaml`` and all.

    Both the option as listed and its basename are in the set: an entry is a
    path relative to one of ComfyUI's model folders (``sdxl/base.safetensors``),
    and a caller holding a registered folder's relpath has no way to know which
    prefix that ComfyUI puts in front of it.

    Args:
        object_info: The map from :func:`fetch_object_info`.

    Returns:
        The normalized names, empty for a map that advertises no loader.
    """
    names: set[str] = set()
    for class_type, spec in (object_info or {}).items():
        for field in model_filename_fields(class_type):
            for option in _combo_options(spec, field) or ():
                normalized = _normalize_filename(option)
                names.add(normalized)
                names.add(normalized.rsplit("/", 1)[-1])
    return names


def detect_model_targets(prompt_graph: dict, object_info: dict) -> list[dict]:
    """Every model-loader field naming a file this ComfyUI does not advertise.

    The detect half of the detect-then-patch pair
    :func:`detect_seed_targets`/:func:`apply_seeds` and
    :func:`detect_lora_targets`/:func:`apply_adapter` already are (#1439); the
    patch half is :func:`apply_model_swap`.

    It is :func:`preflight_prompt`'s ``missing_models`` and nothing else, which
    is the point rather than laziness: the swap has to be aimed at exactly the
    fields the pre-flight would report, or it would either patch a field ComfyUI
    was perfectly happy with or leave one it will refuse.

    Args:
        prompt_graph: The API-format graph.
        object_info: The map from :func:`fetch_object_info`.

    Returns:
        ``{node_id, class_type, field, value, note}`` per unloadable field.
    """
    return preflight_prompt(prompt_graph, object_info)["missing_models"]


def _alias_key(value: str) -> str:
    """One loader value as :func:`model_name_aliases` keys its map.

    Its keys are ``workflow_hash.normalized_filename`` - a **lowercased**
    basename - so a lookup that merely unified separators would miss every
    mixed-case filename, which is most of them.
    """
    return _normalize_filename(value).rsplit("/", 1)[-1].lower()


def apply_model_swap(
    prompt_graph: dict,
    targets: list[dict],
    aliases: dict[str, list[str]],
    object_info: dict,
) -> list[dict]:
    """Point each target at another name for the same model, where one loads.

    The patch half of :func:`detect_model_targets`. *aliases* maps a normalized
    basename to the other names the shelf knows the **same** model by - another
    copy of the identical bytes, which is what makes this a substitution and not
    a suggestion (a same-weights-different-precision file is a different output
    and stays a hint the owner accepts).

    **Every candidate is verified against ``object_info`` before it is written.**
    A swap PixlStash believes in and ComfyUI does not advertise only moves the
    failure from the pre-flight to the queue, so the accepted candidate is one
    this install's own combo list contains - which is also why the aliases may
    be generous: the combo list, not this function, decides.

    The graph is mutated in place, which is what the caller wants for the graph
    it is about to submit; nothing is written back to the stored recipe, whose
    filenames remain the picture's provenance.

    Args:
        prompt_graph: The API-format graph, mutated in place.
        targets: :func:`detect_model_targets`' output.
        aliases: normalized basename -> other names for the same model.
        object_info: The map from :func:`fetch_object_info`.

    Returns:
        One ``{node_id, class_type, field, was, now}`` per field patched, for the
        caller to report and log. A run that quietly loaded a different file
        makes its own lineage a lie, so this list is the whole point of the
        return value.
    """
    substitutions: list[dict] = []
    for target in targets or []:
        node = (prompt_graph or {}).get(str(target.get("node_id")))
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        field = str(target.get("field") or "")
        value = target.get("value")
        if not isinstance(inputs, dict) or not field or not isinstance(value, str):
            continue
        options = _combo_options(object_info.get(node.get("class_type")), field)
        if not options:
            continue
        # Folded to match `model_name_aliases`' keys, which are
        # `normalized_filename` - LOWERCASE. Looking up the graph's own
        # spelling instead finds nothing for any name with a capital in
        # it, which is most real model filenames.
        candidates = aliases.get(_alias_key(value)) or ()
        for candidate in candidates:
            # The OPTION, not the candidate: `_match_option` accepts a candidate
            # whose separators merely normalize onto an advertised entry, and
            # ComfyUI compares exactly - so writing the candidate's own spelling
            # could put `sub\x.safetensors` into a graph on an install that
            # advertises `sub/x.safetensors`, which is the one thing this
            # function's contract promises cannot happen.
            listed = _matching_option(candidate, options)
            if listed is None:
                continue
            inputs[field] = listed
            substitutions.append(
                {
                    "node_id": str(target.get("node_id")),
                    "class_type": node.get("class_type"),
                    "field": field,
                    "was": value,
                    "now": listed,
                }
            )
            break
    return substitutions


def _swap_key(value: str, swaps: dict[str, str]) -> str | None:
    """The key of *swaps* one loader value names, or ``None``.

    The whole recorded name only, separators unified and case folded. There is
    no basename tier: the clone dialog sends the graph's own values as keys, so
    an exact match always exists, and a basename one would let a swap of
    ``diffusion_pytorch_model.safetensors`` rewrite a ControlNet loader's
    ``canny/diffusion_pytorch_model.safetensors`` too. Two keys folding onto
    one value is no answer: which of them was meant is a guess.
    """
    wanted = _normalize_filename(value).lower()
    hits = [was for was in swaps if _normalize_filename(was).lower() == wanted]
    return hits[0] if len(hits) == 1 else None


def _option_by_basename(value: str, options: list[str]) -> str | None:
    """The one option whose basename is *value*'s, in ComfyUI's spelling.

    A shelf row knows its file by name, and ComfyUI lists it under whatever
    subfolder of its model folder it sits in (``flux/krea2.safetensors``), so a
    whole-name match alone would refuse nearly every real swap. Two options of
    that basename is not an answer. Case is kept, as in :func:`_matching_option`.
    """
    hits = _options_by_basename(value, options)
    return hits[0] if len(hits) == 1 else None


def _options_by_basename(value: str, options: list[str]) -> list[str]:
    """Every option whose basename is *value*'s, in ComfyUI's spelling."""
    base = _normalize_filename(value).rsplit("/", 1)[-1]
    return [
        option
        for option in options
        if _normalize_filename(option).rsplit("/", 1)[-1] == base
    ]


def apply_filename_swap(
    prompt_graph: dict,
    swaps: dict[str, str],
    object_info: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Point every loader naming one file at another file instead.

    The clone-with-new-models rewrite. Deliberately not :func:`apply_model_swap`:
    that one substitutes **the same bytes** under another name, aimed at fields
    ComfyUI cannot load; this one replaces a file that loads perfectly well with
    a **different** file the owner chose. Same shape, opposite intent.

    Swapped by filename, never by node address: every loader field naming a
    *swaps* key is rewritten, wherever it is in the graph. The fields read come
    from :func:`pixlstash.utils.comfyui_utilities.iter_model_fields_api`, the
    walk the clone dialog's slot list reads too, so the two agree on which
    widgets name a model.

    With *object_info* the replacement is written in **the option's spelling**
    (:func:`_matching_option`, then :func:`_option_by_basename`), because
    ComfyUI compares exactly; a replacement
    that loader does not advertise is left unwritten and reported. Without it
    the replacement is written as given and marked ``verified: False``: the
    clone must not need ComfyUI to be running.

    Args:
        prompt_graph: The API-format graph, mutated in place.
        swaps: The graph's filename -> the filename to load instead.
        object_info: The map from :func:`fetch_object_info`, or ``None``.

    Returns:
        ``(substitutions, unswapped)``. One ``{node_id, class_type, field, was,
        now, verified}`` per field rewritten, and one ``{was, now, reason}`` per
        swap that did not land: ``not_in_graph`` when no field names it,
        ``not_on_comfyui`` when ComfyUI does not list the replacement, and
        ``several_on_comfyui`` when it lists that name in more than one folder.
    """
    # Local for the cycle: `comfyui_utilities` imports `model_filename_fields`
    # from this module.
    from pixlstash.utils.comfyui_utilities import iter_model_fields_api

    substitutions: list[dict] = []
    unswapped: list[dict] = []
    matched: set[str] = set()
    # Listed first, then written: the walk reads the inputs it rewrites.
    for node_id, class_type, field, value in list(iter_model_fields_api(prompt_graph)):
        key = _swap_key(value, swaps)
        if key is None:
            continue
        matched.add(key)
        now = swaps[key]
        verified = False
        options = (
            _combo_options(object_info.get(class_type), field)
            if object_info is not None
            else None
        )
        if options:
            listed = _matching_option(now, options) or _option_by_basename(now, options)
            if listed is None:
                # Two files of that name in two folders may be two different
                # models: writing either is a guess, so it is refused - but as
                # what it is, not as a file ComfyUI does not have.
                several = len(_options_by_basename(now, options)) > 1
                unswapped.append(
                    {
                        "was": value,
                        "now": now,
                        "reason": "several_on_comfyui" if several else "not_on_comfyui",
                    }
                )
                continue
            now, verified = listed, True
        if now == value:
            # The owner kept this file: nothing to write or report.
            continue
        prompt_graph[node_id]["inputs"][field] = now
        substitutions.append(
            {
                "node_id": str(node_id),
                "class_type": class_type,
                "field": field,
                "was": value,
                "now": now,
                "verified": verified,
            }
        )
    unswapped.extend(
        {"was": was, "now": now, "reason": "not_in_graph"}
        for was, now in swaps.items()
        if was not in matched
    )
    return substitutions, unswapped


def detect_seed_targets(prompt_graph: dict, object_info: dict) -> list[dict]:
    """Find every patchable seed input in *prompt_graph*.

    A class allowlist cannot converge on arbitrary user graphs, so this asks
    ComfyUI instead: **an input is a seed when its declared type is ``INT`` and
    its options carry a truthy ``control_after_generate``** - the flag ComfyUI
    sets on exactly the inputs its own frontend re-rolls between runs. That
    covers core samplers and every custom node pack for free, with no list to
    maintain. Both serialisations count: legacy nodes emit ``true``, V3-schema
    nodes emit the string ``"fixed"`` / ``"randomize"``, so truthiness is the
    test, not identity.

    :data:`SEED_PASSTHROUGH_CLASSES` are the exception and are reached **only**
    by following a link from a real seed consumer. ``PrimitiveInt`` carries
    ``control_after_generate`` unconditionally, including when it is driving
    width or height - scanning it directly would randomize the image
    dimensions, which the shipped ``Flux2-Klein-t2i`` template would hit.

    Args:
        prompt_graph: The API-format graph.
        object_info: The map from :func:`fetch_object_info`.

    Returns:
        ``[{"node_id", "class_type", "field", "value", "max"}, …]``, deduped.
    """
    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _visit(node_id: str, depth: int = 0) -> None:
        if depth > _MAX_SEED_LINK_DEPTH:
            return
        node = (prompt_graph or {}).get(node_id)
        if not isinstance(node, dict):
            return
        class_type = node.get("class_type")
        spec = object_info.get(class_type)
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or not isinstance(spec, dict):
            return
        for field, value in inputs.items():
            found = find_input_spec(spec, field)
            if found is None:
                continue
            type_field, opts = found
            if type_field != "INT":
                continue
            if not opts.get("control_after_generate"):
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                key = (str(node_id), field)
                if key in seen:
                    continue
                seen.add(key)
                targets.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "field": field,
                        "value": int(value),
                        "max": int(opts.get("max") or MAX_SEED_64),
                    }
                )
            elif isinstance(value, (list, tuple)) and len(value) == 2:
                # The seed is wired in from a passthrough primitive; patch that.
                ref_id = str(value[0])
                ref = (prompt_graph or {}).get(ref_id)
                if isinstance(ref, dict) and ref.get("class_type") in (
                    SEED_PASSTHROUGH_CLASSES
                ):
                    _visit(ref_id, depth + 1)

    for node_id in list((prompt_graph or {}).keys()):
        node = prompt_graph[node_id]
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in SEED_PASSTHROUGH_CLASSES:
            # Only reachable via a link from a real consumer - see the docstring.
            continue
        _visit(str(node_id))

    return targets


def apply_seeds(prompt_graph: dict, targets: list[dict], seed: int | None) -> int:
    """Write *seed* (or a fresh random value) into every detected seed target.

    Args:
        prompt_graph: The graph to mutate in place.
        targets: The output of :func:`detect_seed_targets`.
        seed: The value to pin, or ``None`` to draw a fresh random one per
            target, clamped to that target's declared maximum.

    Returns:
        How many inputs were written.
    """
    written = 0
    for target in targets or []:
        node = (prompt_graph or {}).get(target.get("node_id"))
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        ceiling = int(target.get("max") or MAX_SEED_64)
        value = random.randint(0, min(ceiling, MAX_SEED_64)) if seed is None else seed
        inputs[target["field"]] = min(value, ceiling)
        written += 1
    return written


def detect_lora_targets(prompt_graph: dict) -> list[dict]:
    """Find every LoRA slot in *prompt_graph* a shelf adapter can be put into.

    By **field name**, not by class: every pack that wraps ComfyUI's own LoRA
    loader keeps its ``lora_name`` widget (``LoraLoaderModelOnly``,
    ``LoraLoaderGGUF``, the ``LoRALoader`` spelling, and the third-party ones
    that copy it), so a class allowlist would have to grow for each and would
    quietly refuse the rest. The same reasoning as :func:`detect_seed_targets`,
    one step cheaper: no ``object_info`` is needed, because the field's name is
    the whole rule. A stacker's numbered widgets (``lora_name_1``,
    ``lora_name_2``) count too, each as its own slot.

    **The known reach is a stacker that does not name its slots that way**:
    rgthree's Power Lora Loader holds them as dicts under ``lora_1``, so this
    finds nothing there and the caller reports the workflow as having no LoRA
    loader. Reading a widget whose shape is one pack's own is what #1376 has to
    decide, along with inserting a loader where there is none.

    Two kinds of slot, and a graph can hold both:

    - ``by: "filename"`` - a core loader naming a file. What goes in is a name
      the target ComfyUI lists, which :func:`apply_adapter` resolves.
    - ``by: "digest"`` - a ComfyUI-PixlStash loader naming the file by its
      SHA-256 (``adapter_sha256``). The shelf's digest goes in as it is; that
      node resolves or fetches the file itself.

    A slot wired from another node (``[node_id, slot]``) is skipped: it is
    computed at run time and overwriting it would drop the link.

    Args:
        prompt_graph: The API-format graph.

    Returns:
        ``[{"node_id", "class_type", "field", "value", "by", "strengths"}, …]``,
        ``strengths`` being :func:`_lora_strengths` for that slot.
    """
    targets: list[dict] = []
    for node_id, node in (prompt_graph or {}).items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        fields = [f for f in inputs if LORA_FILENAME_FIELD_RE.match(str(f))]
        # One digest slot per node, not one per spelling: the pack has called
        # the widget both things, and a node carrying two names is still one
        # adapter to load.
        digest = next((f for f in LORA_DIGEST_FIELDS if f in inputs), None)
        if digest is not None:
            fields.append(digest)
        for field in fields:
            value = inputs.get(field)
            if not isinstance(value, str):
                continue
            targets.append(
                {
                    "node_id": str(node_id),
                    "class_type": node.get("class_type"),
                    "field": field,
                    "value": value,
                    "by": "digest" if field == digest else "filename",
                    "strengths": _lora_strengths(inputs, field),
                }
            )
    return targets


def _lora_strengths(inputs: dict, field: str) -> dict:
    """The strengths applied beside the LoRA slot named by *field*.

    A stacker that numbers its widgets in step - ``lora_name_2`` weighted by
    ``strength_model_2`` - has each slot's strengths picked out by that slot's
    own suffix rather than the node's first pair. **A pack that numbers them
    some other way** (``model_weight_2``, ``lora_wt_2``) reports the slot with
    no strengths, the same honest empty answer a wired one gets; the rule here
    is the core loader's spelling, as the slot rule above is. A wired strength is computed at run time and has no
    value to report, so it is left out rather than reported as its link.

    Returns:
        ``{"model": float, "clip": float}``, either key absent when the node
        does not carry it.
    """
    suffix = field[len("lora_name") :] if field.startswith("lora_name") else ""
    strengths: dict[str, float] = {}
    for key, widget in _LORA_STRENGTH_FIELDS:
        value = inputs.get(f"{widget}{suffix}")
        if value is None and key == "model":
            value = inputs.get(f"strength{suffix}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        # A non-finite strength is refused rather than reported: the graph is
        # attacker-authorable file metadata, `json.loads` accepts the `NaN` and
        # `Infinity` literals, and every route that hands these dicts back
        # renders with `allow_nan=False` - so reporting one is a 500 on a read
        # a share-token holder can make.
        if math.isfinite(value):
            strengths[key] = float(value)
    return strengths


def _shelf_name_in(filenames: list[str], options: list[str]) -> str | None:
    """Return the option naming one of *filenames*, or ``None`` for no match.

    ComfyUI lists a LoRA as its path relative to that install's ``loras``
    folder; the shelf knows the file's own name and its path relative to the
    folder PixlStash scanned. Those agree only when both sides use the same
    tree, so the names are compared on the basename too, which is what actually
    identifies the file. An exact match wins; a basename that names **several**
    of ComfyUI's files is refused rather than guessed at, because picking one of
    two ``style.safetensors`` is picking the wrong one half the time.

    **This is a match by name, not by content**, and it cannot be anything else:
    ``object_info`` lists names and no digests, so a file of that name on that
    machine is all ComfyUI can be asked for. The digest slot of a
    ComfyUI-PixlStash loader is the exact one, because that node asks PixlStash.

    Raises:
        LookupError: When the basename matches more than one of *options*.
    """
    by_path = {_normalize_filename(opt): opt for opt in options}
    by_base: dict[str, set[str]] = {}
    for normalized, option in by_path.items():
        by_base.setdefault(normalized.rsplit("/", 1)[-1].lower(), set()).add(option)
    candidates = [_normalize_filename(name) for name in filenames if name]
    for name in candidates:
        if name in by_path:
            return by_path[name]
    for name in candidates:
        matched = by_base.get(name.rsplit("/", 1)[-1].lower())
        if not matched:
            continue
        if len(matched) > 1:
            raise LookupError(
                f"{name.rsplit('/', 1)[-1]} names {len(matched)} different files "
                "on this ComfyUI, so PixlStash cannot tell which one you mean."
            )
        return next(iter(matched))
    return None


def apply_adapter(
    prompt_graph: dict, targets: list[dict], adapter: dict, object_info: dict
) -> int:
    """Put one shelf adapter into every LoRA slot of *prompt_graph*.

    Each slot is written the way its own loader reads it (the decision on
    #1310): a core loader gets a filename this ComfyUI lists, a
    ComfyUI-PixlStash loader gets the digest. No node is substituted and none is
    added, so this works on any ComfyUI and leaves a graph it cannot serve
    alone - see the refusal below.

    Args:
        prompt_graph: The graph to mutate in place.
        targets: The output of :func:`detect_lora_targets`.
        adapter: ``{"sha256": str, "filenames": [str, …]}`` - the shelf model's
            digest and the names it is known by (its own filename and each
            copy's path relative to the folder holding it).
        object_info: The map from :func:`fetch_object_info`, for resolving a
            filename slot against what this ComfyUI actually has.

    Returns:
        How many slots were written.

    Raises:
        LookupError: When a filename slot cannot be resolved - the adapter's
            file is not on that ComfyUI, its name is ambiguous there, or the
            loader does not enumerate its files. Refusing is the point: a name
            ComfyUI does not have comes back as an opaque 400 from ``/prompt``
            after the run has been queued.
    """
    written = 0
    for target in targets or []:
        node = (prompt_graph or {}).get(target.get("node_id"))
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if target.get("by") == "digest":
            inputs[target["field"]] = adapter["sha256"]
            written += 1
            continue
        class_type = target.get("class_type")
        if class_type not in object_info:
            # Named as the missing node pack it is. The pre-flight would say
            # the same, but the swap is resolved first, and "does not say which
            # files it can load" sends the owner looking in the wrong place.
            raise LookupError(
                f"This ComfyUI has no {class_type} node, which node "
                f"{target['node_id']} needs to load a LoRA."
            )
        options = _combo_options(object_info.get(class_type), target["field"])
        if options is None:
            raise LookupError(
                f"This ComfyUI does not say which LoRA files {class_type} "
                f"(node {target['node_id']}) can load, so PixlStash will not "
                "guess at a name for it."
            )
        name = _shelf_name_in(adapter.get("filenames") or [], options)
        if name is None:
            raise LookupError(
                "That LoRA is on your shelf but not on the ComfyUI this would "
                f"run on, under any name node {target['node_id']} lists."
            )
        inputs[target["field"]] = name
        written += 1
    return written


def _live_graph(graph: dict, object_info: dict) -> dict:
    """*graph* without the nodes no output reads, as ComfyUI runs it.

    ComfyUI executes backwards from its output nodes (``output_node`` in
    ``object_info``: SaveImage, PreviewImage), so a leftover branch - a second
    UNET loader and a LoRA wired into nothing - never runs, and must not be
    read as a second model or a second chain. A graph whose ``object_info``
    names no output node is returned whole: nothing says what is dead.
    """
    outputs = [
        node_id
        for node_id, node in graph.items()
        if isinstance(node, dict)
        and (object_info.get(node.get("class_type")) or {}).get("output_node")
    ]
    if not outputs:
        return graph
    live: set[str] = set()
    pending = [str(node_id) for node_id in outputs]
    while pending:
        node_id = pending.pop()
        if node_id in live or not isinstance(graph.get(node_id), dict):
            continue
        live.add(node_id)
        pending.extend(
            str(value[0])
            for value in (graph[node_id].get("inputs") or {}).values()
            if is_link(value)
        )
    dead = sorted(set(map(str, graph)) - live)
    if dead:
        logger.info("Nodes %s feed no output, so the LoRA chain ignores them.", dead)
    return {node_id: node for node_id, node in graph.items() if str(node_id) in live}


def _model_links(graph: dict, object_info: dict) -> list[dict]:
    """Every link in *graph* carrying a model or a CLIP, typed by its source.

    The type is read off the SOURCE's ``object_info`` output list, never off
    the consumer's input name: an API-format link is ``[node_id, index]`` with
    no type on it, and ``ModelMergeSimple`` reads its models as ``model1`` and
    ``model2``. Shared by the insertion planner and the chain reader, which
    must agree on what reads the model.

    Returns:
        ``[{"node_id", "class_type", "field", "type", "source", "kind"}, …]``,
        ``type`` being ``MODEL`` or ``CLIP``, ``source`` ``(node_id, index)``.
        A link carrying any other kind of model (an upscaler's, a detector's)
        is left out: no LoRA loader goes on its path.

    **A source ComfyUI cannot type is typed by its reader instead.** A seed
    node from a pack this ComfyUI lacks hands the sampler an INT, and the
    sampler's own spec says ``seed`` is an INT: that link is no model, and the
    single MODEL path from checkpoint to sampler is still there to follow. Only
    when neither end says what the link carries is it refused.

    Raises:
        LookupError: A link neither end can type - a source class this ComfyUI
            lacks (or whose output list does not reach the link's index) read
            by an input whose type is unknown too - since it may be the model.
    """
    links: list[dict] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        for field, value in node["inputs"].items():
            if not is_link(value) or not isinstance(graph.get(value[0]), dict):
                continue
            source_class = graph[value[0]].get("class_type")
            spec = object_info.get(source_class)
            outputs = spec.get("output") if isinstance(spec, dict) else None
            if isinstance(outputs, list) and 0 <= value[1] <= len(outputs) - 1:
                kind = outputs[value[1]]
            else:
                # Not a type PixlStash can read off the source: no such class,
                # no output list, or one shorter than the graph's own link. The
                # reader's declared input type answers instead; with neither,
                # the link may be the model itself.
                kind = _declared_input_type(
                    object_info.get(node.get("class_type")), field
                )
                if kind is None:
                    what = (
                        f"This ComfyUI has no {source_class} node"
                        if not isinstance(spec, dict)
                        else f"This ComfyUI does not say what {source_class} hands on"
                    )
                    raise LookupError(
                        f"{what}, and #{node_id} {node.get('class_type')} does not "
                        f"say what its {field} takes, so PixlStash cannot tell "
                        f"whether node {value[0]} hands on the model."
                    )
                logger.info(
                    "Node %s (%s) cannot be typed by this ComfyUI; its link into "
                    "#%s %s is read as %s, the type that input declares.",
                    value[0],
                    source_class,
                    node_id,
                    field,
                    kind,
                )
            # Only the two types a LoRA loader patches. Any other "model"
            # (UPSCALE_MODEL, a detector, WANVIDEOMODEL) is on a path of its
            # own, and the MODEL path from source to sampler is the only one a
            # loader goes in.
            if kind in ("MODEL", "CLIP"):
                links.append(
                    {
                        "node_id": str(node_id),
                        "class_type": node.get("class_type"),
                        "field": field,
                        "type": kind,
                        "source": (value[0], value[1]),
                    }
                )
    return links


def _declared_input_type(node_spec: dict | None, field: str) -> str | None:
    """The type *node_spec* declares for its input *field*, or ``None``.

    A combo (a list of options) is a value, not a link type, and reads as
    ``"COMBO"``. ``None`` for an unknown class, an undeclared field, or the
    wildcard ``*``, none of which says what the link carries.
    """
    inputs = node_spec.get("input") if isinstance(node_spec, dict) else None
    for group in ("required", "optional"):
        entry = ((inputs or {}).get(group) or {}).get(field)
        if not isinstance(entry, (list, tuple)) or not entry:
            continue
        declared = entry[0]
        if isinstance(declared, list):
            return "COMBO"
        if isinstance(declared, str) and declared and declared != "*":
            return declared
        return None
    return None


def _sources(links: list[dict], kind: str) -> list[tuple]:
    """Every ``(node_id, output)`` handing out *kind* from a node taking none."""
    takers = {link["node_id"] for link in links if link["type"] == kind}
    return sorted(
        {
            link["source"]
            for link in links
            if link["type"] == kind and link["source"][0] not in takers
        }
    )


def _source_of(graph: dict, links: list[dict], kind: str) -> dict | None:
    """The one node handing out *kind* that takes no *kind* itself, or ``None``.

    Raises:
        LookupError: When there are several - a refiner, a merge: which one a
            LoRA is for is the owner's call.
    """
    roots = _sources(links, kind)
    if len(roots) > 1:
        named = ", ".join(f"#{n} {graph[n].get('class_type')}" for n, _ in roots)
        what = "models" if kind == "MODEL" else "text encoders"
        raise LookupError(
            f"This workflow loads {len(roots)} {what} ({named}), so PixlStash "
            "cannot tell which one the LoRA is for. Add the loader in ComfyUI."
        )
    if not roots:
        return None
    node_id, output = roots[0]
    return {
        "node_id": node_id,
        "class_type": graph[node_id].get("class_type"),
        "output": output,
    }


def plan_lora_insertion(prompt_graph: dict, object_info: dict) -> dict:
    """Where a LoRA loader would go in a graph that has none (#1376).

    The loader is spliced in **right after the model source**: every input that
    reads the loaded model's MODEL output reads the loader's instead, and the
    same for CLIP, so the whole chain downstream - model patches, samplers,
    text encoders - keeps working and sees the LoRA. Nothing is written here;
    the plan is what the owner is shown before a run, and what
    :func:`insert_adapter` carries out.

    **Typed by ComfyUI, not by field name.** A link in an API-format graph is
    ``[node_id, output_index]`` with no type, and a consumer's input name is a
    guess (``ModelMergeSimple`` takes ``model1``). ``object_info`` lists every
    class's outputs, so a link's type is exact - and an input the rewiring
    missed would run that branch without the LoRA and say nothing.

    The source is the node handing out MODEL that takes no MODEL itself: a
    checkpoint, a UNET or GGUF loader. CLIP the same way, which may be another
    node (a UNET graph with its own CLIP loader) or none at all (a graph whose
    conditioning does not come from a CLIP), when a model-only loader goes in.

    Returns:
        ``{"model": {"node_id", "class_type", "output"}, "clip": … or None,
        "rewires": [{"node_id", "class_type", "field", "type"}, …],
        "pixlstash_loader": bool}`` - the last being whether the loader that
        resolves by digest could be the one inserted, which is what makes a
        picture from that run unreplayable by "Generate variants".

    **A node that loads a LoRA some way of its own does not stop it.** A
    stacker, a prompt-tag encoder or a character prompt builder is an ordinary
    node here: a loader spliced into the MODEL path patches the model whatever
    that node does, and it applies alongside, never instead.

    Raises:
        LookupError: When the graph cannot be spliced honestly - no model
            source, more than one (a refiner, a merge: which one the LoRA is
            for is the owner's call), a CLIP source that reads the model
            (splicing would make a cycle), or a link neither end can type, so
            what it carries is unknown. Another kind of model (an upscaler's,
            ``WANVIDEOMODEL``) is on a path of its own and changes nothing.
    """
    graph = _live_graph(prompt_graph or {}, object_info)
    links = _model_links(graph, object_info)

    def source_of(kind: str) -> dict | None:
        return _source_of(graph, links, kind)

    model = source_of("MODEL")
    if model is None:
        raise LookupError(
            "PixlStash could not find the model this workflow loads, so there is "
            "nowhere to put a LoRA."
        )
    clip = source_of("CLIP")
    if clip is not None and clip["node_id"] in {
        link["node_id"] for link in links if link["type"] == "MODEL"
    }:
        # The loader would feed a node its own CLIP input comes through: a cycle
        # ComfyUI refuses, after the run is queued.
        raise LookupError(
            f"#{clip['node_id']} {clip['class_type']} hands out this workflow's "
            "CLIP and reads its model, so a LoRA loader cannot sit in front of "
            "both. Add the loader in ComfyUI."
        )
    sources = {"MODEL": model, "CLIP": clip}
    rewires = [
        {key: link[key] for key in ("node_id", "class_type", "field", "type")}
        for link in links
        if sources[link["type"]] is not None
        and link["source"]
        == (sources[link["type"]]["node_id"], sources[link["type"]]["output"])
    ]
    # Numeric where the id is a number, so the owner-facing sentence reads
    # #3 before #10; a subgraph id ("75:83") keeps its place after them.
    rewires.sort(
        key=lambda r: (
            r["type"],
            0 if r["node_id"].isdigit() else 1,
            int(r["node_id"]) if r["node_id"].isdigit() else 0,
            r["node_id"],
            r["field"],
        )
    )
    return {
        "model": model,
        "clip": clip,
        "rewires": rewires,
        # Whether _inserted_loader *could* reach for the digest loader: which
        # one it takes depends on the adapter, which is not chosen yet, and the
        # owner is owed the worse case before they choose.
        "pixlstash_loader": PIXLSTASH_ADAPTER_LOADER in object_info,
    }


def _widget_defaults(node_spec: dict) -> dict:
    """The value every widget of a node would start at in ComfyUI's own editor."""
    values = {}
    inputs = node_spec.get("input") if isinstance(node_spec, dict) else None
    for group in ("required", "optional"):
        for field, entry in ((inputs or {}).get(group) or {}).items():
            if not isinstance(entry, (list, tuple)) or not entry:
                continue
            opts = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
            if "default" in opts:
                values[field] = opts["default"]
            elif group == "required":
                options = _combo_options(node_spec, field)
                if options:
                    values[field] = options[0]
    return values


def _inserted_loader(
    adapter: dict, object_info: dict, with_clip: bool
) -> tuple[str, str, str]:
    """``(class, field, value)`` of the loader to insert for *adapter*.

    ComfyUI's own loader first, when this ComfyUI lists the file: it needs no
    node pack. The ComfyUI-PixlStash loader when the file is not there by
    name, since it resolves the file by its digest and fetches it when it has
    to.

    Raises:
        LookupError: When neither can load it, saying why the first could not.
    """
    core = "LoraLoader" if with_clip else "LoraLoaderModelOnly"
    reason = f"This ComfyUI has no {core} node"
    if core in object_info:
        options = _combo_options(object_info[core], "lora_name")
        if options is None:
            reason = f"This ComfyUI does not say which LoRA files {core} can load"
        else:
            try:
                name = _shelf_name_in(adapter.get("filenames") or [], options)
            except LookupError as exc:
                logger.info("A LoRA name is ambiguous on this ComfyUI: %s", exc)
                name, reason = None, str(exc).rstrip(".")
            else:
                reason = "That LoRA is on your shelf but not on this ComfyUI"
            if name is not None:
                return core, "lora_name", name
    if PIXLSTASH_ADAPTER_LOADER in object_info:
        return PIXLSTASH_ADAPTER_LOADER, "adapter_sha256", adapter["sha256"]
    raise LookupError(
        f"{reason}, and ComfyUI-PixlStash, which could fetch it by its hash, is "
        "not installed there."
    )


def insert_adapter(
    prompt_graph: dict,
    plan: dict,
    adapter: Optional[dict],
    object_info: dict,
) -> dict:
    """Add a LoRA loader carrying *adapter* to *prompt_graph*, as *plan* says.

    The loader is chosen by :func:`_inserted_loader`, starts at its widgets'
    own defaults (strength 1.0), takes the planned sources, and every planned
    input is rewired to it. The plan is checked against the graph first, so a
    graph that no longer reads what the plan saw is refused whole rather than
    half rewired.

    Args:
        prompt_graph: The graph to mutate in place.
        plan: The output of :func:`plan_lora_insertion` for this graph.
        adapter: ``{"sha256", "filenames"}``, as for :func:`apply_adapter`, or
            ``None`` to add the loader **without choosing a LoRA**: ComfyUI's
            own loader, wired in and left at its widget defaults exactly as
            dropping the node in ComfyUI would leave it. That is what
            ``POST /workflows/{key}/insert-lora-loader`` writes into a stored
            file, so the workflow has a slot to swap from then on. No adapter
            means no digest loader either - nothing has a digest to resolve.
        object_info: The map the plan was made with.

    Returns:
        ``{"node_id", "class_type"}`` of the loader added.

    Raises:
        LookupError: When no loader can carry the adapter on this ComfyUI, or
            the graph has diverged from the plan.
    """
    clip = plan.get("clip")
    if adapter is None:
        loader = "LoraLoader" if clip is not None else "LoraLoaderModelOnly"
        if loader not in object_info:
            raise LookupError(f"This ComfyUI has no {loader} node.")
        if not _combo_options(object_info[loader], "lora_name"):
            # The same check `_inserted_loader` makes below, for the same
            # reason: with no options `_widget_defaults` yields no `lora_name`
            # key at all, so the copy would be written and answered 201 and
            # then refused by ComfyUI on a missing required input — after the
            # owner was told it was ready to pick a LoRA in.
            raise LookupError(
                f"This ComfyUI does not say which LoRA files {loader} can load."
            )
        field = value = None
    else:
        loader, field, value = _inserted_loader(adapter, object_info, clip is not None)
    spec = object_info.get(loader) or {}
    outputs = spec.get("output") if isinstance(spec.get("output"), list) else []
    sources = {"MODEL": plan["model"], "CLIP": clip}
    for kind, wire in (("MODEL", "model"), ("CLIP", "clip"))[: 2 if clip else 1]:
        if kind not in outputs:
            raise LookupError(
                f"{loader} on this ComfyUI hands on no {kind}, so PixlStash "
                "cannot wire it in."
            )
        # Its inputs are checked too, not only its outputs: a fork naming them
        # something else would take the wiring and fail in ComfyUI's own
        # validation, after the run was queued - the failure this whole
        # pre-flight exists to happen before.
        if find_input_spec(spec, wire) is None:
            raise LookupError(
                f"{loader} on this ComfyUI takes no {wire} input, so PixlStash "
                "cannot wire it in."
            )
    for rewire in plan.get("rewires") or []:
        source = sources.get(rewire["type"])
        node = prompt_graph.get(rewire["node_id"])
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if source is None or not isinstance(inputs, dict):
            current = None
        else:
            current = inputs.get(rewire["field"])
        if source is None or current != [source["node_id"], source["output"]]:
            raise LookupError(
                f"Node {rewire['node_id']} no longer reads its {rewire['type']} "
                "where PixlStash planned the LoRA loader."
            )
    node_id = str(
        max((int(k) for k in prompt_graph if str(k).isdigit()), default=0) + 1
    )
    inputs = _widget_defaults(spec)
    if field is not None:
        inputs[field] = value
    inputs["model"] = [plan["model"]["node_id"], plan["model"]["output"]]
    if clip:
        inputs["clip"] = [clip["node_id"], clip["output"]]
    prompt_graph[node_id] = {
        "class_type": loader,
        "inputs": inputs,
        "_meta": {"title": "LoRA (added by PixlStash)"},
    }
    for rewire in plan.get("rewires") or []:
        prompt_graph[rewire["node_id"]]["inputs"][rewire["field"]] = [
            node_id,
            outputs.index(rewire["type"]),
        ]
    return {"node_id": node_id, "class_type": loader}


def bypass_node(prompt_graph: dict, node_id: str, object_info: dict) -> None:
    """Take one node out of the chain, wiring its consumers to its own inputs.

    This is ComfyUI's own bypass - a node set to mode 4 - carried out on the
    API-format graph rather than in the editor: each output is answered by the
    node's **first input of the same type**, so a ``LoraLoader``'s MODEL and
    CLIP consumers read the checkpoint directly and the adapter is simply never
    applied. The inverse of :func:`plan_lora_insertion` / :func:`insert_adapter`,
    which splice a loader in, and typed from ``object_info`` for the same reason
    they are: a link in an API graph is ``[node_id, output_index]`` with no type
    on it, and a consumer's input name is a guess.

    Mutates *prompt_graph* in place, and only once every consumer has been
    checked: a graph half rewired around a node that is still there is worse
    than one that refused.

    Args:
        prompt_graph: The API-format graph to mutate.
        node_id: The node to take out.
        object_info: The map from :func:`fetch_object_info`.

    Raises:
        LookupError: When the node is not in the graph, when this ComfyUI does
            not say what it hands on, or when something reads an output no
            input of the same type can stand in for - which would leave that
            consumer wired to nothing.
    """
    node = (prompt_graph or {}).get(node_id)
    if not isinstance(node, dict):
        raise LookupError(f"Node {node_id} is not in this graph.")
    class_type = node.get("class_type")
    spec = object_info.get(class_type)
    outputs = spec.get("output") if isinstance(spec, dict) else None
    if not isinstance(outputs, list):
        raise LookupError(
            f"This ComfyUI does not say what {class_type} (node {node_id}) hands "
            "on, so PixlStash cannot tell what would take its place."
        )
    inputs = node.get("inputs")
    inputs = inputs if isinstance(inputs, dict) else {}
    # Output index -> the link that answers it once this node is gone. A widget
    # value cannot: what is being replaced is a connection.
    passthrough: dict[int, list] = {}
    for index, out_type in enumerate(outputs):
        for field, value in inputs.items():
            if not is_link(value):
                continue
            declared = find_input_spec(spec, field)
            if declared is not None and declared[0] == out_type:
                passthrough[index] = value
                break
    rewires: list[tuple[dict, str, list]] = []
    for other_id, other in (prompt_graph or {}).items():
        other_inputs = other.get("inputs") if isinstance(other, dict) else None
        if not isinstance(other_inputs, dict):
            continue
        for field, value in other_inputs.items():
            if not is_link(value) or value[0] != node_id:
                continue
            if value[1] not in passthrough:
                raise LookupError(
                    f"Node {other_id} reads output {value[1]} of {class_type} "
                    f"(node {node_id}), which takes no input of the same kind, "
                    "so there is nothing to put in its place."
                )
            rewires.append((other_inputs, field, passthrough[value[1]]))
    for other_inputs, field, link in rewires:
        other_inputs[field] = list(link)
    del prompt_graph[node_id]


# ── The LoRA chain (#1478) ──────────────────────────────────────────────────
#
# A LoRA loader patches what the model source hands out and everything below it
# reads the result, so the loaders between the source and the sampler are a
# CHAIN and their order is the order a run applies them. The editor reads that
# chain (`read_lora_chain`), the owner rearranges it, and the whole new chain is
# planned (`plan_lora_chain`) and carried out (`apply_lora_chain`) in one go, so
# four gestures make one new workflow rather than four. A deleted loader goes
# through `bypass_node`, the removal primitive the insertion pair's inverse
# already is; a new one is `_inserted_loader`'s choice at `_widget_defaults`, as
# `insert_adapter` makes it. Every link is typed from `object_info`, never from
# an input's name, for the reason the insertion planner gives.

# A stacker spells its second and later slot with a number. Its slots are one
# node, so they cannot be put in order against other loaders.
_NUMBERED_LORA_FIELD_RE = re.compile(r"^lora_name_\d+$")


def lora_display_name(value: str) -> str:
    """A LoRA as the owner reads it: the file's basename, without its extension."""
    base = _normalize_filename(str(value or "")).rsplit("/", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    if dot and stem and f".{ext.lower()}" in MODEL_EXTENSIONS:
        return stem
    return base


def _node_order_key(node_id: str) -> tuple:
    """Numeric ids in number order, a subgraph id (``75:83``) after them."""
    text = str(node_id)
    return (0, int(text), text) if text.isdigit() else (1, 0, text)


def _input_of_type(node_spec: dict, kind: str) -> str | None:
    """The first input *node_spec* declares as *kind*, or ``None``."""
    inputs = node_spec.get("input") if isinstance(node_spec, dict) else None
    for group in ("required", "optional"):
        for field, entry in ((inputs or {}).get(group) or {}).items():
            if isinstance(entry, (list, tuple)) and entry and entry[0] == kind:
                return field
    return None


def _chain_loader(node_id: str, node: dict, object_info: dict) -> dict | None:
    """This node as a link of the chain, ``None`` when it loads no LoRA slot.

    A node counts when it carries exactly one LoRA slot
    :func:`detect_lora_targets` can read, and ComfyUI says it takes a MODEL in
    and hands one on - which is what makes it a link rather than a leaf.

    Anything else that loads a LoRA - a stacker with several slots, a node
    that takes no model in (a character prompt builder), one with nothing
    wired into its model - is ``None`` too: an ordinary node of the graph,
    which loaders can go in front of or after, but not one this chain edits.

    A class this ComfyUI lacks is ``None`` as well: its wiring cannot be read,
    so it cannot be moved, but loaders can still go around it.
    """
    inputs = node["inputs"]
    class_type = node.get("class_type")
    slots = detect_lora_targets({node_id: node})
    numbered = [f for f in inputs if _NUMBERED_LORA_FIELD_RE.match(str(f))]
    if not slots and not numbered:
        return None
    if numbered or len(slots) != 1:
        logger.info(
            "Node %s (%s) holds several LoRAs, so it is left in the graph as it "
            "is rather than edited as a link of the chain.",
            node_id,
            class_type,
        )
        return None
    spec = object_info.get(class_type)
    if not isinstance(spec, dict):
        logger.info(
            "Node %s (%s) loads a LoRA and this ComfyUI does not have its class, "
            "so it is left in the graph as it is rather than edited as a link.",
            node_id,
            class_type,
        )
        return None
    outputs = spec.get("output") if isinstance(spec.get("output"), list) else []
    model_field = _input_of_type(spec, "MODEL")
    clip_field = _input_of_type(spec, "CLIP")
    if (
        model_field is None
        or "MODEL" not in outputs
        or (clip_field is not None and "CLIP" not in outputs)
        or any(
            field is not None and not is_link(inputs.get(field))
            for field in (model_field, clip_field)
        )
    ):
        logger.info(
            "Node %s (%s) loads a LoRA but is not wired as a LoRA loader is, so "
            "it is left in the graph as it is rather than edited as a link.",
            node_id,
            class_type,
        )
        return None
    slot = slots[0]
    return {
        **slot,
        "name": lora_display_name(slot["value"]),
        "model_field": model_field,
        "clip_field": clip_field,
        "model_out": outputs.index("MODEL"),
        "clip_out": outputs.index("CLIP") if clip_field is not None else None,
    }


def _walk_chain(
    head: str,
    loaders: dict,
    readers: dict,
    wire: str,
    out: str,
) -> list[str]:
    """The loaders from *head* on, each the only reader of the one before.

    **A branch ends the chain.** When a loader's output is read by the next
    loader AND by something else (a second sampler pass reading the model
    before its extra LoRA), the chain stops at that loader: everything after
    the branch reads it, so a LoRA added at the end reaches both, and the
    loaders past the branch are left as ordinary nodes.
    """
    order = [head]
    current = head
    while True:
        read_by = readers.get((current, loaders[current][out]), [])
        chained = [
            link
            for link in read_by
            if link["node_id"] in loaders
            and link["node_id"] not in order
            and link["field"] == loaders[link["node_id"]][wire]
        ]
        if not chained or len(read_by) > 1:
            return order
        current = chained[0]["node_id"]
        order.append(current)


def _readers_named(sinks: list[dict], rail: bool) -> tuple[str, int]:
    """The nodes behind *sinks* in a few words, and how many there are.

    A node reading only CLIP off the chain is a text encoder - by what it reads,
    not by its class name - and several are counted rather than listed.
    ``rail`` spells a node ``KSampler #7``, otherwise ``#7 KSampler``.
    """
    nodes: dict[str, dict] = {}
    for sink in sinks:
        entry = nodes.setdefault(
            sink["node_id"], {"cls": str(sink["class_type"] or "node"), "types": set()}
        )
        entry["types"].add(sink["type"])

    def named(node_id: str) -> str:
        cls = nodes[node_id]["cls"]
        return f"{cls} #{node_id}" if rail else f"#{node_id} {cls}"

    encoders = [n for n, entry in nodes.items() if entry["types"] == {"CLIP"}]
    parts = [named(n) for n in nodes if n not in encoders]
    if len(encoders) > 1:
        parts.append(f"{len(encoders)} text encoders")
    elif encoders:
        parts.append(named(encoders[0]))
    joined = (
        " and ".join(parts)
        if len(parts) < 3
        else (", ".join(parts[:-1]) + f" and {parts[-1]}")
    )
    return joined, len(nodes)


def _readers_phrase(sinks: list[dict], kind: str, noun: str) -> str | None:
    """``"KSampler #7 reads model"`` / ``"2 text encoders read clip"``."""
    of_kind = [sink for sink in sinks if sink["type"] == kind]
    if not of_kind:
        return None
    joined, count = _readers_named(of_kind, rail=True)
    return f"{joined} {'reads' if count == 1 else 'read'} {noun}"


def _sink_summary(sinks: list[dict]) -> str | None:
    """``KSampler #7 reads model · 2 text encoders read clip``, or ``None``."""
    summary = " · ".join(
        phrase
        for phrase in (
            _readers_phrase(sinks, "MODEL", "model"),
            _readers_phrase(sinks, "CLIP", "clip"),
        )
        if phrase
    )
    return summary or None


def _pass_of(graph: dict, links: list[dict], starts: list[str]) -> dict:
    """The sampler a lane's model reaches, as the owner names that pass.

    Searched breadth first down every link from the lane's readers, nearest
    first, for a node whose class is a sampler (``KSampler``,
    ``SamplerCustomAdvanced``): a model patch, a guider or a scheduler is on the
    way to one, and the pass is named by what samples it. A lane that reaches
    no sampler (a face detailer) is named by the first node its model stops at.
    ``title`` is the node's ComfyUI title when the owner gave it one, ``None``
    when it is only the class name again.
    """
    onward: dict[str, set[str]] = {}
    for node_id, node in graph.items():
        for value in ((node or {}).get("inputs") or {}).values():
            if is_link(value):
                onward.setdefault(str(value[0]), set()).add(str(node_id))
    seen: set[str] = set()
    level = sorted({str(n) for n in starts}, key=_node_order_key)
    found = None
    while level and found is None:
        seen.update(level)
        found = next(
            (n for n in level if _samples((graph.get(n) or {}).get("class_type"))),
            None,
        )
        level = sorted(
            {m for n in level for m in onward.get(n, ()) if m not in seen},
            key=_node_order_key,
        )
    if found is None:
        # No sampler downstream: follow the model while it goes one way.
        found = str(sorted(starts, key=_node_order_key)[0])
        visited: set[str] = set()
        while found not in visited:
            visited.add(found)
            model_onward = {
                link["node_id"]
                for link in links
                if link["type"] == "MODEL" and str(link["source"][0]) == found
            }
            if len(model_onward) != 1:
                break
            found = next(iter(model_onward))
    node = graph.get(found) or {}
    class_type = node.get("class_type")
    title = str((node.get("_meta") or {}).get("title") or "").strip()
    return {
        "node_id": found,
        "class_type": class_type,
        "title": title if title and title != class_type else None,
    }


def _samples(class_type: Any) -> bool:
    """Whether *class_type* reads as a sampler a pass is named by.

    By name, since ``object_info`` does not say. ``Unsampler`` (an inversion
    step) reads the model on the way to the real sampler and is not one.
    ``KSamplerSelect`` and its kin take no link, so a search down the links
    never reaches them.
    """
    name = str(class_type or "").lower()
    return "sampler" in name and not name.startswith("unsampler")


def pass_label(lane: dict) -> str:
    """A lane as the owner reads it: its sampler's title, else ``KSampler #15``."""
    found = lane["pass"]
    return found.get("title") or (
        f"{found.get('class_type') or 'node'} #{found['node_id']}"
    )


def _model_downstream(links: list[dict], node_id: str) -> set[str]:
    """Every node the model handed on by *node_id* reaches."""
    found: set[str] = set()
    pending = [str(node_id)]
    while pending:
        current = pending.pop()
        for link in links:
            if (
                link["type"] == "MODEL"
                and str(link["source"][0]) == current
                and link["node_id"] not in found
            ):
                found.add(link["node_id"])
                pending.append(link["node_id"])
    return found


def read_lora_chain(prompt_graph: dict, object_info: dict) -> dict:
    """The graph's LoRA loaders in the order a run applies them, typed by ComfyUI.

    The chain runs from an **anchor** - the output the first loader reads, or
    with no loader the model source :func:`plan_lora_insertion` would splice
    after - through each loader in turn to the **sinks**, every input reading
    the last loader's output. The CLIP chain is read the same way over the
    loaders that carry one, and must pass them in the same order the MODEL
    chain does. A model-only loader sits in the MODEL chain alone.

    **Where the model goes several ways, the chain is a tree.** The loaders
    every pass reads are the trunk (``loaders``), and each node reading the
    fork - a base sampler, a hires pass, a detailer - starts a **lane** of its
    own with the loaders only it reads. A fork straight at the anchor has an
    empty trunk. A workflow loading **several models** (Wan 2.2's high and low
    noise) has no trunk at all: ``model_source`` is ``None`` and each lane
    starts at its own model. A lane's CLIP, when its loaders carry one, must
    come off the trunk's CLIP end.

    Returns:
        ``{"model_source": {"node_id", "class_type", "output"} or None,
        "clip_source": … or None, "loaders": [{…detect_lora_targets slot,
        "name", "model_field", "clip_field", "model_out", "clip_out"}, …],
        "sinks": [{"node_id", "class_type", "field", "type"}, …],
        "sink_summary": str or None, "lanes": [{"source": … or None,
        "pass": {"node_id", "class_type", "title"}, "loaders", "sinks",
        "sink_summary"}, …], "branch_note": str or None}``. ``lanes`` is
        empty for a straight chain; for a tree the trunk's ``sinks`` are only
        what reads its CLIP, since every reader of its model is a lane.

    Raises:
        LookupError: With the owner-facing sentence, when the chain cannot be
            edited honestly: no model source, a link neither end can type,
            loaders that do not start from one place, a second model that
            itself goes several ways, CLIP passed in another order than the
            model, or a loader output something else reads. A node loading a
            LoRA some other way (a stacker, rgthree's dicts, a prompt tag, a
            character prompt builder) is not one: it stays in the graph as an
            ordinary node, so a chain can always be built in the MODEL path
            around it.
    """
    graph = _live_graph(prompt_graph or {}, object_info)
    loaders: dict[str, dict] = {}
    for raw_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        node_id = str(raw_id)
        loader = _chain_loader(node_id, node, object_info)
        if loader is not None:
            loaders[node_id] = loader
    links = _model_links(graph, object_info)
    roots = [(str(node_id), output) for node_id, output in _sources(links, "MODEL")]
    if not roots:
        raise LookupError(
            "PixlStash could not find the model this workflow loads, so there is "
            "no chain of LoRAs to edit."
        )
    # Any OTHER output of a loader being read would be dropped by a move.
    for raw_id, node in graph.items():
        for field, value in ((node or {}).get("inputs") or {}).items():
            if not is_link(value) or str(value[0]) not in loaders:
                continue
            loader = loaders[str(value[0])]
            if value[1] not in (loader["model_out"], loader["clip_out"]):
                raise LookupError(
                    f"Node {raw_id} reads output {value[1]} of loader "
                    f"#{value[0]}, which is not its model or its CLIP, so "
                    "PixlStash cannot move that loader. Change it in ComfyUI."
                )
    readers: dict[tuple, list[dict]] = {}
    for link in links:
        readers.setdefault((str(link["source"][0]), link["source"][1]), []).append(link)

    def link_of(end: tuple) -> dict:
        return {
            "node_id": end[0],
            "class_type": graph[end[0]].get("class_type"),
            "output": end[1],
        }

    def input_of(node_id: str, wire: str) -> tuple:
        source = graph[node_id]["inputs"][loaders[node_id][wire]]
        return (str(source[0]), source[1])

    def readers_by_node(end: tuple) -> dict[str, list[dict]]:
        by_node: dict[str, list[dict]] = {}
        for link in readers.get(end, []):
            by_node.setdefault(link["node_id"], []).append(link)
        return by_node

    def run_from(by_node: dict[str, list[dict]]) -> list[str]:
        """The loaders from the lone reader in *by_node* on, if it is a loader."""
        if len(by_node) != 1:
            return []
        node_id, reads = next(iter(by_node.items()))
        if node_id not in loaders or not any(
            link["field"] == loaders[node_id]["model_field"] for link in reads
        ):
            return []
        return _walk_chain(node_id, loaders, readers, "model_field", "model_out")

    trunk: list[str] = []
    # Each lane as read: where it starts, its loaders, and - for a lane with no
    # loader off a shared fork - the one node that reads the fork for it.
    lanes: list[dict] = []
    if len(roots) > 1:
        model_source = None
        fork = None
        for root in roots:
            by_node = readers_by_node(root)
            if len(by_node) > 1:
                raise LookupError(
                    f"This workflow loads {len(roots)} models, and the one from "
                    f"#{root[0]} {graph[root[0]].get('class_type')} goes several "
                    "ways, so there is no single chain of LoRAs to edit for it. "
                    "Change them in ComfyUI."
                )
            lanes.append(
                {
                    "source": link_of(root),
                    "start": root,
                    "order": run_from(by_node),
                    "own": None,
                }
            )
    else:
        anchor = roots[0]
        if loaders:
            heads = [n for n in loaders if input_of(n, "model_field")[0] not in loaders]
            starts = {input_of(n, "model_field") for n in heads}
            # The chain is the run of loaders straight after the model source; a
            # lone run elsewhere on the path (after a model patch) is one too,
            # and so are several runs off the same node (a fork before any
            # loader).
            at_root = any(input_of(n, "model_field")[0] == anchor[0] for n in heads)
            if not at_root and len(starts) == 1:
                anchor = next(iter(starts))
            elif not at_root:
                raise LookupError(
                    "The LoRA loaders in this workflow do not form one chain "
                    "between the model and the sampler, so there is no single "
                    "order to edit. Change them in ComfyUI."
                )
        model_source = link_of(anchor)
        fork = anchor
        by_node = readers_by_node(anchor)
        if len(by_node) == 1:
            trunk = run_from(by_node)
            if trunk:
                fork = (trunk[-1], loaders[trunk[-1]]["model_out"])
                by_node = readers_by_node(fork)
        if len(by_node) > 1:
            # A lane per loader run off the fork, and one per sampler for the
            # readers that are not loaders: a guider and a scheduler reading
            # the same model are one pass (SamplerCustomAdvanced's), while a
            # base and a hires sampler reading it straight are two.
            plain: dict[str, set[str]] = {}
            for node_id in by_node:
                order = run_from({node_id: by_node[node_id]})
                if not order:
                    sampler = _pass_of(graph, links, [node_id])["node_id"]
                    plain.setdefault(sampler, set()).add(node_id)
                    continue
                lanes.append(
                    {"source": None, "start": fork, "order": order, "own": None}
                )
            for own in plain.values():
                lanes.append({"source": None, "start": fork, "order": [], "own": own})
            if len(lanes) < 2:
                lanes = []

    members = set(trunk) | {n for lane in lanes for n in lane["order"]}
    off_chain = sorted(set(loaders) - members, key=_node_order_key)
    if off_chain:
        logger.info(
            "LoRA loaders %s are past the chain's end, so they are left as "
            "ordinary nodes rather than edited.",
            off_chain,
        )

    def clip_run(order: list[str]) -> list[str]:
        """The loaders of *order* carrying a CLIP, checked to pass it in order."""
        carriers = [n for n in order if loaders[n]["clip_field"] is not None]
        if not carriers:
            return []
        subset = {n: loaders[n] for n in carriers}
        heads = [n for n in carriers if input_of(n, "clip_field")[0] not in subset]
        walked = (
            _walk_chain(heads[0], subset, readers, "clip_field", "clip_out")
            if len(heads) == 1
            else []
        )
        if walked != carriers:
            raise LookupError(
                "The model and the CLIP pass through this workflow's LoRA loaders "
                "in different orders, so moving one would reorder only half of "
                "it. Change the chain in ComfyUI."
            )
        return carriers

    model_readers = {link["node_id"] for link in links if link["type"] == "MODEL"}

    def refuse_cycle(source: dict | None) -> None:
        # A loader would feed a node its own CLIP input comes through: a cycle
        # ComfyUI refuses, after the run is queued.
        if source is not None and source["node_id"] in model_readers:
            raise LookupError(
                f"#{source['node_id']} {source['class_type']} hands out "
                "this workflow's CLIP and reads its model, so a LoRA loader cannot "
                "sit in front of both. Change the chain in ComfyUI."
            )

    trunk_clip = clip_run(trunk)
    if trunk_clip:
        clip_source = link_of(input_of(trunk_clip[0], "clip_field"))
        clip_end = (trunk_clip[-1], loaders[trunk_clip[-1]]["clip_out"])
    else:
        if model_source is None:
            # A model per pass may come with a text encoder per pass (SDXL's
            # base and refiner checkpoints). Each lane then starts its CLIP
            # where its own loaders read it; one shared source is only the
            # fallback for a lane that has none.
            clip_roots = _sources(links, "CLIP")
            clip_source = (
                link_of((str(clip_roots[0][0]), clip_roots[0][1]))
                if len(clip_roots) == 1
                else None
            )
        else:
            clip_source = _source_of(graph, links, "CLIP")
        refuse_cycle(clip_source)
        clip_end = (
            (str(clip_source["node_id"]), clip_source["output"])
            if clip_source is not None
            else None
        )

    def sinks_of(end: tuple | None, own: set[str] | None = None) -> list[dict]:
        return [
            {key: link[key] for key in ("node_id", "class_type", "field", "type")}
            for link in (readers.get(end, []) if end is not None else [])
            if link["node_id"] not in members
            and (own is None or link["node_id"] in own)
        ]

    def in_order(sinks: list[dict]) -> list[dict]:
        return sorted(
            sinks,
            key=lambda s: (
                s["type"] != "MODEL",
                _node_order_key(s["node_id"]),
                s["field"],
            ),
        )

    model_end = (trunk[-1], loaders[trunk[-1]]["model_out"]) if trunk else fork
    sinks = in_order((sinks_of(model_end) if not lanes else []) + sinks_of(clip_end))
    notes: list[str] = []
    claimed: set[str] = set()

    def note_for(end: str, end_sinks: list[dict]) -> None:
        past = [
            n
            for n in off_chain
            if n not in claimed and n in _model_downstream(links, end)
        ]
        claimed.update(past)
        note = _branch_note(graph, end, past, end_sinks)
        if note:
            notes.append(note)

    read_lanes: list[dict] = []
    for lane in lanes:
        order = lane["order"]
        carriers = clip_run(order)
        lane_clip = clip_end
        if carriers and lane["source"] is not None:
            lane_clip = input_of(carriers[0], "clip_field")
            refuse_cycle(link_of(lane_clip))
        if carriers and input_of(carriers[0], "clip_field") != lane_clip:
            raise LookupError(
                "The LoRAs on one side of this workflow's fork take their CLIP "
                "from somewhere other than the chain they branch off, so "
                "PixlStash cannot tell where a moved loader's CLIP should come "
                "from. Change them in ComfyUI."
            )
        end = (order[-1], loaders[order[-1]]["model_out"]) if order else lane["start"]
        lane_sinks = in_order(
            sinks_of(end, None if order else lane["own"])
            + (
                sinks_of((carriers[-1], loaders[carriers[-1]]["clip_out"]))
                if carriers
                else []
            )
        )
        starts = [s["node_id"] for s in lane_sinks if s["type"] == "MODEL"] or [end[0]]
        read_lanes.append(
            {
                "source": lane["source"],
                # Where a lane with a model of its own starts its CLIP; a lane
                # off the trunk starts at the trunk's CLIP end, wherever the
                # edit leaves it.
                "clip_source": link_of(lane_clip) if lane_clip is not None else None,
                "pass": _pass_of(graph, links, starts),
                "loaders": [loaders[n] for n in order],
                "sinks": lane_sinks,
                "sink_summary": _sink_summary(lane_sinks),
            }
        )
        note_for(
            order[-1] if order else min(lane["own"] or {end[0]}, key=_node_order_key),
            lane_sinks,
        )
    if not lanes and off_chain:
        note_for(model_end[0], sinks)
    named = [lane["pass"]["node_id"] for lane in read_lanes]
    for node_id in named:
        if named.count(node_id) > 1:
            # Two branches that meet again (a model merge) are one pass, and
            # a move "to KSampler #3 only" would name both sides alike.
            raise LookupError(
                f"The branches of this workflow's model meet again at #{node_id} "
                f"{graph[node_id].get('class_type')}, so there are no separate "
                "passes to edit their LoRAs by. Change them in ComfyUI."
            )
    # "In the order ComfyUI runs them" as near as ids say it: the base sampler
    # is normally the lower id.
    read_lanes.sort(key=lambda lane: _node_order_key(lane["pass"]["node_id"]))
    return {
        "model_source": model_source,
        "clip_source": clip_source,
        "loaders": [loaders[n] for n in trunk],
        "sinks": sinks,
        "sink_summary": _sink_summary(sinks),
        "lanes": read_lanes,
        "branch_note": " ".join(notes) or None,
    }


def _branch_note(
    graph: dict, end: str, off_chain: list[str], sinks: list[dict]
) -> str | None:
    """Why a chain stops at *end*, when loaders lie past it.

    Said rather than left for the owner to puzzle over: the list shows fewer
    loaders than the workflow has, and the reason is either a node that is not
    a loader in between, or a further branch - the model goes several ways
    from *end*, so a LoRA added there reaches every one of them, while one
    past the branch reaches only its own side.
    """
    if not off_chain:
        return None
    readers = [sink for sink in sinks if sink["type"] == "MODEL"]
    named, count = _readers_named(readers, rail=False)
    past = " and ".join(
        f"#{n} {graph[n].get('class_type')}" for n in off_chain if n in graph
    )
    one = len(off_chain) == 1
    if count < 2:
        return (
            f"{past} {'is' if one else 'are'} further along the model path, "
            f"past #{end} {graph[end].get('class_type')}, so "
            f"{'it is' if one else 'they are'} left as "
            f"{'it is' if one else 'they are'}; change {'it' if one else 'them'} "
            "in ComfyUI."
        )
    return (
        f"The chain stops at #{end} {graph[end].get('class_type')}, because its "
        f"model goes {count} ways from there ({named}). A LoRA added here reaches "
        f"all of them. {past} {'is' if one else 'are'} past the branch and "
        f"{'reaches' if one else 'reach'} only {'its' if one else 'their'} own "
        f"side, so {'it is' if one else 'they are'} left as "
        f"{'it is' if one else 'they are'}; change {'it' if one else 'them'} in "
        "ComfyUI."
    )


def read_lora_chain_untyped(
    prompt_graph: dict, object_info: dict | None = None
) -> dict:
    """The loaders as a best-effort list, for a chain that cannot be edited.

    Follows each loader's ``model`` input by NAME, which is the guess
    :func:`read_lora_chain` exists not to make - so this is only ever shown
    read-only, beside the sentence saying why it could not be edited. Every
    LoRA slot is listed, a stacker's numbered ones included: a list that left
    one out would be the silent drop #1478 is about.

    With *object_info* (ComfyUI answered, and the chain was refused for its
    shape) the two ends are read typed as well, best effort: the model source
    when no loader named one, and the nodes reading the chain's end, so the
    read-only view names what the chain runs between.

    Returns:
        The shape :func:`read_lora_chain` returns, with ``clip_source`` ``None``,
        and no sinks or summary unless *object_info* could type them.
    """
    graph = prompt_graph or {}
    slots: dict[str, list[dict]] = {}
    for raw_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        found = detect_lora_targets({str(raw_id): node})
        if found:
            slots[str(raw_id)] = [
                {**slot, "name": lora_display_name(slot["value"])} for slot in found
            ]

    def upstream(node_id: str) -> str | None:
        link = graph[node_id]["inputs"].get("model")
        return str(link[0]) if is_link(link) else None

    followers: dict[str | None, list[str]] = {}
    for node_id in sorted(slots, key=_node_order_key):
        up = upstream(node_id)
        followers.setdefault(up if up in slots else None, []).append(node_id)
    order: list[str] = []
    pending = list(reversed(followers.get(None, [])))
    while pending:
        node_id = pending.pop()
        if node_id in order:
            continue
        order.append(node_id)
        pending.extend(reversed(followers.get(node_id, [])))
    # A cycle of loaders reading each other has no head; they are still listed.
    order += [n for n in sorted(slots, key=_node_order_key) if n not in order]
    # Only the loaders ON the model path say where it runs: a LoRA slot on a
    # node that takes no model (a prompt builder) is listed, but is no end.
    on_path = [n for n in order if is_link(graph[n]["inputs"].get("model"))]
    model_source = None
    if on_path:
        link = graph[on_path[0]]["inputs"].get("model")
        if is_link(link) and isinstance(graph.get(link[0]), dict):
            model_source = {
                "node_id": str(link[0]),
                "class_type": graph[link[0]].get("class_type"),
                "output": link[1],
            }
    sinks: list[dict] = []
    if object_info is not None:
        try:
            links = _model_links(graph, object_info)
            if model_source is None:
                model_source = _source_of(graph, links, "MODEL")
        except LookupError as exc:
            logger.info("The ends of a read-only LoRA chain cannot be typed: %s", exc)
            links = []
        # What the chain's end hands on: the last loader's outputs, or the model
        # source's own when there is no loader to follow.
        end = on_path[-1] if on_path else (model_source or {}).get("node_id")
        sinks = sorted(
            (
                {key: link[key] for key in ("node_id", "class_type", "field", "type")}
                for link in links
                if str(link["source"][0]) == str(end)
                and link["type"] in ("MODEL", "CLIP")
                and link["node_id"] not in slots
            ),
            key=lambda s: (
                s["type"] != "MODEL",
                _node_order_key(s["node_id"]),
                s["field"],
            ),
        )
    return {
        "model_source": model_source,
        "clip_source": None,
        "loaders": [slot for node_id in order for slot in slots[node_id]],
        "sinks": sinks,
        "sink_summary": _sink_summary(sinks),
        "lanes": [],
    }


def _strength_text(value: Any) -> str:
    return f"{float(value):.2f}"


def _strength_update(node_id: str, inputs: dict, strength: float) -> tuple[dict, Any]:
    """The widgets a new strength writes on an existing loader, and the old one.

    ``strength_clip`` follows only when it equalled ``strength_model``: a loader
    whose two strengths were set apart was set apart on purpose.

    Raises:
        LookupError: The loader has no strength widget, or takes it from a link.
    """
    widget = next((w for w in ("strength_model", "strength") if w in inputs), None)
    if widget is None:
        raise LookupError(f"Loader #{node_id} has no strength PixlStash can set.")
    old = inputs[widget]
    if isinstance(old, bool) or not isinstance(old, (int, float)):
        raise LookupError(
            f"Loader #{node_id} takes its strength from another node, so it "
            "cannot be set here."
        )
    if float(old) == strength:
        return {}, old
    fields = {widget: strength}
    clip = inputs.get("strength_clip")
    if (
        widget == "strength_model"
        and isinstance(clip, (int, float))
        and not isinstance(clip, bool)
        and float(clip) == float(old)
    ):
        fields["strength_clip"] = strength
    return fields, old


def _longest_increasing(values: list[int]) -> set[int]:
    """Indices of one longest strictly increasing subsequence of *values*."""
    best: list[list[int]] = []
    for i, value in enumerate(values):
        chain = [i]
        for j in range(i):
            if values[j] < value and len(best[j]) + 1 > len(chain):
                chain = best[j] + [i]
        best.append(chain)
    return set(max(best, key=len)) if best else set()


def _move_changes(old: list[str], new: list[str], names: dict) -> list[dict]:
    """How the surviving loaders moved, in graph terms (``#22 and #31 swapped``)."""
    survivors = [n for n in new if n in old]
    before = [n for n in old if n in survivors]
    if survivors == before:
        return []
    moved = [i for i in range(len(before)) if before[i] != survivors[i]]
    if len(moved) == 2:
        earlier, later = before[moved[0]], before[moved[1]]
        first = survivors[moved[0]]
        return [
            {
                "kind": "moved",
                "node_id": first,
                "text": (
                    f"#{earlier} and #{later} swapped: {names[first]} now applies first"
                ),
            }
        ]
    kept = _longest_increasing([before.index(n) for n in survivors])
    changes = []
    for index, node_id in enumerate(survivors):
        if index in kept:
            continue
        position = new.index(node_id)
        where = (
            "now applies first"
            if position == 0
            else f"now applies after #{new[position - 1]} {names[new[position - 1]]}"
        )
        changes.append(
            {
                "kind": "moved",
                "node_id": node_id,
                "text": f"#{node_id} {names[node_id]} moved: {where}",
            }
        )
    return changes


def _rewired_change(sinks: list[dict]) -> dict | None:
    """``#7 KSampler and 2 text encoders rewired``, or ``None`` for no reader."""
    if not sinks:
        return None
    joined, _count = _readers_named(sinks, rail=False)
    return {
        "kind": "rewired",
        "node_id": sinks[0]["node_id"],
        "text": f"{joined} rewired",
    }


def _wire_segment(
    work: dict | None,
    order: list[str],
    wiring: dict,
    model_link: list | None,
    clip_link: list | None,
) -> tuple[list | None, list | None]:
    """Chain *order* one loader after another from the two links; the ends.

    Each loader reads the one before it (*model_link* / *clip_link* for the
    first), and the answer is what the last hands on. With *work* ``None``
    nothing is written, which is how the planner asks where a segment ends.
    """
    for node_id in order:
        wire = wiring[node_id]
        if work is not None:
            inputs = work[node_id]["inputs"]
            inputs[wire["model_field"]] = list(model_link)
            if wire["clip_field"]:
                inputs[wire["clip_field"]] = list(clip_link)
        model_link = [node_id, wire["model_out"]]
        if wire["clip_field"]:
            clip_link = [node_id, wire["clip_out"]]
    return model_link, clip_link


def plan_lora_chain(
    prompt_graph: dict,
    chain: dict,
    entries: list[dict],
    object_info: dict,
    lanes: list[list[dict]] | None = None,
) -> dict:
    """Validate the chain the owner left and say what it changes, writing nothing.

    Every refusal is found here, before :func:`apply_lora_chain` touches the
    graph: a half-rewired graph is worse than a refusal.

    Args:
        prompt_graph: The graph *chain* was read from.
        chain: :func:`read_lora_chain`'s answer for it.
        entries: The trunk in apply order. ``{"node_id": str, "strength":
            float | None}`` keeps an existing loader (moved and re-weighted as
            it lands); ``{"node_id": None, "adapter": {"sha256", "filenames"},
            "strength": float | None, "name": str | None}`` adds one.
        object_info: The map *chain* was typed with.
        lanes: One list of entries per lane of *chain*, in its order, shaped
            like *entries*. An existing loader may land in any segment, which
            is how one crosses the fork. ``None`` keeps every lane as read.
            An existing loader missing from every list is deleted.

    Returns:
        The plan :func:`apply_lora_chain` carries out, with ``changes`` - the
        owner-facing list, empty when nothing would change.

    Raises:
        LookupError: An unknown or repeated loader, a strength that cannot be
            written, a new LoRA no loader on this ComfyUI can carry, lanes
            that do not match the chain's, or a trunk loader in a workflow
            whose models have no shared stretch.
    """
    graph = prompt_graph or {}
    read_lanes = chain.get("lanes") or []
    if lanes is None:
        # Every pass as read, less any loader *entries* already moved into the
        # trunk: that is a crossing, not a loader listed twice.
        listed = {str(e["node_id"]) for e in entries if e.get("node_id") is not None}
        lanes = [
            [
                {"node_id": loader["node_id"]}
                for loader in lane["loaders"]
                if loader["node_id"] not in listed
            ]
            for lane in read_lanes
        ]
    if len(lanes) != len(read_lanes):
        raise LookupError(
            f"This workflow's model goes {len(read_lanes) or 1} "
            f"{'way' if len(read_lanes) < 2 else 'ways'}, and the edit names "
            f"{len(lanes)} passes. Open Edit LoRAs again."
        )
    if chain.get("model_source") is None and entries:
        raise LookupError(
            "This workflow loads a model for each pass, so no LoRA can go before "
            "them all. Add it to one pass."
        )
    everyone = "both passes" if len(read_lanes) == 2 else "every pass"
    segments = [
        {
            "entries": entries,
            "old": [loader["node_id"] for loader in chain["loaders"]],
            "with_clip": chain.get("clip_source") is not None,
            "clip_read": any(s["type"] == "CLIP" for s in chain["sinks"]),
            "label": everyone,
        }
    ] + [
        {
            "entries": lane_entries,
            "old": [loader["node_id"] for loader in lane["loaders"]],
            # A new loader in a lane carries a CLIP only where something in
            # that lane reads one; elsewhere its CLIP half would reach nothing.
            "with_clip": any(s["type"] == "CLIP" for s in lane["sinks"]),
            "clip_read": any(s["type"] == "CLIP" for s in lane["sinks"]),
            # Who a CLIP half there reaches, said in the change: those text
            # encoders may well feed every pass, so "only" is the model's.
            "clip_names": _readers_named(
                [s for s in lane["sinks"] if s["type"] == "CLIP"], rail=False
            )[0],
            "label": pass_label(lane),
        }
        for lane_entries, lane in zip(lanes, read_lanes)
    ]
    loaders = {
        loader["node_id"]: loader
        for segment_loaders in [chain["loaders"]]
        + [lane["loaders"] for lane in read_lanes]
        for loader in segment_loaders
    }
    home = {
        node_id: index
        for index, segment in enumerate(segments)
        for node_id in segment["old"]
    }
    names = {node_id: loader["name"] for node_id, loader in loaders.items()}
    wiring = {
        node_id: {
            key: loader[key]
            for key in ("model_field", "clip_field", "model_out", "clip_out")
        }
        for node_id, loader in loaders.items()
    }
    next_id = max((int(k) for k in graph if str(k).isdigit()), default=0) + 1
    placed: set[str] = set()
    added: dict[str, dict] = {}
    widgets: dict[str, dict] = {}
    added_changes: list[dict] = []
    move_changes: list[dict] = []
    strength_changes: list[dict] = []
    for index, segment in enumerate(segments):
        order: list[str] = []
        segment["order"] = order
        for entry in segment["entries"]:
            node_id = entry.get("node_id")
            strength = entry.get("strength")
            if node_id is not None:
                node_id = str(node_id)
                if node_id not in loaders:
                    raise LookupError(f"This workflow has no LoRA loader #{node_id}.")
                if node_id in placed:
                    raise LookupError(
                        f"Loader #{node_id} is listed twice, and one loader can "
                        "only sit in one place in the chain."
                    )
                placed.add(node_id)
                order.append(node_id)
                if home[node_id] != index:
                    move_changes.append(
                        _crossed_change(node_id, names[node_id], segment, index, wiring)
                    )
                if strength is None:
                    continue
                fields, old = _strength_update(
                    node_id, graph[node_id]["inputs"], float(strength)
                )
                if fields:
                    widgets[node_id] = fields
                    strength_changes.append(
                        {
                            "kind": "strength",
                            "node_id": node_id,
                            "text": (
                                f"#{node_id} {names[node_id]} from "
                                f"{_strength_text(old)} to {_strength_text(strength)}"
                            ),
                        }
                    )
                continue
            adapter = entry.get("adapter")
            if not adapter:
                raise LookupError("A new loader needs a LoRA from your model shelf.")
            with_clip = segment["with_clip"]
            loader_class, field, value = _inserted_loader(
                adapter, object_info, with_clip
            )
            spec = object_info.get(loader_class) or {}
            outputs = spec.get("output") if isinstance(spec.get("output"), list) else []
            model_field = _input_of_type(spec, "MODEL")
            clip_field = _input_of_type(spec, "CLIP") if with_clip else None
            for kind, wire in (("MODEL", model_field), ("CLIP", clip_field))[
                : 2 if with_clip else 1
            ]:
                if wire is None or kind not in outputs:
                    raise LookupError(
                        f"{loader_class} on this ComfyUI does not take and hand on "
                        f"a {kind}, so PixlStash cannot wire it in."
                    )
            inputs = _widget_defaults(spec)
            inputs[field] = value
            if strength is not None:
                for widget in ("strength_model", "strength_clip", "strength"):
                    if widget in inputs:
                        inputs[widget] = float(strength)
            shown = inputs.get("strength_model", inputs.get("strength", 1.0))
            node_id = str(next_id)
            next_id += 1
            added[node_id] = {
                "class_type": loader_class,
                "inputs": inputs,
                "_meta": {"title": "LoRA (added by PixlStash)"},
            }
            wiring[node_id] = {
                "model_field": model_field,
                "clip_field": clip_field,
                "model_out": outputs.index("MODEL"),
                "clip_out": outputs.index("CLIP") if clip_field else None,
            }
            names[node_id] = entry.get("name") or lora_display_name(
                (adapter.get("filenames") or [adapter.get("sha256", "")])[-1]
            )
            order.append(node_id)
            where = ""
            if read_lanes and index == 0:
                where = f", before the fork: {everyone} get it"
            elif read_lanes and clip_field:
                where = (
                    f", its model on {segment['label']} only; its CLIP goes to "
                    f"{segment['clip_names']}"
                )
            elif read_lanes:
                where = (
                    f", on {segment['label']} only, as {loader_class}: nothing on "
                    "that side reads a CLIP, so it does not change the prompt"
                )
            added_changes.append(
                {
                    "kind": "added",
                    "node_id": node_id,
                    "text": (
                        f"A loader added for {names[node_id]} at "
                        f"{_strength_text(shown)}{where}"
                    ),
                }
            )
    deleted = [node_id for node_id in loaders if node_id not in placed]
    for segment in segments:
        move_changes += _move_changes(segment["old"], segment["order"], names)

    def moved_sinks(sinks: list[dict], model_end, clip_end) -> list[dict]:
        return [
            sink
            for sink in sinks
            if graph[sink["node_id"]]["inputs"].get(sink["field"])
            != (model_end if sink["type"] == "MODEL" else clip_end)
        ]

    model = chain["model_source"]
    clip = chain.get("clip_source")
    model_end, clip_end = _wire_segment(
        None,
        segments[0]["order"],
        wiring,
        [model["node_id"], model["output"]] if model else None,
        [clip["node_id"], clip["output"]] if clip else None,
    )
    rewired = moved_sinks(chain["sinks"], model_end, clip_end)
    plan_lanes = []
    for segment, lane in zip(segments[1:], read_lanes):
        source = lane["source"]
        start = [source["node_id"], source["output"]] if source else model_end
        clip_start = _lane_clip_start(lane, clip_end)
        carrier = next((n for n in segment["order"] if wiring[n]["clip_field"]), None)
        if carrier is not None and clip_start is None:
            raise LookupError(
                f"#{carrier} {names[carrier]} carries a CLIP, and nothing on "
                f"{segment['label']}'s side hands one out for it to read. Change "
                "it in ComfyUI."
            )
        lane_model, lane_clip = _wire_segment(
            None, segment["order"], wiring, start, clip_start
        )
        rewired += moved_sinks(lane["sinks"], lane_model, lane_clip)
        plan_lanes.append(
            {
                "order": segment["order"],
                "source": source,
                "clip_source": lane.get("clip_source"),
                "sinks": lane["sinks"],
            }
        )

    changes = [
        {
            "kind": "deleted",
            "node_id": node_id,
            "text": f"Loader #{node_id} deleted: {names[node_id]}",
        }
        for node_id in deleted
    ]
    changes += added_changes
    changes += move_changes
    changes += strength_changes
    rewire = _rewired_change(rewired)
    if rewire is not None:
        changes.append(rewire)
    return {
        "order": segments[0]["order"],
        "lanes": plan_lanes,
        "added": added,
        "deleted": deleted,
        "widgets": widgets,
        "wiring": wiring,
        "model_source": model,
        "clip_source": clip,
        "sinks": chain["sinks"],
        "changes": changes,
    }


def _lane_clip_start(lane: dict, clip_end: list | None) -> list | None:
    """Where a lane's CLIP chain starts: its own source for a lane with a
    model of its own, the trunk's CLIP end (as the edit leaves it) otherwise."""
    if lane.get("source") is None:
        return clip_end
    clip = lane.get("clip_source")
    return [clip["node_id"], clip["output"]] if clip else None


def _crossed_change(
    node_id: str, name: str, segment: dict, index: int, wiring: dict
) -> dict:
    """``#12 detail moved to Hires pass only``: a loader taken across the fork."""
    if index == 0:
        text = f"#{node_id} {name} moved before the fork: {segment['label']} get it"
    else:
        text = f"#{node_id} {name} moved to {segment['label']} only"
        if wiring[node_id]["clip_field"] and not segment["clip_read"]:
            # Nothing on that side reads a CLIP, so the loader's text-encoder
            # half stops reaching any prompt. Said, because it changes pictures.
            text += "; nothing there reads its CLIP, so it no longer changes the prompt"
        elif wiring[node_id]["clip_field"]:
            text += f"; its CLIP goes to {segment['clip_names']}"
    return {"kind": "moved", "node_id": node_id, "text": text}


def apply_lora_chain(prompt_graph: dict, plan: dict, object_info: dict) -> None:
    """Carry out :func:`plan_lora_chain`'s plan on *prompt_graph*, whole or not at all.

    Deleted loaders go through :func:`bypass_node`, new ones are added under
    their planned ids, then every loader in the trunk reads the one before it
    (the anchor for the first), each lane chains the same way off the trunk's
    end (or off its own model), and every sink reads the end of its segment.
    Node ids survive a move, so a picture's recorded graph can still be read
    against the edited one. Worked on a copy and swapped in at the end, so a
    refusal part way leaves the graph as it was.

    Raises:
        LookupError: From :func:`bypass_node`, when a loader to delete cannot
            be taken out; the graph is then unchanged.
    """
    work = deepcopy(prompt_graph)
    for node_id in plan["deleted"]:
        bypass_node(work, node_id, object_info)
    for node_id, node in plan["added"].items():
        work[node_id] = deepcopy(node)
    wiring = plan["wiring"]

    def rewire(sinks: list[dict], model_end, clip_end) -> None:
        for sink in sinks:
            end = model_end if sink["type"] == "MODEL" else clip_end
            work[sink["node_id"]]["inputs"][sink["field"]] = list(end)

    model = plan["model_source"]
    clip = plan["clip_source"]
    model_end, clip_end = _wire_segment(
        work,
        plan["order"],
        wiring,
        [model["node_id"], model["output"]] if model else None,
        [clip["node_id"], clip["output"]] if clip else None,
    )
    for node_id, fields in plan["widgets"].items():
        work[node_id]["inputs"].update(fields)
    rewire(plan["sinks"], model_end, clip_end)
    for lane in plan.get("lanes") or []:
        source = lane["source"]
        start = [source["node_id"], source["output"]] if source else model_end
        lane_model, lane_clip = _wire_segment(
            work, lane["order"], wiring, start, _lane_clip_start(lane, clip_end)
        )
        rewire(lane["sinks"], lane_model, lane_clip)
    prompt_graph.clear()
    prompt_graph.update(work)


def format_prompt_rejection(body: Any) -> str | None:
    """Render ComfyUI's structured ``POST /prompt`` rejection as one sentence.

    This is the backstop pre-flight cannot replace: ComfyUI validates the graph
    itself and answers 400 with

    .. code-block:: json

        {"error": {"type": "prompt_outputs_failed_validation",
                   "message": "Prompt outputs failed validation", "details": ""},
         "node_errors": {"4": {"class_type": "CheckpointLoaderSimple",
                               "errors": [{"type": "value_not_in_list",
                                           "message": "Value not in list",
                                           "details": "ckpt_name: 'x' not in [...]"}]}}}

    Every field is treated as optional - a custom fork or a future version may
    omit any of them, and an unparseable body must degrade to ``None`` (the
    caller then falls back to the raw text) rather than raise.

    Args:
        body: The parsed JSON response body.

    Returns:
        A readable summary, or ``None`` when *body* is not that shape.
    """
    if not isinstance(body, dict):
        return None
    parts: list[str] = []

    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type")
        details = error.get("details")
        if message:
            parts.append(f"{message}{f' ({details})' if details else ''}")

    node_errors = body.get("node_errors")
    if isinstance(node_errors, dict):
        for node_id, node_error in node_errors.items():
            if not isinstance(node_error, dict):
                continue
            class_type = node_error.get("class_type") or "node"
            for entry in node_error.get("errors") or []:
                if not isinstance(entry, dict):
                    continue
                detail = (
                    entry.get("details") or entry.get("message") or entry.get("type")
                )
                if detail:
                    parts.append(f"{class_type} (node {node_id}): {detail}")

    return "; ".join(parts) if parts else None


def unchecked_preflight(error: str) -> dict:
    """Return a pre-flight result meaning "we could not check".

    Distinct from a *failed* pre-flight: the run is still allowed, because the
    only thing we actually know is that ComfyUI did not answer our question.

    Args:
        error: Human-readable reason, surfaced verbatim in the UI.
    """
    return {
        "ok": True,
        "checked": False,
        "error": error,
        "missing_node_classes": [],
        "missing_models": [],
        "unchecked_fields": 0,
        "unchecked_models": 0,
    }


def sanitize_prompt_graph(prompt_graph: dict) -> dict:
    """Return a submittable copy of *prompt_graph*.

    ComfyUI's own ``prompt`` chunk sometimes carries bookkeeping keys that are
    not nodes (``extra_pnginfo``-style leftovers, PixlStash's own
    ``pixlstash_*`` hints). ``POST /prompt`` iterates every top-level entry as a
    node, so a non-node value there is a hard failure. Drop anything that is not
    a ``{class_type, inputs}`` node.

    Args:
        prompt_graph: The extracted API-format graph.

    Returns:
        A deep copy containing only node entries.
    """
    clean: dict = {}
    for node_id, node in (prompt_graph or {}).items():
        if str(node_id).startswith("pixlstash_"):
            continue
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str):
            logger.debug(
                "Dropping non-node entry %r from embedded prompt graph.", node_id
            )
            continue
        clean[str(node_id)] = deepcopy(node)
    return clean

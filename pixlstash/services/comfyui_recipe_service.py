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

import random
import re
from copy import deepcopy
from typing import Any

import requests

from pixlstash.pixl_logging import get_logger

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
}


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


def _normalize_filename(value: str) -> str:
    """Return *value* with path separators unified.

    ComfyUI builds combo entries with ``os.path.relpath``, so the same model
    lists as ``SDXL\\base.safetensors`` on a Windows host and
    ``SDXL/base.safetensors`` on Linux. A recipe generated on one and replayed
    on the other would otherwise read as a missing model that is right there.
    """
    return value.replace("\\", "/")


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
       check cannot masquerade as a clean bill of health.
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
        three missing-lists are empty), ``checked``, and ``unchecked_fields``.
    """
    missing_classes: list[str] = []
    missing_models: list[dict] = []
    missing_input_images: list[dict] = []
    unchecked_fields = 0
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

        for field in MODEL_FILENAME_FIELDS.get(class_type, ()):
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

    return {
        "ok": not missing_classes and not missing_models and not missing_input_images,
        "checked": True,
        "missing_node_classes": missing_classes,
        "missing_models": missing_models,
        "missing_input_images": missing_input_images,
        "has_save_image": has_save_image,
        "unchecked_fields": unchecked_fields,
    }


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
        ``[{"node_id", "class_type", "field", "value", "by"}, …]``.
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
                }
            )
    return targets


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

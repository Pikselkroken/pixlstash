"""Utilities for extracting and interpreting ComfyUI workflow metadata
embedded in image files.

These functions mirror the extraction logic from the frontend's ImageOverlay
component and are used both in API endpoint responses and internally by the
picture tagger when building text embeddings from ComfyUI generation data.
"""

import json
import math
from functools import lru_cache
from typing import Any

from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_recipe_service import model_filename_fields
from pixlstash.services.workflow_hash import MODEL_EXTENSIONS

logger = get_logger(__name__)

# ── node type constants ───────────────────────────────────────────────────────

_CHECKPOINT_CLASSES = {
    "CheckpointLoaderSimple",
    "CheckpointLoader",
    "CheckpointLoaderNF4",
}
_UNET_CLASSES = {
    "UNETLoader",
    "UnetLoaderGGUF",
    "UNETLoaderGGUF",
}
_LORA_CLASSES = {
    "LoraLoader",
    "LoRALoader",
    "LoraLoaderModelOnly",
    "LoRALoaderModelOnly",
    "LoraLoaderGGUF",
}
_CLIP_TEXT_ENCODE_CLASSES = {
    "CLIPTextEncode",
    "CLIPTextEncodeSDXL",
    "CLIPTextEncodeFlux",
}
# Nodes that have a named "positive" conditioning input connected to a sampler
_SAMPLER_CLASSES = {
    "KSampler",
    "KSamplerAdvanced",
    "CFGGuider",
    "SamplerCustom",
}
# Nodes that carry a seed value
_SEED_CLASSES = {
    "KSampler",
    "KSamplerAdvanced",
    "RandomNoise",
}
# Input field names that hold seed values
_SEED_FIELDS = {"seed", "noise_seed"}
# The settings a recipe is read for. Named rather than "every scalar input",
# because a node also carries its wiring and its pack's own extras, and a
# settings block nobody can read is worse than a short one. Read from ANY node
# that names one, not from the sampler: the split-sampler graphs (the shipped
# Flux2-Klein templates among them) put the step count on a scheduler node, the
# sampler name on a `KSamplerSelect` and the CFG on a `CFGGuider`, so reading
# only the sampler reports one field out of five for PixlStash's own workflows.
# Typed, because the value's *kind* is not the field's type: `steps: "twenty"`
# and `sampler_name: 12345` are not settings, and reporting them puts a graph's
# author in charge of what a client's formatter is handed.
_SETTING_FIELDS = {
    "steps": int,
    "cfg": float,
    # Flux and its kin have no CFG and carry a guidance scale instead, so a
    # recipe block without it reports nothing for the setting that shaped the
    # picture.
    "guidance": float,
    "sampler_name": str,
    "scheduler": str,
    "denoise": float,
    # The design's Settings block draws a Size row ("832x1216"), which is two
    # settings written as one value.
    "width": int,
    "height": int,
}
# Nodes that carry a raw STRING value (positive-prompt primitive wired into
# subgraphs). Public because `services/workflow_export.py` needs the same list:
# a prompt sitting in one of these is a prompt, and it is the population that
# name-based prose detection cannot see.
PRIMITIVE_STRING_CLASSES = {
    "PrimitiveStringMultiline",
    "TextBox",
    "Textbox",
    "String Literal",
    "StringNode",
}

_MAX_FOLLOW_DEPTH = 8

# String values in widgets_values that are control tokens, not prompt text.
_TEXT_CONTROL_VALUES = frozenset(
    {
        "randomize",
        "increment",
        "decrement",
        "fixed",
        "enable",
        "disable",
    }
)


# ── UI-format helpers ─────────────────────────────────────────────────────────


def _build_ui_maps(workflow: dict) -> tuple[dict, dict]:
    """Return ``(node_map, link_map)`` for a UI-format workflow.

    ``node_map`` maps ``str(node_id) → node``.
    ``link_map`` maps ``str(link_id) → str(from_node_id)``.

    Top-level graphs encode links as arrays:
        ``[link_id, from_node_id, from_slot, to_node_id, to_slot, type_string]``
    Subgraph definitions encode links as dicts:
        ``{id, origin_id, origin_slot, target_id, target_slot, type}``
    Both formats are handled.
    """
    node_map = {str(n["id"]): n for n in (workflow.get("nodes") or []) if "id" in n}
    link_map: dict[str, str] = {}
    for link in workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 3:
            # Top-level array format: [link_id, from_node_id, from_slot, ...]
            link_map[str(link[0])] = str(link[1])
        elif isinstance(link, dict) and "id" in link and "origin_id" in link:
            # Subgraph dict format: {id, origin_id, origin_slot, target_id, ...}
            link_map[str(link["id"])] = str(link["origin_id"])
    return node_map, link_map


def _iter_ui_graphs(workflow: dict):
    """Yield every graph dict (top-level + subgraphs) in a UI-format workflow.

    ComfyUI v0.4+ stores reusable subgraph definitions under
    ``workflow["definitions"]["subgraphs"]``, each of which has its own
    ``nodes`` and ``links`` arrays with the same schema as the top-level graph.
    """
    yield workflow
    for sg in (workflow.get("definitions") or {}).get("subgraphs") or []:
        if isinstance(sg, dict) and "nodes" in sg:
            yield sg


def _get_widget_value_ui(node: dict, input_name: str) -> Any:
    """Return the widget value for a named input in a UI-format node.

    Widget inputs consume positional slots from ``node["widgets_values"]``
    in the order they appear in ``node["inputs"]``, skipping non-widget inputs.
    Even when a widget input is overridden by an external link at runtime,
    the ``widgets_values`` slot still holds the last static/default value,
    which is useful for embedding purposes.
    """
    widgets_values = node.get("widgets_values") or []
    widget_index = 0
    for inp in node.get("inputs") or []:
        if inp.get("name") == input_name:
            if "widget" in inp and widget_index < len(widgets_values):
                return widgets_values[widget_index]
            return None
        if "widget" in inp:
            widget_index += 1
    return None


def _extract_text_from_node_ui(node: dict | None) -> str | None:
    """Extract a prompt string from any node that produces a STRING output.

    Handles simple text nodes (Text Multiline, Textbox, PrimitiveStringMultiline)
    and custom nodes (e.g. LoRACharacterPromptBuilder) where the prompt is the
    longest non-trivial string in ``widgets_values``.
    """
    if not isinstance(node, dict):
        return None
    # Try named widget inputs first (covers standard text nodes)
    for field in ("text", "value", "string"):
        val = _get_widget_value_ui(node, field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Fallback: longest non-trivial string in widgets_values (covers custom nodes)
    wv = node.get("widgets_values") or []
    candidates = [
        v
        for v in wv
        if isinstance(v, str)
        and v.strip()
        and v.strip().lower() not in _TEXT_CONTROL_VALUES
    ]
    if candidates:
        return max(candidates, key=len).strip()
    return None


def _follow_positive_ui(
    node_id: str,
    node_map: dict,
    link_map: dict,
    depth: int = 0,
) -> str | None:
    """Walk upstream conditioning links until a CLIPTextEncode node is found.

    Returns its text widget value, or ``None`` if the chain cannot be resolved.
    """
    if depth > _MAX_FOLLOW_DEPTH:
        return None
    node = node_map.get(str(node_id))
    if not isinstance(node, dict):
        return None
    if node.get("type") in _CLIP_TEXT_ENCODE_CLASSES:
        text = _get_widget_value_ui(node, "text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        # text may be fed by an external STRING link - follow it
        for inp in node.get("inputs") or []:
            if inp.get("name") == "text" and inp.get("link") is not None:
                src_id = link_map.get(str(inp["link"]))
                if src_id:
                    return _extract_text_from_node_ui(node_map.get(src_id))
        # text sits in widgets_values[0] without being declared in inputs[]
        return _extract_text_from_node_ui(node)
    # Follow any CONDITIONING-type input upstream (skips non-conditioning inputs)
    for inp in node.get("inputs") or []:
        if inp.get("type") == "CONDITIONING" and inp.get("link") is not None:
            upstream_id = link_map.get(str(inp["link"]))
            if upstream_id:
                result = _follow_positive_ui(upstream_id, node_map, link_map, depth + 1)
                if result is not None:
                    return result
    return None


def _extract_generation_info_ui(workflow: dict) -> dict:
    """Extract models, LoRAs, and positive prompt from a UI-format workflow.

    UI format stores widget values positionally in ``node["widgets_values"]``
    and connections as a separate ``links`` array.  Subgraph definitions in
    ``workflow["definitions"]["subgraphs"]`` are traversed recursively.
    """
    models: list[str] = []
    loras: list[str] = []
    positive_prompt: str | None = None
    seed: int | None = None

    for graph in _iter_ui_graphs(workflow):
        node_map, link_map = _build_ui_maps(graph)

        for node in graph.get("nodes") or []:
            node_type = node.get("type", "")
            # mode 2 = muted/never, mode 4 = bypassed - skip both
            if node.get("mode", 0) not in (0, None):
                continue

            if node_type in _CHECKPOINT_CLASSES:
                name = _get_widget_value_ui(node, "ckpt_name")
                if name is None:  # widget not declared in inputs[]; use slot 0
                    wv = node.get("widgets_values") or []
                    name = wv[0] if wv and isinstance(wv[0], str) else None
                if isinstance(name, str) and name:
                    models.append(name)

            elif node_type in _UNET_CLASSES:
                name = _get_widget_value_ui(node, "unet_name")
                if name is None:  # widget not declared in inputs[]; use slot 0
                    wv = node.get("widgets_values") or []
                    name = wv[0] if wv and isinstance(wv[0], str) else None
                if isinstance(name, str) and name:
                    models.append(name)

            elif node_type in _LORA_CLASSES:
                name = _get_widget_value_ui(node, "lora_name")
                if name is None:  # widget not declared in inputs[]; use slot 0
                    wv = node.get("widgets_values") or []
                    name = wv[0] if wv and isinstance(wv[0], str) else None
                if isinstance(name, str) and name:
                    loras.append(name)

            elif node_type in _SAMPLER_CLASSES:
                if positive_prompt is None:
                    for inp in node.get("inputs") or []:
                        if (
                            inp.get("name") == "positive"
                            and inp.get("link") is not None
                        ):
                            upstream_id = link_map.get(str(inp["link"]))
                            if upstream_id:
                                positive_prompt = _follow_positive_ui(
                                    upstream_id, node_map, link_map
                                )
                            break
                if seed is None and node_type in _SEED_CLASSES:
                    for field in _SEED_FIELDS:
                        val = _get_widget_value_ui(node, field)
                        if isinstance(val, int):
                            seed = val
                            break
                    if seed is None and node_type == "KSamplerAdvanced":
                        # noise_seed not declared in inputs[]; layout: [add_noise, noise_seed, ...]
                        wv = node.get("widgets_values") or []
                        if (
                            len(wv) >= 2
                            and wv[0] == "enable"
                            and isinstance(wv[1], int)
                        ):
                            seed = wv[1]

            elif node_type in _SEED_CLASSES and seed is None:
                for field in _SEED_FIELDS:
                    val = _get_widget_value_ui(node, field)
                    if isinstance(val, int):
                        seed = val
                        break
                if seed is None and node_type == "KSamplerAdvanced":
                    wv = node.get("widgets_values") or []
                    if len(wv) >= 2 and wv[0] == "enable" and isinstance(wv[1], int):
                        seed = wv[1]

    # Fallback: if no prompt was found via the conditioning chain (e.g. the text
    # lives in a top-level PrimitiveStringMultiline wired into a subgraph), scan
    # all graphs for connected primitive string nodes and use the first one found.
    if positive_prompt is None:
        for graph in _iter_ui_graphs(workflow):
            for node in graph.get("nodes") or []:
                if node.get("type") not in PRIMITIVE_STRING_CLASSES:
                    continue
                # Only consider nodes that have at least one outgoing link (are wired up)
                has_link = any(out.get("links") for out in (node.get("outputs") or []))
                if not has_link:
                    continue
                text = _extract_text_from_node_ui(node)
                if text:
                    positive_prompt = text
                    break
            if positive_prompt is not None:
                break

    return {
        "models": models,
        "loras": loras,
        "positive_prompt": positive_prompt,
        "seed": seed,
    }


# ── API-format helpers ────────────────────────────────────────────────────────


def _is_api_ref(value: Any) -> bool:
    """Return True if *value* is a ComfyUI API node reference ``[node_id, slot]``."""
    return isinstance(value, list) and len(value) == 2


def _resolve_text_api(value: Any, workflow: dict, depth: int = 0) -> str | None:
    """Resolve a text input value in API format, following single-hop references.

    Handles both plain strings and references to primitive/text passthrough nodes
    (e.g. ``PrimitiveStringMultiline``).
    """
    if depth > _MAX_FOLLOW_DEPTH:
        return None
    if isinstance(value, str):
        return value
    if _is_api_ref(value):
        ref_node = workflow.get(str(value[0]))
        if not isinstance(ref_node, dict):
            return None
        inputs = ref_node.get("inputs") or {}
        for key in ("value", "text", "string"):
            v = inputs.get(key)
            if v is not None:
                return _resolve_text_api(v, workflow, depth + 1)
    return None


def _follow_prompt_api(
    node_id: str,
    workflow: dict,
    depth: int = 0,
    side: str = "positive",
) -> str | None:
    """Walk upstream conditioning links in API format to find prompt text.

    ``side`` names the conditioning input a passthrough node is followed
    through, so the same walk reaches the negative encoder: a node that takes
    both (a ControlNet applier, a conditioning combine) has to be followed on
    the side the caller started on, or the negative chain arrives at the
    positive prompt.
    """
    if depth > _MAX_FOLLOW_DEPTH:
        return None
    node = workflow.get(str(node_id))
    if not isinstance(node, dict):
        return None
    class_type = node.get("class_type", "")
    inputs = node.get("inputs") or {}

    if class_type in _CLIP_TEXT_ENCODE_CLASSES:
        text = inputs.get("text")
        return _resolve_text_api(text, workflow, depth + 1)

    # Follow conditioning passthrough nodes upstream, **the caller's side
    # first**: a node carrying both a generic `conditioning` and a named
    # `positive`/`negative` is followed on the side this walk started on, or
    # the negative chain arrives at the positive prompt - the exact failure
    # `side` exists to prevent, which trying the generic key first reinstated.
    for key in (side, "conditioning"):
        ref = inputs.get(key)
        if _is_api_ref(ref):
            result = _follow_prompt_api(str(ref[0]), workflow, depth + 1, side)
            if result is not None:
                return result
    return None


def _extract_generation_info_api(workflow: dict) -> dict:
    """Extract models, LoRAs, and positive prompt from an API-format workflow.

    API format stores each node as a top-level dict keyed by node id, with
    named ``inputs`` dicts rather than positional widget arrays.
    """
    models: list[str] = []
    loras: list[str] = []
    positive_prompt: str | None = None
    seed: int | None = None

    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs") or {}

        if class_type in _CHECKPOINT_CLASSES:
            name = inputs.get("ckpt_name")
            if isinstance(name, str) and name:
                models.append(name)

        elif class_type in _UNET_CLASSES:
            name = inputs.get("unet_name")
            if isinstance(name, str) and name:
                models.append(name)

        elif class_type in _LORA_CLASSES:
            name = inputs.get("lora_name")
            if isinstance(name, str) and name:
                loras.append(name)

        elif class_type in _SAMPLER_CLASSES:
            if positive_prompt is None:
                ref = inputs.get("positive")
                if _is_api_ref(ref):
                    positive_prompt = _follow_prompt_api(str(ref[0]), workflow)
            if seed is None and class_type in _SEED_CLASSES:
                for field in _SEED_FIELDS:
                    val = inputs.get(field)
                    if isinstance(val, int):
                        seed = val
                        break

        elif class_type in _SEED_CLASSES and seed is None:
            for field in _SEED_FIELDS:
                val = inputs.get(field)
                if isinstance(val, int):
                    seed = val
                    break

    return {
        "models": models,
        "loras": loras,
        "positive_prompt": positive_prompt,
        "seed": seed,
    }


# ── public extraction API ─────────────────────────────────────────────────────


def extract_generation_info(workflow: dict) -> dict:
    """Extract model names, LoRA names, and positive prompt from a workflow.

    Works with both serialisations, and which one this is is
    :func:`is_api_format`'s answer rather than a second opinion taken here
    (#1482): a document carrying the ``last_node_id`` / ``last_link_id`` hints
    is the editor graph even where its body would read as a flat node map, and
    is read as one rather than walked as both.

    Args:
        workflow: A parsed ComfyUI workflow dict as returned by
            ``find_comfy_workflow``.

    Returns:
        A dict with keys:

        - ``models`` (list[str]): checkpoint / UNET file names found in
          loader nodes.
        - ``loras`` (list[str]): LoRA file names found in LoRA loader nodes.
        - ``positive_prompt`` (str | None): text from the CLIPTextEncode node
          connected to the sampler's ``positive`` input, or ``None`` if the
          chain could not be resolved.
        - ``seed`` (int | None): the seed value used for sampling, taken from
          the ``seed`` widget of ``KSampler``/``KSamplerAdvanced`` or the
          ``noise_seed`` input of ``RandomNoise``.
    """
    # The format check used to sit outside this `try`, so a non-dict raised
    # `AttributeError` at the caller. `is_api_format` answers for one instead
    # ("not the API format"), the UI reader then raises inside the `try`, and
    # such a caller now gets the empty result and a logged traceback. No
    # caller passes one; the widened `except` is the cost of the single sniff.
    try:
        if not is_api_format(workflow):
            return _extract_generation_info_ui(workflow)
        return _extract_generation_info_api(workflow)
    except Exception:
        logger.warning("Failed to extract generation info from workflow", exc_info=True)
        return {"models": [], "loras": [], "positive_prompt": None, "seed": None}


# The loaders whose model name is the FIRST widget value, for the UI-format
# node that declares no inputs at all and is therefore a bare positional array.
# Only these three: every other loader in the map below either interleaves its
# widgets or carries several, and a positional guess there would name the wrong
# file rather than none.
_NAME_FIRST_CLASSES = _CHECKPOINT_CLASSES | _UNET_CLASSES | _LORA_CLASSES

# `config_name` is a YAML beside the checkpoint, not a model: it reaches no
# shelf row and would draw a mark for a file nobody loads.
_NOT_A_MODEL_WIDGET = frozenset({"config_name"})


@lru_cache(maxsize=1)
def _base_model_widgets() -> tuple[str, ...]:
    """Every widget that names a BASE MODEL, in a fixed order.

    Read in addition to the loader's own widgets because
    ``CHECKPOINT_WIDGETS`` - not a list of loader classes - is where "what
    counts as a base model" is decided. A class list cannot answer for a node
    it has never heard of, and two of these widgets reach a card through
    PixlStash's own ComfyUI node (``checkpoint_id``) and through Diffusers,
    neither of which the pre-flight map names.

    **Derived, never copied**, for the reason ``BASE_MODEL_KINDS`` is derived:
    a sixth widget added to that set has to reach this the same day. The
    import is local only because ``workflow_identity`` reaches back into this
    module through ``workflow_io`` -> ``comfyui_service``, so a top-level one
    is a cycle - the single case CLAUDE.md sanctions a local import for.
    """
    from pixlstash.services.workflow_identity import CHECKPOINT_WIDGETS

    return tuple(sorted(CHECKPOINT_WIDGETS))


def _model_widgets_of(class_type: str) -> tuple[str, ...]:
    """Every widget of this loader that holds a model file name."""
    return tuple(
        field
        for field in model_filename_fields(class_type)
        if field not in _NOT_A_MODEL_WIDGET
    )


def loaded_model_widgets(workflow: dict) -> list[tuple[str, str]]:
    """``[(widget name, filename)]`` for every model loader in *workflow*.

    Both serialisations, like :func:`extract_generation_info`, and the same
    best effort: a **UI-format** file names its widget values by position, so a
    name is read off ``widgets_values`` through the node's declared inputs, and
    falls back to slot 0 only for the three loader families whose model name is
    the first widget (:data:`_NAME_FIRST_CLASSES`).

    **Which widget each loader reads is ``MODEL_FILENAME_FIELDS``'** (through
    ``model_filename_fields``, so a ComfyUI-MultiGPU wrapper reads as the
    loader it wraps), the map
    the pre-flight check already uses, rather than a list of its own: a card
    built from these is read beside one built from a stored slot list, and two
    answers to "which widget names a model" is how the two drift. That map
    carries the VAE, CLIP, ControlNet, upscale and Diffusers loaders as well as
    the three that name a base model, so each recovered slot arrives under the
    widget ``_SLOT_KINDS`` turns into its real kind.

    It is still a list of classes, and no list of classes is every loader there
    is: a graph loading through a custom node recovers nothing for it. That is
    why an empty answer means "this document did not say" and never "this
    workflow loads no models" -- and why a card drawn from this must not turn
    a missing slot into a claim (``utils/workflowCard.checkpointUnread``).

    Muted and bypassed nodes are skipped, because they do not load anything
    when the workflow runs.

    Order is document order, which is the order a reader sees the loaders in.
    Duplicates are kept: two LoRA loaders holding one file is two slots.
    """
    if not isinstance(workflow, dict):
        return []
    if is_api_format(workflow):
        return _loaded_model_widgets_api(workflow)
    return _loaded_model_widgets_ui(workflow)


def count_model_file_values_ui(workflow: dict) -> int:
    """How many widget values in an editor graph look like a model file.

    The denominator :func:`loaded_model_widgets` is read against: a value with
    a model file extension, on a node that runs, whether or not any loader map
    knows which widget it sits in. The difference between the two is how many
    models a reader of the file could not name - the number that keeps a short
    list from passing for a complete one. Same walk as the reader (subgraphs
    included, muted and bypassed nodes skipped), so the two count alike.
    """
    if not isinstance(workflow, dict):
        return 0
    count = 0
    for graph in _iter_ui_graphs(workflow):
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict) or node.get("mode", 0) not in (0, None):
                continue
            values = node.get("widgets_values")
            if isinstance(values, dict):
                values = list(values.values())
            if not isinstance(values, list):
                continue
            count += sum(
                1
                for value in values
                if isinstance(value, str) and value.lower().endswith(MODEL_EXTENSIONS)
            )
    return count


def _loaded_model_widgets_ui(workflow: dict) -> list[tuple[str, str]]:
    found = []
    for graph in _iter_ui_graphs(workflow):
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            # mode 2 = muted/never, mode 4 = bypassed - skip both.
            if node.get("mode", 0) not in (0, None):
                continue
            class_type = node.get("type", "")
            widgets = _model_widgets_of(class_type)
            named = [(widget, _get_widget_value_ui(node, widget)) for widget in widgets]
            if (
                widgets
                and all(value is None for _, value in named)
                and class_type in _NAME_FIRST_CLASSES
            ):
                # No input declared the widget, so the array is bare and the
                # model name is slot 0 - true of these three families only.
                values = node.get("widgets_values") or []
                named = [(widgets[0], values[0] if values else None)]
            named.extend(
                (widget, _get_widget_value_ui(node, widget))
                for widget in _base_model_widgets()
                if widget not in widgets
            )
            for widget, value in named:
                if isinstance(value, str) and value:
                    found.append((widget, value))
    return found


def iter_model_fields_api(workflow: dict):
    """Yield ``(node_id, class_type, widget, filename)`` for an API graph.

    The one walk over "which widget of which loader names a model file" in an
    API-format graph: :func:`loaded_model_widgets` reads through it, and so do
    the clone dialog's slot list and ``apply_filename_swap``'s rewrite, which
    must agree with it about which fields a swap reaches.
    """
    for node_id, node in (workflow or {}).items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = node.get("class_type", "")
        widgets = _model_widgets_of(class_type)
        for widget in (
            *widgets,
            *(w for w in _base_model_widgets() if w not in widgets),
        ):
            value = inputs.get(widget)
            if isinstance(value, str) and value:
                yield node_id, class_type, widget, value


def _loaded_model_widgets_api(workflow: dict) -> list[tuple[str, str]]:
    return [
        (widget, value) for _id, _cls, widget, value in iter_model_fields_api(workflow)
    ]


def extract_recipe_extras(workflow: dict) -> dict:
    """The negative prompt and the sampler settings of an **API-format** graph.

    Split from :func:`extract_generation_info` rather than folded into it
    because only the API format is read here: the recipe endpoint works on the
    embedded ``prompt`` chunk, and a UI graph reaching this would quietly
    report no settings at all. A caller holding a UI graph gets the same empty
    answer as one holding a graph with no sampler, which is honest for both.

    Args:
        workflow: The API-format graph.

    Returns:
        ``{"negative_prompt": str | None, "settings": {field: value}}``. The
        first node that names a field wins, and that is **iteration order, not
        execution order**: a graph that samples twice (a hires-fix pass) can
        report the second pass's step count beside the first's CFG. Reading it
        as "the settings of the pass that made the picture" is therefore wrong;
        it is "what this graph says", which is what a recipe read can honestly
        offer without walking the execution graph.
    """
    negative_prompt: str | None = None
    settings: dict[str, Any] = {}
    try:
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") or {}
            if not isinstance(inputs, dict):
                continue
            if (
                negative_prompt is None
                and node.get("class_type", "") in _SAMPLER_CLASSES
            ):
                ref = inputs.get("negative")
                if _is_api_ref(ref):
                    negative_prompt = _follow_prompt_api(
                        str(ref[0]), workflow, side="negative"
                    )
            for field, kind in _SETTING_FIELDS.items():
                if field in settings:
                    continue
                value = _typed_setting(inputs.get(field), kind)
                if value is not None:
                    settings[field] = value
    except Exception:
        logger.warning("Failed to extract recipe extras from workflow", exc_info=True)
        return {"negative_prompt": None, "settings": {}}
    return {"negative_prompt": negative_prompt, "settings": settings}


def _typed_setting(value: Any, kind: type) -> Any:
    """*value* as *kind* if it is a reportable setting of that field, else ``None``.

    The field's type, not the value's kind. A graph is attacker-authorable file
    metadata, so `steps: "twenty"` and `sampler_name: 12345` both arrive as
    plausible-looking JSON and neither is a setting; a number field written as
    text is refused rather than passed on for a client to parse.

    **A non-finite float is refused, and that is not tidiness.** ``json.loads``
    accepts the ``Infinity`` and ``NaN`` literals while the response renders
    with ``allow_nan=False``, so a crafted ``prompt`` chunk would turn this
    read into a 500 for anyone holding a share token for the picture. An
    integer past the range of a float is refused on the same ground: it
    overflows the conversion rather than the renderer.

    A wired input (``[node_id, slot]``) has no value until the graph runs, and
    a bool is not a setting any of these fields declares.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if kind is str:
        return value if isinstance(value, str) else None
    if isinstance(value, str):
        return None
    try:
        typed = kind(value)
    except (OverflowError, ValueError):
        return None
    # Only a float can be non-finite, and `math.isfinite` itself OVERFLOWS on
    # an integer too large for a float - which would have been raised out of
    # here and cost the whole settings block, not just the one field.
    if isinstance(typed, float) and not math.isfinite(typed):
        return None
    return typed


def _parse_metadata_value(value: Any) -> Any:
    """Recursively parse JSON strings nested within metadata values."""
    if isinstance(value, str):
        trimmed = value.strip()
        if (trimmed.startswith("{") and trimmed.endswith("}")) or (
            trimmed.startswith("[") and trimmed.endswith("]")
        ):
            try:
                return json.loads(trimmed)
            except (json.JSONDecodeError, ValueError):
                return value
        return value
    if isinstance(value, list):
        return [_parse_metadata_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _parse_metadata_value(v) for k, v in value.items()}
    return value


def _workflow_candidate(value: Any) -> dict | None:
    """Attempt to interpret a raw metadata value as a ComfyUI workflow dict.

    Handles string-encoded JSON and nested ``{"workflow": ...}`` wrappers.
    """
    if not value:
        return None
    if isinstance(value, str):
        trimmed = value.strip()
        if (trimmed.startswith("{") and trimmed.endswith("}")) or (
            trimmed.startswith("[") and trimmed.endswith("]")
        ):
            try:
                return json.loads(trimmed)
            except (json.JSONDecodeError, ValueError):
                return None
        return None
    if isinstance(value, dict):
        if "workflow" in value:
            inner = _workflow_candidate(value["workflow"])
            return inner if inner is not None else value
        return value
    return None


def is_comfy_workflow(value: Any) -> bool:
    """Return True if *value* looks like a ComfyUI workflow (UI or API format).

    UI format detection: contains ``nodes`` / ``links`` arrays, or
    ``last_node_id`` / ``last_link_id`` integer hints.

    API format detection: top-level values are node dicts with
    ``class_type`` + ``inputs`` keys.
    """
    if not isinstance(value, dict):
        return False
    # UI format: the same four hints, and `is_api_format` is their one reader.
    # Sound as an inversion only because a non-dict was refused two lines up -
    # `is_api_format` answers "not the API format" for one of those as well.
    if not is_api_format(value):
        return True
    # API format: most top-level entries must be node dicts
    vals = list(value.values())
    api_node_count = sum(
        1
        for v in vals
        if isinstance(v, dict)
        and isinstance(v.get("class_type"), str)
        and "inputs" in v
    )
    return api_node_count > 0 and api_node_count >= min(len(vals), 2)


class NotAWorkflowError(ValueError):
    """A document offered for import is not a ComfyUI workflow."""


def check_comfy_workflow(value: Any) -> None:
    """Refuse *value* unless it is shaped like a ComfyUI workflow file.

    Stricter than :func:`is_comfy_workflow`, which only sniffs metadata: this
    guards what gets stored, so any JSON object must not pass.

    - UI format: a non-empty ``nodes`` list whose every node has an ``id`` and
      a non-empty string ``type``, beside a ``links`` list or, where the
      schema-version-1 serialiser drops an empty ``links``, its ``state`` or
      ``last_node_id``. Those tell it from other node-graph exports (React
      Flow, n8n).
    - API format (bare, or wrapped as ``{"prompt": graph}``): at least one
      entry, and every entry other than PixlStash's own ``pixlstash_*`` keys a
      node with a non-empty string ``class_type`` and an ``inputs`` object.

    Raises:
        NotAWorkflowError: *value* is not a workflow; the message says why.
    """
    if not isinstance(value, dict):
        raise NotAWorkflowError("not a ComfyUI workflow: not a JSON object")
    if "nodes" in value:
        nodes = value["nodes"]
        if not isinstance(nodes, list) or not nodes:
            raise NotAWorkflowError("not a ComfyUI workflow: it has no nodes")
        if not (
            isinstance(value.get("links"), list)
            or (
                "links" not in value
                and (
                    isinstance(value.get("state"), dict)
                    or isinstance(value.get("last_node_id"), int)
                )
            )
        ):
            raise NotAWorkflowError("not a ComfyUI workflow: it has no links list")
        for index, node in enumerate(nodes, start=1):
            if not isinstance(node, dict) or node.get("id") is None:
                raise NotAWorkflowError(
                    f"not a ComfyUI workflow: entry {index} of its nodes has no id"
                )
            if not _is_name(node.get("type")):
                raise NotAWorkflowError(
                    f"not a ComfyUI workflow: node {_shown(node['id'])} has no type"
                )
        return
    graph = value["prompt"] if isinstance(value.get("prompt"), dict) else value
    entries = {
        key: node
        for key, node in graph.items()
        if not str(key).startswith("pixlstash_")
    }
    strays = [
        key
        for key, node in entries.items()
        if not (
            isinstance(node, dict)
            and _is_name(node.get("class_type"))
            and isinstance(node.get("inputs"), dict)
        )
    ]
    if not entries or len(strays) == len(entries):
        raise NotAWorkflowError("not a ComfyUI workflow")
    if strays:
        raise NotAWorkflowError(
            f"not a ComfyUI workflow: {_shown(strays[0])} is not a node with a "
            "class_type and inputs"
        )


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _shown(value: Any) -> str:
    """*value* quoted for a message, cut short so a huge key cannot flood it."""
    return repr(str(value)[:60])


def find_comfy_workflow(metadata: dict) -> dict | None:
    """Search well-known metadata keys for a valid ComfyUI workflow.

    Checks (in priority order):

    - ``metadata["png"]["workflow"]``
    - ``metadata["png"]["workflow_json"]``
    - ``metadata["workflow"]``
    - ``metadata["workflow_json"]``
    - ``metadata["comfyui_workflow"]``
    - ``metadata["comfyui"]["workflow"]``
    - ``metadata["comfyui"]["workflow_json"]``
    - ``metadata["png"]["prompt"]`` / ``metadata["prompt"]`` /
      ``metadata["comfyui"]["prompt"]`` (display fallback)

    The ``prompt``-chunk candidates come last so a genuine UI ``workflow``
    chunk always wins. They exist because PixlStash-generated PNGs no longer
    embed anything in the ``workflow`` chunk (issue #628); ComfyUI's own
    ``prompt`` chunk (the executed API graph) is then the only thing left to
    display. A plain-text ``prompt`` value from other tools is filtered out by
    :func:`is_comfy_workflow`.

    Returns:
        The first valid workflow dict, or ``None`` if none is found.
    """
    png = metadata.get("png") or {}
    if not isinstance(png, dict):
        png = _workflow_candidate(png) or {}

    comfyui_block = metadata.get("comfyui") or {}
    if not isinstance(comfyui_block, dict):
        comfyui_block = _workflow_candidate(comfyui_block) or {}

    candidates = [
        png.get("workflow"),
        png.get("workflow_json"),
        metadata.get("workflow"),
        metadata.get("workflow_json"),
        metadata.get("comfyui_workflow"),
        comfyui_block.get("workflow"),
        comfyui_block.get("workflow_json"),
        # Lowest priority: the API-format ``prompt`` chunk, for files with no
        # UI workflow chunk at all (e.g. PixlStash-generated PNGs, issue #628).
        png.get("prompt"),
        metadata.get("prompt"),
        comfyui_block.get("prompt"),
    ]

    for raw in candidates:
        candidate = _workflow_candidate(raw)
        if candidate and is_comfy_workflow(candidate):
            return candidate

    return None


def is_api_format(workflow: dict) -> bool:
    """Return True if *workflow* is the API/headless format, not the UI graph.

    The UI graph carries ``nodes`` / ``links`` arrays (or the ``last_node_id`` /
    ``last_link_id`` hints); the API format is a flat ``{node_id: {class_type,
    inputs}}`` dict. Only the latter is submittable to ``POST /prompt``.
    """
    if not isinstance(workflow, dict):
        return False
    return not (
        isinstance(workflow.get("nodes"), list)
        or isinstance(workflow.get("links"), list)
        or isinstance(workflow.get("last_node_id"), int)
        or isinstance(workflow.get("last_link_id"), int)
    )


def find_comfy_api_prompt(metadata: dict) -> dict | None:
    """Return the embedded ComfyUI **API-format** ``prompt`` graph, or ``None``.

    This is deliberately NOT :func:`find_comfy_workflow`. ComfyUI embeds two
    different things in a generated PNG:

    - the ``workflow`` chunk - the *UI* node graph, for reopening in the editor.
      It is **not submittable** to ``POST /prompt``.
    - the ``prompt`` chunk - the *resolved API graph the server actually
      executed*. This is the only executable one.

    Only the ``prompt`` chunk is considered here, and it must additionally pass
    :func:`is_api_format`. **This function never falls back to the UI graph**,
    and that is not the same thing as PixlStash refusing to read one: a caller
    that wants the editor chunk rebuilt into a runnable graph asks
    :func:`pixlstash.services.comfyui_ui_graph.convert_ui_graph_to_api`, which
    needs ComfyUI's ``/object_info`` to do it and refuses rather than
    approximates. Keeping the two apart is what lets a caller say which it got:
    the answer here is the graph ComfyUI actually executed, and the answer there
    is PixlStash's reading of the editor's view of it.

    Args:
        metadata: Raw embedded metadata as returned by
            ``ImageUtils.extract_embedded_metadata`` - PNG text chunks live
            under ``metadata["png"]``.

    Returns:
        The API-format graph dict, or ``None`` when the file carries no
        executable prompt (UI-graph-only, A1111, or stripped metadata).
    """
    if not metadata:
        return None

    png = metadata.get("png") or {}
    if not isinstance(png, dict):
        png = _workflow_candidate(png) or {}

    comfyui_block = metadata.get("comfyui") or {}
    if not isinstance(comfyui_block, dict):
        comfyui_block = _workflow_candidate(comfyui_block) or {}

    candidates = [
        png.get("prompt"),
        metadata.get("prompt"),
        comfyui_block.get("prompt"),
    ]

    for raw in candidates:
        candidate = _workflow_candidate(raw)
        if not candidate or not is_comfy_workflow(candidate):
            continue
        if not is_api_format(candidate):
            # A UI graph stored under the "prompt" key. Not submittable.
            logger.debug(
                "Ignoring embedded 'prompt' chunk: it holds a UI graph, not an "
                "API-format prompt."
            )
            continue
        return candidate

    return None


def collect_seed_inputs(workflow: dict) -> list[dict]:
    """List the patchable seed inputs in an API-format *workflow*.

    Used to tell the user, before they submit, whether "new seed" will actually
    change anything for this graph.

    Returns:
        A list of ``{"node_id", "class_type", "field", "value"}`` dicts, one per
        known seed input carrying a numeric value.
    """
    found: list[dict] = []
    if not isinstance(workflow, dict):
        return found
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if class_type not in _SEED_CLASSES:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for field in _SEED_FIELDS:
            value = inputs.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            found.append(
                {
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "field": field,
                    "value": int(value),
                }
            )
    return found


def summarize_comfy_workflow(workflow: dict) -> dict:
    """Return basic statistics about a ComfyUI workflow.

    Returns:
        dict with keys ``node_count`` (int) and ``link_count`` (int or None).
    """
    nodes = workflow.get("nodes")
    links = workflow.get("links")

    if (
        isinstance(nodes, list)
        or (nodes and isinstance(nodes, dict))
        or isinstance(links, list)
    ):
        node_count = (
            len(nodes)
            if isinstance(nodes, list)
            else len(nodes)
            if isinstance(nodes, dict)
            else 0
        )
        link_count = (
            len(links)
            if isinstance(links, list)
            else len(links)
            if isinstance(links, dict)
            else None
        )
        return {"node_count": node_count, "link_count": link_count}

    # API format: count entries that look like nodes
    node_count = sum(
        1
        for v in workflow.values()
        if isinstance(v, dict) and isinstance(v.get("class_type"), str)
    )
    return {"node_count": node_count, "link_count": None}


def extract_comfy_workflow_info(metadata: dict) -> dict | None:
    """Extract ComfyUI workflow information from embedded image metadata.

    Args:
        metadata: The raw embedded metadata dict as returned by
            ``ImageUtils.extract_embedded_metadata``.

    Returns:
        A dict with keys:

        - ``workflow`` (dict): the parsed ComfyUI workflow object.
        - ``is_api_format`` (bool): ``True`` when the workflow is in
          API/headless format rather than the UI node-graph format.
        - ``summary`` (str): human-readable description (e.g.
          ``"Workflow · 12 nodes · 15 links"``).
        - ``models`` (list[str]): checkpoint / UNET file names.
        - ``loras`` (list[str]): LoRA file names.
        - ``positive_prompt`` (str | None): text from the CLIPTextEncode
          connected to the sampler's ``positive`` input.
        - ``seed`` (int | None): the seed value used for sampling.

        Returns ``None`` if no ComfyUI workflow is detected.
    """
    if not metadata:
        return None

    workflow = find_comfy_workflow(metadata)
    if not workflow:
        return None

    api_format = is_api_format(workflow)

    stats = summarize_comfy_workflow(workflow)
    fmt = "API Workflow" if api_format else "Workflow"
    summary_parts = [f"{fmt} · {stats['node_count']} nodes"]
    if stats["link_count"] is not None:
        summary_parts.append(f"{stats['link_count']} links")
    summary = " · ".join(summary_parts) or "Detected ComfyUI metadata"

    # **The graph shown and the graph read are two different questions.**
    # `find_comfy_workflow` prefers the UI `workflow` chunk, which is right for
    # what is displayed, copied and pasted back into ComfyUI. It is the wrong
    # source for what the picture was MADE with: the UI chunk is the editor's
    # view, read here by mapping named inputs onto positional `widgets_values`
    # and, failing that, taking the longest string in a node - so a graph whose
    # encoder is fed by a custom prompt-builder reports that node's template
    # instead of the prompt, and can miss the models and the seed entirely.
    #
    # The `prompt` chunk is the resolved graph the ComfyUI server actually
    # executed, which is why `GET /comfyui/pictures/{id}/recipe` reads only that
    # one. Facts come from it whenever the file has one; a UI-only file falls
    # back to the editor's view, which is then genuinely all there is.
    executed = find_comfy_api_prompt(metadata)
    gen_info = extract_generation_info(executed if executed is not None else workflow)

    return {
        "workflow": workflow,
        "is_api_format": api_format,
        "summary": summary,
        "models": gen_info["models"],
        "loras": gen_info["loras"],
        "positive_prompt": gen_info["positive_prompt"],
        "negative_prompt": extract_recipe_extras(executed)["negative_prompt"]
        if executed is not None
        else None,
        "seed": gen_info["seed"],
        # The seed as text as well as a number. ComfyUI draws seeds up to
        # 2**64-1 and JavaScript's Number loses precision above 2**53, so a
        # panel rendering `seed` would print the wrong digits for about half of
        # real seeds. `generation.seed` is TEXT in the vault for this reason.
        "seed_text": None if gen_info["seed"] is None else str(gen_info["seed"]),
    }

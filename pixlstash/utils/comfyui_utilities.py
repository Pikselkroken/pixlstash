"""Utilities for extracting and interpreting ComfyUI workflow metadata
embedded in image files.

These functions mirror the extraction logic from the frontend's ImageOverlay
component and are used both in API endpoint responses and internally by the
picture tagger when building text embeddings from ComfyUI generation data.
"""

import json
from typing import Any

from pixlstash.pixl_logging import get_logger

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
# Nodes that carry a raw STRING value (positive-prompt primitive wired into subgraphs)
_PRIMITIVE_STRING_CLASSES = {
    "PrimitiveStringMultiline",
    "TextBox",
    "String Literal",
    "StringNode",
}

_MAX_FOLLOW_DEPTH = 8

# String values in widgets_values that are control tokens, not prompt text.
_TEXT_CONTROL_VALUES = frozenset({
    "randomize", "increment", "decrement", "fixed", "enable", "disable",
})


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
    node_map = {
        str(n["id"]): n
        for n in (workflow.get("nodes") or [])
        if "id" in n
    }
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
        v for v in wv
        if isinstance(v, str) and v.strip() and v.strip().lower() not in _TEXT_CONTROL_VALUES
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
        # text may be fed by an external STRING link — follow it
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
                        if inp.get("name") == "positive" and inp.get("link") is not None:
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
                        if len(wv) >= 2 and wv[0] == "enable" and isinstance(wv[1], int):
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
                if node.get("type") not in _PRIMITIVE_STRING_CLASSES:
                    continue
                # Only consider nodes that have at least one outgoing link (are wired up)
                has_link = any(
                    out.get("links")
                    for out in (node.get("outputs") or [])
                )
                if not has_link:
                    continue
                text = _extract_text_from_node_ui(node)
                if text:
                    positive_prompt = text
                    break
            if positive_prompt is not None:
                break

    return {"models": models, "loras": loras, "positive_prompt": positive_prompt, "seed": seed}


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


def _follow_positive_api(
    node_id: str,
    workflow: dict,
    depth: int = 0,
) -> str | None:
    """Walk upstream conditioning links in API format to find prompt text."""
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

    # Follow conditioning passthrough nodes upstream
    for key in ("conditioning", "positive"):
        ref = inputs.get(key)
        if _is_api_ref(ref):
            result = _follow_positive_api(str(ref[0]), workflow, depth + 1)
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
                    positive_prompt = _follow_positive_api(str(ref[0]), workflow)
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

    return {"models": models, "loras": loras, "positive_prompt": positive_prompt, "seed": seed}


# ── public extraction API ─────────────────────────────────────────────────────


def extract_generation_info(workflow: dict) -> dict:
    """Extract model names, LoRA names, and positive prompt from a workflow.

    Works with both UI format (``nodes`` + ``links`` arrays) and API format
    (flat dict of node dicts with ``class_type`` + ``inputs``).

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
    is_ui_format = isinstance(workflow.get("nodes"), list) or isinstance(
        workflow.get("links"), list
    )
    try:
        if is_ui_format:
            return _extract_generation_info_ui(workflow)
        return _extract_generation_info_api(workflow)
    except Exception:
        logger.warning("Failed to extract generation info from workflow", exc_info=True)
        return {"models": [], "loras": [], "positive_prompt": None, "seed": None}


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
    # UI format
    if isinstance(value.get("nodes"), list) or isinstance(value.get("links"), list):
        return True
    if isinstance(value.get("last_node_id"), int) or isinstance(
        value.get("last_link_id"), int
    ):
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
    ]

    for raw in candidates:
        candidate = _workflow_candidate(raw)
        if candidate and is_comfy_workflow(candidate):
            return candidate

    return None


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

    is_api_format = not (
        isinstance(workflow.get("nodes"), list)
        or isinstance(workflow.get("links"), list)
        or isinstance(workflow.get("last_node_id"), int)
        or isinstance(workflow.get("last_link_id"), int)
    )

    stats = summarize_comfy_workflow(workflow)
    fmt = "API Workflow" if is_api_format else "Workflow"
    summary_parts = [f"{fmt} · {stats['node_count']} nodes"]
    if stats["link_count"] is not None:
        summary_parts.append(f"{stats['link_count']} links")
    summary = " · ".join(summary_parts) or "Detected ComfyUI metadata"

    gen_info = extract_generation_info(workflow)

    return {
        "workflow": workflow,
        "is_api_format": is_api_format,
        "summary": summary,
        "models": gen_info["models"],
        "loras": gen_info["loras"],
        "positive_prompt": gen_info["positive_prompt"],
        "seed": gen_info["seed"],
    }

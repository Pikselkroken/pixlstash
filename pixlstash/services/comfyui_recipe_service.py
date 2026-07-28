"""Recipe replay: pre-flighting an embedded ComfyUI prompt graph (Remix v1.9).

"Recipe mode" replays the API-format ``prompt`` chunk a generated image carries —
the graph the ComfyUI server actually executed — against the user's *current*
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

from copy import deepcopy
from typing import Any

import requests

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

OBJECT_INFO_TIMEOUT_S = 15.0

# Loader input fields that hold a model FILE NAME, keyed by the node's own
# `class_type`. Checks are filename-level only: we compare the graph's value
# against the combo list ComfyUI advertises for that field. Anything not listed
# here is simply not checked — this map is deliberately conservative, because a
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


def _field_options(node_spec: Any, field: str) -> list[str] | None:
    """Return the enumerated string options for *field*, or ``None``.

    ``object_info[class]["input"]["required"][field]`` is a list whose first
    element is either a type name (``"INT"``, ``"MODEL"``, …) or, for a combo
    widget, the list of allowed values. Only that second shape is checkable.

    ``None`` means "not enumerable" — a plain type, a missing entry, or an
    empty combo. An empty combo is genuinely ambiguous (ComfyUI emits ``[]``
    both for "no files installed" and for lists it populates lazily), so it is
    treated as unknown rather than as "everything is missing".
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
        options = entry[0]
        if not isinstance(options, (list, tuple)):
            # A type name like "INT" / "MODEL" — not a filename combo.
            return None
        values = [opt for opt in options if isinstance(opt, str)]
        return values or None
    return None


def preflight_prompt(prompt_graph: dict, object_info: dict) -> dict:
    """Check *prompt_graph* against *object_info* and report what is missing.

    Two checks, both deliberately narrow:

    1. **Node classes** — a ``class_type`` that is not a key of ``object_info``
       cannot run. This one is exact.
    2. **Model filenames** — for the loader fields in
       :data:`MODEL_FILENAME_FIELDS`, a literal string value that does not
       appear in ComfyUI's advertised combo list for that field. Values that
       are node references (``[node_id, slot]``) are skipped: they are computed
       at run time, not filenames. Fields ComfyUI does not enumerate are
       skipped too, and counted in ``unchecked_fields`` so the UI can say the
       check was partial instead of implying it passed.

    Args:
        prompt_graph: The API-format graph.
        object_info: The map from :func:`fetch_object_info`.

    Returns:
        ``{"ok", "checked", "missing_node_classes", "missing_models",
        "unchecked_fields"}``. ``ok`` is True only when both lists are empty.
    """
    missing_classes: list[str] = []
    missing_models: list[dict] = []
    unchecked_fields = 0
    seen_classes: set[str] = set()

    for node_id, node in (prompt_graph or {}).items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            continue

        if class_type not in object_info:
            if class_type not in seen_classes:
                seen_classes.add(class_type)
                missing_classes.append(class_type)
            # Without a spec there is nothing to check its filenames against.
            continue

        fields = MODEL_FILENAME_FIELDS.get(class_type)
        if not fields:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for field in fields:
            value = inputs.get(field)
            if not isinstance(value, str) or not value:
                # Missing, or wired from another node — not a literal filename.
                continue
            options = _field_options(object_info.get(class_type), field)
            if options is None:
                unchecked_fields += 1
                continue
            if value not in options:
                missing_models.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "field": field,
                        "value": value,
                    }
                )

    return {
        "ok": not missing_classes and not missing_models,
        "checked": True,
        "missing_node_classes": missing_classes,
        "missing_models": missing_models,
        "unchecked_fields": unchecked_fields,
    }


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

    Every field is treated as optional — a custom fork or a future version may
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

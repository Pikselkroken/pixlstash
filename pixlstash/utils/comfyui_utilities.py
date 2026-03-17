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

    return {
        "workflow": workflow,
        "is_api_format": is_api_format,
        "summary": summary,
    }

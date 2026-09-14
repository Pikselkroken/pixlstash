"""How each picture input of a workflow is filled (implementation plan §F3).

Every picture input gets one of three modes:

- **Selection**: the grid's selection fills it. At most one per workflow, and
  it sets the run count.
- **Picker**: asked when the workflow runs.
- **Fixed**: one picture chosen at setup, read-only at run time.

The inputs themselves come from :func:`pixlstash.services.workflow_io.detect_workflow_io`;
this module only decides their modes. The modes are stored beside the workflow
in the hub (``workflow_picture_input``), never written into it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

SELECTION = "selection"
PICKER = "picker"
FIXED = "fixed"
MODES = (SELECTION, PICKER, FIXED)

# The token the import dialog bakes into the input it bound. Until #1303 stops
# writing it, the bound input is the obvious default Selection.
_IMAGE_PLACEHOLDER = "{{image_path}}"


@dataclass(frozen=True)
class PictureInput:
    """One picture input and how it is filled.

    ``pixel_sha`` names the Fixed picture and is ``None`` for the other modes.
    """

    node_id: str
    title: str
    mode: str
    pixel_sha: Optional[str] = None


def _raw_node(document: dict, node_id: str) -> dict:
    """The node as the file spells it, or ``{}`` when it is not at top level.

    A UI-format node inside a subgraph has a namespaced id (``"75:61"``) that no
    entry in ``nodes`` carries, so it falls back to its class name for a title.
    """
    if isinstance(document.get("nodes"), list):
        return next(
            (
                node
                for node in document["nodes"]
                if isinstance(node, dict) and str(node.get("id")) == node_id
            ),
            {},
        )
    graph = document["prompt"] if isinstance(document.get("prompt"), dict) else document
    node = graph.get(node_id)
    return node if isinstance(node, dict) else {}


def _title(document: dict, node_id: str, class_type: str) -> str:
    node = _raw_node(document, node_id)
    meta = node.get("_meta")
    title = node.get("title") or (meta.get("title") if isinstance(meta, dict) else None)
    return title if isinstance(title, str) and title else class_type


def resolve_input_modes(
    document: dict,
    picture_inputs: dict[str, str],
    stored: list,
) -> list[PictureInput]:
    """Each detected picture input with its stored mode, or its default.

    Defaults apply to inputs nothing stored names: the input carrying the image
    placeholder, or else the first detected (lowest node id), is Selection and
    the rest are Picker. Once a setup is stored a new input is Picker, so it
    never adds a second Selection, unless the stored Selection named a node the
    file has lost: then the default Selection goes to an input with no stored
    row, so replacing a file does not quietly leave it without one.

    Args:
        document: The workflow file, in either serialisation.
        picture_inputs: Detected picture inputs, ``node_id -> class_type``, in
            detection order.
        stored: Rows with ``node_id``, ``mode`` and ``pixel_sha``.
    """
    known = {row["node_id"]: row for row in stored if row["node_id"] in picture_inputs}
    lost_selection = any(
        row["mode"] == SELECTION and row["node_id"] not in picture_inputs
        for row in stored
    ) and not any(row["mode"] == SELECTION for row in known.values())
    unstored = [node_id for node_id in picture_inputs if node_id not in known]
    default = None
    if unstored and (not known or lost_selection):
        default = next(
            (
                node_id
                for node_id in unstored
                if _IMAGE_PLACEHOLDER in json.dumps(_raw_node(document, node_id))
            ),
            unstored[0],
        )
    resolved = []
    for node_id, class_type in picture_inputs.items():
        row = known.get(node_id)
        if row is not None:
            mode, pixel_sha = row["mode"], row["pixel_sha"]
        else:
            mode, pixel_sha = (SELECTION if node_id == default else PICKER), None
        resolved.append(
            PictureInput(
                node_id=node_id,
                title=_title(document, node_id, class_type),
                mode=mode,
                pixel_sha=pixel_sha,
            )
        )
    return resolved


def validate_requested_modes(
    picture_inputs: dict[str, str], requested: object
) -> list[tuple[str, str, Optional[int]]]:
    """Check a setup sent by a client, returning ``(node_id, mode, picture_id)``.

    Every detected input must be named exactly once, so a stale client cannot
    leave an input it did not know about behind. A Fixed input without a
    ``picture_id`` keeps the picture already stored for it; the caller checks
    there is one.

    Raises:
        ValueError: The setup is malformed, names the wrong inputs, holds more
            than one Selection, or has a picture_id that is not an integer.
    """
    if not isinstance(requested, list):
        raise ValueError("inputs must be a list")
    result = []
    for item in requested:
        if not isinstance(item, dict):
            raise ValueError("each input must be an object")
        node_id, mode = item.get("node_id"), item.get("mode")
        if not isinstance(node_id, str):
            raise ValueError("each input needs a node_id")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        picture_id = item.get("picture_id") if mode == FIXED else None
        if picture_id is not None and (
            isinstance(picture_id, bool) or not isinstance(picture_id, int)
        ):
            raise ValueError(f"picture_id of input {node_id} must be an integer")
        result.append((node_id, mode, picture_id))
    named = [node_id for node_id, _, _ in result]
    if sorted(named) != sorted(picture_inputs):
        raise ValueError(
            "inputs must name each picture input exactly once: "
            + ", ".join(picture_inputs)
        )
    if sum(mode == SELECTION for _, mode, _ in result) > 1:
        raise ValueError("at most one input can be filled by the selection")
    return result

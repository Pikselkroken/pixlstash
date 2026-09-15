"""What a ComfyUI workflow needs and produces, read from the graph alone.

No placeholder, no binding, no configuration: the save node, the picture inputs
and the prompts are found from node classes and wiring. Either serialisation
works, because detection runs over the reductions in
:mod:`pixlstash.services.workflow_hash`, which inline subgraphs and step through
reroutes on the UI side. A muted or bypassed node is dropped with its edges, so
prompts wired through a bypassed node are not found.

**Reports, never writes.** The document is not modified, and where the graph
admits more than one reading (two samplers wired to different prompts) the
ambiguity is reported and the prompts are left empty rather than guessed.

Prompts are found by following each guider's own ``positive`` / ``negative``
inputs upstream, through conditioning-only nodes (``ReferenceLatent``,
``ControlNetApplyAdvanced``, ...), to the text encoder that feeds them. A path
through ``ConditioningZeroOut`` carries no prompt: that is how a model without
a negative prompt is wired. A path that ends anywhere else is reported as not
found, never as no prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

from pixlstash.services.comfyui_recipe_service import INPUT_IMAGE_FIELDS
from pixlstash.services.comfyui_service import SAVE_NODE_CLASSES
from pixlstash.services.workflow_hash import (
    ReducedNode,
    reduce_api_graph,
    reduce_ui_graph,
)

_PROMPT_SIDES = ("positive", "negative")

# Loaders the name rule below misses.
_PICTURE_INPUT_CLASSES = frozenset(INPUT_IMAGE_FIELDS) | {
    "PixlStashPictureLoader",
    "Image Load",
}


@dataclass(frozen=True)
class WorkflowIO:
    """The detected inputs and outputs of one workflow, as node ids.

    Node ids are the graph's own: an API id (``"9"``, ``"75:61"``) or a UI id
    namespaced by its subgraph instances the same way.
    """

    save_nodes: tuple[str, ...] = ()
    picture_inputs: tuple[str, ...] = ()
    # The class of each picture input, in the same order.
    picture_input_classes: tuple[str, ...] = ()
    positive_prompts: tuple[str, ...] = ()
    negative_prompts: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """A workflow with a save node produces something PixlStash can import."""
        return bool(self.save_nodes)

    @property
    def workflow_type(self) -> str:
        """``i2i`` when the workflow takes a picture, ``t2i`` otherwise."""
        return "i2i" if self.picture_inputs else "t2i"


def detect_workflow_io(document: dict) -> WorkflowIO:
    """Detect the save node, picture inputs and prompts of *document*.

    Args:
        document: A workflow in UI format (``{"nodes": [...], "links": ...}``)
            or API format (``{node_id: {"class_type", "inputs"}}``).

    Returns:
        What was found, with any ambiguity described in ``ambiguities``.

    Raises:
        WorkflowGraphError: The document cannot be read as a graph.
    """
    if isinstance(document, dict) and isinstance(document.get("nodes"), list):
        nodes = reduce_ui_graph(document)
    elif isinstance(document, dict) and isinstance(document.get("prompt"), dict):
        # The import dialog stores an embedded prompt chunk as this envelope.
        nodes = reduce_api_graph(document["prompt"])
    else:
        nodes = reduce_api_graph(document)

    # Output collection (_extract_output_node_ids) takes an explicit choice
    # first, whatever its class, and falls back to the save classes.
    chosen = document.get("pixlstash_output_nodes") or document.get(
        "pixlstash_output_node"
    )
    if chosen is not None:
        chosen = {
            str(key) for key in (chosen if isinstance(chosen, list) else [chosen])
        }
        save_nodes = sorted(key for key in nodes if key in chosen)
    else:
        save_nodes = sorted(
            key for key, node in nodes.items() if node.class_type in SAVE_NODE_CLASSES
        )
    # ponytail: name rule plus a known-class list; a loader named otherwise is
    # not found, and its image field shows as a parameter (#1306) rather than
    # an input. Typing loaders from object_info would find it.
    picture_inputs = sorted(
        key
        for key, node in nodes.items()
        if "loadimage" in node.class_type.lower().replace(" ", "")
        or node.class_type in _PICTURE_INPUT_CLASSES
    )
    ambiguities = []
    if len(save_nodes) > 1:
        ambiguities.append(f"{len(save_nodes)} save nodes: {', '.join(save_nodes)}")
    if len(picture_inputs) > 1:
        ambiguities.append(
            f"{len(picture_inputs)} picture inputs: {', '.join(picture_inputs)}"
        )

    readings = {}
    for guider_id in _guiders(nodes):
        readings[guider_id] = tuple(
            _prompts_for(nodes, guider_id, side) for side in _PROMPT_SIDES
        )
    positive: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    distinct = set(readings.values())
    if len(distinct) > 1:
        ambiguities.append(
            f"{len(readings)} samplers read different prompts: "
            f"{', '.join(sorted(readings))}"
        )
    elif distinct:
        sides = dict(zip(_PROMPT_SIDES, distinct.pop()))
        for side, ids in sides.items():
            if ids is None:
                ambiguities.append(f"{side} prompt not found")
                sides[side] = ()
            elif len(ids) > 1:
                ambiguities.append(f"{len(ids)} {side} prompts: {', '.join(ids)}")
        positive, negative = sides["positive"], sides["negative"]

    return WorkflowIO(
        save_nodes=tuple(save_nodes),
        picture_inputs=tuple(picture_inputs),
        picture_input_classes=tuple(nodes[key].class_type for key in picture_inputs),
        positive_prompts=positive,
        negative_prompts=negative,
        ambiguities=tuple(ambiguities),
    )


def _is_conditioning_input(name: str) -> bool:
    return name in _PROMPT_SIDES or "conditioning" in name


def _prompt_inputs(node: ReducedNode) -> dict[str, tuple[str, int]]:
    """A guider's prompt inputs by side. ``BasicGuider`` has one, unnamed."""
    inputs = {name: (source, slot) for name, source, slot in node.inputs}
    if "positive" not in inputs and node.class_type.endswith("Guider"):
        if "conditioning" in inputs:
            return {"positive": inputs["conditioning"]}
    return {side: inputs[side] for side in _PROMPT_SIDES if side in inputs}


def _guiders(nodes: dict[str, ReducedNode]) -> list[str]:
    """Nodes that take prompts and hand on something other than conditioning.

    ``ControlNetApplyAdvanced`` takes ``positive`` too, but its outputs feed a
    sampler's conditioning inputs; a sampler's or guider's never do.
    """
    feeds_conditioning = {
        source
        for node in nodes.values()
        for name, source, _slot in node.inputs
        if _is_conditioning_input(name)
    }
    return sorted(
        key
        for key, node in nodes.items()
        if _prompt_inputs(node) and key not in feeds_conditioning
    )


def _prompts_for(
    nodes: dict[str, ReducedNode], guider_id: str, side: str
) -> tuple[str, ...] | None:
    """The text encoders reached from one side of a guider, sorted.

    ``None`` when a path ends somewhere that is neither an encoder nor a
    ``ConditioningZeroOut``: the prompt is unknown, not absent.
    """
    start = _prompt_inputs(nodes[guider_id]).get(side)
    found = set()
    pending = [start] if start else []
    seen = set()
    while pending:
        key, slot = pending.pop()
        if (key, slot) in seen:
            continue
        seen.add((key, slot))
        node = nodes.get(key)
        if node is None:
            return None
        if "TextEncode" in node.class_type:
            found.add(key)
            continue
        if node.class_type == "ConditioningZeroOut":
            continue
        by_name = {name: (source, s) for name, source, s in node.inputs}
        if "positive" in by_name and "negative" in by_name:
            # Conditioning nodes (ControlNetApplyAdvanced) emit positive on slot
            # 0 and negative on 1. A node that also takes a model is a sampler
            # from a pack (KSampler (Efficient)) with its own output layout.
            if "model" in by_name or slot > 1:
                return None
            pending.append(by_name["positive" if slot == 0 else "negative"])
            continue
        upstream = [
            (source, s)
            for name, source, s in node.inputs
            if _is_conditioning_input(name)
        ]
        if not upstream:
            return None
        pending.extend(upstream)
    return tuple(sorted(found))

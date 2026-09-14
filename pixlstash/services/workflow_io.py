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
a negative prompt is wired.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pixlstash.services.comfyui_service import SAVE_NODE_CLASSES
from pixlstash.services.workflow_hash import (
    ReducedNode,
    reduce_api_graph,
    reduce_ui_graph,
)

_PROMPT_SIDES = ("positive", "negative")


@dataclass(frozen=True)
class WorkflowIO:
    """The detected inputs and outputs of one workflow, as node ids.

    Node ids are the graph's own: an API id (``"9"``, ``"75:61"``) or a UI id
    namespaced by its subgraph instances the same way.
    """

    save_nodes: list[str] = field(default_factory=list)
    picture_inputs: list[str] = field(default_factory=list)
    positive_prompts: list[str] = field(default_factory=list)
    negative_prompts: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)

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

    # The classes output collection imports from, so `valid` means runnable.
    save_nodes = sorted(
        key for key, node in nodes.items() if node.class_type in SAVE_NODE_CLASSES
    )
    # ponytail: name rule (LoadImage, LoadImageMask, LoadImageFromPath, ...);
    # a loader named otherwise is not found until object_info types it.
    picture_inputs = sorted(
        key for key, node in nodes.items() if "LoadImage" in node.class_type
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
    positive: list[str] = []
    negative: list[str] = []
    distinct = set(readings.values())
    if len(distinct) > 1:
        ambiguities.append(
            f"{len(readings)} samplers read different prompts: "
            f"{', '.join(sorted(readings))}"
        )
    elif distinct:
        positive, negative = (list(ids) for ids in distinct.pop())
        for side, ids in (("positive", positive), ("negative", negative)):
            if len(ids) > 1:
                ambiguities.append(f"{len(ids)} {side} prompts: {', '.join(ids)}")

    return WorkflowIO(
        save_nodes=save_nodes,
        picture_inputs=picture_inputs,
        positive_prompts=positive,
        negative_prompts=negative,
        ambiguities=ambiguities,
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


def _prompts_for(nodes: dict[str, ReducedNode], guider_id: str, side: str) -> tuple:
    """The text encoders reached from one side of a guider, sorted."""
    start = _prompt_inputs(nodes[guider_id]).get(side)
    found = set()
    pending = [start] if start else []
    seen = set()
    while pending:
        key, slot = pending.pop()
        if (key, slot) in seen or key not in nodes:
            continue
        seen.add((key, slot))
        node = nodes[key]
        if "TextEncode" in node.class_type:
            found.add(key)
            continue
        if node.class_type == "ConditioningZeroOut":
            continue
        # A node emitting both sides (ControlNetApplyAdvanced) orders them
        # positive, negative: follow the input matching the output we came from.
        by_name = {name: (source, s) for name, source, s in node.inputs}
        if "positive" in by_name and "negative" in by_name:
            pending.append(by_name["positive" if slot == 0 else "negative"])
            continue
        pending.extend(
            (source, s) for name, source, s in node.inputs if "conditioning" in name
        )
    return tuple(sorted(found))

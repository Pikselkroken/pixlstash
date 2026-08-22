"""Content-addressed identity for a ComfyUI graph: topology and structural keys.

Two tiers, both computed by one function over one reduced form:

* **Topology** — node classes and named-input edges, nothing else. The only
  tier computable from *either* ComfyUI serialisation, which is what lets a
  dropped ``workflow.json`` be filed without ComfyUI running.
* **Recipe (structural)** — that graph plus its topology assets: the model and
  image filenames a node names. Parameters and volatile values are nulled, so
  a recipe is prompt-free by construction and safe to keep forever.

Both are ``pixlstash-hash-field-classification.md`` §Node identity's corrected
rule, and the correction is the whole point of this module. The superseded rule
relabelled nodes by topological sort with ``(class_type, input signature)`` as
the tie-break, and **that tie-break does not break the ties that occur**: every
txt2img graph holds two ``CLIPTextEncode`` nodes identical on every key, so the
sort fell through to JSON serialisation order. Measured, 12 of 40 real
workflows changed their structural hash when nothing but the key order moved.

So no positional ids are assigned at all. Each node gets an order-invariant
label by Weisfeiler-Leman refinement over its sorted neighbours, and the graph
is emitted as a **sorted multiset of node descriptors** keyed on those labels.
Genuine twins produce identical descriptors, and sorting identical things is
invariant by construction, so the automorphism stops mattering. Node ids are
never read, which is also why subgraphs need no special handling on the API
side: a colon path (``75:61``) is an id, and ids do not reach the hash.

The UI format is where subgraphs *do* matter, and :func:`reduce_ui_graph`
inlines ``definitions.subgraphs`` before keying. A subgraph instance's ``type``
is a per-definition UUID, so treating it as an opaque node both under-counts the
graph and gives two people who built the same thing different keys — see
§Subgraphs.

**Accepted residual:** WL refinement cannot separate every pair of
non-isomorphic graphs, so it can in principle over-group. That is the direction
the spec declares recoverable: a later ``hash_version`` can split recipes
cleanly, whereas merging shattered ones requires guessing intent.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Stamped on every row this module's hashes key, so a change of rule is visible
# in the data rather than inferred from a build number.
HASH_VERSION = "v1"

# How many refinement rounds. The spec says 3 to 4; four is taken because the
# cost is linear in edges and the extra round is what separates nodes that are
# only distinguishable four hops out.
REFINEMENT_ROUNDS = 4

# Recursion guard for the UI graph's boundary and passthrough walks.
_MAX_RESOLVE_DEPTH = 64

MODEL_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")
IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tiff",
    ".mp4",
    ".webm",
)

# §Unknown-node defaults rule 2 and 3: a seed is volatile, an output path names
# where a file lands rather than what it is.
_SEED_RE = re.compile(r"(^|_)(seed|noise_seed)$")
_OUTPUT_PATH_RE = re.compile(r"^(output|save)_?(path|name)")

# The ComfyUI-PixlStash loaders name their asset by digest rather than by
# filename (`lora_sha256`, `checkpoint_sha256`), so the extension rules below
# cannot see them. Without this a LoRA swap on a PixlStash node would leave the
# recipe unchanged, which is the one error the spec calls unrecoverable.
_SHA256_FIELD_RE = re.compile(r"(^|_)sha256$")

# Defense in depth against a third-party node that puts a credential in a
# widget. Nothing in the shipped ComfyUI-PixlStash suite does — its connection
# settings never reach the workflow JSON — but the stored document is kept
# forever and shared, so a matching field is dropped from it outright.
SECRET_FIELD_RE = re.compile(r"(api_?key|token|auth|password|secret)", re.IGNORECASE)

# Present in the UI graph, absent from the executed API graph. Dropping them is
# what makes a UI-side topology key comparable to an API-side one.
UI_PASSTHROUGH_CLASSES = frozenset({"Reroute", "GetNode", "SetNode"})
UI_ONLY_CLASSES = (
    frozenset(
        {
            "Note",
            "MarkdownNote",
            "PrimitiveNode",
            "PrimitiveString",
            "PrimitiveStringMultiline",
            "PrimitiveInt",
            "PrimitiveFloat",
            "PrimitiveBoolean",
        }
    )
    | UI_PASSTHROUGH_CLASSES
)

# `mode` 2 is muted and 4 is bypassed. Neither reaches the executed graph.
_UI_INACTIVE_MODES = (2, 4)

# Internal node types this module invents for a subgraph's two boundary nodes.
# They are resolved through, never emitted.
_SUBGRAPH_INPUT = "\x00subgraph-input"
_SUBGRAPH_OUTPUT = "\x00subgraph-output"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class WorkflowGraphError(ValueError):
    """The graph cannot be reduced to something worth hashing."""


class MissingSubgraphDefinitionError(WorkflowGraphError):
    """A node instantiates a subgraph whose definition is not in the file.

    Reported rather than treated as a leaf: an instance node stands for the
    whole of its definition, so keying it as one opaque node silently produces
    a key for a graph that does not exist.
    """


@dataclass(frozen=True)
class ReducedNode:
    """One node, stripped to what a key is allowed to see.

    ``widgets`` is empty for the topology tier and carries ``(name, value)``
    pairs for the structural tier, where *value* is the topology-asset value or
    ``None`` for anything bucketed P or V. The name survives the nulling
    deliberately: which widgets a node has is part of its shape.
    """

    class_type: str
    widgets: tuple[tuple[str, Optional[str]], ...]
    inputs: tuple[tuple[str, str, int], ...]


def _digest(payload: Any) -> str:
    """SHA-256 over a canonical JSON rendering of *payload*."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_link(value: Any) -> bool:
    """True for an API-format ``[node_id, output_slot]`` connection."""
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _normalized_filename(value: str) -> str:
    """Lowercase basename, extension kept, directory stripped (rule 5)."""
    return value.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def structural_widget_value(name: str, value: Any) -> Optional[str]:
    """Return what the structural form keeps for this widget, or ``None``.

    ``None`` means the widget is bucket P or V and its value is nulled. A
    returned string is a topology asset (bucket TA), normalized per rule 5.
    """
    if _SEED_RE.search(name) or name == "filename_prefix":
        return None
    if _OUTPUT_PATH_RE.match(name):
        return None
    if not isinstance(value, str):
        return None
    if _SHA256_FIELD_RE.search(name):
        return value.lower()
    lowered = value.lower()
    if lowered.endswith(MODEL_EXTENSIONS) or lowered.endswith(IMAGE_EXTENSIONS):
        return _normalized_filename(value)
    return None


def reduce_api_graph(graph: dict, *, keep_widgets: bool) -> dict[str, ReducedNode]:
    """Reduce an API-format ``prompt`` graph to keyable nodes.

    Args:
        graph: The API-format graph, ``{node_id: {"class_type", "inputs"}}``.
        keep_widgets: True for the structural tier, False for topology.

    Raises:
        WorkflowGraphError: The graph holds no usable node.
    """
    if not isinstance(graph, dict):
        raise WorkflowGraphError(f"API graph is {type(graph).__name__}, not a mapping")
    nodes: dict[str, ReducedNode] = {}
    for node_id, node in graph.items():
        if not isinstance(node, dict) or "class_type" not in node:
            continue
        widgets: list[tuple[str, Optional[str]]] = []
        inputs: list[tuple[str, str, int]] = []
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, dict):
            raw_inputs = {}
        for name, value in raw_inputs.items():
            if _is_link(value):
                inputs.append((str(name), str(value[0]), int(value[1])))
            elif keep_widgets:
                widgets.append((str(name), structural_widget_value(str(name), value)))
        nodes[str(node_id)] = ReducedNode(
            class_type=str(node.get("class_type")),
            widgets=tuple(sorted(widgets)),
            inputs=tuple(sorted(inputs)),
        )
    if not nodes:
        raise WorkflowGraphError("API graph holds no node carrying a class_type")
    return nodes


def graph_key(nodes: dict[str, ReducedNode]) -> str:
    """Return the order-invariant key for a reduced graph.

    Weisfeiler-Leman refinement over sorted neighbour lists, then a sorted
    multiset of node descriptors. Nothing here reads a node id, so relabelling
    every node — or nesting half of them in a subgraph — cannot change the
    result.
    """
    if not nodes:
        raise WorkflowGraphError("cannot key an empty graph")
    labels = {
        node_id: _digest([node.class_type, node.widgets])
        for node_id, node in nodes.items()
    }
    downstream: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for node_id, node in nodes.items():
        for _, source, _ in node.inputs:
            if source in downstream:
                downstream[source].append(node_id)

    for _ in range(REFINEMENT_ROUNDS):
        labels = {
            node_id: _digest(
                [
                    labels[node_id],
                    _wired_inputs(node, labels),
                    sorted(labels[target] for target in downstream[node_id]),
                ]
            )
            for node_id, node in nodes.items()
        }

    descriptors = sorted(
        json.dumps(
            [
                node.class_type,
                _wired_inputs(node, labels),
                [[name, value] for name, value in node.widgets],
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        for node in nodes.values()
    )
    return _digest(descriptors)


def _wired_inputs(node: ReducedNode, labels: dict[str, str]) -> list[list[Any]]:
    """The node's connections, named by the upstream *label* rather than its id.

    An input whose source is absent from the graph keeps the edge — a dangling
    connection is part of the shape — but carries an empty label, so every
    dangling edge of the same name and slot looks alike.
    """
    return sorted(
        [name, labels.get(source, ""), slot] for name, source, slot in node.inputs
    )


def structural_hash(api_graph: dict) -> str:
    """The recipe key: the graph bound to its models, parameters nulled."""
    return graph_key(reduce_api_graph(api_graph, keep_widgets=True))


def topology_hash(api_graph: dict) -> str:
    """The portable key: node classes and named-input edges, nothing else."""
    return graph_key(reduce_api_graph(api_graph, keep_widgets=False))


def structural_document(api_graph: dict) -> dict:
    """The graph as it is stored: topology and assets kept, everything else nulled.

    This is what makes the library plan's §5 deletion boundary true rather than
    aspirational. A recipe is *prompt-free by construction*, so "forget the
    pictures" can purge instances and ghosts and leave the recipe standing
    without rewriting a single stored document. Node titles go too: ``_meta``
    is bucket V, and a title is something a person wrote.
    """
    document: dict[str, Any] = {}
    for node_id, node in api_graph.items():
        if not isinstance(node, dict) or "class_type" not in node:
            continue
        inputs: dict[str, Any] = {}
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, dict):
            raw_inputs = {}
        for name, value in raw_inputs.items():
            if _is_link(value):
                inputs[str(name)] = [str(value[0]), int(value[1])]
            elif SECRET_FIELD_RE.search(str(name)):
                continue
            else:
                inputs[str(name)] = structural_widget_value(str(name), value)
        document[str(node_id)] = {
            "class_type": str(node.get("class_type")),
            "inputs": inputs,
        }
    if not document:
        raise WorkflowGraphError("API graph holds no node carrying a class_type")
    return document


# --------------------------------------------------------------------------
# UI format
# --------------------------------------------------------------------------


def _normalized_ui_links(links: Any) -> list[tuple[Any, int, Any, int]]:
    """Both link spellings, as ``(origin_id, origin_slot, target_id, target_slot)``.

    The top level writes a positional array
    ``[id, origin_id, origin_slot, target_id, target_slot, type]``; a subgraph
    definition writes an object with the same fields named. Real files carry
    both, in the same file.
    """
    out: list[tuple[Any, int, Any, int]] = []
    for link in links or ():
        if isinstance(link, list) and len(link) >= 5:
            out.append((link[1], link[2], link[3], link[4]))
        elif isinstance(link, dict) and "origin_id" in link:
            out.append(
                (
                    link.get("origin_id"),
                    link.get("origin_slot", 0),
                    link.get("target_id"),
                    link.get("target_slot", 0),
                )
            )
    return [
        (origin, int(origin_slot or 0), target, int(target_slot or 0))
        for origin, origin_slot, target, target_slot in out
        if origin is not None and target is not None
    ]


def _slot_index_by_name(entries: Any) -> dict[str, int]:
    """Map a node's or definition's input/output names onto their slot indices."""
    mapping: dict[str, int] = {}
    for index, entry in enumerate(entries or ()):
        if isinstance(entry, dict) and entry.get("name") is not None:
            mapping.setdefault(str(entry["name"]), index)
    return mapping


class _UiGraph:
    """A UI workflow flattened to one namespaced node table and one link table.

    Subgraph instances are expanded in place. Their boundaries survive as two
    synthetic nodes so that resolution can step across them the same way it
    steps across a ``Reroute``, rather than needing the links rewritten.
    """

    def __init__(self, workflow: dict, *, inline: bool):
        self.definitions = _subgraph_definitions(workflow) if inline else {}
        self.inline = inline
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, int], tuple[str, int]] = {}
        self._flatten(workflow.get("nodes"), workflow.get("links"), prefix="", depth=0)

    def _flatten(self, node_list: Any, links: Any, *, prefix: str, depth: int) -> None:
        if depth > _MAX_RESOLVE_DEPTH:
            raise WorkflowGraphError("subgraph nesting exceeded the depth guard")
        for node in node_list or ():
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            key = f"{prefix}{node['id']}"
            self.nodes[key] = node
            node_type = str(node.get("type", "?"))
            definition = self.definitions.get(node_type)
            if definition is None:
                if (
                    self.inline
                    and _UUID_RE.match(node_type)
                    and node.get("mode") not in _UI_INACTIVE_MODES
                ):
                    raise MissingSubgraphDefinitionError(
                        f"node {key} instantiates subgraph {node_type}, which is "
                        "absent from definitions.subgraphs"
                    )
                continue
            self._expand(key, node, definition, depth=depth)
        for origin, origin_slot, target, target_slot in _normalized_ui_links(links):
            self.edges[(f"{prefix}{target}", target_slot)] = (
                f"{prefix}{origin}",
                origin_slot,
            )

    def _expand(self, key: str, node: dict, definition: dict, *, depth: int) -> None:
        """Bring one subgraph definition's nodes in under the instance's key."""
        inner = f"{key}:"
        input_node = definition.get("inputNode") or {}
        output_node = definition.get("outputNode") or {}
        boundary_in = f"{inner}{input_node.get('id', -10)}"
        boundary_out = f"{inner}{output_node.get('id', -20)}"
        self.nodes[boundary_in] = {
            "type": _SUBGRAPH_INPUT,
            "instance": key,
            # The definition's declared inputs, in slot order, mapped onto the
            # instance's own input slots BY NAME: an instance lists only the
            # inputs it actually uses, so the two arrays differ in both length
            # and order (measured: 3 against 7).
            "outer_slot": _boundary_slot_map(node, definition),
        }
        self.nodes[boundary_out] = {"type": _SUBGRAPH_OUTPUT}
        self.nodes[key] = dict(node, _definition=definition, _boundary_out=boundary_out)
        self._flatten(
            definition.get("nodes"),
            definition.get("links"),
            prefix=inner,
            depth=depth + 1,
        )

    def source_of(
        self, key: str, slot: int, depth: int = 0
    ) -> Optional[tuple[str, int]]:
        """Follow the edge into ``(key, slot)`` back to a node a key may see."""
        edge = self.edges.get((key, slot))
        if edge is None:
            return None
        return self.resolve_output(edge[0], edge[1], depth + 1)

    def resolve_output(
        self, key: str, slot: int, depth: int = 0
    ) -> Optional[tuple[str, int]]:
        """Resolve an origin to a real node, stepping through everything else."""
        if depth > _MAX_RESOLVE_DEPTH:
            logger.warning(
                "Gave up resolving UI graph origin %s slot %s at depth %s; the "
                "graph has a passthrough or subgraph cycle.",
                key,
                slot,
                depth,
            )
            return None
        node = self.nodes.get(key)
        if node is None or node.get("mode") in _UI_INACTIVE_MODES:
            return None
        node_type = str(node.get("type", "?"))
        if node_type == _SUBGRAPH_INPUT:
            outer = node["outer_slot"]
            if slot >= len(outer) or outer[slot] is None:
                return None
            return self.source_of(node["instance"], outer[slot], depth + 1)
        if "_definition" in node:
            outputs = node.get("outputs") or ()
            if slot >= len(outputs):
                return None
            name = str((outputs[slot] or {}).get("name"))
            inner_slot = _slot_index_by_name(node["_definition"].get("outputs")).get(
                name
            )
            if inner_slot is None:
                return None
            return self.source_of(node["_boundary_out"], inner_slot, depth + 1)
        if node_type in UI_PASSTHROUGH_CLASSES:
            for index in range(len(node.get("inputs") or ())):
                resolved = self.source_of(key, index, depth + 1)
                if resolved is not None:
                    return resolved
            return None
        if node_type in UI_ONLY_CLASSES:
            return None
        return (key, slot)

    def reduce(self) -> dict[str, ReducedNode]:
        """Emit the real nodes with their edges resolved to real producers."""
        reduced: dict[str, ReducedNode] = {}
        for key, node in self.nodes.items():
            node_type = str(node.get("type", "?"))
            if (
                node_type in (_SUBGRAPH_INPUT, _SUBGRAPH_OUTPUT)
                or "_definition" in node
            ):
                continue
            if node_type in UI_ONLY_CLASSES or node.get("mode") in _UI_INACTIVE_MODES:
                continue
            inputs: list[tuple[str, str, int]] = []
            for index, entry in enumerate(node.get("inputs") or ()):
                if not isinstance(entry, dict):
                    continue
                resolved = self.source_of(key, index)
                if resolved is not None:
                    inputs.append(
                        (str(entry.get("name", "?")), resolved[0], resolved[1])
                    )
            reduced[key] = ReducedNode(
                class_type=node_type, widgets=(), inputs=tuple(sorted(inputs))
            )
        if not reduced:
            raise WorkflowGraphError("UI graph holds no executable node")
        return reduced


def _boundary_slot_map(node: dict, definition: dict) -> list[Optional[int]]:
    """Definition input slots mapped onto the instance's own input slots, by name.

    An instance lists only the inputs it actually wires; the definition lists
    every one it declares. Measured on a real file: 3 against 7, in a different
    order. Matching by position would silently cross the wires.
    """
    outer = _slot_index_by_name(node.get("inputs"))
    return [
        outer.get(str(entry.get("name")))
        for entry in (definition.get("inputs") or ())
        if isinstance(entry, dict)
    ]


def _subgraph_definitions(workflow: dict) -> dict[str, dict]:
    definitions = (workflow.get("definitions") or {}).get("subgraphs") or ()
    return {
        str(definition["id"]): definition
        for definition in definitions
        if isinstance(definition, dict) and definition.get("id") is not None
    }


def reduce_ui_graph(workflow: dict, *, inline: bool = True) -> dict[str, ReducedNode]:
    """Reduce a UI-format ``workflow`` chunk to topology-keyable nodes.

    Args:
        workflow: The UI-format workflow document.
        inline: Expand ``definitions.subgraphs`` first. **Only ever False in
            the fixture that proves the step is not silently droppable** — a
            collapsed graph keys as a fraction of its node count and its
            instance types are per-user UUIDs.
    """
    if not isinstance(workflow, dict):
        raise WorkflowGraphError(
            f"UI workflow is {type(workflow).__name__}, not a mapping"
        )
    return _UiGraph(workflow, inline=inline).reduce()


def ui_topology_hash(workflow: dict, *, inline: bool = True) -> str:
    """The portable key, computed from the UI serialisation."""
    return graph_key(reduce_ui_graph(workflow, inline=inline))

"""Content-addressed identity for a ComfyUI graph: topology and structural keys.

Two tiers, both computed by one function over one reduced form:

* **Topology** - node classes and named-input edges, nothing else. The only
  tier computable from *either* ComfyUI serialisation, which is what lets a
  dropped ``workflow.json`` be filed without ComfyUI running.
* **Recipe (structural)** - that graph plus its topology assets: the model and
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
graph and gives two people who built the same thing different keys - see
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

# Recursion guard for the UI graph's boundary and passthrough walks. Counted in
# CALL frames, and `source_of` and `resolve_output` each take one, so this is
# ~128 hops rather than 256 - comfortably past any chain of reroutes a person
# would build, while still bounded well under CPython's own recursion limit.
_MAX_RESOLVE_DEPTH = 256

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

# Inputs that carry what a person WROTE. The extension rules below are
# name-blind by design (§Unknown-node defaults rule 4 and 5 exist because
# custom nodes are unbounded), and name-blindness has one bad case: a prompt
# ending in an image extension is read as a topology asset, which both forks
# the recipe on a prompt-only edit and writes the prompt into the stored graph,
# breaking the prompt-free guarantee library plan §5 rests on.
#
# So prose-carrying inputs are excluded first. The set is small and anchored
# rather than a substring match, so a custom node's `text_encoder_name` still
# resolves as the asset it is. Measured against the owner's libraries: not one
# of these names currently reaches the extension test, so this closes a hole
# without moving a single existing key.
_TEXT_FIELD_NAMES = frozenset(
    {
        "text",
        "text_g",
        "text_l",
        "prompt",
        "caption",
        "description",
        "wildcard",
    }
)
_TEXT_FIELD_SUFFIX_RE = re.compile(r"_(text|prompt|caption|query|search)$", re.I)

# A filename is one path component; prose is not. A newline can never appear in
# a filename and 255 bytes is the component limit on every filesystem
# PixlStash runs on, so neither guard can reject a real asset. Measured: zero
# real TA values trip either. They are the backstop for a prose field this
# module has not been told about - 5,066 genuine `lora_name` and `image` values
# DO contain spaces, which is why "has a space" is not one of these rules.
_MAX_FILENAME_LENGTH = 255

# The ComfyUI-PixlStash loaders name their asset by digest rather than by
# filename (`lora_sha256`, `checkpoint_sha256`), so the extension rules below
# cannot see them. Without this a LoRA swap on a PixlStash node would leave the
# recipe unchanged, which is the one error the spec calls unrecoverable.
_SHA256_FIELD_RE = re.compile(r"(^|_)sha256$")

# Defense in depth against a third-party node that puts a credential in a
# widget. Nothing in the shipped ComfyUI-PixlStash suite does - its connection
# settings never reach the workflow JSON - but the stored document is kept
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

    ``instance_widgets`` is the same list one tier finer: the parameters
    themselves, with only the **volatile** bucket nulled. A seed is V, not P
    ("a generation is an instance plus a seed"), so it is absent here and two
    re-rolls of one prompt share an instance. Carried on the same node rather
    than reduced separately, because the walk is the expensive part.
    """

    class_type: str
    widgets: tuple[tuple[str, Optional[str]], ...]
    inputs: tuple[tuple[str, str, int], ...]
    instance_widgets: tuple[tuple[str, Any], ...] = ()


def _digest(payload: Any) -> str:
    """SHA-256 over a canonical JSON rendering of *payload*."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_link(value: Any) -> bool:
    """True for an API-format ``[node_id, output_slot]`` connection.

    **The node id must be a string, and that is load-bearing rather than
    defensive.** A widget can legitimately hold a two-element list of numbers -
    a resolution pair, a pair of coordinates - and reading one as a connection
    puts a bucket-P value into the topology *and* writes a dangling edge into
    the stored document. The spec calls that direction unrecoverable:
    "misclassifying a param as T shatters grouping into near-duplicate recipes
    (destroys the feature)". Measured over the owner's three libraries, all
    501,128 links in 28,289 API graphs carry a string node id, and every one of
    them resolves to a node in its own graph.
    """
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
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
    lowered = name.lower()
    if lowered in _TEXT_FIELD_NAMES or _TEXT_FIELD_SUFFIX_RE.search(lowered):
        return None
    if "\n" in value or len(value) > _MAX_FILENAME_LENGTH:
        return None
    lowered = value.lower()
    if lowered.endswith(MODEL_EXTENSIONS) or lowered.endswith(IMAGE_EXTENSIONS):
        return _normalized_filename(value)
    return None


def instance_widget_value(name: str, value: Any) -> Any:
    """Return what the instance form keeps for this widget, or ``None``.

    The instance tier is the recipe plus **one set of parameters, including the
    prompt**, so this keeps everything the structural form nulls -- steps, cfg,
    sampler, dimensions, the text somebody wrote -- and drops only bucket V.

    V is what makes one *generation* rather than one instance: a seed, and the
    output path a file happens to land at. Null them and two re-rolls of the
    same prompt share an instance, which is the equivalence "Covered only" is
    asking about. Keep them and every picture is its own instance and the tier
    answers nothing.

    Credential-named widgets never reach here at all: :func:`reduce_api_graph`
    drops them before bucketing, so they are absent from this key as well as
    from the stored document.
    """
    if _SEED_RE.search(name) or name == "filename_prefix":
        return None
    if _OUTPUT_PATH_RE.match(name):
        return None
    return value


def asset_reference(normalized_filename: str) -> str:
    """The stable, unreadable token a stored document names an asset by.

    Content-derived, so two recipes using one model name it identically and a
    document rebuilt from the same graph is byte-identical. Deliberately NOT
    the name: :func:`document_from_reduction` explains why the readable form
    lives only in ``workflow_recipe_asset``.
    """
    return "asset:" + hashlib.sha256(normalized_filename.encode("utf-8")).hexdigest()


def reduce_api_graph(graph: dict) -> dict[str, ReducedNode]:
    """Reduce an API-format ``prompt`` graph to keyable nodes, once.

    One walk serves all three products: :func:`structural_hash` keys this
    directly, :func:`topology_hash` keys :func:`drop_widgets` of it, and
    :func:`structural_document` renders it. The backfill is a pass over every
    picture in every library, so re-parsing the same graph three times is a
    cost worth not paying.

    Args:
        graph: The API-format graph, ``{node_id: {"class_type", "inputs"}}``.

    Raises:
        WorkflowGraphError: The graph holds no usable node.
    """
    if not isinstance(graph, dict):
        raise WorkflowGraphError(f"API graph is {type(graph).__name__}, not a mapping")
    nodes: dict[str, ReducedNode] = {}
    for node_id, node in graph.items():
        if not isinstance(node, dict) or "class_type" not in node:
            # Not node-shaped at all. Skipped rather than refused, because a
            # caller may hand us a prompt envelope carrying sibling keys.
            continue
        # Node-shaped but malformed is a different case, and it is refused.
        # `str(None)` would otherwise become the class name "None" and key as a
        # perfectly ordinary one-node recipe. Measured: zero occurrences in
        # 28,289 real API graphs, so nothing real is being rejected here.
        class_type = node["class_type"]
        if not isinstance(class_type, str) or not class_type:
            raise WorkflowGraphError(
                f"node {node_id} has class_type {class_type!r}, which is not a "
                "non-empty string"
            )
        widgets: list[tuple[str, Optional[str]]] = []
        instance_widgets: list[tuple[str, Any]] = []
        inputs: list[tuple[str, str, int]] = []
        raw_inputs = node.get("inputs")
        if raw_inputs is None:
            raw_inputs = {}
        elif not isinstance(raw_inputs, dict):
            raise WorkflowGraphError(
                f"node {node_id} has inputs of type "
                f"{type(raw_inputs).__name__}, which is not a mapping"
            )
        for name, value in raw_inputs.items():
            name = str(name)
            if _is_link(value):
                inputs.append((name, str(value[0]), int(value[1])))
            elif SECRET_FIELD_RE.search(name):
                # Dropped here rather than nulled, so a credential-named widget
                # is absent from the stored document AND absent from the key.
                # Whether a third-party node carried one is not what makes a
                # graph a different graph.
                logger.info(
                    "Dropping widget %r on node class %s from the workflow "
                    "recipe: its name matches the credential pattern.",
                    name,
                    node.get("class_type"),
                )
            else:
                widgets.append((name, structural_widget_value(name, value)))
                instance_widgets.append((name, instance_widget_value(name, value)))
        nodes[str(node_id)] = ReducedNode(
            class_type=class_type,
            widgets=tuple(sorted(widgets)),
            inputs=tuple(sorted(inputs)),
            # Sorted on the NAME alone. Input names are unique within a node so
            # the values are never compared, and a raw parameter value can be a
            # dict, which does not order.
            instance_widgets=tuple(sorted(instance_widgets, key=lambda kv: kv[0])),
        )
    if not nodes:
        raise WorkflowGraphError("API graph holds no node carrying a class_type")
    return nodes


def drop_widgets(nodes: dict[str, ReducedNode]) -> dict[str, ReducedNode]:
    """The topology tier: the same graph with every widget removed.

    Topology is *node classes and named-input edges, nothing else*, so it is
    literally the structural reduction minus its assets - which is also why a
    recipe can never span two topologies.
    """
    return {
        node_id: ReducedNode(node.class_type, (), node.inputs)
        for node_id, node in nodes.items()
    }


def promote_instance_widgets(nodes: dict[str, ReducedNode]) -> dict[str, ReducedNode]:
    """The instance tier: the same graph keyed on its parameters as well.

    The spec words this tier as "structural hash plus canonical JSON of all P
    values". It is rendered here as one more reduction through the *same*
    :func:`graph_key` instead, and deliberately: a flat JSON of parameters has
    to key them by node id, and node ids are the one thing this module refuses
    to read -- keying by them is exactly the superseded rule that re-keyed 12 of
    40 real workflows when nothing but the serialisation order moved. Riding the
    WL-labelled descriptor keeps the tier order-invariant like the two above it,
    and the equivalence classes are the ones the spec asks for.
    """
    return {
        node_id: ReducedNode(node.class_type, node.instance_widgets, node.inputs)
        for node_id, node in nodes.items()
    }


def graph_key(nodes: dict[str, ReducedNode]) -> str:
    """Return the order-invariant key for a reduced graph.

    Weisfeiler-Leman refinement over sorted neighbour lists, then a sorted
    multiset of node descriptors. Nothing here reads a node id, so relabelling
    every node - or nesting half of them in a subgraph - cannot change the
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
            # The instance tier puts RAW parameter values through here; every
            # one comes from JSON so this never fires, and it is the difference
            # between a key and a crash if a caller ever hands us one that does.
            default=str,
        )
        for node in nodes.values()
    )
    return _digest(descriptors)


def _wired_inputs(node: ReducedNode, labels: dict[str, str]) -> list[list[Any]]:
    """The node's connections, named by the upstream *label* rather than its id.

    An input whose source is absent from the graph keeps the edge - a dangling
    connection is part of the shape - but carries an empty label, so every
    dangling edge of the same name and slot looks alike.
    """
    return sorted(
        [name, labels.get(source, ""), slot] for name, source, slot in node.inputs
    )


def structural_hash(api_graph: dict) -> str:
    """The recipe key: the graph bound to its models, parameters nulled."""
    return graph_key(reduce_api_graph(api_graph))


def topology_hash(api_graph: dict) -> str:
    """The portable key: node classes and named-input edges, nothing else."""
    return graph_key(drop_widgets(reduce_api_graph(api_graph)))


def instance_hash(api_graph: dict) -> str:
    """The instance key: the recipe plus one set of parameters, seed excluded.

    **This is a PICTURE column, not a hub table.** Two pictures share an
    instance exactly when they share this value, which is all "Covered only"
    needs; storing instances hub-side is AI-toolkit Phase 2 and belongs to
    v1.12. Nothing in v1.11 writes an instance row anywhere.
    """
    return graph_key(promote_instance_widgets(reduce_api_graph(api_graph)))


def assets_from_reduction(nodes: dict[str, ReducedNode]) -> list[tuple[str, str]]:
    """Every ``(widget_name, normalized_filename)`` the recipe names.

    The readable half of what the stored document refers to by
    :func:`asset_reference`, and the substrate the model-companions plan's
    Workflow sets are built on. Deduplicated and ordered, so re-filing one
    graph writes the identical row set.
    """
    return sorted(
        {
            (name, value)
            for node in nodes.values()
            for name, value in node.widgets
            if value is not None
        }
    )


def document_from_reduction(nodes: dict[str, ReducedNode]) -> dict:
    """Render a reduced graph back as the document that gets stored.

    This is what makes the library plan's §5 deletion boundary true rather than
    aspirational. A recipe is *prompt-free by construction*, so "forget the
    pictures" can purge instances and ghosts and leave the recipe standing.
    Node titles go the same way: ``_meta`` is bucket V, and a title is
    something a person wrote.

    **Prompt-free was never the whole of it, and the earlier wording here said
    it was.** Bucket TA survives the nulling by design, and TA is model and
    image filenames -- which on a real shelf name people (a character LoRA is
    named after its subject) and state content. So this document carries an
    :func:`asset_reference` in place of every asset value, and
    ``workflow_recipe_asset`` is the only home of the readable name. Forgetting
    a model name is then a row delete: **no stored graph is ever rewritten and
    ``document_sha256`` stays valid**, which is the property the old docstring
    claimed and did not have.

    The substitution is uniform, including the PixlStash loaders'
    ``*_sha256`` values, which name nobody. One rule -- *the document holds
    references, never asset values* -- cannot be got half right by the next
    reader, where "filenames but not digests" invites exactly that.

    Neither hash moves: :func:`structural_hash` and :func:`topology_hash` key
    :func:`reduce_api_graph`, not this rendering.

    It is rendered from the reduction rather than from the graph so that the
    document and the hash can never disagree about what was kept.
    """
    if not nodes:
        raise WorkflowGraphError("cannot render an empty graph")
    document: dict[str, Any] = {}
    for node_id, node in nodes.items():
        inputs: dict[str, Any] = {
            name: (asset_reference(value) if value is not None else None)
            for name, value in node.widgets
        }
        inputs.update({name: [source, slot] for name, source, slot in node.inputs})
        document[node_id] = {"class_type": node.class_type, "inputs": inputs}
    return document


def structural_document(api_graph: dict) -> dict:
    """The graph as it is stored: topology and assets kept, everything else nulled."""
    return document_from_reduction(reduce_api_graph(api_graph))


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
            inactive = node.get("mode") in _UI_INACTIVE_MODES
            if definition is None:
                if self.inline and _UUID_RE.match(node_type) and not inactive:
                    raise MissingSubgraphDefinitionError(
                        f"node {key} instantiates subgraph {node_type}, which is "
                        "absent from definitions.subgraphs"
                    )
                continue
            if inactive:
                # A muted or bypassed instance does not execute, and neither
                # does anything inside it. Expanding it anyway would leave its
                # whole node set standing while every edge through it vanished
                # - a key for a graph ComfyUI has never run. The instance node
                # itself stays registered and is dropped by `reduce`, exactly
                # as a bypassed ordinary node is.
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
        self.nodes[key] = dict(node, _definition=definition, _boundary_out=boundary_out)
        self._flatten(
            definition.get("nodes"),
            definition.get("links"),
            prefix=inner,
            depth=depth + 1,
        )
        # AFTER the definition's own node list, deliberately. A definition that
        # serialises its IO nodes into `nodes` would otherwise overwrite these
        # two synthetic entries and key both boundaries as ordinary graph nodes
        # (9 nodes emitted for a 7-node graph). Ours must win.
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
            # Refused, not dropped. Returning None here would delete one edge
            # and hand back a confident key for a graph missing a connection,
            # which is the silent-failure shape the house rules forbid; a key
            # nobody can compute is a state the caller can report.
            raise WorkflowGraphError(
                f"gave up resolving UI graph origin {key} slot {slot} after "
                f"{_MAX_RESOLVE_DEPTH} hops: a passthrough or subgraph cycle, "
                "or a chain longer than this guard allows"
            )
        node = self.nodes.get(key)
        if node is None:
            # A link naming a node that is nowhere in the file. Dropping it
            # would hash identically to the same graph with that connection
            # deleted, and would do so while the API-side reduction KEEPS its
            # dangling edges - so the two serialisations of one workflow could
            # key differently, which is the portability claim this tier exists
            # for. Measured: zero occurrences in 28,069 real UI graphs, so this
            # refuses malformed input rather than rejecting anything real.
            raise WorkflowGraphError(
                f"UI graph link names origin node {key}, which the file does not define"
            )
        if node.get("mode") in _UI_INACTIVE_MODES:
            return None
        node_type = str(node.get("type", "?"))
        if node_type == _SUBGRAPH_INPUT:
            outer = node["outer_slot"]
            if slot >= len(outer):
                logger.warning(
                    "Subgraph instance %s declares %d inputs but an inner link "
                    "reads slot %d; the edge is dropped from the key.",
                    node["instance"],
                    len(outer),
                    slot,
                )
                return None
            if outer[slot] is None:
                # Normal and common: the instance proxies that input as a
                # widget rather than wiring it, so there is no edge to follow.
                return None
            return self.source_of(node["instance"], outer[slot], depth + 1)
        if "_definition" in node:
            outputs = node.get("outputs") or ()
            entry = outputs[slot] if slot < len(outputs) else None
            name = str((entry or {}).get("name")) if isinstance(entry, dict) else None
            inner_slot = (
                _slot_index_by_name(node["_definition"].get("outputs")).get(name)
                if name is not None
                else None
            )
            if inner_slot is None:
                logger.warning(
                    "Subgraph instance %s output slot %d does not match any "
                    "output its definition declares; the edge is dropped from "
                    "the key.",
                    key,
                    slot,
                )
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
    # A malformed entry emits None rather than being skipped: skipping would
    # shift every later slot by one and quietly cross the wires, which is the
    # exact failure matching by name exists to avoid.
    return [
        outer.get(str(entry.get("name"))) if isinstance(entry, dict) else None
        for entry in (definition.get("inputs") or ())
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
            the fixture that proves the step is not silently droppable** - a
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

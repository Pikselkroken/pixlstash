"""How each picture input of a workflow is filled (implementation plan §F3).

Every picture input gets one of three modes:

- **Selection**: the grid's selection fills it. At most one per workflow, and
  it sets the run count.
- **Picker**: asked when the workflow runs.
- **Fixed**: one picture chosen at setup, read-only at run time.

The inputs themselves come from :func:`pixlstash.services.workflow_io.detect_workflow_io`;
this module only decides their modes. The modes are stored beside the workflow
in the hub (``workflow_key_picture_input`` for a card), never written into it.

A card addresses an input by ``(slot_label, input_name)`` and detection speaks
node ids; :func:`card_input_modes` is the bridge, and :func:`resolve_fills` is
the one place the order a run fills them in is written down (#1457).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from pixlstash.services import workflow_bindings
from pixlstash.services.workflow_hash import reduce_api_graph, structural_document
from pixlstash.services.workflow_identity import topology_node_labels
from pixlstash.services.workflow_io import (
    api_graph,
    detect_workflow_io,
    picture_fields,
)

SELECTION = "selection"
PICKER = "picker"
FIXED = "fixed"
MODES = (SELECTION, PICKER, FIXED)

# How one input of a run was answered, in the order :func:`resolve_fills` asks.
FILL_REQUEST = "request"
FILL_FIXED = "fixed"
FILL_SELECTION = "selection"


@dataclass(frozen=True)
class PictureInput:
    """One picture input and how it is filled.

    ``pixel_sha`` names the Fixed picture, or for another mode the one the input
    had before it left Fixed, if any.
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
    graph = api_graph(document)
    if graph is None:
        return next(
            (
                node
                # An editor document is one the hints can name, so ``nodes`` is
                # not guaranteed to be there, let alone to be a list.
                for node in document.get("nodes") or ()
                if isinstance(node, dict) and str(node.get("id")) == node_id
            ),
            {},
        )
    node = graph.get(node_id)
    return node if isinstance(node, dict) else {}


def node_title(document: dict, node_id: str, class_type: str) -> str:
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

    Defaults apply to inputs nothing stored names: the input a run already fills
    with the picture (its migrated binding, or the one detected picture input),
    or else the first detected (lowest node id), is Selection and
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
        # Only with a document: without one, which input defaults does not
        # matter, only whether one does.
        filled = set()
        if document:
            filled = {
                workflow_bindings.target_node(document, target.get("path"))
                for target in workflow_bindings.run_targets(document)[
                    workflow_bindings.IMAGE
                ]
            }
        default = next(
            (node_id for node_id in unstored if node_id in filled), unstored[0]
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
                title=node_title(document, node_id, class_type),
                mode=mode,
                pixel_sha=pixel_sha,
            )
        )
    return resolved


@dataclass(frozen=True)
class CardInput:
    """One picture input of a card, as the card's stored setup addresses it.

    ``node_ids`` is plural because the address is derived from the topology:
    two loaders the graph cannot tell apart share one label, and a setup
    written against that label answers for both, so a fill reaches both.
    ``class_type`` is the first node's, which is the one ``input_name`` came
    from.
    """

    slot_label: str
    input_name: str
    title: str
    mode: str
    pixel_sha: Optional[str]
    node_ids: tuple[str, ...]
    class_type: str

    @property
    def address(self) -> tuple[str, str]:
        return (self.slot_label, self.input_name)


def card_input_modes(graph: dict, stored: list[dict]) -> list[CardInput]:
    """Every picture input of *graph*, with a card's stored mode or its default.

    The label-addressed twin of :func:`resolve_input_modes`, and a wrapper of
    it rather than a restatement: the stored rows are mapped onto node ids
    through :func:`topology_node_labels` - the bridge ``_apply_addressed``
    already uses for parameter values - so the defaulting rule (one
    Selection, the rest Picker, a lost Selection re-granted) is written once.

    Enumerated from the graph and not from the rows, because a card nobody
    has set up has no rows at all and still has inputs.

    Args:
        graph: The API-format graph a run resolved for the card.
        stored: The card's rows, ``[{slot_label, input_name, mode, pixel_sha}]``.

    Raises:
        WorkflowGraphError: The graph will not reduce, so nothing in it can be
            addressed.
    """
    detected = detect_workflow_io(graph)
    labels = _picture_input_labels(graph, detected.picture_inputs)
    nodes_by_address: dict[tuple[str, str], list[str]] = {}
    class_of: dict[str, str] = {}
    for node_id, class_type in zip(
        detected.picture_inputs, detected.picture_input_classes
    ):
        label = labels.get(node_id)
        if label is None:
            continue
        class_of[node_id] = class_type
        nodes_by_address.setdefault((label, picture_fields(class_type)[0]), []).append(
            node_id
        )
    representative = {address: ids[0] for address, ids in nodes_by_address.items()}
    # A row whose address the graph no longer has keeps a node id no input
    # carries, which is exactly what `resolve_input_modes` reads as "lost".
    by_node = [
        {
            **row,
            "node_id": representative.get(
                (row["slot_label"], row["input_name"]),
                f"lost:{row['slot_label']}/{row['input_name']}",
            ),
        }
        for row in stored
    ]
    modes = resolve_input_modes(
        graph,
        {node_id: class_of[node_id] for node_id in sorted(representative.values())},
        by_node,
    )
    address_of = {node_id: address for address, node_id in representative.items()}
    return [
        CardInput(
            slot_label=address_of[item.node_id][0],
            input_name=address_of[item.node_id][1],
            title=item.title,
            mode=item.mode,
            pixel_sha=item.pixel_sha,
            node_ids=tuple(nodes_by_address[address_of[item.node_id]]),
            class_type=class_of[item.node_id],
        )
        for item in modes
    ]


def _picture_input_labels(graph: dict, picture_nodes) -> dict[str, str]:
    """A slot label per picture input, telling apart what the topology cannot.

    :func:`topology_node_labels` refines a node by its neighbours' labels and
    not by which of their inputs it feeds, so two loaders wired into one node -
    a subject into ``latent_image`` and a reference beside it, the ordinary
    shape of a reference-to-image workflow - get the same label. Those are
    given a suffix digesting the edges they feed, ``(consumer label, input
    name, output slot)``: derived from the topology like the label itself, so a
    card's three graph tiers still agree, and never from a node id. Loaders
    that match even on that share one address, which then answers for both.
    """
    labels = topology_node_labels(structural_document(graph))
    counts = Counter(labels[node_id] for node_id in picture_nodes if node_id in labels)
    if all(count == 1 for count in counts.values()):
        return labels
    feeds: dict[str, list] = {node_id: [] for node_id in picture_nodes}
    for consumer_id, node in reduce_api_graph(graph).items():
        for name, source, slot in node.inputs:
            if source in feeds:
                feeds[source].append([labels.get(consumer_id, ""), name, slot])
    refined = dict(labels)
    for node_id in picture_nodes:
        label = labels.get(node_id)
        if label is not None and counts[label] > 1:
            edges = json.dumps(sorted(feeds[node_id]), separators=(",", ":"))
            digest = hashlib.sha256(edges.encode("utf-8")).hexdigest()
            refined[node_id] = f"{label}:{digest[:16]}"
    return refined


@dataclass(frozen=True)
class InputFill:
    """How one input of one run is answered.

    ``how`` is ``None`` for an input nothing answered; ``picture_id`` is the
    one picture every submission feeds it, and ``None`` when the selection
    does - one of its pictures per submission.
    """

    input: CardInput
    how: Optional[str] = None
    picture_id: Optional[int] = None


def resolve_fills(
    inputs: list[CardInput],
    requested: dict[tuple[str, str], Optional[int]],
    pinned: dict[str, int],
    has_selection: bool,
) -> list[InputFill]:
    """Answer each picture input of one run, strictly in this order.

    1. the request's own entry for that address: a picture, or ``None`` for
       "the selection goes here";
    2. a Fixed pin whose content a kept picture still holds;
    3. a stored Selection, when the run has a selection and the request has
       not sent it to another input;
    4. **the lone-unresolved-input rule**: when 1-3 leave exactly one input
       open, the run has a selection and nothing has taken it yet, the
       selection fills that one.

    **Step 4 is a whole-card decision and is taken only after 1-3 have been
    applied to every input.** Asked per input ("is this the only picture
    input?") it would refuse every two-input workflow with a pinned
    reference, which is the case it exists for. A pin whose picture has gone
    does not resolve its input, so a dead reference leaves two open and the
    rule does not guess between them.

    Args:
        inputs: :func:`card_input_modes`' answer for the card.
        requested: The request's entries by address.
        pinned: ``{pixel_sha: picture_id}`` for the pins that are still kept.
        has_selection: Whether the run carries pictures a Selection can take.

    Returns:
        One :class:`InputFill` per input, in *inputs*' order; an unanswered
        one has ``how`` ``None`` and is the caller's to judge.
    """
    # A request that sends the selection somewhere has moved it, so a stored
    # (or defaulted) Selection elsewhere yields rather than reading it twice.
    # Asked of THIS card's addresses only: a request spanning several cards
    # that routes the selection on one of them has said nothing about another.
    routed = has_selection and any(
        item.address in requested and requested[item.address] is None for item in inputs
    )
    fills: list[InputFill] = []
    for item in inputs:
        if item.address in requested:
            picture_id = requested[item.address]
            if picture_id is not None:
                fills.append(InputFill(item, FILL_REQUEST, picture_id))
                continue
            if has_selection:
                fills.append(InputFill(item, FILL_SELECTION))
                continue
        elif item.mode == FIXED and item.pixel_sha in pinned:
            fills.append(InputFill(item, FILL_FIXED, pinned[item.pixel_sha]))
            continue
        elif item.mode == SELECTION and has_selection and not routed:
            fills.append(InputFill(item, FILL_SELECTION))
            continue
        fills.append(InputFill(item))
    open_at = [index for index, fill in enumerate(fills) if fill.how is None]
    selection_taken = any(fill.how == FILL_SELECTION for fill in fills)
    if has_selection and not selection_taken and len(open_at) == 1:
        index = open_at[0]
        fills[index] = InputFill(fills[index].input, FILL_SELECTION)
    return fills


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

    **No production caller since #1410**, and kept rather than deleted with its
    route: the mode rules are still what a card's own inputs write, and
    ``tests/test_workflow_io.py`` exercises it directly, so it is covered
    behaviour
    rather than dead code. Delete the test with it if it goes.
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

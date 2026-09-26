"""What a workflow card is, and which cards stack, derived from a stored graph.

``workflow_hash`` gives a graph three content keys: topology, structural
(called a **variant** in new code) and instance. None of them is what a person
means by "a workflow" (user plan §2), so this module derives the two that are:

* **workflow_key** - the card. The topology plus every non-LoRA model slot
  (checkpoint, VAE, UNET, CLIP) and every LoRA slot marked *structural*. A
  different checkpoint is a different workflow; a character LoRA, marked
  *recipe*, is not. The structural hash alone would give every character LoRA
  its own card, and the topology alone would merge checkpoints.
* **core_hash** - the automatic stack. The topology with plumbing,
  post-processing and (by default) LoRA loaders removed and the edges re-wired
  through them, so "adds a face detailer" and "differs only in checkpoint"
  stack by construction.

Everything here reads the **stored document** (``workflow_recipe_graph``,
rendered by :func:`workflow_hash.structural_document`), whose assets are opaque
references. That is what lets the backfill run over the hub alone, with no
picture rescan, and what keeps a forgotten model name forgotten: a slot names
its model by reference, never by filename.

Pure functions only. No schema, no I/O; the marks this module guesses are
frozen into the hub by the caller the first time a slot is seen.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Collection, Optional

from pixlstash.services.comfyui_recipe_service import (
    INPUT_IMAGE_FIELDS,
    LORA_DIGEST_FIELD_RE,
    LORA_FILENAME_FIELD_RE,
)
from pixlstash.services.workflow_hash import (
    ReducedNode,
    WorkflowGraphError,
    _digest,
    drop_widgets,
    graph_key,
    node_labels,
    reduce_api_graph,
)
from pixlstash.services.workflow_io import is_picture_loader

# Stamped beside every cached value, so a change of rule re-keys visibly.
# Stack overrides are keyed on workflow keys, not on core hashes, so a
# CORE_VERSION bump regroups automatic stacks without losing a user's choice.
WORKFLOW_KEY_VERSION = "v1"
CORE_VERSION = "v1"

ASSET_REFERENCE_PREFIX = "asset:"

STRUCTURAL = "structural"
RECIPE = "recipe"

# The filename guess for a LoRA slot's mark. Speed LoRAs change how a workflow
# samples (steps, CFG), so they are part of the workflow; anything else is
# taken to be the look. The guess is frozen on first sight, so precision beats
# recall: a false "structural" pulls a character LoRA into the card key. Hence
# whole words only ("superturbo_style" stays recipe), "hyper" only as Hyper-SD,
# and a step count of at most two digits (speed LoRAs run 1-16 steps; a
# training checkpoint says "3000steps").
_STRUCTURAL_LORA_RE = re.compile(
    r"(?<![a-z])(lightning|turbo|lcm|lightx2v|causvid|dmd2?|tcd|pcm)(?![a-z])"
    r"|(?<![a-z])hyper[-_ ]?(sd|sdxl|flux)(?![a-z])"
    r"|(?<![a-z])sdxl[-_ ]?flash(?![a-z])"
    r"|(?<![a-z0-9])\d{1,2}[-_ ]?steps?(?![a-z])"
)

# A widget naming the picture a run starts from. It is an asset in the
# structural hash, but an input rather than part of the workflow, so it never
# reaches the card key: an img2img workflow is one card whatever it was fed.
# By name, because the stored document holds only a reference and cannot say
# whether it was a picture or a model. A custom node naming its input
# picture some other way still splits cards per picture.
_PICTURE_WIDGET_RE = re.compile(r"(^|_)(image|images|video|mask)(_|$)")
# Every widget a base model is named by, so "other checkpoint" says which model
# changed rather than falling back to "other models". The shelf loader names
# its checkpoint by id, not by filename (#1416).
#
# **The client's copy is gone; a server one is not.** The retired workflow
# shelf kept a `BASE_WIDGETS` of its own for the Models column, and the two
# drifted in both directions - `diffusion_model` and `model_path` only there,
# `checkpoint_id` only here - so a workflow read as having a base model on one
# side and a changed one on the other (#1416). A guardrail held them equal
# until F1b (#1404) deleted the shelf.
#
# **`_SLOT_KINDS` in `workflow_card_service.py` still answers the same
# question and already disagrees**, and it is the one a card is labelled from:
# `unet_name` is `"unet"` there, never `"checkpoint"`, and `diffusion_model`,
# `model_path` and `checkpoint_id` are absent altogether. So a Flux/SD3/Wan
# graph is a base-model change to `differs_by` here and is NOT the card's
# headline model there, which falls back to the first slot. Same drift, moved
# from client-vs-server to server-vs-server, and nothing asserts it: the two
# want reconciling behind one helper rather than a comment (#1404 review).
CHECKPOINT_WIDGETS = frozenset(
    {
        "ckpt_name",
        "unet_name",
        "diffusion_model",
        "model_path",
        "checkpoint_id",
    }
)

PLUMBING = "plumbing"
UPSCALE = "upscale"
FACE_DETAILER = "face_detailer"
LORA = "lora"

# Only these reach a stored document: API graphs have no Reroute or Note, and
# the UI reduction drops them before anything is stored.
_PLUMBING_CLASSES = frozenset({"PreviewImage"})
_UPSCALE_CLASS_RE = re.compile(
    r"^(UpscaleModelLoader|ImageUpscaleWithModel|ImageScale|LatentUpscale|UltimateSDUpscale)"
)
_LATENT_UPSCALE_RE = re.compile(r"^LatentUpscale")
_FACE_DETAILER_CLASS_RE = re.compile(
    r"^(FaceDetailer|Detailer|SAMLoader|SAMDetector|UltralyticsDetectorProvider"
    r"|BboxDetector|SegmDetector|ONNXDetector)"
)
_SAMPLER_CLASS_RE = re.compile(r"Sampler")

# What a removed node's output stands for when an edge is re-wired through it:
# the input carrying the same stream. A LoRA loader passes MODEL on slot 0 and
# CLIP on slot 1; everything else stripped here passes one picture or latent.
_LORA_THROUGH = ("model", "clip")
_STREAM_INPUTS = ("image", "images", "pixels", "samples", "latent_image", "latent")
_MAX_THROUGH_HOPS = 256


@dataclass(frozen=True)
class Slot:
    """One model a workflow names, addressed so it survives re-serialisation.

    ``label`` is the loader node's Weisfeiler-Leman label at topology tier plus
    the widget name, so it is the same however the file numbered its nodes.
    Refined until stable, so loaders in a long chain are told apart; only
    loaders WL cannot separate (genuine twins) share a label and a mark.

    **A label only means something within one topology.** At full refinement
    it encodes the whole graph, so adding a PreviewImage changes every label.
    Marks and parameter addresses keyed by label must be keyed with the
    topology too, as :func:`workflow_key` is.
    """

    label: str
    node_id: str
    class_type: str
    widget: str
    asset: str
    is_lora: bool


def is_lora_widget(widget: str) -> bool:
    """Whether this widget names a LoRA, so its slot takes a mark.

    Both spellings carry the numbered form a stacker gives its second and third
    slot (`lora_name_2`, `lora_sha256_2`). A slot missed here is not a LoRA
    slot, so it reaches the card key unconditionally and a character LoRA swap
    forks the workflow into a new card.
    """
    return bool(
        LORA_FILENAME_FIELD_RE.match(widget) or LORA_DIGEST_FIELD_RE.match(widget)
    )


def slots(document: dict) -> list[Slot]:
    """Every model slot a stored document names, pictures excluded.

    Args:
        document: A stored structural document, assets as references.

    Raises:
        WorkflowGraphError: The document holds no usable node, or is a raw
            graph rather than a stored document.
    """
    return _slots(_reduce(document))


def _slots(nodes: dict[str, ReducedNode]) -> list[Slot]:
    """:func:`slots` over an already-reduced document.

    Split out because ``differs_by`` needs both, and reducing the same document
    twice per comparison was a third of the card grid's cost.
    """
    labels = node_labels(drop_widgets(nodes), rounds=None)
    found = []
    for node_id, node in nodes.items():
        for widget, value in node.widgets:
            if value is None:
                continue
            if _PICTURE_WIDGET_RE.search(widget) or widget in INPUT_IMAGE_FIELDS.get(
                node.class_type, ()
            ):
                continue
            found.append(
                Slot(
                    label=f"{labels[node_id]}/{widget}",
                    node_id=node_id,
                    class_type=node.class_type,
                    widget=widget,
                    asset=value,
                    is_lora=is_lora_widget(widget),
                )
            )
    return sorted(found, key=lambda s: (s.label, s.asset))


def _reduce(document: dict) -> dict[str, ReducedNode]:
    """The document's reduction, each widget carrying its asset reference.

    ``reduce_api_graph`` nulls a reference under a filename widget (it has no
    extension), which would make two loaders with swapped models look alike, so
    the references are put back from the document.

    Raises:
        WorkflowGraphError: A widget holds a readable asset, meaning this is a
            raw graph. Every function here would otherwise see no models and
            quietly collapse every checkpoint into one card.
    """
    nodes = reduce_api_graph(document)
    raw = {str(node_id): node for node_id, node in document.items()}
    reduced = {}
    for node_id, node in nodes.items():
        if any(
            value is not None and not value.startswith(ASSET_REFERENCE_PREFIX)
            for _, value in node.widgets
        ):
            raise WorkflowGraphError(
                f"node {node_id} names an asset by value; workflow identity "
                "needs a stored document (structural_document), not a raw graph"
            )
        inputs = raw[node_id].get("inputs") or {}
        widgets = tuple(
            (name, _reference(inputs.get(name))) for name, _ in node.widgets
        )
        reduced[node_id] = ReducedNode(node.class_type, widgets, node.inputs)
    return reduced


def _reference(value: object) -> Optional[str]:
    if isinstance(value, str) and value.startswith(ASSET_REFERENCE_PREFIX):
        return value
    return None


def topology_node_labels(document: dict) -> dict[str, str]:
    """Each node's slot label within its topology: ``{node_id: label}``.

    The same label :class:`Slot` carries, without the ``/widget`` suffix, so it
    addresses a whole node rather than one of its model widgets. That is the
    address ``workflow_default_override`` uses - a node id is whatever the file
    that was serialised last happened to call it, and the same workflow rebuilt
    from scratch renumbers every one of them.

    Refined until stable (``rounds=None``), exactly as :func:`slots` refines,
    so the two agree about which node a label names.

    Raises:
        WorkflowGraphError: The document holds no usable node, or is a raw
            graph rather than a stored document.
    """
    return node_labels(drop_widgets(_reduce(document)), rounds=None)


def guess_mark(normalized_filename: str) -> str:
    """The first-sight guess for a LoRA slot: ``structural`` or ``recipe``.

    **Frozen by the caller, never recomputed.** Re-guessing as pictures arrive
    would silently re-key cards; the guess is only where the mark starts.
    """
    if _STRUCTURAL_LORA_RE.search(normalized_filename.lower()):
        return STRUCTURAL
    return RECIPE


def workflow_key(
    topology_hash: str, workflow_slots: list[Slot], structural_labels: Collection[str]
) -> str:
    """The card key: topology, non-LoRA models and structural LoRA slots.

    Args:
        topology_hash: The document's topology hash.
        workflow_slots: :func:`slots` of the document.
        structural_labels: Labels of the LoRA slots marked structural. A LoRA
            slot not named here is a recipe slot and does not reach the key.
    """
    pairs = sorted(
        [slot.label, slot.asset]
        for slot in workflow_slots
        if not slot.is_lora or slot.label in structural_labels
    )
    return _digest([WORKFLOW_KEY_VERSION, topology_hash, pairs])


# The post-processing groups a card says it has, in the order a name lists
# them. A tuple and not the set itself: the cached value is a string, and two
# derivations of one topology have to compare equal byte for byte.
SPECIAL_GROUPS = (UPSCALE, FACE_DETAILER)

# A class that only LOADS the thing, and so is not on its own evidence the
# graph does it. `node_groups` groups these with the work they feed because it
# exists to STRIP a group whole - leaving a detector loader behind while
# removing its detailer would change the stack key for nothing. Printing is the
# opposite case: a `SAMLoader` sitting in a document with no detailer in front
# of it must not put "+ FaceDetailer" on the card's only identifying text.
_LOADER_CLASS_RE = re.compile(r"(Loader|DetectorProvider)$")


def special_groups(document: dict) -> tuple[str, ...]:
    """Which of :data:`SPECIAL_GROUPS` this document actually DOES.

    The same classification ``core_hash`` strips and ``differs_by`` chips, asked
    of one graph on its own rather than of a pair: a lone card has no cover to
    differ from, and what a card *has* is what its name is allowed to say.

    **Narrower than the strip, deliberately.** Over-inclusion is free when the
    answer is which nodes to remove and costly when it is a sentence printed on
    the card, so a group counts only where some node in it does the work rather
    than loads the model for it (:data:`_LOADER_CLASS_RE`).

    Raises:
        WorkflowGraphError: The document is a raw graph rather than a stored one.
    """
    nodes = _reduce(document)
    present = {
        group
        for node_id, group in node_groups(nodes).items()
        if group in SPECIAL_GROUPS
        and not _LOADER_CLASS_RE.search(nodes[node_id].class_type)
    }
    return tuple(group for group in SPECIAL_GROUPS if group in present)


def node_groups(nodes: dict[str, ReducedNode]) -> dict[str, Optional[str]]:
    """Each node's taxonomy group, or ``None`` for one that does real work.

    A sampler is post-processing only when its latent comes straight from a
    latent upscale: that pair is a hires fix, and the second sampler goes with
    the upscale it refines.
    """
    groups: dict[str, Optional[str]] = {}
    for node_id, node in nodes.items():
        cls = node.class_type
        if cls in _PLUMBING_CLASSES or cls.startswith("Primitive"):
            groups[node_id] = PLUMBING
        elif _UPSCALE_CLASS_RE.match(cls):
            groups[node_id] = UPSCALE
        elif _FACE_DETAILER_CLASS_RE.match(cls):
            groups[node_id] = FACE_DETAILER
        elif any(is_lora_widget(name) for name, _ in node.widgets) or (
            "lora" in cls.lower() and "loader" in cls.lower()
        ):
            groups[node_id] = LORA
        elif _SAMPLER_CLASS_RE.search(cls) and any(
            name == "latent_image"
            and source in nodes
            and _LATENT_UPSCALE_RE.match(nodes[source].class_type)
            for name, source, _ in node.inputs
        ):
            groups[node_id] = UPSCALE
        else:
            groups[node_id] = None
    return groups


def _through_input(node: ReducedNode, group: str, slot: int) -> Optional[str]:
    names = {name for name, _, _ in node.inputs}
    if group == LORA:
        wanted = _LORA_THROUGH[slot] if slot < len(_LORA_THROUGH) else None
        return wanted if wanted in names else None
    for name in _STREAM_INPUTS:
        if name in names:
            return name
    return next(iter(names)) if len(names) == 1 else None


def core_hash(document: dict, *, strip_loras: bool = True) -> str:
    """The automatic stack key: the topology without plumbing or post-processing.

    Removed nodes are stepped through rather than cut out, so a sampler fed by
    a LoRA loader reads as fed by the checkpoint, and a save node fed by an
    upscale reads as fed by the decode. Checkpoint names never reach the
    topology tier, so members differing only in checkpoint stack.

    Args:
        document: A stored structural document.
        strip_loras: Remove LoRA loaders too, so an extra character LoRA
            loader does not split a stack.

    Raises:
        WorkflowGraphError: Nothing is left once the strip groups are removed.
    """
    strip = {PLUMBING, UPSCALE, FACE_DETAILER} | ({LORA} if strip_loras else set())
    return _stripped_key(_reduce(document), strip)


def _stripped_key(
    nodes: dict[str, ReducedNode],
    strip: Collection[str],
    *,
    keep_widgets: bool = False,
) -> str:
    groups = node_groups(nodes)
    removed = {node_id for node_id, group in groups.items() if group in strip}

    def resolve(source: str, slot: int) -> Optional[tuple[str, int]]:
        for _ in range(_MAX_THROUGH_HOPS):
            if source not in removed:
                return source, slot
            node = nodes[source]
            name = _through_input(node, groups[source], slot)
            edge = next((e for e in node.inputs if e[0] == name), None)
            if edge is None:
                return None
            source, slot = edge[1], edge[2]
        raise WorkflowGraphError(
            f"re-wiring through stripped nodes did not end after {_MAX_THROUGH_HOPS} hops"
        )

    kept: dict[str, ReducedNode] = {}
    for node_id, node in nodes.items():
        if node_id in removed:
            continue
        inputs = []
        for name, source, slot in node.inputs:
            resolved = resolve(source, slot)
            if resolved is not None:
                inputs.append((name, resolved[0], resolved[1]))
        kept[node_id] = ReducedNode(
            node.class_type,
            node.widgets if keep_widgets else (),
            tuple(sorted(inputs)),
        )
    if not kept:
        raise WorkflowGraphError("nothing is left of the graph once stripped")
    return graph_key(kept)


def workflow_type(document: dict) -> Optional[str]:
    """What kind of picture the workflow makes, from its node classes.

    One of ``outpaint``, ``inpaint``, ``upscale``, ``img2img``, ``txt2img``,
    tested in that order because an outpaint graph also encodes for inpaint
    and an img2img graph may still carry an empty latent. ``None`` when none
    applies. A picture source is whatever ``workflow_io`` calls a picture
    loader, the PixlStash one included.
    """
    nodes = _reduce(document)
    classes = {node.class_type for node in nodes.values()}
    if "ImagePadForOutpaint" in classes:
        return "outpaint"
    if classes & {
        "VAEEncodeForInpaint",
        "InpaintModelConditioning",
        "SetLatentNoiseMask",
    }:
        return "inpaint"
    has_picture_input = any(is_picture_loader(cls) for cls in classes)
    has_sampler = any(_SAMPLER_CLASS_RE.search(cls) for cls in classes)
    if (
        has_picture_input
        and classes & {"UpscaleModelLoader", "ImageUpscaleWithModel"}
        and not has_sampler
    ):
        return "upscale"
    if any(
        node.class_type == "VAEEncode" and _fed_by_picture(node_id, nodes)
        for node_id, node in nodes.items()
    ):
        return "img2img"
    if any(re.match(r"^Empty.*Latent", cls) for cls in classes):
        return "txt2img"
    return None


def _fed_by_picture(node_id: str, nodes: dict[str, ReducedNode]) -> bool:
    seen, stack = set(), [node_id]
    while stack:
        current = stack.pop()
        if current in seen or current not in nodes:
            continue
        seen.add(current)
        if is_picture_loader(nodes[current].class_type):
            return True
        stack.extend(source for _, source, _ in nodes[current].inputs)
    return False


def differs_by(
    cover_document: dict,
    member_document: dict,
    *,
    upscale_factor: Optional[float] = None,
) -> list[str]:
    """How a stack member differs from the stack's cover, as card chips.

    Chips: ``+ face detailer`` / ``− face detailer``, ``+ upscale`` (``+
    upscale 2×`` when the member's factor is known) / ``− upscale``, ``other
    checkpoint`` / ``other models``, ``plumbing only``, and ``N nodes differ``
    for everything this taxonomy does not classify. Empty only when the two
    documents are the same graph with the same models in the same places.

    **"plumbing only" is never a guess.** It is returned only when every
    differing node is plumbing and the two graphs are identical once plumbing
    is stepped through: same wiring, and every asset, LoRAs included, on the
    same loader. A wrong one invites hiding a workflow that really is
    different.
    """
    return differs_by_reduced(
        reduce_stored_document(cover_document),
        reduce_stored_document(member_document),
        upscale_factor=upscale_factor,
    )


def reduce_stored_document(document: dict) -> dict[str, ReducedNode]:
    """A stored document's reduction, for a caller comparing one against many.

    Raises:
        WorkflowGraphError: The document is a raw graph rather than a stored
            one, or holds no usable node.
    """
    return _reduce(document)


def differs_by_reduced(
    cover_nodes: dict[str, ReducedNode],
    member_nodes: dict[str, ReducedNode],
    *,
    upscale_factor: Optional[float] = None,
) -> list[str]:
    """:func:`differs_by` over reductions the caller already holds.

    Split out for the reason :func:`_slots` was: a stack compares every member
    against ONE cover, so reducing that cover once per member is most of what
    describing a stack costs, and it grows with the stack rather than with the
    library.
    """
    return [
        difference.chip
        for difference in differences_reduced(
            cover_nodes, member_nodes, upscale_factor=upscale_factor
        )
    ]


@dataclass(frozen=True)
class Difference:
    """One ``differs_by`` chip and what it stands for (#1597).

    ``detail`` spells out an ``N nodes differ`` chip: the node classes added
    and removed, or that the same nodes load other models or are wired
    differently. It is ``None`` on every other chip.

    ``cover_assets`` and ``member_assets`` are set on ``other checkpoint`` /
    ``other models``: the non-LoRA models only the cover loads and only the
    member loads, as the stored document's asset references, base models first.
    Readable names live only in ``workflow_recipe_asset``, so turning them into
    words is the caller's.

    **No settings.** A stored document nulls every parameter (steps, cfg, a
    seed alike), and a card is many recipes with many settings, so there is no
    one "steps 20 -> 28" for a card to state.
    """

    chip: str
    detail: Optional[str] = None
    cover_assets: tuple[str, ...] = ()
    member_assets: tuple[str, ...] = ()


def differences_reduced(
    cover_nodes: dict[str, ReducedNode],
    member_nodes: dict[str, ReducedNode],
    *,
    upscale_factor: Optional[float] = None,
) -> list[Difference]:
    """:func:`differs_by_reduced` with what each chip stands for."""
    cover = _class_counts(cover_nodes)
    member = _class_counts(member_nodes)
    added, removed = member - cover, cover - member

    def count(counter: Counter, group: Optional[str]) -> int:
        return sum(n for (_, g), n in counter.items() if g == group)

    chips: list[Difference] = []
    # The groups whose class changes the "N nodes differ" chip counts, so its
    # detail names exactly the nodes it counted.
    unclassified_groups: set[Optional[str]] = {LORA, None}
    # A step one side lacks brings its own models (an upscaler, a detector);
    # they are that step's chip, not "other models".
    chipped: set[str] = set()
    for group, label in ((FACE_DETAILER, "face detailer"), (UPSCALE, "upscale")):
        plus, minus = count(added, group), count(removed, group)
        if plus or minus:
            chipped.add(group)
        if plus and minus:
            unclassified_groups.add(group)
        elif plus:
            factor = (
                f" {upscale_factor:g}×" if group == UPSCALE and upscale_factor else ""
            )
            chips.append(Difference(f"+ {label}{factor}"))
        elif minus:
            chips.append(Difference(f"− {label}"))

    cover_slots = _slots_outside(cover_nodes, chipped)
    member_slots = _slots_outside(member_nodes, chipped)
    cover_models = _assets(cover_slots, lora=False)
    member_models = _assets(member_slots, lora=False)
    if cover_models != member_models:
        checkpoint = _assets(cover_slots, lora=False, widgets=CHECKPOINT_WIDGETS)
        chip = (
            "other checkpoint"
            if checkpoint
            != _assets(member_slots, lora=False, widgets=CHECKPOINT_WIDGETS)
            else "other models"
        )
        chips.append(
            Difference(
                chip,
                cover_assets=_changed_assets(cover_models - member_models),
                member_assets=_changed_assets(member_models - cover_models),
            )
        )

    plumbing = count(added, PLUMBING) + count(removed, PLUMBING)
    unclassified = sum(
        count(counter, group)
        for counter in (added, removed)
        for group in unclassified_groups
    )
    if (
        plumbing
        and not chips
        and not unclassified
        and _stripped_key(cover_nodes, {PLUMBING}, keep_widgets=True)
        == _stripped_key(member_nodes, {PLUMBING}, keep_widgets=True)
    ):
        return [Difference("plumbing only")]
    unclassified += plumbing
    unclassified_groups.add(PLUMBING)
    detail = _class_changes(added, removed, unclassified_groups)
    if not chips and not unclassified:
        # Same classes and the same models overall, but not on the same
        # loaders (a base and a refiner swapped) or not wired the same way.
        # Never [] for graphs that are not the same.
        unclassified, detail = _nodes_differing(cover_nodes, member_nodes)
    if unclassified:
        chips.append(
            Difference(
                "1 node differs"
                if unclassified == 1
                else f"{unclassified} nodes differ",
                detail=detail,
            )
        )
    return chips


def _class_changes(
    added: Counter, removed: Counter, groups: Collection[Optional[str]]
) -> str:
    """``+ ImageScaleBy · − LoraLoaderModelOnly ×2`` for the counted groups."""
    parts = [
        f"{sign} {cls}" + (f" ×{n}" if n > 1 else "")
        for sign, counter in (("+", added), ("−", removed))
        for (cls, group), n in sorted(counter.items(), key=lambda kv: kv[0][0])
        if group in groups
    ]
    return " · ".join(parts)


def _changed_assets(changed: Counter) -> tuple[str, ...]:
    """The asset references in *changed*, base models first, then by widget."""
    return tuple(
        asset
        for widget, asset in sorted(
            changed, key=lambda wa: (wa[0] not in CHECKPOINT_WIDGETS, wa)
        )
    )


def _nodes_differing(
    cover_nodes: dict[str, ReducedNode], member_nodes: dict[str, ReducedNode]
) -> tuple[int, Optional[str]]:
    """How many nodes differ when no class does, and how they differ."""
    if graph_key(cover_nodes) == graph_key(member_nodes):
        return 0, None
    cover = Counter((n.class_type, n.widgets) for n in cover_nodes.values())
    member = Counter((n.class_type, n.widgets) for n in member_nodes.values())
    # Wiring alone can differ with every node descriptor equal; that is still
    # at least one node that differs.
    count = max(sum((cover - member).values()), sum((member - cover).values()), 1)
    moved = sorted({cls for cls, _ in (cover - member) + (member - cover)})
    if moved:
        return count, " · ".join(f"{cls}: other model" for cls in moved)
    return count, "same nodes, wired differently"


def _slots_outside(
    nodes: dict[str, ReducedNode], groups: Collection[str]
) -> list[Slot]:
    node_group = node_groups(nodes)
    return [s for s in _slots(nodes) if node_group[s.node_id] not in groups]


def _class_counts(nodes: dict[str, ReducedNode]) -> Counter:
    groups = node_groups(nodes)
    return Counter(
        (node.class_type, groups[node_id]) for node_id, node in nodes.items()
    )


def _assets(
    workflow_slots: list[Slot], *, lora: bool, widgets: Collection[str] = ()
) -> Counter:
    return Counter(
        (slot.widget, slot.asset)
        for slot in workflow_slots
        if slot.is_lora == lora and (not widgets or slot.widget in widgets)
    )

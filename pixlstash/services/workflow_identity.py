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
    asset_reference,
    drop_widgets,
    graph_key,
    is_link,
    node_labels,
    normalized_filename,
    reduce_api_graph,
)
from pixlstash.services.workflow_io import is_picture_loader
from pixlstash.utils.adapter_header import FILE_CHECKPOINT, FILE_TEXT_ENCODER, FILE_VAE

# Stamped beside every cached value, so a change of rule re-keys visibly.
# Stack overrides are keyed on workflow keys, not on core hashes, so a
# CORE_VERSION bump regroups automatic stacks without losing a user's choice.
WORKFLOW_KEY_VERSION = "v1"
# v2: the v1 strip, then the second pass of :func:`_core_v2` (dead nodes,
# string primitives, film grain, seed variance, loader variants).
# v3: v2 also strips integer primitives, previews, prompt builders, the project
# loader and model patches, and reads samplers and savers as the stock ones.
CORE_VERSION = "v3"

ASSET_REFERENCE_PREFIX = "asset:"

# What an address on the core graph is spelled with, so a core address and a
# base slot label (a bare digest) can never be read as each other.
CORE_ADDRESS_PREFIX = "core:"

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
# **The one answer to "is this the base model".** The retired workflow shelf
# kept a `BASE_WIDGETS` of its own and it drifted from this set (#1416), and
# `workflow_card_service._SLOT_KINDS` later drifted the same way. Which KIND of
# base model a spelling is lives beside it in :func:`base_model_kind`, and the
# card service asks that rather than keeping a copy (#1622).
CHECKPOINT_WIDGETS = frozenset(
    {
        "ckpt_name",
        "unet_name",
        "diffusion_model",
        "model_path",
        "checkpoint_id",
    }
)


# The base-model spellings that are a diffusion model on its own (a UNET) rather
# than a whole checkpoint. A graph carrying both is led by its checkpoint.
_UNET_WIDGETS = frozenset({"unet_name", "diffusion_model"})


def base_model_kind(widget: str) -> Optional[str]:
    """``checkpoint`` or ``unet`` for a widget naming the base model, else ``None``.

    Derived from :data:`CHECKPOINT_WIDGETS`, so a widget added there is a base
    model everywhere the same day.
    """
    if widget not in CHECKPOINT_WIDGETS:
        return None
    return "unet" if widget in _UNET_WIDGETS else "checkpoint"


def model_fix_kind(class_type: str, widget: str) -> Optional[str]:
    """The shelf ``file_kind`` a model fix may put in this loader field, or ``None``.

    A fix replaces a missing file with a shelf model of the kind the slot
    takes (``PUT /workflows/{key}/model-fix``), and every read of a fix -
    which slots it names, which fields a run rewrites, which covers it
    supersedes - asks this, so a file of the same name in another kind of
    slot is never touched. ``clip_name`` on a vision loader is an image
    encoder, not a text encoder (the clone dialog's rule, and the pre-flight's
    ``clip_vision`` folder).
    """
    if widget in CHECKPOINT_WIDGETS:
        return FILE_CHECKPOINT
    if widget == "vae_name":
        return FILE_VAE
    if widget.startswith("clip_name") and "CLIPVision" not in class_type:
        return FILE_TEXT_ENCODER
    return None


PLUMBING = "plumbing"
UPSCALE = "upscale"
FACE_DETAILER = "face_detailer"
SEED_VARIANCE = "seed_variance"
POST_PROCESS = "post_process"
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
# A node that writes frames out as a video or an animation: ComfyUI's
# SaveVideo / SaveWEBM / SaveAnimatedWEBP|PNG, and VideoHelperSuite's combine.
# The OUTPUT decides, not the latent: Wan 2.2 text to image starts from
# `EmptyHunyuanLatentVideo` and saves one picture, and is text to image.
_VIDEO_OUTPUT_RE = re.compile(r"^Save(Video|WEBM|Animated)|VideoCombine")
# Noise on the conditioning before sampling: a variation knob, not a different
# workflow, so it is a stage (on or off in the default recipe), not core.
_SEED_VARIANCE_CLASSES = frozenset({"SeedVarianceEnhancer"})

# Core rule v2's second pass (:func:`_core_v2`), which only the core strip
# applies. A text box feeding a prompt is where the prompt was typed, not a
# step; a filter of one picture in, one picture out after the decode is a look.
_STRING_PRIMITIVE_CLASSES = frozenset(
    {"Textbox", "Text Multiline", "JWString", "\u270f\ufe0f Literal String"}
)
# Core rule v3's additions to that plumbing: integer primitives (`PrimitiveInt`
# is plumbing by its prefix already, `node_groups`), a preview, a
# prompt builder, the project a saver files into, and model patches (one MODEL
# in, MODEL out, stepped through by `_through_input`'s single-input fallback,
# as a LoRA loader is). # ponytail: a class list, because the stored document
# has no output types to read "MODEL in, MODEL out" from; a rule once it does.
_V3_PLUMBING_CLASSES = frozenset(
    {
        "Seed",
        "JWInteger",
        "PreviewAny",
        "LoRACharacterPromptBuilder",
        "PixlStashProjectLoader",
        "ModelSamplingAuraFlow",
        "PathchSageAttentionKJ",
    }
)
# The same sampler and the same save, other spellings (core rule v3).
_V3_CANONICAL_CLASSES = {
    "KSamplerAdvanced": "KSampler",
    "PixlStashPictureSaver": "SaveImage",
}
_POST_PROCESS_CLASSES = frozenset(
    {"PhotoFilmGrain", "Image Levels Adjustment", "ImageSharpen", "ImageBlur"}
)
# What a node with no consumer may still be for: it writes, shows or sends.
# Anchored on purpose: a bare `Combine` or `Output` also matched
# `ConditioningCombine` and friends, so orphan conditioning was never pruned.
# `Output$` keeps a node NAMED as an output (`ImageOutput`); `Save` covers
# every `*Saver`.
_SINK_CLASS_RE = re.compile(
    r"Save|Preview|VideoCombine|Export|Upload|WebSocket|^Send|Send(Image|Video|To)"
    r"|Output$",
    re.IGNORECASE,
)
# A loader's GGUF / multi-GPU spelling loads the same thing as the stock one.
_LOADER_VARIANT_RE = re.compile(r"GGUF(?:Advanced)?|DisTorch2?|MultiGPU")
_CANONICAL_LOADERS = {
    "UnetLoader": "UNETLoader",
    "CheckpointLoaderAdvanced": "CheckpointLoaderSimple",
    "PixlStashCheckpointLoader": "CheckpointLoaderSimple",
    "PixlStashCLIPLoader": "CLIPLoader",
    "PixlStashVAELoader": "VAELoader",
}

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


def lora_assets(document: dict) -> set[str]:
    """Every LoRA file a stored document loads, as its ``asset:`` reference.

    A read of the document as stored, with no reduction: which files, not
    which slot each sat in, which is all a picture filter asks. A widget that
    :func:`is_lora_widget` names and whose value is a reference counts;
    anything else in a LoRA widget (a nulled or unfilled one) names no file.
    """
    found = set()
    for node in document.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for widget, value in inputs.items():
            if (
                is_lora_widget(str(widget))
                and isinstance(value, str)
                and value.startswith(ASSET_REFERENCE_PREFIX)
            ):
                found.add(value)
    return found


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
    topology_hash: str,
    workflow_slots: list[Slot],
    structural_labels: Collection[str],
    promoted: Collection[tuple[str, str]] = (),
) -> str:
    """The card key: topology, non-LoRA models and structural LoRA slots.

    Args:
        topology_hash: The document's topology hash.
        workflow_slots: :func:`slots` of the document.
        structural_labels: Labels of the LoRA slots marked structural. A LoRA
            slot not named here is a recipe slot and does not reach the key.
        promoted: ``(label, asset)`` of the LoRA files promoted one at a time
            (``workflow_lora_promotion``). A recipe slot holding exactly that
            file reaches the key; the same slot holding any other file does
            not. Empty by default, so a topology with no promotion keys the
            way it always has.
    """
    pairs = sorted(
        [slot.label, slot.asset]
        for slot in workflow_slots
        if not slot.is_lora
        or slot.label in structural_labels
        or (slot.label, slot.asset) in promoted
    )
    return _digest([WORKFLOW_KEY_VERSION, topology_hash, pairs])


@dataclass(frozen=True)
class LoaderSwap:
    """A PixlStash loader a model fix put in place of the workflow's own (#1605).

    Swapping the node changes the topology, so a picture made with the swapped
    graph would file on a card of its own. This is what reads it back as the
    original: :func:`unswapped` puts the original loader back in a stored
    document before the card key is computed. Keyed by the swapped topology
    and the node's label in it, as a slot is.

    ``fields`` is ``(swapped widget, the reference it holds, original widget,
    the reference to read it as)`` per file, the original reference naming the
    file the node loads (a model fix's replacement then reads as its original
    through ``workflow_cards.fixed_slots``, as a rewritten name does).
    """

    topology_hash: str
    node_label: str
    class_type: str
    swap_class: str
    fields: tuple[tuple[str, str, str, str], ...]


def loader_swaps(
    original: dict, swapped: dict, files: dict[str, dict[str, tuple[str, str]]]
) -> tuple[str, list[LoaderSwap]]:
    """The swapped topology and a :class:`LoaderSwap` per swapped node.

    Args:
        original: The API graph before the swap.
        swapped: The same graph, node ids kept, with loaders swapped.
        files: ``{node_id: {swapped widget: (original widget, filename)}}``
            for each swapped node; the swapped widget holds the file's digest.

    Raises:
        WorkflowGraphError: Either graph will not reduce.
    """
    before = reduce_api_graph(original)
    after = reduce_api_graph(swapped)
    topology = graph_key(drop_widgets(before))
    labels = node_labels(drop_widgets(after), rounds=None)
    swaps = [
        LoaderSwap(
            topology_hash=topology,
            node_label=labels[node_id],
            class_type=before[node_id].class_type,
            swap_class=after[node_id].class_type,
            fields=tuple(
                sorted(
                    (
                        widget,
                        asset_reference(
                            str(swapped[node_id]["inputs"][widget]).lower()
                        ),
                        original_widget,
                        asset_reference(normalized_filename(filename)),
                    )
                    for widget, (original_widget, filename) in by_widget.items()
                )
            ),
        )
        for node_id, by_widget in files.items()
    ]
    return graph_key(drop_widgets(after)), swaps


def unswapped(
    document: dict, swaps: Collection[LoaderSwap]
) -> tuple[Optional[str], dict]:
    """*document* with each swapped-in loader put back, and the topology it is then.

    ``(None, document)`` when no node of *document* is one of *swaps*: a node
    matches on its label, its class and exactly the references the swap
    recorded, so a PixlStash loader holding another file, or a second one,
    stays what it is. The document put back must then BE the recorded
    topology, so a graph where only some of a run's swapped loaders match
    keys as itself. One placed by hand with the very files a swap recorded is
    the same graph, and so the same variant: it cards as the original too.
    *swaps* are those of the document's own topology.

    Raises:
        WorkflowGraphError: The document will not reduce.
    """
    if not swaps:
        return None, document
    by_label: dict[str, list[LoaderSwap]] = {}
    for swap in swaps:
        by_label.setdefault(swap.node_label, []).append(swap)
    restored = dict(document)
    topologies = set()
    for node_id, label in topology_node_labels(document).items():
        node = document[node_id]
        inputs = node.get("inputs") or {}
        held = {
            name: value
            for name, value in inputs.items()
            if isinstance(value, str) and value.startswith(ASSET_REFERENCE_PREFIX)
        }
        matching = [
            s
            for s in by_label.get(label, ())
            if s.swap_class == node.get("class_type")
            and held == {w: ref for w, ref, _, _ in s.fields}
        ]
        if not matching:
            continue
        # Two originals swapped to this one graph (a core and a GGUF loader
        # holding the same file) leave which it was a guess.
        topologies.update(s.topology_hash for s in matching)
        swap = matching[0]
        # Links only: the original's other widgets are parameters, nulled in a
        # stored document, and widget names never reach the topology.
        put_back = {name: value for name, value in inputs.items() if is_link(value)}
        put_back.update({widget: ref for _, _, widget, ref in swap.fields})
        restored[node_id] = {"class_type": swap.class_type, "inputs": put_back}
    if len(topologies) != 1:
        # None matched, or two swaps disagree about the graph this was (or
        # which loader a node was): a guess, so the document keys as itself.
        return None, document
    topology = topologies.pop()
    if graph_key(drop_widgets(reduce_api_graph(restored))) != topology:
        # Only some of a run's swapped loaders matched (another holds other
        # files), so what was put back is not the graph they were swapped
        # from, and keying it there would cache the wrong slots on it.
        return None, document
    return topology, restored


# The post-processing groups a card says it has, in the order a name lists
# them. A tuple and not the set itself: the cached value is a string, and two
# derivations of one topology have to compare equal byte for byte.
SPECIAL_GROUPS = (UPSCALE, FACE_DETAILER, SEED_VARIANCE)

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


# What a graph's core DOES that a generated name cannot otherwise say (#1722):
# two cards with one model, one type and one post-processing can still be
# genuinely different workflows. In the order a name lists them; reference
# counts are spelled ``references:N``.
TWO_PASS = "two_pass"
REFINE = "refine"
MODEL_PER_PASS = "model_per_pass"
INTERMEDIATE_SAVE = "intermediate_save"
LIKENESS_GATE = "likeness_gate"
NEGATIVE_PROMPT = "negative_prompt"
REFERENCES_PREFIX = "references:"
TRAITS = (
    TWO_PASS,
    REFINE,
    MODEL_PER_PASS,
    INTERMEDIATE_SAVE,
    LIKENESS_GATE,
    NEGATIVE_PROMPT,
)
_LIKENESS_GATE_CLASSES = frozenset(
    {"PixlStashFaceLikenessGate", "PixlStashPictureLikenessGate"}
)
_SAVER_CLASS_RE = re.compile(r"Save", re.IGNORECASE)
_TEXT_ENCODER_CLASS_RE = re.compile(r"TextEncode")


def graph_traits(document: dict, *, strip_loras: bool = True) -> tuple[str, ...]:
    """What this stored document's core does beyond its type and its stages.

    *strip_loras* is :func:`core_hash`'s, and a caller passes the value its
    grouping uses, so the traits are read off the core that grouped them.

    Raises:
        WorkflowGraphError: The document is a raw graph, or nothing survives
            the core strip.
    """
    return reduced_traits(_reduce(document), strip_loras=strip_loras)


def reduced_traits(
    nodes: dict[str, ReducedNode], *, strip_loras: bool = True
) -> tuple[str, ...]:
    """:func:`graph_traits` of an already reduced graph, raw or stored.

    **Read off the core graph, never the whole one**: the same strip as
    :func:`core_hash`, so two graphs one automatic workflow groups always have
    the same traits, and a trait can only name a difference the grouping kept.
    A hires fix's second sampler is an upscale stage there and is gone, so it
    is not "two-pass"; a refine through an upscale model reads as a decode fed
    straight back into a sampler, which is what it is.

    Class types and edges only, so a manual workflow's raw graph answers as
    its stored document would.
    """
    core = _core_of(nodes, strip_loras)[0]
    samplers = {
        node_id
        for node_id, node in core.items()
        if _SAMPLER_CLASS_RE.search(node.class_type)
        and any(name == "latent_image" for name, _, _ in node.inputs)
    }
    present: set[str] = set()
    first_passes: set[str] = set()
    for node_id in samplers:
        found = _upstream_sampler(core, node_id, samplers)
        if found is None:
            continue
        source, encoded = found
        first_passes.add(source)
        present.add(REFINE if encoded else TWO_PASS)
        if _model_root(core, source) != _model_root(core, node_id):
            present.add(MODEL_PER_PASS)
    for node_id, node in core.items():
        if not _SAVER_CLASS_RE.search(node.class_type) or node_id in samplers:
            continue
        found = _upstream_sampler(core, node_id, samplers)
        if found is not None and found[0] in first_passes:
            present.add(INTERMEDIATE_SAVE)
    if any(n.class_type in _LIKENESS_GATE_CLASSES for n in core.values()):
        present.add(LIKENESS_GATE)
    if any(_negative_is_prompted(core, node) for node in core.values()):
        present.add(NEGATIVE_PROMPT)
    traits = [trait for trait in TRAITS if trait in present]
    references = sum(n.class_type == "ReferenceLatent" for n in core.values())
    if references:
        traits.append(f"{REFERENCES_PREFIX}{references}")
    return tuple(traits)


def _upstream_sampler(
    nodes: dict[str, ReducedNode], node_id: str, samplers: set[str]
) -> Optional[tuple[str, bool]]:
    """The sampler whose picture or latent *node_id* is fed, and whether a
    ``VAEEncode`` lies between them. ``None`` when none does.

    Walks the picture/latent stream only (:data:`_STREAM_INPUTS`), so a model
    or a conditioning shared with another sampler is not mistaken for a pass.
    """
    stack = [
        (source, False)
        for name, source, _ in nodes[node_id].inputs
        if name in _STREAM_INPUTS
    ]
    seen: set[str] = set()
    while stack:
        current, encoded = stack.pop()
        if current in seen or current not in nodes:
            continue
        seen.add(current)
        if current in samplers:
            return current, encoded
        node = nodes[current]
        encoded = encoded or node.class_type == "VAEEncode"
        stack.extend(
            (source, encoded)
            for name, source, _ in node.inputs
            if name in _STREAM_INPUTS
        )
    return None


def _model_root(nodes: dict[str, ReducedNode], node_id: str) -> Optional[str]:
    """The node a sampler's model chain starts at: its loader."""
    seen: set[str] = set()
    current: Optional[str] = node_id
    while current in nodes and current not in seen:
        seen.add(current)
        edge = next(
            (
                source
                for wanted in ("model", "guider")
                for name, source, _ in nodes[current].inputs
                if name == wanted
            ),
            None,
        )
        if edge is None:
            return current
        current = edge
    return current


def _negative_is_prompted(nodes: dict[str, ReducedNode], node: ReducedNode) -> bool:
    """Whether *node*'s ``negative`` input is a typed prompt.

    The walk follows conditioning only and stops at ``ConditioningZeroOut``,
    whose input is the positive prompt: zeroing that is "no negative".
    """
    stack = [source for name, source, _ in node.inputs if name == "negative"]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen or current not in nodes:
            continue
        seen.add(current)
        upstream = nodes[current]
        if upstream.class_type == "ConditioningZeroOut":
            continue
        if _TEXT_ENCODER_CLASS_RE.search(upstream.class_type):
            return True
        stack.extend(
            source
            for name, source, _ in upstream.inputs
            if name.startswith("conditioning")
        )
    return False


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
        elif cls in _SEED_VARIANCE_CLASSES:
            groups[node_id] = SEED_VARIANCE
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
    return graph_key(_core_graph(document, strip_loras))


def core_node_labels(document: dict, *, strip_loras: bool = True) -> dict[str, str]:
    """Each surviving node's label on the core graph: ``{node_id: label}``.

    The **core address** (#1622). A slot label is refined over the whole
    topology, so it means nothing outside one topology, and a workflow spans
    several. Every topology of one automatic workflow shares a
    :func:`core_hash`, so their stripped graphs are isomorphic and these
    labels agree across all of them: the sampler of the graph with a detailer
    and the sampler of the one without get the same label.

    The same strip as :func:`core_hash`, so the hash and the address cannot
    disagree about what the core is. Nodes the strip removes (plumbing, stages,
    LoRA loaders, dead nodes) have no core label and are left out.

    Raises:
        WorkflowGraphError: The document is a raw graph, or nothing survives.
    """
    return node_labels(_core_graph(document, strip_loras), rounds=None)


def core_node_ids(document: dict, *, strip_loras: bool = True) -> set[str]:
    """The node ids :func:`core_node_labels` labels, without the refinement.

    Raises:
        WorkflowGraphError: The document is a raw graph, or nothing survives.
    """
    return set(_core_graph(document, strip_loras))


def _core_graph(document: dict, strip_loras: bool) -> dict[str, ReducedNode]:
    return _core_pass(document, strip_loras)[0]


def _core_pass(
    document: dict, strip_loras: bool, *, v3: bool = True
) -> tuple[dict[str, ReducedNode], Counter, bool]:
    """:func:`_core_v2`'s answer for a whole document: the live core rule.

    *v3* off is core rule v2, kept only for data step 10's label maps.
    """
    return _core_of(_reduce(document), strip_loras, v3=v3)


def _core_of(
    nodes: dict[str, ReducedNode], strip_loras: bool, *, v3: bool = True
) -> tuple[dict[str, ReducedNode], Counter, bool]:
    """:func:`_core_pass` of an already reduced graph, stored or raw."""
    strip = _core_strip(strip_loras)
    if not has_sampler(nodes):
        # Every stage is an optional addition to a graph that samples. With no
        # sampler, a face detailer is what the graph does: kept as its core,
        # an upscale after it still optional. With no detailer either, the
        # upscale is the core. Kept, so the model chain is not made dead.
        groups = node_groups(nodes)
        detailer = any(
            group == FACE_DETAILER
            and not _LOADER_CLASS_RE.search(nodes[node_id].class_type)
            for node_id, group in groups.items()
        )
        strip -= {FACE_DETAILER} if detailer else {UPSCALE, FACE_DETAILER}
    return _core_v2(_strip(nodes, strip), v3=v3)


def has_sampler(nodes: dict[str, ReducedNode]) -> bool:
    """Whether a reduced graph has a sampler-class node of its own."""
    return any(_SAMPLER_CLASS_RE.search(n.class_type) for n in nodes.values())


def _core_strip(strip_loras: bool) -> set[str]:
    return {PLUMBING, UPSCALE, FACE_DETAILER} | ({LORA} if strip_loras else set())


def _core_v2(
    nodes: dict[str, ReducedNode], *, v3: bool = True
) -> tuple[dict[str, ReducedNode], Counter, bool]:
    """Core rule v2's second pass over an already v1-stripped graph.

    ``(core graph, pruned classes, whether the prune was refused)``. Applied to
    the stripped graph and never to the document, so two graphs with one v1
    core have one v2 core unless the graph has no sampler (whose stages
    :func:`_core_graph` keeps): workflows only split by that and by base-model
    family (data step 8). In order:

    * string primitives (plumbing), picture filters (``POST_PROCESS``) and
      seed variance are stripped, edges re-wired through them. The filters
      and seed variance only where the graph samples: without a sampler a
      filter is what the graph does (``LoadImage -> ImageSharpen -> SaveImage``
      is not ``... ImageBlur ...``), as :func:`_core_pass` keeps a detailer;
    * **dead nodes are pruned**: every node nothing reads, unless it writes,
      shows or sends (:data:`_SINK_CLASS_RE`), repeatedly. An orphan prompt
      encoder is left-over editing, not a different workflow. **Refused** when
      it would remove every sampler the graph had (a graph whose only output
      was a stripped preview), or everything: the graph is kept whole;
    * loader variants read as the stock loader (:data:`_CANONICAL_LOADERS`).

    *v3* (the live rule) adds :data:`_V3_PLUMBING_CLASSES` to the plumbing and
    :data:`_V3_CANONICAL_CLASSES` to the stock spellings.
    """
    plumbing = _STRING_PRIMITIVE_CLASSES | (_V3_PLUMBING_CLASSES if v3 else set())
    groups = node_groups(nodes)
    for node_id, node in nodes.items():
        if node.class_type in plumbing:
            groups[node_id] = PLUMBING
        elif node.class_type in _POST_PROCESS_CLASSES:
            groups[node_id] = POST_PROCESS
    strip = (
        {PLUMBING, POST_PROCESS, SEED_VARIANCE} if has_sampler(nodes) else {PLUMBING}
    )
    kept, pruned, refused = _prune(_strip(nodes, strip, groups=groups))
    return (
        {
            node_id: ReducedNode(
                _canonical_class(n.class_type, v3), n.widgets, n.inputs
            )
            for node_id, n in kept.items()
        },
        pruned,
        refused,
    )


def _prune(
    nodes: dict[str, ReducedNode],
) -> tuple[dict[str, ReducedNode], Counter, bool]:
    """*nodes* without their dead nodes, as :func:`_core_v2` describes."""
    kept = dict(nodes)
    while True:
        read = {source for node in kept.values() for _, source, _ in node.inputs}
        dead = [
            node_id
            for node_id, node in kept.items()
            if node_id not in read and not _SINK_CLASS_RE.search(node.class_type)
        ]
        if not dead:
            break
        for node_id in dead:
            del kept[node_id]
    samplers = {i for i, n in nodes.items() if _SAMPLER_CLASS_RE.search(n.class_type)}
    refused = not kept or bool(samplers and not samplers & kept.keys())
    if refused:
        kept = nodes
    return (
        kept,
        Counter(nodes[i].class_type for i in nodes.keys() - kept.keys()),
        refused,
    )


def _canonical_class(class_type: str, v3: bool = True) -> str:
    if "Loader" in class_type:
        class_type = _LOADER_VARIANT_RE.sub("", class_type)
    if v3 and class_type in _V3_CANONICAL_CLASSES:
        return _V3_CANONICAL_CLASSES[class_type]
    return _CANONICAL_LOADERS.get(class_type, class_type)


def _stripped_key(
    nodes: dict[str, ReducedNode],
    strip: Collection[str],
    *,
    keep_widgets: bool = False,
) -> str:
    return graph_key(_strip(nodes, strip, keep_widgets=keep_widgets))


def _strip(
    nodes: dict[str, ReducedNode],
    strip: Collection[str],
    *,
    keep_widgets: bool = False,
    groups: Optional[dict[str, Optional[str]]] = None,
) -> dict[str, ReducedNode]:
    """*nodes* without the *strip* groups, edges re-wired through them.

    *groups* overrides :func:`node_groups`, for a strip that classifies more.
    """
    groups = groups if groups is not None else node_groups(nodes)
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
    return kept


def workflow_type(document: dict) -> Optional[str]:
    """What kind of picture the workflow makes, from its node classes.

    One of ``video``, ``outpaint``, ``inpaint``, ``upscale``, ``img2img``,
    ``txt2img``, tested in that order: a graph that saves a video is a video
    workflow whatever it starts from (an empty video latent would otherwise
    read as ``txt2img``, a start frame as ``img2img``), an outpaint graph also
    encodes for inpaint, and an img2img graph may still carry an empty latent.
    ``None`` when none applies. A picture source is whatever ``workflow_io``
    calls a picture loader, the PixlStash one included.
    """
    return reduced_workflow_type(_reduce(document))


def reduced_workflow_type(nodes: dict[str, ReducedNode]) -> Optional[str]:
    """:func:`workflow_type` of an already reduced graph, raw or stored.

    It reads class types and edges only, so a manual workflow's raw graph
    (``reduce_api_graph`` / ``reduce_ui_graph``) answers as its stored
    document would.
    """
    classes = {node.class_type for node in nodes.values()}
    if any(_VIDEO_OUTPUT_RE.search(cls) for cls in classes):
        return "video"
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
    for group, label in (
        (FACE_DETAILER, "face detailer"),
        (UPSCALE, "upscale"),
        (SEED_VARIANCE, "seed variance"),
    ):
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
    moved = sorted(
        {
            f"{cls}: other {'picture' if _names_pictures(cls, widgets) else 'model'}"
            for cls, widgets in (cover - member) + (member - cover)
        }
    )
    if moved:
        return count, " · ".join(moved)
    return count, "same nodes, wired differently"


def _names_pictures(class_type: str, widgets: tuple) -> bool:
    """Whether every asset a node names is a picture input, as :func:`_slots` reads it.

    The stored document keeps an input picture's reference beside the models,
    so a changed ``LoadImage`` is "other picture", never "other model".
    """
    named = [name for name, value in widgets if value is not None]
    return bool(named) and all(
        _PICTURE_WIDGET_RE.search(name)
        or name in INPUT_IMAGE_FIELDS.get(class_type, ())
        for name in named
    )


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

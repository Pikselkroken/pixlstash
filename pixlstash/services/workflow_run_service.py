"""What a workflow card can be run from, and why it cannot (v1.12 B7).

Two questions, kept apart because they fail differently:

1. **Where does a runnable graph come from?** A card is an identity derived from
   pictures, not a file, so "run this card" has to find something to submit.
   :func:`resolve_source` tries three places in a fixed order and says
   ``no_runnable_source`` when none of them answers.
2. **Would submitting it work?** :func:`judge` turns a pre-flight, and the
   handful of refusals that are not a pre-flight's business, into the reason
   codes the run panel shows. Every refusal is a code and a payload, never a
   sentence assembled here: the same batch mixes sources, and a caller
   grouping "three pictures are missing the same model" cannot do it from
   prose.

**The codes are the contract.** One route returns them as a dry run
(``/workflows/run/preflight``) and the other refuses on them, so a code that
means one thing on the first and another on the second is a bug in the feature
rather than in a caller.
"""

from __future__ import annotations

import io
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pixlstash.services.comfyui_recipe_service import (
    LORA_DIGEST_FIELD_RE,
    LORA_FILENAME_FIELD_RE,
    bypass_node,
    detect_seed_targets,
    preflight_prompt,
    sanitize_prompt_graph,
    unchecked_preflight,
)
from pixlstash.services.comfyui_service import pixlstash_node_refusals
from pixlstash.services.workflow_bindings import BINDINGS_KEY
from pixlstash.services.workflow_hash import (
    asset_reference,
    is_link,
    normalized_filename,
)
from pixlstash.services.workflow_io import api_graph
from pixlstash.utils.comfyui_utilities import collect_seed_inputs
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Every reason a run is refused or a pre-flight reports. Named rather than
# spelled at each site: three route branches and the tests share them, and a
# typo in a string literal is a refusal that silently never matches.
COMFYUI_NOT_CONFIGURED = "comfyui_not_configured"
COMFYUI_UNREACHABLE = "comfyui_unreachable"
UI_FORMAT = "ui_format"
MISSING_NODES = "missing_nodes"
MISSING_MODELS = "missing_models"
A1111 = "a1111"
PICTURE_INPUT_UNFILLED = "picture_input_unfilled"
NO_LORA_LOADER = "no_lora_loader"
PIXLSTASH_NODES = "pixlstash_nodes"
NO_SAVE_NODE = "no_save_node"
NO_RUNNABLE_SOURCE = "no_runnable_source"
LORA_NOT_SKIPPABLE = "lora_not_skippable"

# A reference whose ``workflow_recipe_asset`` row is gone: the owner forgot the
# model's name, and the stored graph still says a model went there without
# saying which. Substituted rather than left as the opaque token so the missing
# model the pre-flight then reports is readable; either way it is a name no
# ComfyUI lists, which is exactly the state the row delete created.
FORGOTTEN_MODEL = "(forgotten model)"

# Which of ComfyUI's model folders a loader field draws from, for
# ``missing_models[{file, folder}]``: the folder is where the owner has to put
# the file, and the field name alone does not say it. Keyed on the field, with
# the three classes whose field name is ambiguous named directly.
_FOLDER_BY_FIELD = {
    "ckpt_name": "checkpoints",
    "config_name": "configs",
    "unet_name": "diffusion_models",
    "model_path": "diffusers",
    "vae_name": "vae",
    "control_net_name": "controlnet",
    "style_model_name": "style_models",
    "gligen_name": "gligen",
    "hypernetwork_name": "hypernetworks",
    "photomaker_model_name": "photomaker",
}

# Custom seed nodes a graph may name without this ComfyUI having their pack
# (#1463). Each exists only to hand a number to a sampler's seed widget, which
# PixlStash writes itself on every run, so the node can be dropped and its link
# replaced with a literal. **An allow-list, not a rule**: a replacement that is
# nearly right changes what the picture looks like, which is worse than the
# refusal, and anything that samples, conditions or loads has no equivalent.
# rgthree's, WAS's, Comfyroll's and image-saver's, by their class_type.
SEED_NODE_CLASSES = frozenset(
    {"Seed (rgthree)", "Seed", "SeedGenerator", "Seed Generator", "CR Seed"}
)

# Text nodes that only hand their own string on, by class_type, to the input
# holding that string. Same allow-list terms as the seed nodes: WAS's
# `Text Multiline` (which also drops its `#` comment lines), Comfyroll's
# `CR Text`, Chibi-Nodes' `Textbox` and core's `PrimitiveStringMultiline`
# (absent on an older ComfyUI). The string becomes a literal in whatever the
# node fed.
TEXT_NODE_CLASSES = {
    "Text Multiline": "text",
    "CR Text": "text",
    "Textbox": "text",
    "PrimitiveStringMultiline": "value",
}

# WAS's own `[token]` substitutions (`[time]`, `[time(%Y)]`, custom names of
# any spelling). A literal cannot expand them, so a text holding anything in
# square brackets keeps its refusal; core ComfyUI gives brackets no meaning.
WAS_TOKEN_RE = re.compile(r"\[[^\[\]]*\]")


def overriding_text_inputs(class_type: str, inputs: dict) -> list[str]:
    """The inputs of a text node, other than its text, that may change its string.

    Textbox's ``passthrough`` replaces its text whenever it is non-empty, so a
    link or a non-empty string in any input but the text field counts. One
    rule for the repair and the Run prompt's target, so they cannot drift.
    """
    return [
        name
        for name, value in inputs.items()
        if name != TEXT_NODE_CLASSES.get(class_type)
        and (is_link(value) or (isinstance(value, str) and value))
    ]


def prompt_text_target(graph: dict, node_id: str) -> Optional[tuple[str, str]]:
    """Where a detected prompt node's text literally lives, as ``(node, field)``.

    The encoder's own ``text`` when it holds a string; otherwise, when that
    input is a link from output 0 of a :data:`TEXT_NODE_CLASSES` node, that
    node's text field. Without the hop a prompt typed into the Run popup was
    skipped, and a missing text node's repair then inlined the stored prompt.
    A text node feeding more than one input is not a target: writing the
    positive prompt and then the negative into it would leave both negative.
    Nor is one with another input overriding its text (Textbox's
    ``passthrough``): the node would ignore the prompt written into it.
    """
    inputs = (graph.get(node_id) or {}).get("inputs")
    if not isinstance(inputs, dict):
        return None
    text = inputs.get("text")
    if isinstance(text, str):
        return node_id, "text"
    if is_link(text) and text[1] == 0:
        source = graph.get(str(text[0]))
        field = TEXT_NODE_CLASSES.get((source or {}).get("class_type"))
        readers = sum(
            1
            for other in graph.values()
            if isinstance(other, dict) and isinstance(other.get("inputs"), dict)
            for link in other["inputs"].values()
            if is_link(link) and str(link[0]) == str(text[0])
        )
        source_inputs = (source or {}).get("inputs") or {}
        if (
            field
            and readers == 1
            and isinstance(source_inputs.get(field), str)
            and not overriding_text_inputs(source["class_type"], source_inputs)
        ):
            return str(text[0]), field
    return None


# Where the source of a runnable graph came from, in the order tried.
FROM_FILE = "file"
FROM_PICTURE = "picture"
FROM_INSTANCE = "instance"


def model_folder(class_type: str, field_name: str) -> Optional[str]:
    """ComfyUI's model folder for one loader field, or ``None`` when unknown.

    ``None`` is returned rather than guessed: a folder named wrongly sends the
    owner to put a file somewhere it will not be found, which is worse than
    naming the file and leaving the folder out.
    """
    if LORA_FILENAME_FIELD_RE.match(field_name or ""):
        return "loras"
    if class_type == "CLIPVisionLoader":
        return "clip_vision"
    if class_type == "UpscaleModelLoader":
        return "upscale_models"
    if str(field_name).startswith("clip_name"):
        return "text_encoders"
    return _FOLDER_BY_FIELD.get(field_name)


@dataclass
class Source:
    """A graph to submit, and where it was found.

    ``forgotten`` counts references the hub could no longer name, which is only
    ever non-zero for :data:`FROM_INSTANCE`: the other two tiers carry the real
    filenames because they were never reduced.
    """

    graph: dict
    origin: str
    picture_id: Optional[int] = None
    forgotten: int = 0
    # True when the seeds came out of a stored instance document, where they
    # are null by design. Such a source has no seed to keep.
    seedless: bool = False
    # A linked file's `pixlstash_bindings`, when it has them (#1303). The graph
    # above is sanitised and has lost them, and they are how a file the old
    # import dialog stored says which picture inputs a run may fill: `[]` is a
    # file that opted out of every one. `None` is a file without the key.
    bindings: Optional[list] = None


@dataclass
class Reason:
    """One refusal: a code and whatever the panel needs to act on it."""

    code: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"code": self.code, **self.detail}


def resolve_references(
    document: dict, names: list[tuple[str, str]]
) -> tuple[dict, int, bool]:
    """A stored instance document as a graph, with its asset references filled.

    ``workflow_recipe_instance.document`` names every model and image by
    :func:`~pixlstash.services.workflow_hash.asset_reference`, so the graph is
    not submittable until the readable names come back from
    ``workflow_recipe_asset``. The map is built from the names rather than from
    the references because the reference *is* the hash of the name, so one pass
    over the rows inverts it exactly.

    A reference with no row left is the owner having forgotten that model's
    name. It becomes :data:`FORGOTTEN_MODEL`, which no ComfyUI lists, so the
    pre-flight reports it as a missing model - the brief's intended surfacing,
    and the honest one: the graph still says a model went there.

    Returns:
        ``(graph, forgotten, had_null_seed)`` - the filled graph, how many
        distinct references could not be named, and whether any seed was stored
        as null. The last one is what makes ``seed_mode: "keep"`` answerable:
        there is no seed here to keep, and running anyway would submit zero for
        every one of ``count`` runs.
    """
    known = {asset_reference(filename): filename for _, filename in names or []}
    forgotten: set[str] = set()

    def fill(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: fill(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fill(item) for item in value]
        if isinstance(value, str) and value.startswith("asset:"):
            if value in known:
                return known[value]
            forgotten.add(value)
            return FORGOTTEN_MODEL
        return value

    graph = fill(document)
    had_null_seed = False
    # Seeds and output paths are the generation's and are stored as null
    # (``workflow_hash.instance_document_from_reduction``). A null reaches
    # ComfyUI as a type error on a required widget, so the two are given a
    # value here; the seed is then set properly by the caller's seed mode.
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for name, value in inputs.items():
            if value is not None:
                continue
            if name == "filename_prefix":
                inputs[name] = "PixlStash"
            elif "seed" in str(name) or name == "noise_seed":
                inputs[name] = 0
                had_null_seed = True
    return graph, len(forgotten), had_null_seed


def resolve_source(
    card,
    *,
    file_document: Optional[dict] = None,
    picture_graph: Optional[dict] = None,
    picture_id: Optional[int] = None,
    instance_documents: Optional[list[tuple[str, dict]]] = None,
    asset_names: Optional[dict[str, list[tuple[str, str]]]] = None,
) -> tuple[Optional[Source], Optional[Reason]]:
    """What to submit for a card, in the order the brief fixes.

    1. The linked imported file, when the card has one on this machine. It is
       the only tier that is the workflow *as authored*, so it wins even when a
       picture would also answer.
    2. The embedded API graph of this library's best kept picture, which is a
       real run of it with real filenames.
    3. The best ``workflow_recipe_instance.document``, references resolved.
    4. Nothing, which is :data:`NO_RUNNABLE_SOURCE`.

    Every argument is already-read data, so the caller owns the I/O and its
    ordering and this stays a pure decision. A tier that is present
    but unusable (a UI-format file) falls through to the next rather than
    failing the card: the owner's question is "can this run", and one export in
    the wrong format is not an answer to it.
    """
    ui_only = False
    if file_document:
        graph = api_graph(file_document)
        if graph:
            bindings = file_document.get(BINDINGS_KEY)
            return Source(
                sanitize_prompt_graph(graph),
                FROM_FILE,
                bindings=bindings if isinstance(bindings, list) else None,
            ), None
        ui_only = True
        logger.info(
            "Card %s names workflow file %s, which is in ComfyUI's UI format "
            "and cannot be submitted; looking for another source.",
            getattr(card, "workflow_key", "?"),
            getattr(card, "file_name", "?"),
        )
    if picture_graph:
        return (
            Source(sanitize_prompt_graph(picture_graph), FROM_PICTURE, picture_id),
            None,
        )
    # The FIRST stored instance and no other: the caller hands them best-first,
    # so this is an index rather than a search. Written as one because a `for`
    # that always returns on its first pass reads as a loop that could do more.
    best = (instance_documents or [None])[0]
    if best is not None:
        structural_hash, document = best
        graph, forgotten, seedless = resolve_references(
            document, (asset_names or {}).get(structural_hash, [])
        )
        return Source(
            sanitize_prompt_graph(graph), FROM_INSTANCE, None, forgotten, seedless
        ), None
    # A UI-format file that was the ONLY tier is reported as what it is. The
    # fall-through above is still right - one export in the wrong format is not
    # an answer to "can this run" while a picture or an instance could answer -
    # but when nothing else did, ``no_runnable_source`` would send the owner
    # looking for a workflow they are in fact holding, in the wrong format.
    return None, Reason(
        UI_FORMAT if ui_only else NO_RUNNABLE_SOURCE,
        {
            "workflow_key": getattr(card, "workflow_key", None),
            **({"file_name": getattr(card, "file_name", None)} if ui_only else {}),
        },
    )


def judge(
    graph: dict,
    object_info: Optional[dict],
    object_info_error: Optional[str],
    *,
    wants_lora: bool = False,
    lora_slots: Optional[list[dict]] = None,
    library_ids: Optional[dict[str, dict[int, str]]] = None,
    picture_loader: bool = False,
    from_file: bool = False,
) -> tuple[list[Reason], dict]:
    """Every reason this graph would not run, and the pre-flight behind them.

    The order is the order they are worth fixing in: a graph that cannot be
    inspected at all is reported before what an inspection would have found,
    and a structural refusal (PixlStash nodes, no save node) before a missing
    file, because installing the file would not help.

    ``wants_lora`` asks the question only when the caller is actually putting a
    LoRA in: a workflow with no loader is perfectly runnable on its own, and
    reporting ``no_lora_loader`` for one would make every plain card look
    broken.

    ``library_ids``, ``picture_loader`` and ``from_file`` are what the
    ComfyUI-PixlStash node policy needs to know about this run (#1521, see
    :func:`~pixlstash.services.comfyui_service.pixlstash_node_refusals`); the
    defaults refuse.

    Returns:
        ``(reasons, preflight)``. ``reasons`` empty means it would run.
    """
    reasons: list[Reason] = []
    if object_info is None:
        preflight = unchecked_preflight(object_info_error or "ComfyUI unreachable")
        reasons.append(Reason(COMFYUI_UNREACHABLE, {"error": object_info_error or ""}))
    else:
        preflight = preflight_prompt(graph, object_info)

    refused = pixlstash_node_refusals(
        graph,
        library_ids=library_ids,
        picture_loader=picture_loader,
        from_file=from_file,
    )
    if refused:
        reasons.append(Reason(PIXLSTASH_NODES, {"nodes": refused}))
    if wants_lora and not (lora_slots or []):
        reasons.append(Reason(NO_LORA_LOADER))

    classes = preflight.get("missing_node_classes") or []
    if classes:
        reasons.append(Reason(MISSING_NODES, {"nodes": list(classes)}))
    models = [
        {
            "file": str(item.get("value")),
            "folder": model_folder(item.get("class_type"), item.get("field")),
        }
        for item in preflight.get("missing_models") or []
        if item
    ]
    if models:
        reasons.append(Reason(MISSING_MODELS, {"models": models}))
    # Only meaningful when the graph was actually inspected: an unchecked
    # pre-flight reports no save node because it read nothing, and refusing on
    # that would turn "ComfyUI is down" into "your workflow is broken".
    if preflight.get("checked") and not preflight.get("has_save_image"):
        reasons.append(Reason(NO_SAVE_NODE))
    return reasons, preflight


def with_pixlstash_refusals(reasons: list[Reason], nodes: list[dict]) -> list[Reason]:
    """*reasons* with *nodes* added to its one ``pixlstash_nodes`` reason.

    One reason per code, so a panel naming the refused nodes names them all.
    """
    for reason in reasons:
        if reason.code == PIXLSTASH_NODES:
            reason.detail["nodes"] = [*reason.detail.get("nodes", []), *nodes]
            return reasons
    return [*reasons, Reason(PIXLSTASH_NODES, {"nodes": nodes})]


def bypass_missing_loras(graph: dict, object_info: dict) -> list[dict]:
    """Take every LoRA loader whose file this ComfyUI lacks out of *graph*.

    **A LoRA is optional and a checkpoint is not** (#1463). Installing a
    checkpoint is a trip away from the keyboard, so refusing the run and
    keeping the whole batch back is the kind answer; a LoRA the graph can
    simply run without is not worth refusing over, and the owner's answer to
    "we must be able to progress while missing a LoRA" is to disable the node.
    So the run happens with the adapter not applied, which is
    :func:`~pixlstash.services.comfyui_recipe_service.bypass_node`'s whole job.

    **Relaxing the refusal alone would not do it**: a graph still naming an
    absent file is one ``POST /prompt`` refuses, so the loader has to actually
    leave the chain.

    Which entries are LoRAs is :func:`model_folder`'s existing answer, on the
    pre-flight's own findings rather than on a reason - a reason has been
    reduced to ``{file, folder}`` and no longer says which node to take out,
    and a LoRA the *request* asked to add reports the same folder without being
    a slot the graph can do without.

    Three loaders keep their refusal instead, each logged: one whose file this
    hub can no longer NAME (:data:`FORGOTTEN_MODEL` - the file may be installed
    and only the name is lost), a stacker whose other LoRA slots are filled
    (taking the node out would drop the adapters that are here), and one
    nothing can be rewired around.

    **Never silent**: the caller reports what went on the group and on the run
    alike, the way a model substitution is reported, and logs it here.

    Args:
        graph: The API-format graph, mutated in place.
        object_info: The map this ComfyUI published. Required - with no list of
            what it holds there is no missing file to find.

    Returns:
        ``[{file, folder, node_id, class_type, field}, …]``, one per missing
        LoRA whose loader was taken out; empty when nothing was missing or
        nothing could be bypassed honestly.
    """
    missing_by_node: dict[str, list[dict]] = {}
    for item in preflight_prompt(graph, object_info).get("missing_models") or []:
        if not item:
            continue
        if model_folder(item.get("class_type"), item.get("field")) != "loras":
            continue
        missing_by_node.setdefault(str(item.get("node_id")), []).append(item)

    bypassed: list[dict] = []
    for node_id, missing in missing_by_node.items():
        item = missing[0]
        if any(item.get("value") == FORGOTTEN_MODEL for item in missing):
            # The hub lost this reference's NAME; the file itself may well be
            # installed. :func:`resolve_references` puts the token in on
            # purpose so the pre-flight surfaces it, and bypassing would trade
            # that surfacing for a run quietly made without an adapter the
            # owner has - then send them to install a file called
            # "(forgotten model)".
            logger.info(
                "Node %s (%s) names a LoRA this hub can no longer name, so it "
                "keeps its refusal rather than being bypassed.",
                node_id,
                item.get("class_type"),
            )
            continue
        node = graph.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        # A slot counts as filled whether it names a file or is WIRED from
        # another node. Converting `lora_name` to an input is an ordinary
        # ComfyUI gesture, and `preflight_prompt` skips a link (it is computed
        # at run time, not a filename), so counting only strings would read a
        # stacker's live second adapter as an empty slot and drop it.
        filled = [
            field
            for field, value in (inputs or {}).items()
            if LORA_FILENAME_FIELD_RE.match(str(field))
            and (is_link(value) or (isinstance(value, str) and value))
        ]
        missing_fields = {str(item.get("field")) for item in missing}
        if not set(filled) <= missing_fields:
            # A stacker holding three LoRAs of which one is gone: the node
            # carries the two that ARE here, so taking it out would drop them
            # too. Left to block, which is the honest answer - the owner is
            # missing one file and would lose three adapters.
            logger.info(
                "Node %s (%s) holds %d LoRA slots and one of them (%s) is not "
                "on this ComfyUI, so it is left in place: bypassing it would "
                "drop the ones that are here.",
                node_id,
                item.get("class_type"),
                len(filled),
                ", ".join(str(item.get("value")) for item in missing),
            )
            continue
        try:
            bypass_node(graph, node_id, object_info)
        except LookupError as exc:
            logger.warning(
                "LoRA %s is not on this ComfyUI and node %s (%s) cannot be "
                "taken out of the graph, so the run is still refused: %s",
                item.get("value"),
                node_id,
                item.get("class_type"),
                exc,
            )
            continue
        logger.info(
            "Node %s (%s) is bypassed: this ComfyUI does not have %s, and a "
            "LoRA is optional, so the run goes ahead without it.",
            node_id,
            item.get("class_type"),
            ", ".join(str(item.get("value")) for item in missing),
        )
        bypassed.extend(
            {
                "file": str(item.get("value")),
                "folder": "loras",
                "node_id": node_id,
                "class_type": item.get("class_type"),
                "field": item.get("field"),
                # Taken out because the file is not here, not because the owner
                # asked: `skip_requested_loras` marks its own `True`.
                "requested": False,
            }
            for item in missing
        )
    return bypassed


def run_seed_targets(graph: dict, object_info: Optional[dict]) -> list[dict]:
    """The seed inputs a run writes into, as the run itself finds them.

    One function for the run and for :func:`replace_missing_seed_nodes`, because
    the replacement is only safe where this pass overwrites what it inlined: two
    copies of the rule could agree today and drift tomorrow, and the drift would
    be a run that quietly kept a placeholder seed.
    """
    return detect_seed_targets(graph, object_info or {}) or collect_seed_inputs(graph)


def replace_missing_seed_nodes(graph: dict, object_info: dict) -> list[dict]:
    """Drop every custom seed node this ComfyUI lacks, inlining its value.

    A graph naming rgthree's ``Seed (rgthree)`` on an install without rgthree is
    ``missing_nodes``, and the owner's only route was installing the pack. The
    node does something PixlStash already does (#1463): it hands a number to a
    sampler's ``seed`` / ``noise_seed``, and the run's seed pass writes that
    widget itself. So the link from the node becomes a literal and the node
    leaves the graph.

    **The seed pass overwriting the literal is what makes this safe**, so it is
    checked rather than assumed, and checked on the FINAL graph: a later
    replacement can change which finder :func:`run_seed_targets` answers with
    (``detect_seed_targets`` finding a new target stops the fallback), so an
    input that passed alone could stop being written. If any inlined input is
    not a target once every node is replaced, the graph is put back whole and
    every node keeps its refusal. A consumer that is not a seed widget - an
    ``INT`` driving width, a pack's own ``SEED`` dict - is refused the same way.

    The literal is the node's own ``seed`` value, which is what
    ``seed_mode: "keep"`` then keeps: a picture's embedded graph carries the
    value the node actually handed on.

    Keeps its refusal, logged: a class that IS installed (nothing to repair), a
    class outside :data:`SEED_NODE_CLASSES`, a node whose own seed is wired
    from elsewhere (the literal would cut that link), a **placeholder** seed
    such as rgthree's ``-1`` (there is no seed to keep, and accepting it only
    for some seed modes would make the pre-flight's answer depend on a control
    the popups do not re-ask on), and a node feeding **more than one** input:
    the seed pass rolls each target separately, so a hires-fix or refiner pair
    built to share one seed would get two.

    Args:
        graph: The API-format graph, mutated in place.
        object_info: The map this ComfyUI published.

    Returns:
        ``[{node_id, class_type, replacement, consumers}, …]``, one per node
        replaced; empty when nothing could be replaced honestly.
    """
    original = deepcopy(graph)
    replaced: list[dict] = []
    for node_id in [str(key) for key in graph]:
        node = graph.get(node_id)
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if class_type not in SEED_NODE_CLASSES or class_type in object_info:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if any(is_link(value) for value in inputs.values()):
            logger.info(
                "Node %s (%s) is not on this ComfyUI but its own seed is wired "
                "from another node, so it keeps its refusal: a literal would "
                "cut that link.",
                node_id,
                class_type,
            )
            continue
        value = inputs.get("seed")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            logger.info(
                "Node %s (%s) is not on this ComfyUI and carries no real seed "
                "(%r), so it keeps its refusal.",
                node_id,
                class_type,
                value,
            )
            continue
        consumers = [
            (str(other_id), str(name))
            for other_id, other in graph.items()
            if isinstance(other, dict) and isinstance(other.get("inputs"), dict)
            for name, link in other["inputs"].items()
            if is_link(link) and str(link[0]) == node_id
        ]
        if len(consumers) > 1:
            logger.info(
                "Node %s (%s) is not on this ComfyUI and feeds %s, which share "
                "one seed by design; the run would roll each separately, so it "
                "keeps its refusal.",
                node_id,
                class_type,
                ", ".join(f"{other}.{name}" for other, name in consumers),
            )
            continue
        for other_id, name in consumers:
            graph[other_id]["inputs"][name] = value
        del graph[node_id]
        replaced.append(
            {
                "node_id": node_id,
                "class_type": class_type,
                "replacement": "seed",
                "consumers": [
                    {"node_id": other, "field": name} for other, name in consumers
                ],
            }
        )
    targets = {
        (str(target.get("node_id")), str(target.get("field")))
        for target in run_seed_targets(graph, object_info)
    }
    unsafe = [
        f"{consumer['node_id']}.{consumer['field']}"
        for entry in replaced
        for consumer in entry["consumers"]
        if (consumer["node_id"], consumer["field"]) not in targets
    ]
    if unsafe:
        logger.info(
            "Seed nodes %s are not on this ComfyUI, but replacing them would "
            "leave %s holding a value the run's seed pass does not write, so "
            "the graph is left as it was and they keep their refusal.",
            ", ".join(f"{e['node_id']} ({e['class_type']})" for e in replaced),
            ", ".join(unsafe),
        )
        graph.clear()
        graph.update(original)
        return []
    for entry in replaced:
        logger.info(
            "Node %s (%s) is not on this ComfyUI, so it is replaced: %s now "
            "takes the run's own seed.",
            entry["node_id"],
            entry["class_type"],
            ", ".join(f"{c['node_id']}.{c['field']}" for c in entry["consumers"])
            or "nothing",
        )
    return replaced


def replace_missing_text_nodes(graph: dict, object_info: dict) -> list[dict]:
    """Drop every custom text node this ComfyUI lacks, inlining its string.

    A prompt typed into WAS's ``Text Multiline`` is ``missing_nodes`` on an
    install without WAS, although the node does nothing but hand its string to
    a ``CLIPTextEncode``. So each link from it becomes that string and the node
    leaves the graph. Unlike a seed, one string may feed any number of inputs.

    Keeps its refusal, logged: a class that IS installed, one outside
    :data:`TEXT_NODE_CLASSES`, a node whose text is wired from elsewhere or is
    not a string, one with another input set that may override it (Textbox's
    ``passthrough``), a WAS text holding a ``[token]`` only the node expands, and a
    consumer reading any output other than the first.

    Args:
        graph: The API-format graph, mutated in place.
        object_info: The map this ComfyUI published.

    Returns:
        ``[{node_id, class_type, replacement: "text", consumers}, …]``.
    """
    replaced: list[dict] = []
    for node_id in [str(key) for key in graph]:
        node = graph.get(node_id)
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if class_type not in TEXT_NODE_CLASSES or class_type in object_info:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        text = inputs.get(TEXT_NODE_CLASSES[class_type])
        if not isinstance(text, str):
            logger.info(
                "Node %s (%s) is not on this ComfyUI and its text is not a "
                "literal (%r), so it keeps its refusal.",
                node_id,
                class_type,
                text,
            )
            continue
        overriding = overriding_text_inputs(class_type, inputs)
        if overriding:
            logger.info(
                "Node %s (%s) is not on this ComfyUI and %s may change its "
                "text, so it keeps its refusal.",
                node_id,
                class_type,
                ", ".join(overriding),
            )
            continue
        if class_type == "Text Multiline":
            if WAS_TOKEN_RE.search(text):
                logger.info(
                    "Node %s (%s) is not on this ComfyUI and its text holds a "
                    "[token] only the node expands, so it keeps its refusal.",
                    node_id,
                    class_type,
                )
                continue
            # The node's own loop, line for line.
            text = "\n".join(
                line.replace("\n", "")
                for line in io.StringIO(text)
                if not line.strip().startswith("#")
            )
        consumers = [
            (str(other_id), str(name), link)
            for other_id, other in graph.items()
            if isinstance(other, dict) and isinstance(other.get("inputs"), dict)
            for name, link in other["inputs"].items()
            if is_link(link) and str(link[0]) == node_id
        ]
        if any(link[1] != 0 for _, _, link in consumers):
            logger.info(
                "Node %s (%s) is not on this ComfyUI and something reads an "
                "output other than its text, so it keeps its refusal.",
                node_id,
                class_type,
            )
            continue
        for other_id, name, _ in consumers:
            graph[other_id]["inputs"][name] = text
        del graph[node_id]
        logger.info(
            "Node %s (%s) is not on this ComfyUI, so it is replaced: %s now "
            "holds its text.",
            node_id,
            class_type,
            ", ".join(f"{other}.{name}" for other, name, _ in consumers) or "nothing",
        )
        replaced.append(
            {
                "node_id": node_id,
                "class_type": class_type,
                "replacement": "text",
                "consumers": [
                    {"node_id": other, "field": name} for other, name, _ in consumers
                ],
            }
        )
    return replaced


@dataclass(frozen=True)
class Repair:
    """One refusal PixlStash can answer by changing the graph (#1463).

    ``code`` is the reason it answers, ``report`` the ``RunGroup`` field that
    names what it changed, and ``apply`` the change itself: it mutates the graph
    and returns one entry per thing it did, empty when it did nothing.
    """

    code: str
    report: str
    apply: Callable[[dict, dict], list[dict]]


# The repair registry: the one place that decides what "repairable" means. Keyed
# on reason code so a repair runs only when `judge` reported what it answers,
# and so a new one is an entry here rather than another patch to the run route.
REPAIRS: tuple[Repair, ...] = (
    Repair(
        MISSING_MODELS,
        "bypassed_loras",
        bypass_missing_loras,
    ),
    # Text before seed, and the order is load-bearing: the seed repair's
    # rollback restores the graph it was handed, which must already hold the
    # text replacement it reports.
    Repair(MISSING_NODES, "replaced_nodes", replace_missing_text_nodes),
    Repair(MISSING_NODES, "replaced_nodes", replace_missing_seed_nodes),
)


def repair(
    graph: dict, object_info: dict, reasons: list[Reason]
) -> dict[str, list[dict]]:
    """Apply every registered repair whose refusal *reasons* contains.

    The caller judges, repairs, then judges **again**: a repair can leave its
    refusal standing (a stacker still holding an adapter that is here, a seed
    node feeding something that is not a seed), and only the second verdict
    says whether the graph now runs. Re-judging is pure over the graph and the
    ``object_info`` already fetched, so it costs no ComfyUI round-trip.

    Returns:
        ``{report_field: [entries…]}`` for every registered repair, empty lists
        included, so the caller can assign each field without knowing the set.
    """
    codes = {reason.code for reason in reasons}
    done: dict[str, list[dict]] = {}
    for entry in REPAIRS:
        done.setdefault(entry.report, []).extend(
            entry.apply(graph, object_info) if entry.code in codes else []
        )
    return done


def lora_slot_fields(inputs: dict) -> list[str]:
    """Every LoRA slot a node's inputs hold, filled or not, in widget order."""
    return [
        str(field)
        for field in inputs
        if LORA_FILENAME_FIELD_RE.match(str(field))
        or LORA_DIGEST_FIELD_RE.match(str(field))
    ]


def skip_requested_loras(
    graph: dict,
    slots: list[tuple[str, str]],
    object_info: Optional[dict],
) -> tuple[list[dict], list[Reason], set[tuple[str, str]]]:
    """Skip the LoRA slots the owner asked this run to do without.

    The owner's explicit "run without this LoRA" from the Run popup (#1478).
    A skip, never a removal: *graph* is the run's own copy, and the stored
    workflow keeps its loader - editing the workflow is the LoRA chain
    editor's job.

    It goes through :func:`bypass_node` like :func:`bypass_missing_loras`, and
    **the request is the consent** that function has to do without: a loader
    naming a :data:`FORGOTTEN_MODEL` is skipped when asked, because the owner
    has said they do not want it, whatever file it is.

    What the request cannot consent to is dropping a LoRA it did NOT name: a
    stacker holding another filled slot keeps its node, and the slot is a
    :data:`LORA_NOT_SKIPPABLE` reason instead - as is a loader nothing can be
    rewired around, and every slot when ComfyUI cannot be asked what to wire
    in its place. A reason and not a 400: the request made sense, this card
    cannot honour it.

    Slots the graph does not have are passed over (node ids belong to one graph,
    and a run can span cards); the caller learns which were found from the
    third value and refuses a slot NO graph of the run had.

    Args:
        graph: The API-format graph, mutated in place.
        slots: ``[(node_id, field), …]`` the request named.
        object_info: This ComfyUI's map, or ``None`` when it could not be asked.

    Returns:
        ``(skipped, reasons, found)``: ``bypassed_loras`` entries marked
        ``"requested": True``, the refusals, and the requested slots this graph
        holds.
    """
    wanted: dict[str, set[str]] = {}
    for node_id, slot_field in slots:
        wanted.setdefault(str(node_id), set()).add(str(slot_field))
    skipped: list[dict] = []
    reasons: list[Reason] = []
    found: set[tuple[str, str]] = set()
    for node_id, fields in wanted.items():
        node = graph.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        here = set(lora_slot_fields(inputs)) if isinstance(inputs, dict) else set()
        asked = sorted(fields & here)
        if not asked:
            logger.debug(
                "Skipping LoRA slot(s) %s on node %s does not apply here: this "
                "graph has no such slot.",
                ", ".join(sorted(fields)),
                node_id,
            )
            continue
        found.update((node_id, field) for field in asked)
        class_type = node.get("class_type")

        def shown(field: str) -> str:
            value = inputs.get(field)
            return value if isinstance(value, str) else ""

        def refuse(message: str) -> None:
            logger.info(
                "LoRA slot(s) %s on node %s (%s) cannot be skipped as asked: %s",
                ", ".join(asked),
                node_id,
                class_type,
                message,
            )
            reasons.extend(
                Reason(
                    LORA_NOT_SKIPPABLE,
                    {
                        "node_id": node_id,
                        "field": field,
                        "file": shown(field),
                        "message": message,
                    },
                )
                for field in asked
            )

        # Filled whether it names a file or is wired, as bypass_missing_loras
        # counts it: a wired second slot is a live adapter.
        filled = {
            field
            for field in here
            if is_link(inputs.get(field))
            or (isinstance(inputs.get(field), str) and inputs.get(field))
        }
        kept = sorted(filled - set(asked))
        if kept:
            refuse(
                f"Node {node_id} ({class_type}) also loads "
                f"{', '.join(shown(f) or f for f in kept)}, and skipping the "
                "node for this run would skip that too."
            )
            continue
        if object_info is None:
            refuse(
                "PixlStash could not reach ComfyUI, so it cannot tell what to "
                f"wire in place of node {node_id} ({class_type})."
            )
            continue
        try:
            bypass_node(graph, node_id, object_info)
        except LookupError as exc:
            refuse(str(exc))
            continue
        logger.info(
            "Node %s (%s) is skipped for this run at the owner's request: %s.",
            node_id,
            class_type,
            ", ".join(shown(field) or field for field in asked),
        )
        skipped.extend(
            {
                "file": shown(field),
                "folder": "loras",
                "node_id": node_id,
                "class_type": class_type,
                "field": field,
                "requested": True,
            }
            for field in asked
        )
    return skipped, reasons, found


def blocks_batch(reasons: list[Reason], *, allow_unchecked: bool = False) -> bool:
    """Whether one source's reasons stop the whole request rather than itself.

    A missing model is the brief's named case and the rule generalises the way
    the owner would expect: installing a file is a trip away from the keyboard,
    so queueing the rest of a mixed batch would leave them re-running the same
    gesture afterwards to catch the ones that were skipped.

    **An uninspectable ComfyUI blocks unless the owner has consented**, which is
    the whole of the ``allow_unchecked`` rule: without it nothing at all is
    known about the graph - not even which node classes this install has - so
    the request fails closed. With it the run goes ahead and the reason is
    still reported, because the fact remains true and the caller should see it.
    Consent reaches no other code: a missing model is a fact that WAS
    established, and there is nothing there to consent to.
    """
    unchecked = (COMFYUI_UNREACHABLE, COMFYUI_NOT_CONFIGURED)
    return any(
        reason.code == MISSING_MODELS
        or (reason.code in unchecked and not allow_unchecked)
        for reason in reasons
    )


def blocks_group(reasons: list[Reason], *, allow_unchecked: bool = False) -> bool:
    """Whether these reasons stop this one card from running.

    Every reason does, with the single exception consent creates: an
    acknowledged uninspectable ComfyUI is reported and run anyway. Without that
    exception ``allow_unchecked`` could never do anything, because the reason it
    consents to would keep its own group out of the submission either way.
    """
    if allow_unchecked:
        return any(
            reason.code not in (COMFYUI_UNREACHABLE, COMFYUI_NOT_CONFIGURED)
            for reason in reasons
        )
    return bool(reasons)


def saved_recipe_body(recipe) -> dict:
    """A saved recipe as the run body fields it stands in for.

    The row is the owner's own look, so it supplies the prompt, the LoRAs and
    the overrides - and the request may still override any of them, which is
    why this returns a body to merge under rather than applying anything.
    """
    try:
        loras = json.loads(recipe.loras or "[]")
    except json.JSONDecodeError as exc:
        logger.error(
            "Saved recipe %s has unreadable LoRAs, so the run uses none: %s",
            recipe.id,
            exc,
        )
        loras = []
    try:
        overrides = json.loads(recipe.overrides or "{}")
    except json.JSONDecodeError as exc:
        logger.error(
            "Saved recipe %s has unreadable overrides, so the run uses none: %s",
            recipe.id,
            exc,
        )
        overrides = {}
    return {
        "workflow_key": recipe.workflow_key,
        "prompt": recipe.prompt,
        "negative": recipe.negative,
        "loras": loras if isinstance(loras, list) else [],
        "overrides": overrides if isinstance(overrides, dict) else {},
        "seed": recipe.seed,
        "keep_seed": bool(recipe.keep_seed),
    }


def place_recipe_loras(
    slots: list[dict],
    recipe_loras: list[dict],
    slot_digests: dict[tuple[str, str], Optional[str]],
) -> tuple[list[tuple[dict, dict]], list[dict]]:
    """Which LoRA slot of the graph each of a saved recipe's LoRAs goes into (#1478).

    A saved LoRA names a file and a digest but no slot, so it is matched to one:
    **digest first** (the slot already loads the same bytes), **then basename**
    (the slot names a file of the same name, case-folded), then **any slot
    still free, in graph order** - the positional fill the run always did, so a
    recipe that ran before still runs the same. It replaces a positional
    ``zip``, which put a recipe stored in the other order onto the wrong
    loaders and dropped a third LoRA on a two-loader graph without a word, while
    the credit matcher had matched all three by name.

    A LoRA with nowhere to go is **reported, never dropped**, and so is one the
    shelf cannot identify (no ``sha256``): PixlStash cannot load a file it
    cannot name, so the recipe's row is not applied. That one still keeps a
    slot naming a file of the same name out of the positional fill - the graph
    loads it there already - and its report says so.

    Args:
        slots: :func:`detect_lora_targets`'s slots, in graph order.
        recipe_loras: The saved ``[{filename, sha256, strength}, …]``.
        slot_digests: ``{(node_id, field): sha256 or None}`` - which shelf LoRA
            each slot already loads, as far as the shelf can tell.

    Returns:
        ``(placements, unplaced)``: ``[(slot, saved LoRA), …]`` to apply, and
        ``[{"filename", "sha256", "node_id", "reason"}, …]`` for the rest.
    """

    def slot_key(slot: dict) -> tuple[str, str]:
        return (str(slot.get("node_id")), str(slot.get("field")))

    def digest_of(saved: dict) -> Optional[str]:
        value = str(saved.get("sha256") or "").strip().lower()
        return value or None

    def basename(value: Any) -> str:
        return normalized_filename(str(value or "").strip())

    free = list(slots)
    placed: dict[int, dict] = {}
    for index, saved in enumerate(recipe_loras):
        digest = digest_of(saved)
        if digest is None:
            continue
        slot = next((s for s in free if slot_digests.get(slot_key(s)) == digest), None)
        if slot is not None:
            placed[index] = slot
            free.remove(slot)
    for index, saved in enumerate(recipe_loras):
        name = basename(saved.get("filename"))
        if index in placed or not name:
            continue
        slot = next(
            (
                s
                for s in free
                if s.get("by") != "digest" and basename(s.get("value")) == name
            ),
            None,
        )
        if slot is not None:
            placed[index] = slot
            free.remove(slot)
    for index, saved in enumerate(recipe_loras):
        if index not in placed and digest_of(saved) is not None and free:
            placed[index] = free.pop(0)

    placements: list[tuple[dict, dict]] = []
    unplaced: list[dict] = []
    for index, saved in enumerate(recipe_loras):
        filename = str(saved.get("filename") or "")
        shown = filename or "A LoRA"
        digest = digest_of(saved)
        slot = placed.get(index)
        if digest is not None and slot is not None:
            placements.append((slot, saved))
            continue
        if digest is None:
            reason = (
                f"Your model shelf cannot identify {shown}, so PixlStash does not "
                "apply this recipe's setting for it"
                + (
                    f"; loader #{slot['node_id']} still loads a file of that name "
                    "as the workflow has it."
                    if slot is not None
                    else ", and nothing in this workflow loads it."
                )
            )
        elif not slots:
            reason = f"This workflow has no LoRA loader, so {shown} is not applied."
        else:
            reason = (
                f"This workflow has {len(slots)} LoRA "
                f"{'loader' if len(slots) == 1 else 'loaders'} and the recipe's "
                f"other LoRAs take {'it' if len(slots) == 1 else 'them all'}, so "
                f"{shown} is not applied."
            )
        unplaced.append(
            {
                "filename": filename,
                "sha256": digest,
                "node_id": str(slot["node_id"]) if slot is not None else None,
                "reason": reason,
            }
        )
    return placements, unplaced

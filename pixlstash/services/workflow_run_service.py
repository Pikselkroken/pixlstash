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

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from pixlstash.services.comfyui_recipe_service import (
    LORA_FILENAME_FIELD_RE,
    preflight_prompt,
    sanitize_prompt_graph,
    unchecked_preflight,
)
from pixlstash.services.comfyui_service import graph_has_pixlstash_nodes
from pixlstash.services.workflow_hash import asset_reference
from pixlstash.services.workflow_io import api_graph
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
FIXED_INPUT_DELETED = "fixed_input_deleted"
NO_LORA_LOADER = "no_lora_loader"
PIXLSTASH_NODES = "pixlstash_nodes"
NO_SAVE_NODE = "no_save_node"
NO_RUNNABLE_SOURCE = "no_runnable_source"

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
    hub,
    library_uuid: Optional[str],
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

    Every argument but the card is already-read data, so the caller owns the
    I/O and its ordering and this stays a pure decision. A tier that is present
    but unusable (a UI-format file) falls through to the next rather than
    failing the card: the owner's question is "can this run", and one export in
    the wrong format is not an answer to it.
    """
    ui_only = False
    if file_document:
        graph = api_graph(file_document)
        if graph:
            return Source(sanitize_prompt_graph(graph), FROM_FILE), None
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
    for structural_hash, document in instance_documents or []:
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

    Returns:
        ``(reasons, preflight)``. ``reasons`` empty means it would run.
    """
    reasons: list[Reason] = []
    if object_info is None:
        preflight = unchecked_preflight(object_info_error or "ComfyUI unreachable")
        reasons.append(Reason(COMFYUI_UNREACHABLE, {"error": object_info_error or ""}))
    else:
        preflight = preflight_prompt(graph, object_info)

    if graph_has_pixlstash_nodes(graph):
        reasons.append(Reason(PIXLSTASH_NODES))
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

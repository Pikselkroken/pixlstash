"""The recipe of one picture, as the overlay's Recipe section shows it (#1313).

Three things the picture's own ``workflow`` chunk does not say on its own:

* **which shelf model each asset is**, and whether the graph named it certainly.
  A graph that names a file by its digest (a ComfyUI-PixlStash loader's
  ``*_sha256``) names *that* file; a graph that names ``style.safetensors``
  names a file of that name, which is all it says. That is the same two-tier
  split ``model_shelf_service.fetch_picture_counts`` counts by, read here in
  the other direction - one picture, which models - so the shelf's
  "12 verified" and this panel's badge can never disagree about what verified
  means;
* **the settings**, the few widget values a person actually tunes, read through
  :mod:`pixlstash.services.workflow_parameters` so the names match the run
  panel's;
* **the resolution lock**, the ``generation_input`` rows saying which picture
  each input of the run actually loaded. Only runs PixlStash submitted have
  them; a scanned import has none, and none is written by guesswork.

Everything here is a read. Nothing in this module writes.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from pixlstash.db_models.generation import GenerationInput
from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_shelf_service import (
    models_for_digest,
    recipe_asset_index,
)
from pixlstash.services.workflow_hash import (
    MODEL_EXTENSIONS,
    SHA256_FIELD_RE,
    WorkflowGraphError,
    normalized_filename,
    reduce_api_graph,
)
from pixlstash.services.workflow_parameters import (
    FEATURED_NAMES,
    describe_parameters,
)

logger = get_logger(__name__)

# The LoRA strength widgets, in the order they are preferred. A stacker spells
# its second slot `lora_name_2` and its strength `strength_2` or
# `strength_model_2`, so the suffix is carried across from the name widget.
_STRENGTH_FIELDS = ("strength_model", "strength")


def describe_recipe(
    hub,
    api_prompt: Optional[dict],
    fallback_names: tuple[list[str], list[str]],
    inputs: list[dict],
    *,
    owner: bool = False,
) -> dict:
    """The Recipe section's view of one picture.

    Args:
        hub: The attached hub, or ``None`` when there is none. Without it no
            asset can be matched to a shelf model, so every slot reads as
            unmatched rather than as unverified.
        api_prompt: The picture's embedded API-format ``prompt`` graph, or
            ``None``.
        fallback_names: ``(models, loras)`` as
            :func:`~pixlstash.utils.comfyui_utilities.extract_generation_info`
            read them from the UI graph, used only when there is no API prompt.
            A name is all a UI graph gives, so those slots carry no strength.
        inputs: The resolution lock, from
            :func:`resolution_lock_in_session`. Passed in rather than read here,
            per the §10.1 rule that a service does its DB work on a session its
            caller opened.
        owner: Whether the caller holds a fully-unscoped owner credential. A
            scoped token is told the filename and the strength - both of which
            it can already read out of the graph it is being served - and never
            which shelf row the file is, which is a fact about the library
            rather than about this picture.

    Returns:
        ``{"model_slots": [...], "settings": [...], "inputs": [...]}``.
    """
    slots = _model_slots(api_prompt, fallback_names)
    return {
        "model_slots": _resolve_against_shelf(hub, slots) if owner else slots,
        "settings": _settings(api_prompt),
        "inputs": inputs,
    }


def _model_slots(
    api_prompt: Optional[dict], fallback_names: tuple[list[str], list[str]]
) -> list[dict]:
    """Every model the graph loads, with the strength it was loaded at.

    Read from :func:`~pixlstash.services.workflow_hash.reduce_api_graph` rather
    than from the raw graph, so the ``(widget_name, normalized_filename)`` pairs
    here are the same ones the hub filed as ``workflow_recipe_asset`` - which is
    what makes the shelf lookup below a lookup rather than a second, drifting
    normalisation.
    """
    if not api_prompt:
        models, loras = fallback_names
        return [
            {"name": normalized_filename(name), "widget": widget, "strength": None}
            for widget, names in (("ckpt_name", models), ("lora_name", loras))
            for name in names
            if isinstance(name, str) and name
        ]
    try:
        nodes = reduce_api_graph(api_prompt)
    except WorkflowGraphError as exc:
        logger.warning(
            "Not listing the models of picture recipe: the API graph could not "
            "be reduced (%s). The section shows no models rather than a guess.",
            exc,
        )
        return []
    slots: list[dict] = []
    for node in nodes.values():
        values = dict(node.instance_widgets)
        for widget, value in node.widgets:
            if value is None or not _names_a_model(widget, value):
                continue
            slots.append(
                {
                    "name": value,
                    "widget": widget,
                    "strength": _strength(widget, values),
                }
            )
    # Two nodes loading one file at one strength are one row on screen.
    seen: set[tuple] = set()
    unique = []
    for slot in slots:
        key = (slot["name"], slot["widget"], slot["strength"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(slot)
    return unique


def _names_a_model(widget: str, value: str) -> bool:
    """Whether this asset is a model rather than an input image.

    The reduction keeps image filenames as assets too (a ``LoadImage``'s
    ``image``), and a picture the run loaded is the resolution lock's business,
    not the model list's.
    """
    return bool(SHA256_FIELD_RE.search(widget)) or value.endswith(MODEL_EXTENSIONS)


def _strength(widget: str, values: dict[str, Any]) -> Optional[float]:
    """The strength a LoRA slot was loaded at, or ``None``.

    ``None`` for a checkpoint, which has no strength, and for a loader whose
    strength is wired from another node rather than set on it: a number that is
    computed at run time is not a number to print beside the file.
    """
    suffix = widget[len("lora_name") :] if widget.startswith("lora_name") else ""
    for field in _STRENGTH_FIELDS:
        value = values.get(field + suffix)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _resolve_against_shelf(hub, slots: list[dict]) -> list[dict]:
    """Add ``model_id`` and ``verified`` to each slot the shelf recognises.

    ``verified`` is true only where the graph named the file by its digest and
    exactly one shelf model has it. Anything else is a match by name: a file
    called that, which is all the graph said, and a name two shelf rows share
    resolves to neither.

    A digest slot's ``name`` is a sha256, which names nothing to a reader, so a
    resolved one is renamed to the shelf's filename. An unresolved one keeps the
    digest: that really is all this machine knows about the file.
    """
    if hub is None:
        return slots
    try:
        by_name, by_digest = recipe_asset_index(hub)
        # Only a digest slot needs the shelf's own filenames, and most graphs
        # have none: a core LoRA loader names the file, not its hash.
        filenames = (
            {
                row["id"]: row["filename"]
                for row in hub.fetchall(
                    "SELECT id, filename FROM model WHERE filename IS NOT NULL"
                )
            }
            if any(SHA256_FIELD_RE.search(slot["widget"]) for slot in slots)
            else {}
        )
    except Exception:
        logger.warning(
            "Could not index the shelf for picture recipe models; the slots are "
            "returned without their shelf rows.",
            exc_info=True,
        )
        return slots
    sorted_digests = sorted(by_digest)
    resolved = []
    for slot in slots:
        by_sha = bool(SHA256_FIELD_RE.search(slot["widget"]))
        if by_sha:
            matched = models_for_digest(slot["name"], by_digest, sorted_digests)
        else:
            matched = set(by_name.get(slot["name"], ()))
        model_id = matched.pop() if len(matched) == 1 else None
        resolved.append(
            {
                **slot,
                "name": (
                    filenames.get(model_id, slot["name"])
                    if by_sha and model_id is not None
                    else slot["name"]
                ),
                "model_id": model_id,
                "verified": by_sha and model_id is not None,
            }
        )
    return resolved


def _settings(api_prompt: Optional[dict]) -> list[dict]:
    """The sampler settings, as ``{"label", "value", "node"}`` rows.

    The same few names the run panel pins by default
    (:data:`~pixlstash.services.workflow_parameters.FEATURED_NAMES`), so a
    person reading the recipe and a person about to run it see one vocabulary.
    A primitive wired into one of them is labelled by what it drives, because
    its own widget is called ``value``.
    """
    if not api_prompt:
        return []
    try:
        parameters = describe_parameters(api_prompt)
    except WorkflowGraphError as exc:
        logger.warning(
            "Not listing the settings of a picture recipe: %s. The section "
            "shows no settings rather than a guess.",
            exc,
        )
        return []
    rows: list[dict] = []
    seen: set[tuple[str, Any]] = set()
    for parameter in parameters:
        driven = sorted(FEATURED_NAMES.intersection(parameter.drives))
        if parameter.name in FEATURED_NAMES:
            label = parameter.name
        elif driven:
            label = ", ".join(driven)
        else:
            continue
        key = (label, parameter.value)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {"label": label, "value": parameter.value, "node": parameter.node_title}
        )
    return rows


def resolution_lock_in_session(session: Session, picture_id: int) -> list[dict]:
    """Which picture each input of the run actually loaded.

    Empty for every picture PixlStash did not run itself: the backfill writes no
    ``generation_input`` row rather than invent lineage a ``LoadImage`` filename
    cannot prove.
    """
    rows = session.exec(
        select(GenerationInput, Picture.deleted)
        .where(GenerationInput.picture_id == picture_id)
        .outerjoin(Picture, Picture.id == GenerationInput.input_picture_id)
        .order_by(GenerationInput.node_ref, GenerationInput.position)
    ).all()
    return [
        {
            "node_ref": row.node_ref,
            "position": row.position,
            "pixel_sha": row.pixel_sha,
            # A soft-deleted input is still gone from the grid, so it is
            # reported the way a hard-deleted one is: the run loaded something
            # this library can no longer show.
            "input_picture_id": None if deleted else row.input_picture_id,
        }
        for row, deleted in rows
    ]

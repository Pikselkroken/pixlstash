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
from pixlstash.utils.model_utils import canonical_quant, quant_from_filename

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
        owner: Whether the caller holds a fully-unscoped owner credential.

    **Two of the three fields are owner-only, on one rule: a field about the
    picture is served to whoever may see the picture, and a field about the
    LIBRARY is not.** The filename and the strength of a model are in the graph
    this route serves anyway, so they go to everyone; which row of the owner's
    shelf that file is, does not. The resolution lock is the sharper case and
    goes no further than the owner at all: a ``generation_input`` row names
    ANOTHER picture's id and its ``pixel_sha``, and a token scoped to this
    picture is refused that picture on every other route - so serving it here
    would hand out an id the gate refuses, plus a content hash that answers
    "does this library hold this exact image?" to anyone who can hash a
    candidate. That is the BOLA-by-omission class ``docs/backend_architecture.md``
    §16 exists to close, and no lineage is worth reopening it.

    Returns:
        ``{"model_slots": [...], "inputs": [...]}``. The settings and the
        prompts are :func:`~pixlstash.utils.comfyui_utilities.extract_recipe_extras`'s,
        which the recipe route already calls: one reading of a graph's settings,
        never a second with a vocabulary of its own.
    """
    slots = _model_slots(api_prompt, fallback_names)
    return {
        "model_slots": _resolve_against_shelf(hub, slots) if owner else slots,
        "inputs": inputs if owner else [],
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

    ``quant`` is the precision the *filename* records, which is all a graph
    ever says; :func:`_resolve_against_shelf` upgrades it to the header's
    answer for any file this machine has scanned. **``name`` stays raw**: it is
    the key the shelf lookup runs on, and the panel shows it verbatim beside a
    name it derives itself (``utils/modelShelf.deriveModelName``, the same
    parser the workflow card's slot names come through), so a chip and the file
    it names never disagree.
    """
    if not api_prompt:
        models, loras = fallback_names
        return [
            {
                "name": normalized_filename(name),
                "widget": widget,
                "strength": None,
                "quant": quant_from_filename(name),
            }
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
                    "quant": quant_from_filename(value),
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

    ``quant`` is upgraded here for the same reason it is renamed: the shelf's
    column is read from the safetensors header, so it knows what a file called
    ``nvfp4_awq`` is actually stored at where the name only guesses, and a
    digest slot has no filename to read at all until this resolves one. The
    filename's answer stays where the shelf has no row and where its column is
    null.
    """
    if hub is None:
        return slots
    try:
        by_name, by_digest, filenames = recipe_asset_index(hub)
    except Exception:
        logger.warning(
            "Could not index the shelf for picture recipe models; the slots are "
            "returned without their shelf rows.",
            exc_info=True,
        )
        return slots
    sorted_digests = sorted(by_digest)
    shelf_quant = _shelf_quant(hub)
    resolved = []
    for slot in slots:
        by_sha = bool(SHA256_FIELD_RE.search(slot["widget"]))
        if by_sha:
            matched = models_for_digest(slot["name"], by_digest, sorted_digests)
        else:
            matched = set(by_name.get(slot["name"], ()))
        model_id = matched.pop() if len(matched) == 1 else None
        name = (
            filenames.get(model_id, slot["name"])
            if by_sha and model_id is not None
            else slot["name"]
        )
        resolved.append(
            {
                **slot,
                "name": name,
                "quant": (
                    shelf_quant.get(model_id)
                    or slot.get("quant")
                    or quant_from_filename(name)
                ),
                "model_id": model_id,
                "verified": by_sha and model_id is not None,
            }
        )
    return resolved


def _shelf_quant(hub) -> dict[int, Optional[str]]:
    """``{model id: canonical quant}`` for every row that records one.

    One read for the whole panel rather than one per slot, and the rows with a
    null column are left out so a ``.get`` miss and a null both fall through to
    the filename.
    """
    try:
        rows = hub.fetchall("SELECT id, quant FROM model WHERE quant IS NOT NULL")
    except Exception:
        logger.warning(
            "Could not read the model shelf's quant column for picture recipe "
            "models; the slots fall back to what their filenames record.",
            exc_info=True,
        )
        return {}
    return {int(row["id"]): canonical_quant(row["quant"]) for row in rows}


def _numeric(node_ref: str) -> tuple:
    """Sort key for a graph node id: numeric where it is a number.

    ``"10"`` after ``"7"``, and a subgraph path (``"75:61"``) segment by
    segment. ``isdecimal``, not ``isdigit``: the latter accepts superscripts
    that ``int()`` then refuses.
    """
    return tuple(
        (0, int(part), "") if part.isdecimal() else (1, 0, part)
        for part in str(node_ref).split(":")
    )


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
    ).all()
    # Ordered here rather than in SQL: a node ref is a graph id, and SQLite
    # would sort it as text, putting node 10 before node 7. A subgraph id
    # ("75:61") sorts by each segment for the same reason.
    rows.sort(key=lambda pair: (_numeric(pair[0].node_ref), pair[0].position))
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

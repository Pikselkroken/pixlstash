"""The kind a shelf model's workflows load it as, offered to the owner.

The shelf files a model from its header, its folder and its parameter count,
and some files beat all three: a GGUF text encoder in a flat folder stays
Unclassified. A workflow that loads the file says what it is - ``clip_name`` on
a text-encoder loader, ``vae_name`` on a VAE loader
(:func:`~pixlstash.services.workflow_identity.model_fix_kind`) - and the
recorded workflows already store which field names which file
(``workflow_recipe_asset``), so the answer is a lookup, not a rescan.

**An offer, never a write.** Nothing here touches ``model.file_kind``; the shelf
shows the kind beside the one it holds and the owner files it. And only when the
evidence agrees: every recorded workflow that loads the model loads it as the
same kind. One that loads it in another slot (a LoRA, an upscaler) or as a
vision encoder, or whose loader class cannot
be read, disagrees.

``model.file_kind_decided`` is the owner's say: set by any kind they pick and
by "Don't suggest", cleared by "Suggest a kind again". The suggestion is still
computed for a decided row, so the shelf can offer the way back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_shelf_service import recipe_asset_index
from pixlstash.services.workflow_hash import SHA256_FIELD_RE, normalized_filename
from pixlstash.services.workflow_identity import model_fix_kind
from pixlstash.utils.adapter_header import (
    FILE_CHECKPOINT,
    FILE_TEXT_ENCODER,
    FILE_UNKNOWN,
    FILE_VAE,
)
from pixlstash.utils.sql_chunking import chunked

logger = get_logger(__name__)

# The kinds a workflow can file a model as. A checkpoint loader is evidence too
# (it disagrees with a text-encoder loader of the same file), but a model the
# shelf cannot place as a checkpoint is left to the header that missed it.
OFFERED_KINDS = (FILE_VAE, FILE_TEXT_ENCODER)
# The kinds an offer may re-file. A model the shelf already files as a VAE, a
# LoRA or a text encoder was placed by what its header says, and a workflow
# loading it oddly is the odd one.
REFILEABLE_KINDS = (FILE_CHECKPOINT, FILE_UNKNOWN)
# A ``clip_name`` whose loader class is a vision loader, or could not be read:
# not a kind anything is filed as, so it only ever disagrees.
_NOT_A_TEXT_ENCODER = "clip_vision"
_UNREAD = "unread"
# A load in a slot ``model_fix_kind`` does not file (a LoRA, a ControlNet, an
# upscaler): a kind of its own as far as agreeing goes, so it only disagrees.
_OTHER_SLOT = "other_slot"


@dataclass(frozen=True)
class KindSuggestion:
    """The kind every recorded workflow loading one model agrees on.

    ``file_kind`` is what the shelf files the model as now. ``workflows``
    counts distinct workflows (their core hash), not recipes; ``fields`` names
    the loader fields that said so. ``decided`` is the owner's mark: the shelf
    shows the suggestion only while it is false.
    """

    file_kind: str
    kind: str
    workflows: int
    fields: tuple[str, ...]
    decided: bool


def kind_suggestions(hub, model_ids: Iterable[int]) -> dict[int, KindSuggestion]:
    """The suggestion for each of *model_ids* that has one.

    A model has one when it is filed as a checkpoint or Unclassified, a file of
    its name (and of no other shelf model's) is loaded by some recorded
    workflow, and every such load is of one kind in :data:`OFFERED_KINDS`.

    Args:
        hub: The open hub database.
        model_ids: The models to answer for. Others are not read.

    Returns:
        ``{model_id: KindSuggestion}``; a model with no agreed kind is absent.
    """
    candidates, kinds, workflows, fields = _evidence(hub, model_ids)
    return {
        model_id: KindSuggestion(
            file_kind=candidates[model_id][0],
            kind=next(iter(found)),
            workflows=len(workflows[model_id]),
            fields=tuple(sorted(fields[model_id])),
            decided=candidates[model_id][1],
        )
        for model_id, found in kinds.items()
        if len(found) == 1 and next(iter(found)) in OFFERED_KINDS
    }


def may_refile_by_name(hub, model_id: int, kind: str) -> bool:
    """Whether a file named like a missing one may be offered as *kind*.

    The weaker evidence: a loader of *kind* names a file this machine has never
    had, and *model_id*'s name reads as that file's renamed. Offered only on a
    checkpoint or Unclassified model nobody has decided the kind of, and only
    where no recorded workflow loads it, under its own name, as anything else.
    """
    candidates, kinds, _workflows, _fields = _evidence(hub, [model_id])
    return (
        model_id in candidates
        and not candidates[model_id][1]
        and kinds.get(model_id, {kind}) == {kind}
    )


def _evidence(
    hub, model_ids: Iterable[int]
) -> tuple[
    dict[int, tuple[str, bool]],
    dict[int, set[str]],
    dict[int, set[str]],
    dict[int, set[str]],
]:
    """What the recorded workflows load each re-fileable model as.

    Returns ``(candidates, kinds, workflows, fields)``: *candidates* maps each
    of *model_ids* filed as a checkpoint or Unclassified to ``(file_kind,
    decided)``; the other three map a model some workflow loads to the kinds
    it is loaded as, the workflows (core hashes) and the loader fields.
    """
    wanted = set(model_ids)
    if not wanted:
        return {}, {}, {}, {}
    candidates = {
        int(row["id"]): (row["file_kind"], bool(row["file_kind_decided"]))
        for batch in chunked(sorted(wanted))
        for row in hub.fetchall(
            "SELECT id, file_kind, file_kind_decided FROM model "
            f"WHERE id IN ({','.join('?' * len(batch))}) "
            "AND file_kind IN (?, ?)",
            (*batch, *REFILEABLE_KINDS),
        )
    }
    if not candidates:
        return {}, {}, {}, {}

    # A name two shelf models share says nothing about either.
    by_name = recipe_asset_index(hub)[0]
    owner = {
        name: next(iter(ids))
        for name, ids in by_name.items()
        if len(ids) == 1 and next(iter(ids)) in candidates
    }
    if not owner:
        return candidates, {}, {}, {}

    loads: list[tuple[int, str, str]] = []
    for batch in chunked(sorted(owner)):
        for row in hub.fetchall(
            "SELECT structural_hash, widget_name, normalized_filename "
            "FROM workflow_recipe_asset "
            f"WHERE normalized_filename IN ({','.join('?' * len(batch))})",
            tuple(batch),
        ):
            widget = row["widget_name"]
            # A digest names the file by its bytes, through a PixlStash loader
            # that only lists shelf models of its own kind already.
            if SHA256_FIELD_RE.search(widget):
                continue
            loads.append(
                (owner[row["normalized_filename"]], row["structural_hash"], widget)
            )
    if not loads:
        return candidates, {}, {}, {}

    recipes = sorted({recipe for _model, recipe, _widget in loads})
    topology: dict[str, tuple[str, str]] = {}
    for batch in chunked(recipes):
        for row in hub.fetchall(
            "SELECT r.structural_hash, r.topology_hash, c.core_hash, c.slots "
            "FROM workflow_recipe r LEFT JOIN workflow_topology_core c "
            "ON c.topology_hash = r.topology_hash "
            f"WHERE r.structural_hash IN ({','.join('?' * len(batch))})",
            tuple(batch),
        ):
            topology[row["structural_hash"]] = (
                row["core_hash"] or row["topology_hash"],
                row["slots"] or "",
            )

    kinds: dict[int, set[str]] = {}
    workflows: dict[int, set[str]] = {}
    fields: dict[int, set[str]] = {}
    for model_id, recipe, widget in loads:
        workflow, slots = topology.get(recipe, (recipe, ""))
        kinds.setdefault(model_id, set()).add(_loaded_as(recipe, widget, slots))
        workflows.setdefault(model_id, set()).add(workflow)
        fields.setdefault(model_id, set()).add(widget)
    return candidates, kinds, workflows, fields


def loader_offers(
    hub, loaders: Iterable[tuple[str, str, str]]
) -> dict[str, tuple[int, KindSuggestion]]:
    """The suggestions to offer on one workflow's loaders, by loader address.

    *loaders* are ``(address, kind the loader takes, filename)``. A loader gets
    its model's suggestion only where it is the kind this loader takes and
    nobody has decided the model's kind: the inspector offers what the shelf
    offers, never more. A file whose name two shelf models share is neither's.

    Returns:
        ``{address: (model_id, KindSuggestion)}``.
    """
    loaders = [
        (address, kind, filename)
        for address, kind, filename in loaders
        if kind in OFFERED_KINDS and filename
    ]
    if not loaders:
        return {}
    by_name = recipe_asset_index(hub)[0]
    owner = {}
    for address, _kind, filename in loaders:
        ids = by_name.get(normalized_filename(filename), set())
        if len(ids) == 1:
            owner[address] = next(iter(ids))
    suggestions = kind_suggestions(hub, owner.values())
    return {
        address: (owner[address], suggestion)
        for address, kind, _filename in loaders
        if address in owner
        and (suggestion := suggestions.get(owner[address])) is not None
        and suggestion.kind == kind
        and not suggestion.decided
    }


def _loaded_as(recipe: str, widget: str, slots: str) -> str:
    """What one loader field files its model as.

    Only ``clip_name`` depends on the loader's class (a vision loader's is an
    image encoder), and the asset row does not carry it, so it is read off the
    topology's slots: every loader in the graph with that field. A graph with
    both kinds of CLIP loader, or whose slots were never recorded, cannot say
    which one named this file, and answers :data:`_UNREAD`.
    """
    kind = model_fix_kind("", widget)
    if kind != FILE_TEXT_ENCODER:
        return kind or _OTHER_SLOT
    if not slots:
        return _UNREAD
    try:
        classes = {
            slot.get("class_type", "")
            for slot in json.loads(slots)
            if slot.get("widget") == widget
        }
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning(
            "Recipe %s: its topology's slots will not parse (%s), so its %s "
            "is no evidence of a text encoder.",
            recipe,
            exc,
            widget,
        )
        return _UNREAD
    answers = {
        model_fix_kind(class_type, widget) or _NOT_A_TEXT_ENCODER
        for class_type in classes
    }
    return answers.pop() if len(answers) == 1 else _UNREAD

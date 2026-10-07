"""Assembling a WORKFLOW's grid entry out of hub rows and vault counts (#1623).

A workflow is a group of topologies (``hub/workflow_card_reads.workflow_index``);
its counts, covers and ghosts are every variant's, and its models are its base
card's. The figures are read per variant and folded per workflow here.

**Computed per request, with no aggregate table.** The grid costs a handful of
grouped vault queries - per-variant counts, the cover window, the saved recipes
per card, the kept pictures per variant, and the checkpoint and LoRA values
the pictures used - and the hub reads ``card_index`` and ``workflow_index``
make, plus F7's ghost pass (one grouped count and the five reads
``model_ghost_names`` makes, measured together at 1.3 ms,
:func:`_describe_ghosts`). An aggregate table would have to be invalidated by
every rating, every import, every soft delete and every re-run of the card
backfill, and would be a second source of truth for numbers the vault can
already produce inside the frame budget.

Two orderings are decided here and nowhere else:

* **Cover rank** is the Bayesian mean ``(C·m + Σscore)/(C + n_rated)`` with
  ``C = 5`` and *m* the library's own mean rating, then picture count. A plain
  mean would put a workflow with one 5★ picture above one with forty averaging
  4.5, which is the failure the prior exists to stop.
* **A workflow's cover pictures** are its best three, picked in memory out of
  the best three of each of its variants - always a superset, because a
  workflow's pictures are the union of its variants'.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_card_reads import (
    Card,
    Workflow,
    adopted_file_variants,
    asset_names,
    card_index,
    find_workflow,
    instance_documents,
    manual_document,
    variant_documents,
    workflow_group_defaults,
    workflow_index,
)
from pixlstash.hub.workflow_cards import STRIP_LORAS_FOR_STACKS, loader_swaps_of
from pixlstash.hub.workflows import model_ghost_names, picture_ghosts_by_variant
from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_recipe_service import (
    LORA_DIGEST_FIELD_RE,
    sanitize_prompt_graph,
)
from pixlstash.services.model_shelf_service import (
    adapter_digest_index,
    models_for_digest,
    recipe_asset_index,
    base_model_family,
)
from pixlstash.services.workflow_hash import (
    SHELF_ID_FIELD,
    WorkflowGraphError,
    asset_reference,
    normalized_filename,
    structural_document,
)
from pixlstash.services.workflow_identity import (
    CORE_ADDRESS_PREFIX,
    Slot,
    base_model_kind,
    core_node_labels,
    unswapped,
    is_lora_widget,
    live_slots,
    model_fix_kind,
    slots,
    topology_node_labels,
)
from pixlstash.services.workflow_library_service import (
    CoverCandidate,
    VariantActivity,
    cover_order,
    read_card_grid,
    read_best_picture_ids,
    read_instance_hashes,
    read_picture_variant,
    read_variant_picture_counts,
)
from pixlstash.services.workflow_io import api_graph
from pixlstash.services.workflow_parameters import (
    FEATURED_NAMES,
    FEATURED_ORDER,
    is_latent_size,
    linked_size,
)
from pixlstash.utils.adapter_header import FILE_ADAPTER, FILE_TEXT_ENCODER, FILE_UNKNOWN
from pixlstash.utils.known_base_models import fold
from pixlstash.utils.model_utils import (
    canonical_quant,
    derive_model_name,
    quant_from_filename,
)
from pixlstash.utils.sql_chunking import chunked
from pixlstash.utils.workflow_ids import MANUAL_PREFIX

logger = get_logger(__name__)

# How many pictures a card's cover strip shows, and therefore how deep the
# window pass goes per variant.
COVER_DEPTH = 3

# The prior's weight in the cover rank. Five is "about one card's worth of
# ratings": a card needs roughly that many before its own mean outweighs the
# library's, which is the point at which the number starts meaning something.
RANK_PRIOR_WEIGHT = 5

# What "your best pictures" means when a default is read off them.
BEST_SCORE = 4

# Where a default came from. ``best`` is the mode over instances of pictures
# rated ``BEST_SCORE`` and up, ``all`` the same over every picture of the card
# (a card nobody has rated still has to offer a default), and ``edited`` is the
# owner's own value, which outranks both.
FROM_BEST = "best"
FROM_ALL = "all"
EDITED = "edited"

# A card nobody is going to look for: too few pictures to be a habit, never
# rated, never imported as a file of its own, and with no saved recipe on it.
# All four clauses are the design's, and the fourth arrived with B6's
# `saved_recipe` table - saving a look is the plainest statement that somebody
# means to run this again, so a card carrying one is never folded away.
ONE_OFF_PICTURES = 3

# How many of a card's newest runs a default is read off. The mode over the
# newest few hundred is the mode, and the read is one stored graph per run.
DEFAULT_SAMPLE = 200

# What a model slot's widget makes it, in the vocabulary `workflowCard.js`
# reads (`kind === "checkpoint"` names the card's one headline model). A widget
# this does not know keeps its own name rather than being called a checkpoint.
#
# **No base-model widget here.** Which spellings name the base model, and
# whether each is a checkpoint or a UNET, is
# `workflow_identity.base_model_kind`, derived from `CHECKPOINT_WIDGETS`; a
# copy here drifted from that set once already (#1404, #1622).
_SLOT_KINDS = {
    "vae_name": "vae",
    "clip_name": "clip",
    "clip_name1": "clip",
    "clip_name2": "clip",
    "clip_name3": "clip",
    "model_name": "upscale",
    "style_model_name": "style",
    "control_net_name": "controlnet",
    "gligen_name": "gligen",
    "hypernetwork_name": "hypernetwork",
    "photomaker_model_name": "photomaker",
}

# The slot kinds that name the BASE MODEL, most preferred first: a graph
# carrying both a checkpoint and a UNET is led by its checkpoint. Every widget
# in `CHECKPOINT_WIDGETS` maps to one of the two (`base_model_kind`).
BASE_MODEL_KINDS = ("checkpoint", "unet")


def slot_kind(widget: str) -> str:
    """What a model slot's widget makes it, or the widget's own name."""
    return base_model_kind(widget) or _SLOT_KINDS.get(widget, widget)


@dataclass(frozen=True)
class SlotModel:
    """One model a workflow's base card names: its file, and which slot it is in.

    ``label`` is the slot's address on the base topology. ``name`` is the
    graph's filename put through
    :func:`~pixlstash.utils.model_utils.derive_model_name`, so a checkpoint
    chip reads ``t5xxl`` rather than ``t5xxl_fp8_e4m3fn.safetensors``. The
    shelf lookup is done on the RAW value before that (:func:`model_titles`
    keys on it), which is the one ordering rule here: strip early and every
    shelf title silently stops resolving.

    **A LoRA slot of a stored recipe carries no name.** Which LoRA fills it is
    the recipe's business (``recipe_values`` and the default recipe say what
    ran); only a manual workflow, read off its own document, names the LoRA
    the document loads.
    """

    name: Optional[str]
    kind: str
    label: Optional[str] = None
    # The precision the file was stored at, as one canonical id
    # (:func:`~pixlstash.utils.model_utils.canonical_quant`): the shelf's own
    # column where this machine has scanned the file, the filename postfix
    # otherwise. ``None`` where neither says, which is most models.
    #
    # It is a field rather than part of ``name`` because ``name`` has just had
    # it stripped: two quant variants of one model collapse to one name, and
    # this is what keeps them apart.
    quant: Optional[str] = None
    # What the model shelf calls this file - the trainer's own name, or the one
    # the owner typed - where the shelf knows it. ``None`` otherwise, and that
    # is the ordinary case: it means only that this machine has not scanned the
    # file, never that the slot is empty.
    title: Optional[str] = None
    # The rest of what the shelf draws this model with (:class:`ShelfMark`):
    # its chosen picture, and the base model - in both spellings - that a
    # generated mark takes its colour from.
    icon: Optional[str] = None
    base_model: Optional[str] = None
    base_model_folded: Optional[str] = None
    # Which shelf file this is, and the family of the base model the shelf
    # identified it as (``base_model_family``), so a client can ask "does this
    # LoRA fit this workflow's checkpoint?" with the shelf's own answer. Both
    # ``None`` for a model the shelf does not hold or cannot pin down to one
    # file.
    sha256: Optional[str] = None
    base_model_family: Optional[str] = None
    # The value the recipe recorded, as ``default_recipe.models[].filename``
    # spells it (a shelf loader's id included), so a client can tell which
    # slot a recipe model is by identity rather than by a derived name.
    filename: Optional[str] = None


@dataclass
class WorkflowFigures:
    """One workflow's counts, its rank and the pictures that would cover it.

    ``card`` is the workflow as the grid draws it: its base card (the card a
    run starts from) with the workflow's own name, notes and hidden flag laid
    over it, and **every** variant of the workflow, the base card's first, so
    the counts, covers and ghosts are the workflow's and the models the base
    card's. ``base`` is that base card as the hub holds it, for anything that
    resolves a graph; ``None`` for a workflow whose base topology has no
    variant filed.
    """

    card: Card
    workflow: Workflow
    base: Optional[Card] = None
    pictures: int = 0
    rated: int = 0
    score_total: int = 0
    # Pictures per star, 1★ first (:attr:`VariantActivity.stars`).
    stars: list[int] = field(default_factory=lambda: [0] * 5)
    last_used: Optional[datetime] = None
    rank: float = 0.0
    covers: list[CoverCandidate] = field(default_factory=list)
    saved_recipes: int = 0
    models: list[SlotModel] = field(default_factory=list)
    loras: list[SlotModel] = field(default_factory=list)
    ghosts: int = 0
    model_ghosts: int = 0
    # ``{"checkpoints": [(name, pictures)], "loras": [(name, pictures)]}``:
    # the values the workflow's kept pictures used, most used first, spelled
    # as the picture filters take them (:func:`_describe_recipe_values`).
    recipe_values: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    @property
    def workflow_id(self) -> str:
        return self.workflow.workflow_id

    @property
    def rating(self) -> Optional[float]:
        """The mean of the stars this workflow has, or ``None`` when it has none.

        The PLAIN mean, deliberately, and not :attr:`rank`. The rank is
        smoothed towards the library's average so that workflows can be
        ordered against each other; showing it as the rating would tell
        somebody their never-rated workflow is rated 4.0.
        """
        return (self.score_total / self.rated) if self.rated else None

    @property
    def one_off(self) -> bool:
        """Too small, unrated, never imported by hand and never saved from.

        A file pulled from ComfyUI does not count as imported here (#1440):
        a pull brings a whole install's experiments in one gesture, and
        exempting all of them would bury the grid. A file the owner dropped
        in, or one a pull merely matched, still does.
        """
        return (
            self.pictures < ONE_OFF_PICTURES
            and self.rated == 0
            and not self.card.hand_imported
            and not self.saved_recipes
        )


@dataclass
class Grid:
    """``GET /workflows``: what the Workflows view opens on.

    ``cards`` is what the grid draws - visible, in cover-rank order, hidden
    workflows and one-offs removed. ``figures`` is every workflow including
    those, because a workflow the grid does not draw still has to open by its
    own URL: hiding one is a decision about the grid, not a deletion.
    """

    cards: list[WorkflowFigures]
    one_offs: int
    hidden: int
    figures: list[WorkflowFigures] = field(default_factory=list)

    def figure(self, workflow_id: str) -> Optional[WorkflowFigures]:
        """One workflow's figures by id, hidden and one-off ones included."""
        return next((f for f in self.figures if f.workflow_id == workflow_id), None)


def _figures(
    workflows: list[Workflow],
    cards: list[Card],
    activity: dict[str, VariantActivity],
    candidates: list[CoverCandidate],
    saved_recipes: dict[str, int],
    superseded: frozenset[str] = frozenset(),
) -> list[WorkflowFigures]:
    """Fold each workflow's variants into one set of counts and one cover strip.

    A picture of a *superseded* variant - made with a model the owner has
    since replaced - is flagged and covers only where no picture made with
    the workflow as it now stands can. *saved_recipes* is by workflow id.
    """
    by_variant: dict[str, list[CoverCandidate]] = {}
    for candidate in candidates:
        by_variant.setdefault(candidate.structural_hash, []).append(candidate)
    by_key = {card.workflow_key: card for card in cards}

    figures = []
    for workflow in workflows:
        members = [by_key[key] for key in workflow.cards if key in by_key]
        base = by_key.get(workflow.base_card or "")
        head = base or members[0]
        # A manual workflow's pictures are filed under its own id (the vault
        # reads group by it, `workflow_library_service._filed_as`), so that id
        # is the one key its counts, covers and values are read under.
        variants = (
            [workflow.workflow_id]
            if base is not None and base.manual
            else list(
                dict.fromkeys([*(base.variants if base else ()), *workflow.variants])
            )
        )
        files = [card.file_name for card in [head, *members] if card.file_name]
        card = replace(
            head,
            name=workflow.name,
            notes=workflow.notes,
            hidden=workflow.hidden,
            imported=any(member.imported for member in members),
            hand_imported=any(member.hand_imported for member in members),
            file_name=files[0] if files else None,
            variants=variants,
        )
        figure = WorkflowFigures(
            card=card,
            workflow=workflow,
            base=base,
            saved_recipes=saved_recipes.get(workflow.workflow_id, 0),
        )
        strip: list[CoverCandidate] = []
        for structural_hash in variants:
            seen = activity.get(structural_hash)
            if seen is not None:
                figure.pictures += seen.pictures
                figure.rated += seen.rated
                figure.score_total += seen.score_total
                figure.stars = [a + b for a, b in zip(figure.stars, seen.stars)]
                if seen.last_used is not None and (
                    figure.last_used is None or seen.last_used > figure.last_used
                ):
                    figure.last_used = seen.last_used
            strip.extend(
                replace(candidate, superseded=True)
                if structural_hash in superseded
                else candidate
                for candidate in by_variant.get(structural_hash, ())
            )
        strip.sort(
            key=lambda candidate: (not candidate.superseded, cover_order(candidate)),
            reverse=True,
        )
        figure.covers = strip[:COVER_DEPTH]
        figures.append(figure)
    return figures


def _superseded_variants(hub: HubDatabase, cards: list[Card]) -> frozenset[str]:
    """The variants that load a model the owner replaced in their workflow.

    In a slot of the kind the fix was made for: a VAE naming a file of the
    same name as a replaced checkpoint is still what the workflow loads there.
    Nothing to read, and no cost, on a hub where nobody has replaced one.

    The asset rows name the widget and not the loader, which is exact for
    every kind but one: ``clip_name`` is a text encoder on a CLIP loader and
    an image encoder on a CLIP vision one. A variant matched only that way is
    confirmed against its stored document's slots, which carry the loader
    class, so the document read is paid for that case alone.
    """
    replaced: dict[str, set[tuple[str, str]]] = {}
    for topology_hash, kind, was_norm in hub.fetchall(
        "SELECT topology_hash, slot_kind, was_norm FROM workflow_model_fix"
    ):
        replaced.setdefault(topology_hash, set()).add((kind, was_norm))
    if not replaced:
        return frozenset()
    topology_of = {
        variant: card.topology_hash
        for card in cards
        if card.topology_hash in replaced
        for variant in card.variants
    }
    superseded: set[str] = set()
    to_confirm: list[str] = []
    for variant, pairs in asset_names(hub, list(topology_of)).items():
        kinds = {
            kind
            for widget, name in pairs
            if (kind := model_fix_kind("", widget), name)
            in replaced[topology_of[variant]]
        }
        if kinds - {FILE_TEXT_ENCODER}:
            superseded.add(variant)
        elif kinds:
            to_confirm.append(variant)
    for variant, document in variant_documents(hub, to_confirm).items():
        wanted = {
            asset_reference(name)
            for kind, name in replaced[topology_of[variant]]
            if kind == FILE_TEXT_ENCODER
        }
        try:
            found = slots(document)
        except WorkflowGraphError as exc:
            logger.warning(
                "Variant %s: its stored document will not reduce, so its "
                "covers are not marked as made with a replaced text encoder: %s",
                variant,
                exc,
            )
            continue
        if any(
            slot.asset in wanted
            and model_fix_kind(slot.class_type, slot.widget) == FILE_TEXT_ENCODER
            for slot in found
        ):
            superseded.add(variant)
    return frozenset(superseded)


def _rank(figures: list[WorkflowFigures]) -> None:
    """Score every workflow by the Bayesian mean of its ratings, in place.

    *m* is the library's own mean rating rather than a constant, so a library
    that rates generously is not flattened towards somebody else's idea of
    average. With nothing rated anywhere the prior is zero and the rank falls
    back to being a picture count, which is the only signal there is.
    """
    rated = sum(figure.rated for figure in figures)
    total = sum(figure.score_total for figure in figures)
    mean = (total / rated) if rated else 0.0
    for figure in figures:
        figure.rank = (RANK_PRIOR_WEIGHT * mean + figure.score_total) / (
            RANK_PRIOR_WEIGHT + figure.rated
        )


def _rank_order(figure: WorkflowFigures) -> tuple:
    """Cover rank, then picture count, then the id so ties are stable."""
    return (-figure.rank, -figure.pictures, figure.workflow_id)


def read_grid(
    hub: HubDatabase,
    vault,
    *,
    include_hidden: bool = False,
    include_one_offs: bool = False,
    manual_models=None,
) -> Grid:
    """Everything ``GET /workflows`` answers: one entry per workflow.

    Figures are read per variant and folded per workflow (a workflow is a
    group of topologies, :func:`~pixlstash.hub.workflow_card_reads.
    workflow_index`); what a workflow's models are is its base card's. A card
    in no workflow yet - its topology not reached by the backfill - is not on
    the grid.

    *manual_models* is how a manual workflow, which has **no variant**, gets
    its models: ``(workflow id) -> [(widget name, filename)]`` read off its
    own document, or ``None`` to leave it without any. Passed in so the
    caller can cache the parse per document.

    The two flags are the Filters panel's *Show hidden workflows* and the
    unticked *Hide one-offs* (F7). They widen what is DRAWN; ``hidden`` and
    ``one_offs`` are counted either way, so the panel can label its own
    checkboxes with the number it is letting in.
    """
    cards = card_index(hub)
    counts = (
        read_variant_picture_counts(vault)
        if getattr(vault, "library_uuid", None)
        else None
    )
    workflows = workflow_index(hub, counts, cards)
    activity, candidates, saved_recipes, model_values = read_card_grid(
        vault, COVER_DEPTH, [card.workflow_key for card in cards if card.manual]
    )
    figures = _figures(
        workflows,
        cards,
        activity,
        candidates,
        saved_recipes,
        _superseded_variants(hub, cards),
    )
    _rank(figures)
    # One read of `workflow_recipe_asset` for every pass below: every variant,
    # which is what the ghost pass needs and a superset of the first variants
    # the slot pass reads.
    names = asset_names(
        hub, [variant for figure in figures for variant in figure.card.variants]
    )

    # **Both counts are taken over the same set whatever the flags say** -
    # hidden over every workflow, one-offs over the ones that are not hidden -
    # so ticking one checkbox does not move the other's number underneath it.
    # An automatic workflow with no picture and no saved recipe, every variant
    # of it a file step 7 made a manual workflow, is that manual workflow's
    # twin: never drawn or counted, though it still opens by its id (#1720).
    adopted = adopted_file_variants(hub)
    listed = [
        figure
        for figure in figures
        if figure.card.manual
        or figure.pictures
        or figure.saved_recipes
        or not figure.workflow.variants
        or not all(v in adopted for v in figure.workflow.variants)
    ]
    hidden = sum(1 for figure in listed if figure.card.hidden)
    one_offs = sum(1 for figure in listed if figure.one_off and not figure.card.hidden)
    drawn = [
        figure
        for figure in listed
        if (include_hidden or not figure.card.hidden)
        and (include_one_offs or not figure.one_off)
    ]
    drawn.sort(key=_rank_order)
    _describe_slots(hub, figures, names, _recovered_slots(figures, manual_models))
    _describe_ghosts(hub, vault, figures, names)
    _describe_recipe_values(figures, model_values)
    return Grid(cards=drawn, one_offs=one_offs, hidden=hidden, figures=figures)


def _describe_recipe_values(
    figures: list[WorkflowFigures], by_variant: dict[str, dict[str, Counter]]
) -> None:
    """Fill in the checkpoints and LoRAs each workflow's kept pictures used.

    Most used first, then by name; spelled as the picture filters take them
    (``workflow_library_service.variant_model_values``), so "this workflow's
    pictures made with X" is one filter away.
    """
    for figure in figures:
        totals = {"checkpoints": Counter(), "loras": Counter()}
        for variant in figure.card.variants:
            for kind, used in by_variant.get(variant, {}).items():
                totals[kind].update(used)
        figure.recipe_values = {
            kind: sorted(used.items(), key=lambda item: (-item[1], item[0]))
            for kind, used in totals.items()
        }


def _shelf_candidates(
    hub: HubDatabase, names: list[str]
) -> tuple[dict[str, set[int]], dict[int, Optional[str]]]:
    """``({name: every shelf model it could be}, {model id: its title})``.

    The resolution behind :func:`model_marks`, which says why all three
    spellings of an asset value are tried, why the index rather than a
    hand-written join answers it, and why the answer is a set. A name with no
    candidate at all is left out.
    """
    wanted = {name.lower() for name in names if name}
    if not wanted:
        return {}, {}
    by_name, by_digest, _filenames, _names = recipe_asset_index(hub)
    sorted_digests = sorted(by_digest)
    # Every model, named or not: the unnamed ones are what make a shared name
    # ambiguous, so filtering them out in SQL would hand back a confident
    # title for a name two files answer to.
    titles = {
        row["id"]: (row["display_name"] or "").strip() or None
        for row in hub.fetchall("SELECT id, display_name FROM model")
    }
    candidates: dict[str, set[int]] = {}
    for value in wanted:
        models = set(by_name.get(value, ()))
        models |= models_for_digest(value, by_digest, sorted_digests)
        # A shelf id is the one asset value that is not a name at all
        # (`SHELF_ID_FIELD`), and the node refuses anything but digits.
        if value.isdigit() and int(value) in titles:
            models.add(int(value))
        if models:
            candidates[value] = models
    return candidates, titles


@dataclass(frozen=True)
class ShelfMark:
    """What the shelf draws a model with, for a card that has no picture.

    ``icon`` is ``model.icon_sha256``. The base model comes in **both**
    spellings the shelf serves it in, because a client hashes the mark's
    colour out of ``folded or raw`` (``utils/modelShelf.baseModelKey``): one
    field would give the same model two colours in two places, which is the
    one thing a mark exists not to do. ``base_model_folded`` is null whenever
    ``known_base_models`` does not recognise the string, raw included.
    ``quant`` is the shelf's own column, read from the safetensors header
    where there is one - so it is the authoritative answer, and the filename
    postfix a card falls back to is only for a file nothing has scanned.
    ``sha256`` names the file and ``base_model_family`` is the shelf's own
    (:func:`base_model_family`), for a client that checks a LoRA against the
    card.

    All of it is null far more often than not: PixlStash generates no sample
    for a checkpoint it registers in place, and most adapters carry no base
    model either, so a mark with none of it is the ordinary case rather than
    a failure.
    """

    title: Optional[str] = None
    icon: Optional[str] = None
    base_model: Optional[str] = None
    base_model_folded: Optional[str] = None
    quant: Optional[str] = None
    sha256: Optional[str] = None
    base_model_family: Optional[str] = None
    # The shelf row's own file, which is what a shelf loader's id names.
    filename: Optional[str] = None


# What a default recipe's shelf loader shows when its id names no shelf row any
# more (the model was forgotten or removed): a bare number would read as a
# model called "75". A card slot serves a null name instead, which every client
# already reads as a forgotten model.
SHELF_MODEL_GONE = "(model no longer on the shelf)"

# The same for a shelf row that is still there but names no single file.
SHELF_MODEL_UNNAMED = "(unnamed shelf model)"


def model_marks(hub: HubDatabase, names: list[str]) -> dict[str, ShelfMark]:
    """``{slot name: ShelfMark}`` - how the shelf would draw each model.

    **The one place the shelf is asked about a card's models**, and therefore
    where the rule lives.

    A card's stored slot value is one of three things
    (``services.workflow_hash.structural_widget_value``): a lowercased
    basename, a SHA-256 digest (whole, or the 10- or 12-hex prefix A1111
    writes), or a shelf id. The card cannot say which it holds, so all three
    are resolved, through :func:`recipe_asset_index` rather than against
    ``model`` by hand: that index knows every *copy*'s basename via
    ``model_file.relpath``, so a second copy filed under a different spelling
    still resolves, and, through :func:`models_for_digest`, an A1111 short
    hash of a digest this hub holds the long form of.

    **A name that could be more than one model is ambiguous, and the two
    halves of a mark answer that differently.** The index maps a name to a
    *set* deliberately, and for the NAME a set is ambiguous unless every
    member is named and they all agree - an unnamed rival is not a tie-break,
    it is a second file this card might equally have used. The PICTURE is
    stricter still: two rows that agree on a name are two files, and a
    thumbnail is a picture *of one of them*, so an ambiguous name takes none
    and the client falls back to the initials it derives from the filename.
    Naming a card after the wrong model is worse than naming it after its
    file, and showing the wrong file's sample is worse than showing neither.

    A name this shelf has never seen is absent rather than present-and-empty,
    so a caller can tell "not on this machine" from "here, with no picture".
    """
    candidates, titles = _shelf_candidates(hub, names)
    if not candidates:
        return {}
    # ``{model id: every name that resolved to it}`` and not the inverse: one
    # model answers to its own filename, to each copy's basename and to its
    # digest, so two slot values of one card can land on one row. Keyed the
    # other way round, the second would overwrite the first and a model whose
    # picture the shelf holds would draw initials instead.
    single: dict[int, list[str]] = {}
    for value, models in candidates.items():
        if len(models) == 1:
            single.setdefault(next(iter(models)), []).append(value)
    pictures: dict[str, tuple] = {}
    for batch in chunked(sorted(single)):
        placeholders = ",".join("?" * len(batch))
        for row in hub.fetchall(
            "SELECT id, icon_sha256, base_model, base_model_canonical, "
            "quant, sha256, filename "
            f"FROM model WHERE id IN ({placeholders})",
            tuple(batch),
        ):
            for value in single[row["id"]]:
                pictures[value] = (
                    row["icon_sha256"],
                    row["base_model"],
                    row["base_model_canonical"] or fold(row["base_model"]),
                    canonical_quant(row["quant"]),
                    row["sha256"],
                    base_model_family(row),
                    row["filename"],
                )
    marks = {}
    for value, models in candidates.items():
        claimed = {titles.get(model_id) for model_id in models}
        title = claimed.pop() if len(claimed) == 1 and None not in claimed else None
        marks[value] = ShelfMark(title, *pictures.get(value, (None,) * 7))
    return marks


def _recovered_slots(figures: list[WorkflowFigures], manual_models) -> dict[str, list]:
    """``{workflow_key: [(widget name, filename)]}`` for the manual workflows.

    A manual workflow is a document and nothing else, so the cached slot list
    ``_describe_slots`` reads - which is written per recipe - has nothing to
    say about it and its rows would read "no checkpoint" about a workflow
    nobody had looked at. *manual_models* is :func:`read_grid`'s reader.

    Best effort by construction, and **empty is "not read"**: the recovery
    reads an editor-format file's widget values by position and reads nothing
    at all from a template-style export whose loaders were never filled in. A
    card that recovers nothing keeps no models, and its client is told which
    of the two it is by ``variant_count: 0``.
    """
    if manual_models is None:
        return {}
    found = {}
    for figure in figures:
        card = figure.card
        if not card.manual:
            continue
        loaded = manual_models(card.workflow_key)
        if loaded:
            found[card.workflow_key] = loaded
    return found


def _wired_names(
    hub: HubDatabase,
    figures: list[WorkflowFigures],
    names: dict[str, list[tuple]],
) -> dict[str, tuple[set[str], dict[str, list[Optional[str]]]]]:
    """``{workflow_key: (repeated widgets, {slot label: [filename]})}``.

    ``asset_names`` keys a filename by widget, never by loader, so a card
    naming one model widget on two loaders (a Wan 2.2 high/low UNET pair)
    cannot tell from it which file sat where (#1691). Only those cards pay for
    a reduction of their first variant's document, which pairs each slot label
    with the reference wired into it, and only their repeated widgets are
    named from it: a widget on one loader holds one file, so its pairing is
    already exact. LoRA slots do not count: the card shows no filename on one.

    A variant a model fix swapped a loader into (#1605) is carded under the
    topology it was swapped from, so it is read with the original loader put
    back, as :func:`_variant_reads` reads it, or its labels would not be the
    card's; that loader is named by the model fix's replacement. Loaders Weisfeiler-Leman cannot separate share a label and take
    its files in ``slots``' order; such twins are interchangeable by
    construction. A document that is missing or will not reduce names no
    model on the repeated loaders rather than falling back to a guess.
    """
    ambiguous: dict[str, tuple[str, str, set[str]]] = {}
    for figure in figures:
        card = figure.card
        counts = Counter(
            str(slot.get("widget") or "")
            for slot in card.slots
            if not slot.get("is_lora")
        )
        repeated = {widget for widget, count in counts.items() if count > 1}
        if card.variants and repeated:
            ambiguous[card.variants[0]] = (
                card.workflow_key,
                card.topology_hash,
                repeated,
            )
    wired = {key: (repeated, {}) for key, _, repeated in ambiguous.values()}
    filed_as: dict[str, str] = {}
    for batch in chunked(sorted(ambiguous)):
        placeholders = ",".join("?" * len(batch))
        filed_as.update(
            hub.fetchall(
                "SELECT structural_hash, topology_hash FROM workflow_recipe "
                f"WHERE structural_hash IN ({placeholders})",
                tuple(batch),
            )
        )
    for variant, document in variant_documents(hub, list(ambiguous)).items():
        key, topology_hash, _ = ambiguous[variant]
        known = {
            asset_reference(filename): filename
            for _, filename in names.get(variant, ())
        }
        try:
            swapped = filed_as.get(variant)
            if swapped and swapped != topology_hash:
                _, document = unswapped(
                    document, loader_swaps_of(hub.fetchall, swapped)
                )
                # The loader put back holds the fix's replacement by reference,
                # and this variant filed only the digest the swapped-in loader
                # took; the fix row is where the replacement's name lives, and
                # forgetting either name deletes it.
                known.update(
                    (asset_reference(now_norm), now_norm)
                    for (now_norm,) in hub.fetchall(
                        "SELECT now_norm FROM workflow_model_fix "
                        "WHERE topology_hash = ?",
                        (topology_hash,),
                    )
                )
            document_slots = slots(document)
        except WorkflowGraphError as exc:
            logger.error(
                "Stored document for variant %s will not reduce, so card %s "
                "names no model on its repeated loaders: %s",
                variant,
                key,
                exc,
            )
            document_slots = []
        by_label = wired[key][1]
        for slot in document_slots:
            by_label.setdefault(slot.label, []).append(known.get(slot.asset))
    return wired


def _describe_slots(
    hub: HubDatabase,
    figures: list[WorkflowFigures],
    names: dict[str, list[tuple]],
    recovered: Optional[dict[str, list]] = None,
) -> None:
    """Fill in each workflow's models and LoRA slots from its base card's slots.

    The slot list is what B2 cached per topology precisely so a read does not
    have to re-derive it. The one exception is a card naming one model widget
    on two loaders: its first variant's document is reduced to say which file
    sat on which (:func:`_wired_names`, #1691), and no other card's is. ``names`` is :func:`read_grid`'s one
    :func:`~pixlstash.hub.workflow_card_reads.asset_names` read, the table that
    holds the readable filenames, shared with :func:`_describe_ghosts`.

    **A LoRA slot is a slot, not a file.** Which LoRA fills it is the
    recipe's business, so its name is left off (see :class:`SlotModel`); what
    the pictures put in it is ``recipe_values``.

    A second hub read puts the shelf's own name - and its picture, for a card
    that has to draw itself out of its models (#1466) - beside each filename,
    once for the whole grid rather than per card. It is a join inside one
    database and not a derivation, which is why it can be afforded on a grid
    read at all.

    *recovered* is :func:`_recovered_slots`' answer for the cards that have no
    cached slot list because they have no recipe. It is empty for every other
    card, and a card is in exactly one of the two branches below.
    """
    # Off `read_grid`'s shared `names` rather than a read of its own: that one
    # covers EVERY variant where this pass only draws the first, so it is a
    # superset and resolving a few filenames no chip shows is cheaper than a
    # second pass over the same table.
    recovered = recovered or {}
    marks_by_name = model_marks(
        hub,
        [filename for pairs in names.values() for _, filename in pairs]
        + [filename for pairs in recovered.values() for _, filename in pairs],
    )
    wired = _wired_names(hub, figures, names)
    for figure in figures:
        card = figure.card
        by_widget: dict[str, list[str]] = {}
        for widget, filename in names.get(
            card.variants[0] if card.variants else "", ()
        ):
            by_widget.setdefault(widget, []).append(filename)
        taken: Counter = Counter()
        repeated, by_label = wired.get(card.workflow_key, (set(), {}))

        def next_name(slot: dict) -> Optional[str]:
            # By wiring where the widget sits on two loaders, else the one
            # filename the widget has, which is exact.
            widget = str(slot.get("widget") or "")
            key, found = (
                (str(slot.get("label") or ""), by_label)
                if widget in repeated
                else (widget, by_widget)
            )
            listed = found.get(key, ())
            index = taken[key]
            taken[key] += 1
            return listed[index] if index < len(listed) else None

        for slot in card.slots:
            widget = str(slot.get("widget") or "")
            if slot.get("is_lora"):
                # Consumed all the same, so the next LoRA slot does not claim
                # this one's file.
                next_name(slot)
                figure.loras.append(
                    SlotModel(
                        name=None,
                        kind="lora",
                        label=str(slot.get("label") or "") or None,
                    )
                )
            else:
                name = next_name(slot)
                filename = _slot_filename(widget, name, marks_by_name)
                figure.models.append(
                    SlotModel(
                        name=_derived(filename),
                        kind=slot_kind(widget) if widget else "model",
                        label=str(slot.get("label") or "") or None,
                        filename=name,
                        **_mark_fields(
                            marks_by_name.get((name or "").lower()), filename
                        ),
                    )
                )

        _fill_base_model(figure, names, marks_by_name)

        # A manual card has no cached slots at all, so this is the other
        # branch of the same `if` rather than an addition to it: the loop
        # above ran zero times. **No `label`** - these are read off the
        # document by widget, not by topology. A LoRA here is named: it is in
        # the document, and there is no recipe to fill it.
        for widget, filename in recovered.get(card.workflow_key, ()):
            shown = _slot_filename(widget, filename, marks_by_name)
            fields = _mark_fields(marks_by_name.get(filename.lower()), shown)
            name = _derived(shown)
            if widget == "lora_name":
                figure.loras.append(
                    SlotModel(name=name, kind="lora", filename=filename, **fields)
                )
            else:
                figure.models.append(
                    SlotModel(
                        name=name,
                        kind=slot_kind(widget) if widget else "model",
                        filename=filename,
                        **fields,
                    )
                )


def _fill_base_model(
    figure: WorkflowFigures,
    names: dict[str, list[tuple]],
    marks_by_name: dict[str, ShelfMark],
) -> None:
    """Name the workflow's base model off its variants when its slot list has none.

    The slot list is cached per topology from whichever variant reached it
    first. A shelf loader filed before its id was kept, or left blank, has no
    slot there, and the card was then called "Text to Image" with "No
    checkpoint" while the workflow's other runs named the model all along.
    The model named by the most variants (then by name, so two reads agree)
    joins the list, with no label: it is read by widget, not off the base
    topology.

    **A base-model slot with no name is left alone.** That is a model whose
    name was forgotten or never recorded, and "Checkpoint missing" is the
    card saying so; a sibling's file is not what that loader loads.
    """
    if any(m.kind in BASE_MODEL_KINDS for m in figure.models):
        return
    # Counted per model, not per spelling: a shelf id and the file it names
    # (`checkpoint_id` and `ckpt_name` on one core) are one vote, once per
    # variant. The first spelling seen is the one the slot carries.
    named: Counter = Counter()
    spelled: dict[str, tuple[str, str]] = {}
    for variant in figure.card.variants:
        models = set()
        for widget, value in names.get(variant, ()):
            # A shelf id the shelf no longer holds names nothing.
            resolved = base_model_kind(widget) and _slot_filename(
                widget, value, marks_by_name
            )
            if resolved:
                models.add(resolved.lower())
                spelled.setdefault(resolved.lower(), (widget, value))
        named.update(models)
    if not named:
        return
    # ponytail: one base model, counted by variants; a Wan high/low pair is
    # named by its commoner half, and pictures would rank better than variants.
    widget, name = spelled[min(named, key=lambda model: (-named[model], model))]
    filename = _slot_filename(widget, name, marks_by_name)
    figure.models.append(
        SlotModel(
            name=_derived(filename),
            kind=slot_kind(widget),
            filename=name,
            **_mark_fields(marks_by_name.get(name.lower()), filename),
        )
    )


def _slot_filename(
    widget: str, name: Optional[str], marks: dict[str, ShelfMark]
) -> Optional[str]:
    """*name* as a filename: a shelf loader's id becomes the shelf row's file.

    ``checkpoint_id`` holds a row id (:data:`SHELF_ID_FIELD`), which is a
    number and not a name, so every reader of ``models[].name`` would show
    ``75``. An id the shelf no longer holds is ``None``, the forgotten-model
    state, so the missing-checkpoint signals still fire. The shelf lookup
    itself stays keyed on the raw id.
    """
    if widget != SHELF_ID_FIELD or not name:
        return name
    mark = marks.get(name.lower())
    return mark.filename if mark else None


def shelf_filenames(hub: HubDatabase, ids: list[str]) -> dict[str, Optional[str]]:
    """``{shelf id: the file it names}`` for a shelf loader's values.

    :data:`SHELF_MODEL_GONE` where the shelf holds no such row, and ``None``
    where it holds one but cannot name a single file for it: that model is
    still on the shelf.
    """
    marks = model_marks(hub, ids)
    return {
        value: marks[value].filename if value in marks else SHELF_MODEL_GONE
        for value in ids
    }


def _derived(name: Optional[str]) -> Optional[str]:
    """A slot's filename as a card shows it: no folders, no extension, no quant.

    **``None`` stays ``None``.** A slot whose name the recipe never recorded
    serves null, and it has to keep doing so: ``derive_model_name(None)``
    answers ``""``, which reads as a model called nothing rather than as a
    model nobody named, and every client that tests the name for truth would
    then draw an empty chip where it draws "no checkpoint" today.

    A name that does not survive the strip falls back to the file's own
    string, which is the shelf's own ``derived -> from-file`` chain
    (``utils/modelShelf.modelName``) rather than a second rule: ``nvfp4_awq``
    is a real filename that is nothing but its quant, and the raw string is
    the only honest thing left to show for it.
    """
    if not name:
        return None
    return derive_model_name(name) or name


def _mark_fields(mark: Optional[ShelfMark], name: Optional[str] = None) -> dict:
    """The shelf's fields for one model, as :class:`SlotModel` keywords.

    Only ``quant`` for a model the shelf does not hold, so the slot keeps the
    dataclass's own defaults rather than being told three times that a file
    this machine has never scanned has no title, no picture and no base model.

    **``quant`` is the one field a card can answer without the shelf**, which
    is why it is computed here rather than left to the caller: the header wins
    where there is one (the shelf's column is read from it), and the filename
    postfix fills the gap for a ``.gguf``, and for every model in a workflow
    this machine has never scanned - which is most of them on a freshly
    imported graph.
    """
    quant = (mark.quant if mark else None) or quant_from_filename(name or "")
    if mark is None:
        return {"quant": quant}
    return {
        "title": mark.title,
        "icon": mark.icon,
        "base_model": mark.base_model,
        "base_model_folded": mark.base_model_folded,
        "quant": quant,
        "sha256": mark.sha256,
        "base_model_family": mark.base_model_family,
    }


def _describe_ghosts(
    hub: HubDatabase,
    vault,
    figures: list[WorkflowFigures],
    names: dict[str, list[tuple]],
) -> None:
    """Fill in what each workflow keeps of something deleted (F7's Ghosts filter).

    Two kinds, counted apart because forgetting them is two different purges in
    Settings › Privacy: a **picture ghost** is the thumbnail and prompt of a
    picture this library no longer has, and a **model ghost** is a VALUE naming
    a model the shelf does not hold - a filename, or a ``*_sha256`` digest,
    which is what :func:`~pixlstash.hub.workflows.model_ghost_names` judges.

    ``model_ghosts`` is therefore **the number of DISTINCT ghost values this
    card's variants name**, and not a number of models: the set comprehension
    dedupes a value repeated across variants (right - one missing file named
    twice is one thing missing), and a single missing model named both by
    filename and by digest is two values and counts 2 (unavoidable without
    resolving a digest to a name the shelf does not have). The Filters row
    asks only whether a card keeps either kind, so nothing on screen depends
    on the number; ⓘ, which could say which kind, must not spell it as a count
    of models.

    **What it costs, measured.** ``picture_ghosts_by_variant`` is one grouped
    count; ``model_ghost_names`` is five reads including a scan of
    ``model_file`` and a ``DISTINCT`` over the whole of
    ``workflow_recipe_asset``. Over a synthetic hub built to the shape quoted
    in this module's own docstring - 629 variants across 192 topologies, two
    asset rows each, 20 shelf models over 2 000 files, 400 picture ghosts -
    the two together are **1.3 ms**, of which the ghost count is 0.2 ms. The
    whole grid read is about 75 ms, so the pass is under 2% of it.

    That number is also the answer to the second cost, which is the one worth
    stating: :func:`read_grid`'s other caller is ``_read_detail``, what every
    workflow write answers with - so a rename now pays these reads too. At
    1.3 ms it is not worth making conditional, and a detail card carrying
    ``ghosts: 0`` when the card does hold one would be a wrong answer on a
    route that is the only way to unhide something. **Caching the name set is
    therefore NOT worth its invalidation** (a model scan, a folder removal and
    a workflow import all move it); if the shelf grows a hundredfold and this
    does become a complaint, that cache is where to look, keyed on something
    the hub already bumps rather than on a timer.

    **The names come from** ``names`` **- every variant - and not from**
    :attr:`WorkflowFigures.models` **and** :attr:`~WorkflowFigures.loras`.
    Those two lists are what the entry is drawn as: they cover the base card's
    first variant alone, and a LoRA slot is anonymous there, so a forgotten
    character LoRA - the commonest model ghost of all - would never be counted.
    """
    # `vault.library_uuid` is a real property returning `Optional[str]`, so a
    # `getattr` default here would only ever hide a typo in the attribute name.
    # The `if` below is the part doing work.
    library_uuid = vault.library_uuid
    if library_uuid:
        by_variant = picture_ghosts_by_variant(hub, library_uuid)
    else:
        # Every card then reports `ghosts: 0`, and on the Filters panel that
        # reads as "this library keeps nothing deleted" - a wrong answer
        # rather than an empty one, so it is said out loud. A vault with no
        # library identity is a real state (nothing attached yet), which is
        # why it is a warning and not a raise.
        by_variant = {}
        # No path in the message: the vault's identity here IS the missing
        # uuid, and the impact is the number that tells somebody how much of
        # the answer is affected.
        logger.warning(
            "This vault has no library uuid, so a picture ghost cannot be "
            "matched to the library that holds it: all %d workflow cards will "
            "report ghosts: 0 and the Workflows Ghosts filter will read as "
            "'nothing deleted' rather than 'not known'.",
            len(figures),
        )
    ghost_names = model_ghost_names(hub)
    for figure in figures:
        figure.ghosts = sum(
            by_variant.get(variant, 0) for variant in figure.card.variants
        )
        figure.model_ghosts = len(
            {
                filename
                for variant in figure.card.variants
                for _widget, filename in names.get(variant, ())
            }
            & ghost_names
        )


@dataclass(frozen=True)
class Default:
    """One parameter a workflow starts from, and where the value came from.

    ``label`` is what a reader sees (``utils/workflowCard.js`` keys the ⓘ list
    on it, so it is unique within a workflow); ``slot_label`` and
    ``input_name`` are the address ``workflow_group_default`` is keyed on
    (``<slot_label>/<input_name>``) and are what ``PUT …/defaults`` edits.
    """

    label: str
    slot_label: str
    input_name: str
    value: bool | int | float | str
    provenance: str


def _address_order(address: tuple[str, str]) -> tuple:
    """``FEATURED_ORDER`` rank, any other name after them, then the slot label."""
    slot_label, input_name = address
    rank = (
        FEATURED_ORDER.index(input_name)
        if input_name in FEATURED_ORDER
        else len(FEATURED_ORDER)
    )
    return rank, input_name, slot_label


def _labels(addresses: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """A unique, readable label per address: the input name, disambiguated.

    ⓘ keys its list on the label, so two slots offering ``steps`` cannot both
    be called "steps" - that is a duplicate `v-for` key and one row silently
    replaces the other. A second one is numbered rather than named after its
    slot label, which is a 64-character digest and means nothing to a reader.
    """
    seen: Counter = Counter()
    labels = {}
    for address in addresses:
        input_name = address[1]
        seen[input_name] += 1
        labels[address] = (
            input_name if seen[input_name] == 1 else f"{input_name} {seen[input_name]}"
        )
    return labels


# ---------------------------------------------------------------------------
# A workflow's default recipe (#1622).
#
# ``card_defaults`` widened from one card to a whole workflow: the same sample
# (the newest ``DEFAULT_SAMPLE`` distinct instances of the pictures rated
# ``BEST_SCORE`` and up, else of every picture), read across every variant of
# every topology in the workflow. Parameters and models are addressed on the
# CORE graph (``core:<label>/<input>``), which every topology of an automatic
# workflow shares; a parameter of a node inside a stage group has no core label
# and is addressed by its slot label on the base topology instead, read only off
# instances of that topology. Prompt, negative and seed are not part of it.
# ---------------------------------------------------------------------------

# How a LoRA of the default recipe is addressed in ``workflow_group_default``:
# by the file's SHA-256, the one handle that survives a rename.
LORA_ADDRESS_PREFIX = "lora:"

# The value a ``lora:`` override holds to take a LoRA out of the default recipe.
LORA_OFF = "off"


@dataclass(frozen=True)
class DefaultModel:
    """One model the default recipe loads, at a loader's core address.

    ``kind`` is the shelf ``file_kind`` the loader takes (``model_fix_kind``).
    ``filename`` is the normalized name the hub holds (``None`` for one whose
    name was forgotten); a run writes it in ComfyUI's spelling. On a shelf
    loader that is a row id, so ``shelf_filename`` is the file it names
    (:data:`SHELF_MODEL_GONE` once the shelf no longer holds it) for a reader
    that shows the model rather than writing it back.
    """

    address: str
    kind: str
    filename: Optional[str]
    provenance: str
    shelf_filename: Optional[str] = None


@dataclass(frozen=True)
class DefaultLora:
    """One LoRA of the default recipe (#1620 D2).

    In the recipe because it is in **more than half** of the sampled instances,
    at its modal strength, or because the owner put it there. ``sha256`` is the
    shelf's, ``None`` where the shelf cannot name the file; a run then cannot
    load it and reports it unplaced, as it does a saved recipe's.
    """

    asset: str
    filename: Optional[str]
    sha256: Optional[str]
    strength: Optional[float]
    provenance: str


@dataclass
class DefaultRecipe:
    """What a workflow runs with when nobody says otherwise.

    ``stages`` maps each stage group the base topology has to whether the
    default recipe runs it: on, unless most of the sampled pictures ran
    without it. ``base_card`` is the card whose source a run resolves.
    """

    workflow_id: str
    base_topology: Optional[str]
    base_card: Optional[str] = None
    sampled: int = 0
    # Whether the sample says which LoRAs the workflow runs: some LoRA has a
    # majority, most instances loaded none, or the owner edited them. A split
    # with no majority is NOT "none", and a run then keeps the graph's LoRAs.
    loras_decided: bool = False
    values: list[Default] = field(default_factory=list)
    models: list[DefaultModel] = field(default_factory=list)
    loras: list[DefaultLora] = field(default_factory=list)
    stages: dict[str, bool] = field(default_factory=dict)

    def recipe_loras(self) -> list[dict]:
        """The LoRAs as a saved recipe holds them, for ``place_recipe_loras``."""
        return [
            {
                "filename": lora.filename,
                "sha256": lora.sha256,
                "strength": lora.strength,
            }
            for lora in self.loras
        ]


@dataclass(frozen=True)
class _VariantRead:
    core: dict[str, str]
    base: dict[str, str]
    slots: list[Slot]


def workflow_defaults(
    hub: HubDatabase, vault, workflow_id: str
) -> Optional[DefaultRecipe]:
    """The default recipe of one workflow, or ``None`` for an unknown id.

    Featured parameters and models are the mode per address; a LoRA is in when
    more than half the sampled instances loaded it, at its modal strength;
    a stage the base topology has is on unless most instances ran without it.
    The owner's edits (``workflow_group_default``) replace what they name and
    say so (``EDITED``). Counted per distinct instance, as ``card_defaults``
    counts, and with its tie-break.
    """
    library_uuid = getattr(vault, "library_uuid", None)
    counts = read_variant_picture_counts(vault) if library_uuid else None
    workflow = find_workflow(hub, workflow_id, counts)
    if workflow is None:
        return None

    provenance = FROM_BEST
    documents: list[tuple[str, dict]] = []
    manual_names: Optional[dict[str, str]] = None
    if workflow_id.startswith(MANUAL_PREFIX):
        # A manual workflow's sample is its own document, read by its own
        # slot labels: never a picture's run, and never a `core:` address.
        provenance = FROM_ALL
        documents, reads, manual_names = _manual_sample(hub, workflow_id)
    else:
        if library_uuid:
            hashes = read_instance_hashes(
                vault, workflow.variants, BEST_SCORE, DEFAULT_SAMPLE
            )
            if not hashes:
                provenance = FROM_ALL
                hashes = read_instance_hashes(
                    vault, workflow.variants, None, DEFAULT_SAMPLE
                )
            documents = instance_documents(hub, library_uuid, hashes)
        # Plus one variant of the base topology, sampled or not: its core
        # labels are what an owner's `core:` default must name to mean
        # anything (a split's copied label map can name a node an heir's core
        # pruned).
        base_variant = next(
            (
                variant
                for variant in workflow.variants
                if workflow.variant_topology.get(variant) == workflow.base_topology
            ),
            None,
        )
        reads = _variant_reads(
            hub,
            workflow,
            {structural_hash for structural_hash, _ in documents}
            | ({base_variant} if base_variant else set()),
        )

    values: dict[tuple[str, str], Counter] = {}
    models: dict[str, Counter] = {}
    model_kinds: dict[str, str] = {}
    lora_seen: Counter = Counter()
    bare = 0
    lora_strengths: dict[str, Counter] = {}
    lora_widgets: dict[str, str] = {}
    base_stages = workflow.specials.get(workflow.base_topology or "") or ()
    without: Counter = Counter()
    # Instances whose topology's stages are known: the stage vote's electorate.
    staged = 0
    sampled = 0
    for structural_hash, document in documents:
        read = reads.get(structural_hash)
        if read is None:
            continue
        sampled += 1
        for node_id, node in document.items():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            node_id = str(node_id)
            if node_id in read.core:
                slot_label = CORE_ADDRESS_PREFIX + read.core[node_id]
            elif node_id in read.base:
                slot_label = read.base[node_id]
            else:
                continue
            for name, value in inputs.items():
                # A wired picture size is offered as the number its source
                # works out to (a ResolutionSelector, a primitive), so a run
                # can set it on the latent.
                if is_latent_size(node.get("class_type"), name):
                    resolved = linked_size(document, value)
                    if resolved is not None:
                        value = resolved
                # The scalar check also rejects a wired input (a link list).
                if name in FEATURED_NAMES and isinstance(
                    value, (bool, int, float, str)
                ):
                    values.setdefault((slot_label, name), Counter())[value] += 1
        loaded = set()
        for slot in read.slots:
            if slot.is_lora:
                loaded.add(slot.asset)
                lora_widgets[slot.asset] = slot.widget
                strength = (
                    (document.get(slot.node_id) or {})
                    .get("inputs", {})
                    .get("strength_model")
                )
                if isinstance(strength, (int, float)) and not isinstance(
                    strength, bool
                ):
                    lora_strengths.setdefault(slot.asset, Counter())[
                        float(strength)
                    ] += 1
                continue
            kind = model_fix_kind(slot.class_type, slot.widget)
            label = read.core.get(slot.node_id)
            if kind and label:
                address = f"{CORE_ADDRESS_PREFIX}{label}/{slot.widget}"
                models.setdefault(address, Counter())[slot.asset] += 1
                model_kinds[address] = kind
        lora_seen.update(loaded)
        bare += not loaded
        ran = workflow.specials.get(workflow.variant_topology.get(structural_hash, ""))
        # A topology the specials pass has not read yet says nothing either way.
        if ran is not None:
            staged += 1
            without.update(stage for stage in base_stages if stage not in ran)

    names = (
        manual_names
        if manual_names is not None
        else {
            asset_reference(filename): filename
            for pairs in asset_names(hub, list(reads)).values()
            for _widget, filename in pairs
        }
    )
    overrides = workflow_group_defaults(hub, workflow_id)
    recipe = DefaultRecipe(
        workflow_id=workflow_id,
        base_topology=workflow.base_topology,
        base_card=workflow.base_card,
        sampled=sampled,
        stages={stage: without[stage] * 2 <= staged for stage in base_stages},
    )

    core_labels = {
        CORE_ADDRESS_PREFIX + label
        for read in reads.values()
        for label in read.core.values()
    }
    value_overrides, model_overrides, lora_overrides = {}, {}, {}
    for address, value in overrides.items():
        if address.startswith(LORA_ADDRESS_PREFIX):
            lora_overrides[address[len(LORA_ADDRESS_PREFIX) :].lower()] = value
            continue
        slot_label, _, input_name = address.rpartition("/")
        if (
            core_labels
            and slot_label.startswith(CORE_ADDRESS_PREFIX)
            and slot_label not in core_labels
        ):
            # Kept in the hub, never shown or run: a node this workflow's core
            # does not have would be a parameter row that sets nothing.
            logger.info(
                "Workflow %s holds default %r = %r on a node its core does not "
                "have; it is left out of the default recipe.",
                workflow_id,
                address,
                value,
            )
        elif not slot_label or not input_name:
            logger.warning(
                "Workflow %s holds default address %r, which names no input, so "
                "it is not part of the default recipe.",
                workflow_id,
                address,
            )
        elif model_fix_kind("", input_name):
            model_overrides[address] = value
        else:
            value_overrides[(slot_label, input_name)] = _stored_value(value)

    # By parameter first: slot labels are digests, so sorting on them alone put
    # the size above the sampler in one workflow and below it in the next.
    addresses = sorted(set(values) | set(value_overrides), key=_address_order)
    labels = _labels(addresses)
    for address in addresses:
        if address in value_overrides:
            recipe.values.append(
                Default(labels[address], *address, value_overrides[address], EDITED)
            )
        else:
            recipe.values.append(
                Default(labels[address], *address, _mode(values[address]), provenance)
            )

    for address in sorted(set(models) | set(model_overrides)):
        widget = address.rpartition("/")[2]
        if address in model_overrides:
            recipe.models.append(
                DefaultModel(
                    address,
                    model_kinds.get(address) or model_fix_kind("", widget) or "",
                    model_overrides[address],
                    EDITED,
                )
            )
        else:
            recipe.models.append(
                DefaultModel(
                    address,
                    model_kinds[address],
                    names.get(_mode(models[address])),
                    provenance,
                )
            )
    # A shelf loader's `filename` is a row id: say which file it names.
    on_shelf = [
        index
        for index, model in enumerate(recipe.models)
        if model.address.endswith("/" + SHELF_ID_FIELD) and model.filename
    ]
    files = shelf_filenames(hub, [recipe.models[i].filename for i in on_shelf])
    for index in on_shelf:
        model = recipe.models[index]
        recipe.models[index] = replace(model, shelf_filename=files[model.filename])

    by_name, _digests = adapter_digest_index(hub)

    def shelf_digest(asset: str) -> Optional[str]:
        filename = names.get(asset)
        if (
            filename is not None
            and LORA_DIGEST_FIELD_RE.match(lora_widgets[asset])
            and re.fullmatch(r"[0-9a-f]{64}", filename.lower())
        ):
            # A whole digest only: an A1111 short hash names no one file.
            return filename.lower()
        shelf = by_name.get(normalized_filename(filename or ""), set())
        return next(iter(shelf)) if len(shelf) == 1 else None

    majority = sorted(asset for asset, seen in lora_seen.items() if seen * 2 > sampled)
    for asset in majority:
        filename = names.get(asset)
        sha256 = shelf_digest(asset)
        strengths = lora_strengths.get(asset)
        lora = DefaultLora(
            asset,
            filename,
            sha256,
            _mode(strengths) if strengths else None,
            provenance,
        )
        edited = lora_overrides.pop(sha256, None) if sha256 else None
        if edited is None:
            recipe.loras.append(lora)
        elif edited != LORA_OFF:
            recipe.loras.append(
                replace(lora, strength=_float_or_none(edited), provenance=EDITED)
            )
    # A LoRA in the default by the owner's edit alone: named by the asset the
    # sample saw it under, else by the shelf's file, so a client can join it to
    # the pile it came from (the pile names LoRAs by asset, not by digest).
    seen_by_digest = {}
    for asset in sorted(lora_seen):
        digest = shelf_digest(asset)
        if digest:
            seen_by_digest.setdefault(digest, (asset, names.get(asset)))
    for sha256, value in sorted(lora_overrides.items()):
        if value == LORA_OFF:
            continue
        asset, filename = seen_by_digest.get(sha256) or _shelf_asset(hub, sha256)
        recipe.loras.append(
            DefaultLora(asset, filename, sha256, _float_or_none(value), EDITED)
        )
    recipe.loras_decided = (
        bool(majority)
        or bare * 2 > sampled
        or any(address.startswith(LORA_ADDRESS_PREFIX) for address in overrides)
    )
    return recipe


def _manual_sample(
    hub: HubDatabase, workflow_id: str
) -> tuple[list[tuple[str, dict]], dict[str, _VariantRead], dict[str, str]]:
    """``(documents, reads, names)`` of a manual workflow: its own graph, once.

    The one instance is the document's API graph, addressed by its own slot
    labels (no core), and its model names are the ones it spells. An editor
    document nobody has converted, or one that will not reduce, has nothing
    to sample, which leaves a default recipe of the owner's edits alone.
    """
    graph = api_graph(manual_document(hub, workflow_id) or {})
    if graph is None:
        return [], {}, {}
    graph = sanitize_prompt_graph(graph)
    try:
        structural = structural_document(graph)
        read = _VariantRead(
            core={},
            base=topology_node_labels(structural),
            slots=live_slots(structural),
        )
    except WorkflowGraphError as exc:
        logger.info(
            "Manual workflow %s will not reduce, so its default recipe is its "
            "owner's edits alone: %s",
            workflow_id,
            exc,
        )
        return [], {}, {}
    names = {}
    for slot in read.slots:
        value = ((graph.get(slot.node_id) or {}).get("inputs") or {}).get(slot.widget)
        if isinstance(value, str) and value:
            names[slot.asset] = value
    return [(workflow_id, graph)], {workflow_id: read}, names


def _variant_reads(
    hub: HubDatabase, workflow: Workflow, structural_hashes: set[str]
) -> dict[str, _VariantRead]:
    """Each sampled variant's core labels, base labels and slots.

    Base labels only for a variant ON the base topology: a slot label means
    nothing outside its topology, which is the whole reason the core address
    exists.

    A variant a model fix swapped a PixlStash loader into (#1605) is carded
    under the topology it was swapped from, and is read as that graph too,
    the original loader put back, or its model would vote at an address the
    workflow's other runs do not have.
    """
    filed_as: dict[str, str] = {}
    for batch in chunked(sorted(structural_hashes)):
        placeholders = ",".join("?" * len(batch))
        filed_as.update(
            hub.fetchall(
                "SELECT structural_hash, topology_hash FROM workflow_recipe "
                f"WHERE structural_hash IN ({placeholders})",
                tuple(batch),
            )
        )
    reads = {}
    for structural_hash, document in variant_documents(
        hub, sorted(structural_hashes)
    ).items():
        try:
            swapped = filed_as.get(structural_hash)
            if swapped and swapped != workflow.variant_topology.get(structural_hash):
                _, document = unswapped(
                    document, loader_swaps_of(hub.fetchall, swapped)
                )
            on_base = (
                workflow.variant_topology.get(structural_hash) == workflow.base_topology
            )
            reads[structural_hash] = _VariantRead(
                core=core_node_labels(document, strip_loras=STRIP_LORAS_FOR_STACKS),
                base=topology_node_labels(document) if on_base else {},
                slots=live_slots(document),
            )
        except WorkflowGraphError as exc:
            logger.info(
                "Variant %s of workflow %s will not reduce, so its instances "
                "contribute nothing to the default recipe: %s",
                structural_hash,
                workflow.workflow_id,
                exc,
            )
    return reads


def _stored_value(text: str) -> bool | int | float | str:
    """An override as a graph takes it: the column is TEXT, a graph is not.

    ``"30"`` is the number 30 and ``"true"`` the boolean, the way the card
    routes write them (``routes/workflows._stored_value``); anything else is
    the string it is, a sampler name say.
    """
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return text
    return value if isinstance(value, (bool, int, float)) else text


def _mode(counter: Counter):
    """Most often, and on a tie the value whose text sorts last (``card_defaults``)."""
    return max(counter.items(), key=lambda item: (item[1], str(item[0])))[0]


def lora_modal_strength(
    hub: HubDatabase, vault, workflow_id: str, asset: str
) -> Optional[float]:
    """The strength a workflow's pictures loaded one LoRA at most often.

    Read off the runs of the variants that load *asset* (up to
    ``DEFAULT_SAMPLE`` of them, newest first), not the default recipe's
    sample, which a LoRA in a few pictures may not reach. ``None`` when no run
    records one.
    """
    library_uuid = getattr(vault, "library_uuid", None)
    workflow = find_workflow(hub, workflow_id) if library_uuid else None
    if workflow is None:
        return None
    reads = _variant_reads(hub, workflow, set(workflow.variants))
    loading = sorted(
        structural_hash
        for structural_hash, read in reads.items()
        if any(slot.is_lora and slot.asset == asset for slot in read.slots)
    )
    if not loading:
        return None
    hashes = read_instance_hashes(vault, loading, None, DEFAULT_SAMPLE)
    seen: Counter = Counter()
    for structural_hash, document in instance_documents(hub, library_uuid, hashes):
        for slot in reads[structural_hash].slots:
            if not (slot.is_lora and slot.asset == asset):
                continue
            strength = (
                (document.get(slot.node_id) or {})
                .get("inputs", {})
                .get("strength_model")
            )
            if isinstance(strength, (int, float)) and not isinstance(strength, bool):
                seen[float(strength)] += 1
    return _mode(seen) if seen else None


def _shelf_asset(hub: HubDatabase, sha256: str) -> tuple[str, Optional[str]]:
    """The asset reference and filename the shelf names a LoRA digest by.

    ``("", None)`` when the shelf holds no such file, or more than one name.
    """
    rows = hub.fetchall(
        "SELECT DISTINCT filename FROM model WHERE lower(sha256) = ? "
        "AND filename IS NOT NULL AND file_kind IN (?, ?)",
        (sha256.lower(), FILE_ADAPTER, FILE_UNKNOWN),
    )
    if len(rows) != 1:
        return "", None
    filename = rows[0]["filename"]
    return asset_reference(normalized_filename(filename)), filename


def _float_or_none(value) -> Optional[float]:
    """A stored strength as a number, or ``None`` (the graph's own) when it is not."""
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(
            "LoRA strength %r in a default recipe is not a number; the graph's "
            "own strength is kept.",
            value,
        )
        return None


# ---------------------------------------------------------------------------
# A workflow's LoRAs, as the Workflow inspector shows them.
#
# The LoRAs every kept picture of the workflow loaded are one list, and
# everything that changes is one pile. Read on demand for one workflow rather
# than on the grid, because it reduces every variant's document to find which
# slot a file sat in, and a grid read cannot afford that per workflow.
# ---------------------------------------------------------------------------

# How many pictures a pile row's strip shows.
LORA_STRIP_DEPTH = 3


@dataclass
class LoraUse:
    """One LoRA file across a workflow: how many kept pictures loaded it.

    ``asset`` is the stored documents' reference (``asset:…``), the one handle
    that survives a forgotten name, and what the picture filter
    ``workflow_lora`` takes. ``filename`` is ``None`` for a file whose name was
    forgotten. ``name`` is the shelf's title where exactly one shelf model
    answers to the file, its derived filename otherwise, ``None`` when there
    is no filename to derive from.
    """

    asset: str
    filename: Optional[str] = None
    name: Optional[str] = None
    on_shelf: bool = False
    # The one shelf LoRA file this is, by content digest, or ``None`` when the
    # shelf cannot say (not there, or several files answer): what the default
    # recipe names a LoRA by, so a client offers *Add to default* only here.
    sha256: Optional[str] = None
    pictures: int = 0
    picture_ids: list[int] = field(default_factory=list)


@dataclass
class LoraSummary:
    """A workflow's LoRAs: the ones in every picture, and the ones that change.

    ``pictures`` counts only the pictures whose variant could be read, which
    is the total both lists are measured against. ``without`` is the pictures
    that loaded none of the changing LoRAs, ``None`` when there are none.
    ``cover_asset`` is the changing LoRA the given cover picture loaded, for
    the top of the pile.
    """

    pictures: int = 0
    shared: list[LoraUse] = field(default_factory=list)
    varying: list[LoraUse] = field(default_factory=list)
    without: Optional[LoraUse] = None
    cover_asset: Optional[str] = None


def workflow_lora_summary(
    hub: HubDatabase,
    vault,
    variants: list[str],
    cover_picture_id: Optional[int] = None,
) -> LoraSummary:
    """Which LoRAs a workflow's pictures share, and which change between them.

    **Shared means in every kept picture of the workflow.** A variant with no
    kept picture says nothing about what the pictures used and is left out,
    and a variant whose document will not reduce is left out of the total as
    well as the lists, so a "No LoRA" row never counts pictures nobody could
    read.

    Args:
        hub: The hub the variants live in.
        vault: The open library, for picture counts and strips.
        variants: The workflow's variants (``Workflow.variants``).
        cover_picture_id: The picture on top of the workflow's cover, if any.
    """
    summary = LoraSummary()
    counts = read_variant_picture_counts(vault)
    live = sorted(v for v in set(variants) if counts.get(v, 0) > 0)
    if not live:
        return summary
    documents = variant_documents(hub, live)

    # {structural_hash: {asset}} for every variant that reduced.
    loaded: dict[str, set[str]] = {}
    for structural_hash in live:
        document = documents.get(structural_hash)
        if document is None:
            logger.info(
                "Variant %s has no stored document, so its pictures are left "
                "out of its workflow's LoRA summary.",
                structural_hash,
            )
            continue
        try:
            document_slots = live_slots(document)
        except WorkflowGraphError as exc:
            logger.info(
                "Variant %s will not reduce, so its pictures are left out of "
                "its workflow's LoRA summary: %s",
                structural_hash,
                exc,
            )
            continue
        loaded[structural_hash] = {
            slot.asset for slot in document_slots if slot.is_lora
        }

    uses: dict[str, LoraUse] = {}
    variants_of: dict[str, list[str]] = {}
    for structural_hash, found in loaded.items():
        pictures = counts[structural_hash]
        summary.pictures += pictures
        for asset in found:
            use = uses.setdefault(asset, LoraUse(asset=asset))
            use.pictures += pictures
            variants_of.setdefault(asset, []).append(structural_hash)

    filenames = {
        asset_reference(filename): filename
        for pairs in asset_names(hub, list(loaded)).values()
        for widget, filename in pairs
        if is_lora_widget(widget)
    }
    candidates, titles = _shelf_candidates(hub, sorted(set(filenames.values())))
    lora_digests = {
        row["id"]: str(row["sha256"]).lower()
        for row in hub.fetchall(
            "SELECT id, sha256 FROM model "
            "WHERE sha256 IS NOT NULL AND file_kind IN (?, ?)",
            (FILE_ADAPTER, FILE_UNKNOWN),
        )
    }
    for use in uses.values():
        use.filename = filenames.get(use.asset)
        models = candidates.get((use.filename or "").lower(), set())
        use.on_shelf = bool(models)
        title = titles.get(next(iter(models))) if len(models) == 1 else None
        use.name = title or _derived(use.filename)
        digests = {lora_digests[m] for m in models if m in lora_digests}
        use.sha256 = next(iter(digests)) if len(digests) == 1 else None

    summary.shared = sorted(
        (use for use in uses.values() if use.pictures == summary.pictures),
        key=lambda use: ((use.name or "").lower(), use.asset),
    )
    summary.varying = sorted(
        (use for use in uses.values() if use.pictures < summary.pictures),
        key=lambda use: (-use.pictures, (use.name or "").lower(), use.asset),
    )
    changing = {use.asset for use in summary.varying}
    for use in summary.varying:
        use.picture_ids = read_best_picture_ids(
            vault, variants_of[use.asset], LORA_STRIP_DEPTH
        )
    bare = [
        structural_hash
        for structural_hash, found in loaded.items()
        if changing and not changing & found
    ]
    if bare:
        summary.without = LoraUse(
            asset="",
            pictures=sum(counts[structural_hash] for structural_hash in bare),
            picture_ids=read_best_picture_ids(vault, bare, LORA_STRIP_DEPTH),
        )
    if cover_picture_id is not None and changing:
        on_cover = loaded.get(
            read_picture_variant(vault, cover_picture_id) or "", set()
        )
        summary.cover_asset = next(
            (use.asset for use in summary.varying if use.asset in on_cover), None
        )
    return summary

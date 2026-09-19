"""The Workflows view: the library list, the cards, and what the owner writes.

**The list opens at topology level** (workflow implementation plan §F1, design
`DECISIONS.md`). A topology is the graph alone; the recipes filed under it are
the same graph bound to different models, and they are the row's *expansion*
rather than rows of their own. On the owner's library that is ~192 rows instead
of ~617, and it is the difference between a list somebody reads and a list
somebody scrolls.

**Two databases, no join.** The rows live in the hub and are content-addressed;
the counts live in whichever vault is attached. Nothing here crosses that
boundary — the hub answers "which workflows exist", the vault answers "how many
of my pictures came from each", and a hash the hub has never heard of is simply
a workflow this machine does not have. That is the arrangement
``pixlstash/hub/schema.py`` chose content addressing for, and it is why a
detached library still lists correctly against a hub that has the recipes.

**Every route here is ``OWNER_ONLY``, and that is not the default speaking.**
``topology_activity`` counts every kept picture in the vault, so handing it to a
picture-, set- or project-scoped token would disclose the size of the whole
library one workflow at a time. The same goes for the picture ids the rail's
tiles are made of. Declared in ``pixlstash/authz/registry.py``, never inline.
They also refuse remote plaintext under ``require_ssl``, like the model-shelf
reads that name the same model files.

**The writes are the owner editing their own library** (v1.12 B4): a card's
name, notes and hidden flag, its parameter overrides, pins and picture inputs,
which of its LoRA slots are part of the workflow, and which cards sit in one
stack. They are ``OWNER_ONLY`` for the reason the reads are and one more: they
are the owner's own decisions about their library and there is nothing scoped
about them.

**No write here decides identity or grouping.** A card key is
``services/workflow_identity``, a stack is resolved in
``hub/workflow_cards.effective_stack_keys``, and the rows are written by
``hub/workflow_card_writes``; this module validates a request, resolves the
keys with those, and says "look again" on the way out
(``EventType.CHANGED_WORKFLOWS``).

Running a workflow is still a later step (§F5), and forgetting ghosts is a
privacy purge that lives with the retention setting in ``routes/config.py``.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from pixlstash.hub.workflow_card_reads import card_index, find_card, keys_in_stack
from pixlstash.hub.workflow_card_writes import (
    AUTO_STACK_PREFIX,
    flip_slot_marks,
    replace_defaults,
    replace_picture_inputs,
    replace_pins,
    set_attributes,
    set_stack_order,
    stack_together,
    unstack_card,
    unstack_stack,
)
from pixlstash.hub.workflow_cards import effective_stack_keys
from pixlstash.hub.workflows import (
    adapter_slots_by_topology,
    assets_by_topology,
    assets_for_topology_recipes,
    forgotten_asset_counts,
    get_document,
    model_ghost_names,
    picture_ghosts_by_topology,
    recipe_exists,
    recipes_for_topology,
    topology_exists,
    topology_index,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_card_service import card_defaults, read_grid
from pixlstash.services import saved_recipe_service
from pixlstash.services.workflow_events import announce_changed_workflows
from pixlstash.services.workflow_identity import RECIPE, STRUCTURAL
from pixlstash.services.workflow_library_service import (
    read_card_picture_ids,
    read_library,
    read_recipe_activity,
    read_topology_picture_ids,
    read_variant_picture_counts,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)

# Every key in this module is a SHA-256 hex digest from
# ``services/workflow_hash.py::graph_key``. Checked rather than trusted so a
# malformed one is a 422 naming the parameter instead of an empty 200 that
# reads as "this machine does not have it".
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# How many tiles the inspector's "Made with it" grid can ask for. The rail draws
# six; the ceiling is here so a hand-made request cannot turn a tile strip into
# a full library dump.
MAX_SAMPLE_PICTURES = 60


class WorkflowAsset(BaseModel):
    """One readable model or image filename a recipe names.

    ``widget`` is the input it was given to (``ckpt_name``, ``lora_name``,
    ``image``…), because that is what says whether a filename is the checkpoint,
    an adapter or a picture the graph loads — and the caller classifies it
    rather than this module inventing a taxonomy the hasher does not have.

    A recipe whose names were forgotten returns none of these. That is the state
    itself, not a missing row: the graph still says a model went here and no
    longer says which.
    """

    widget: str
    name: str


class WorkflowVariant(BaseModel):
    """One recipe: the topology bound to a particular set of models."""

    structural_hash: str
    node_count: int
    first_seen_at: str
    pictures: int = 0
    last_used: str | None = None
    assets: list[WorkflowAsset] = Field(default_factory=list)
    forgotten_models: int = Field(
        0, description="Models this recipe loads whose names were forgotten."
    )


class WorkflowSummary(BaseModel):
    """One row of the list: a topology, and what this library made with it.

    ``assets`` is the **set** of files this topology's variants reach for, not a
    list per variant, so a family of 159 character LoRAs contributes 159 names
    and not 159 copies of its checkpoint.

    ``adapter_slots`` is the other half of that, and the two must not be
    confused: it is how many adapters **one run** loads, which the set cannot
    answer. A caller describing the row from ``len(assets)`` alone would say
    that family loads 159 adapters at once.
    """

    topology_hash: str
    hash_version: str
    node_count: int
    first_seen_at: str
    variants: int
    pictures: int = 0
    last_used: str | None = None
    assets: list[WorkflowAsset] = Field(default_factory=list)
    adapter_slots: int = 0
    forgotten_models: int = Field(
        0,
        description=(
            "Models one variant loads whose names were forgotten: the most any "
            "variant has, for the reason ``adapter_slots`` is a maximum."
        ),
    )
    ghosts: int = Field(
        0, description="Picture ghosts the active library holds for this workflow."
    )
    model_ghosts: int = Field(
        0,
        description="Model names in ``assets`` for models no longer on the shelf.",
    )


class WorkflowScan(BaseModel):
    """How far the extraction pass has read, so an empty list can say why.

    Three of the four states the list has to survive are "correct and nearly
    empty" (design `States.dc.html`), and the list alone cannot tell them apart.
    ``scanned == 0`` is *not looked yet*, ``scanned < pictures`` is *looking*,
    and equal-with-nothing-listed is *looked, and there is genuinely nothing*.
    """

    pictures: int
    scanned: int


class WorkflowLibrary(BaseModel):
    """``GET /workflows``: the whole list, plus the state it was read in."""

    scan: WorkflowScan
    workflows: list[WorkflowSummary]


class WorkflowGraph(BaseModel):
    """``GET /workflows/recipes/{structural_hash}/graph``: the stored document.

    **This is the recipe's graph, not the file that was imported.** Parameters,
    seeds and prompts are already nulled and assets are named by an opaque
    reference, so it describes the workflow without carrying anything a purge
    would have to reach into — and it is therefore *not* runnable in ComfyUI.
    The verbatim import store that would be (§B5) is a different thing and is
    not shipped; ``runnable`` says so in the payload rather than leaving a
    caller to discover it by feeding this to ComfyUI.
    """

    structural_hash: str
    document: dict
    runnable: bool = False


class WorkflowSlotModel(BaseModel):
    """One model a card names, in the slot it sits in.

    ``kind`` is the slot rather than the file (``checkpoint``, ``unet``,
    ``vae``, ``clip``, ``lora``…), because that is what a card row shows: it
    names the checkpoint only, and ⓘ lists the rest. ``mark`` is B1's own
    vocabulary (``structural`` | ``recipe``) and is set on LoRA slots only.

    ``name`` is ``None`` for a **recipe** LoRA, which is a slot rather than a
    file — which character LoRA went in it is the recipe's business, not the
    workflow's — and for a model whose name was forgotten.
    """

    name: str | None = None
    kind: str
    mark: str | None = None


class WorkflowDefault(BaseModel):
    """One parameter a card starts from.

    ``label`` is what a reader sees and is unique within a card.
    ``slot_label`` and ``input_name`` are the address
    ``workflow_default_override`` is keyed on — never a node id, which every
    re-serialisation renumbers and which a card's variants disagree about.

    ``provenance`` is ``best`` (the mode over the card's pictures rated 4 stars
    and up), ``all`` (the same over every picture of the card, when none is
    rated that highly) or ``edited`` (the owner's own value, which replaces
    both); a client renders ``best`` as "from your best pictures".
    """

    label: str
    slot_label: str
    input_name: str
    value: bool | int | float | str
    provenance: str


class WorkflowCard(BaseModel):
    """One card of the Workflows grid (v1.12 B3).

    A **card** is a workflow as a person means it: the topology, the non-LoRA
    models and the LoRA slots marked structural, so swapping a character LoRA
    stays the same card. The `workflow_recipe` / `structural_hash` tier is a
    **variant**; a *saved recipe* is the look a person keeps.

    **This shape is fixed by a shipped consumer**, `frontend/src/utils/
    workflowCard.js`, and `docs/frontend_architecture.md` §"WorkflowCard.vue"
    guarantees it needs no mapping layer — so the field names are snake_case
    and `mark` carries B1's vocabulary rather than anything invented here.

    ``stack_size`` of 2 or more is what makes a card a stack: the grid draws
    one card per stack, the cover's, and ``member_keys`` names the rest so a
    caller can open them. ``differs_by`` is then the union over the members.
    """

    key: str
    name: str | None = Field(
        None, description="The owner's own name, if they gave one."
    )
    type: str | None = None
    imported: bool = Field(
        False, description="A workflow file on this machine runs this card."
    )
    models: list[WorkflowSlotModel] = Field(default_factory=list)
    loras: list[WorkflowSlotModel] = Field(default_factory=list)
    differs_by: list[str] = Field(default_factory=list)
    picture_count: int = 0
    rating: float | None = Field(
        None, description="Mean of the stars this card has; null when it has none."
    )
    covers: list[str] = Field(
        default_factory=list, description="Thumbnail URLs, up to three, cover first."
    )
    stack_size: int = 1
    saved_recipe_count: int = 0
    defaults: list[WorkflowDefault] = Field(default_factory=list)
    # Beyond the shared shape, and additive: a caller that only knows
    # `workflowCard.js` ignores these and needs no translation for the rest.
    topology_hash: str
    variant_count: int = 0
    member_keys: list[str] = Field(default_factory=list)
    rank: float = Field(
        0.0,
        description=(
            "The Bayesian cover rank the grid is ordered by. Not `rating`: "
            "it is smoothed towards the library's mean so cards can be "
            "ordered against each other, and is meaningless on its own."
        ),
    )


class WorkflowCards(BaseModel):
    """``GET /workflows/cards``: the grid, and what it left out.

    ``one_offs`` and ``hidden`` are counts rather than rows on purpose: both
    sets are excluded from ``cards``, and the view offers them as a way back in
    rather than as clutter. Both still open on the detail route.
    """

    cards: list[WorkflowCard]
    one_offs: int = 0
    hidden: int = 0


class WorkflowCardDetail(BaseModel):
    """``GET /workflows/cards/{workflow_key}``: one card opened."""

    card: WorkflowCard
    notes: str | None = None
    hidden: bool = False
    variants: list[WorkflowVariant] = Field(default_factory=list)


# ── The writes (v1.12 B4) ───────────────────────────────────────────────────
# Every ceiling below is here so a hand-made request cannot turn one of these
# whole-set writes into a place to park a document. The forms the frontend
# offers are nowhere near any of them.
MAX_NAME_LENGTH = 200
MAX_NOTES_LENGTH = 4000
MAX_LABEL_LENGTH = 200
MAX_VALUE_LENGTH = 2000
# A slot list, a pin list and a parameter form are all per-topology and small;
# a stack is a handful of cards somebody selected. The stack ceiling is the
# largest because a merge expands each selection to its whole stack first.
MAX_SLOT_MARKS = 200
MAX_DEFAULTS = 200
MAX_PINS = 200
MAX_INPUTS = 200
MAX_STACK_KEYS = 500

# A stack is named either by the id ``stack_together`` minted (a uuid4 hex) or,
# for an automatic grouping, by ``auto:`` and the core hash that IS the
# grouping. Checked rather than trusted, so a malformed id is a 422 naming the
# parameter instead of a write against a stack nothing will ever read.
_STACK_ID_RE = re.compile(rf"^(?:{AUTO_STACK_PREFIX}[0-9a-f]{{64}}|[0-9a-f]{{32}})$")


class WorkflowCardEdit(BaseModel):
    """``PATCH /workflows/{key}``: the fields the request carries, and no more.

    ``null`` for ``name`` or ``notes`` clears it, which is the card's own
    default and not the same as leaving the field out - so the handler reads
    ``exclude_unset`` rather than testing for ``None``.
    """

    name: str | None = Field(None, max_length=MAX_NAME_LENGTH)
    notes: str | None = Field(None, max_length=MAX_NOTES_LENGTH)
    hidden: bool | None = None


class SlotMarks(BaseModel):
    """``PUT /workflows/{key}/slots``: which LoRA slots are the workflow's.

    ``marks`` is ``{slot_label: "structural" | "recipe"}`` over this card's
    LoRA slots. A slot left out keeps the mark it has, so correcting one is one
    entry rather than the whole list.
    """

    marks: dict[str, str] = Field(default_factory=dict, max_length=MAX_SLOT_MARKS)

    @field_validator("marks")
    @classmethod
    def _known_marks(cls, value: dict[str, str]) -> dict[str, str]:
        for label, mark in value.items():
            if len(label) > MAX_LABEL_LENGTH:
                raise ValueError("A slot label is longer than a slot label can be.")
            if mark not in (STRUCTURAL, RECIPE):
                raise ValueError(f"mark must be {STRUCTURAL!r} or {RECIPE!r}.")
        return value


def _one_row_per_address(entries) -> None:
    """Refuse a whole-set write that names one parameter twice.

    Both tables these feed are keyed on ``(…, slot_label, input_name)``, so a
    repeated address is a UNIQUE violation out of the database - a 500 for
    what is a bad request, and the same class as the ``fixed``-without-a-
    picture CHECK the model beside this one pre-validates. Refused here for
    the same reason, rather than left for whichever of the two constraints the
    caller happens to trip first.
    """
    seen = set()
    for entry in entries:
        address = (entry.slot_label, entry.input_name)
        if address in seen:
            raise ValueError(
                f"{entry.input_name!r} is named twice for one slot; each "
                "parameter may appear once."
            )
        seen.add(address)


class ParameterAddress(BaseModel):
    """One parameter of a card, addressed the way the card's defaults are.

    ``(slot_label, input_name)`` and never a node id: every re-serialisation
    renumbers those and a card's variants disagree about them.
    """

    slot_label: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    input_name: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)


class CardDefault(ParameterAddress):
    """One parameter the owner has set this card to start from."""

    value: bool | int | float | str


class CardDefaults(BaseModel):
    """``PUT /workflows/{key}/defaults``: the card's whole override set.

    Whole rather than per parameter, because the form shows every featured
    parameter at once - so an empty list is somebody clearing them all, which
    is a state and not a no-op.
    """

    defaults: list[CardDefault] = Field(default_factory=list, max_length=MAX_DEFAULTS)

    @field_validator("defaults")
    @classmethod
    def _bounded_values(cls, value: list[CardDefault]) -> list[CardDefault]:
        for default in value:
            if len(str(default.value)) > MAX_VALUE_LENGTH:
                raise ValueError(
                    f"A parameter value is longer than {MAX_VALUE_LENGTH} characters."
                )
        _one_row_per_address(value)
        return value


class CardPins(BaseModel):
    """``PUT /workflows/{key}/pins``: which parameters the form shows first.

    ``null`` forgets the card's pins, so the defaults apply again; ``[]`` is
    somebody who unpinned everything, which the hub keeps as a row.
    """

    pins: list[ParameterAddress] | None = Field(None, max_length=MAX_PINS)


class CardPictureInput(ParameterAddress):
    """How one picture input of a card is filled.

    ``fixed`` names a picture by content (``pixel_sha``) rather than by id,
    because SQLite reuses a vault id the moment the next import lands.
    """

    mode: Literal["selection", "picker", "fixed"]
    pixel_sha: str | None = Field(None, max_length=64)


class CardPictureInputs(BaseModel):
    """``PUT /workflows/{key}/inputs``: this card's whole picture-input setup."""

    inputs: list[CardPictureInput] = Field(default_factory=list, max_length=MAX_INPUTS)

    @field_validator("inputs")
    @classmethod
    def _fixed_names_a_picture(
        cls, value: list[CardPictureInput]
    ) -> list[CardPictureInput]:
        for entry in value:
            if entry.mode == "fixed" and not entry.pixel_sha:
                raise ValueError("A fixed input must name a picture.")
        _one_row_per_address(value)
        return value


class StackKeys(BaseModel):
    """A complete, ordered list of card keys. ``keys[0]`` is the cover."""

    keys: list[str] = Field(default_factory=list, max_length=MAX_STACK_KEYS)


class StackResult(BaseModel):
    """A stack as it stands after the write, so a caller can confirm it."""

    stack_id: str | None = None
    keys: list[str] = Field(default_factory=list)


class SlotMarkResult(BaseModel):
    """Where a mark flip left the card that was addressed, and what else moved.

    ``key`` is where the requested card now lives: flipping a mark re-keys
    every variant of the topology, so the card the caller was looking at has a
    new URL and nothing else in the response would tell them. A split has
    several successors and this is the biggest of them - the one holding most
    of the pictures the card had - because a client has to open one of them.

    ``moved`` is ``{old key: [key, ...]}`` over every card of that topology,
    biggest first, and empty when the marks asked for were already the marks
    in force.

    **A key may list itself**, and a client must not read every entry as a
    card that went away. A variant whose stored document will not parse keeps
    the key it is on, so if a sibling moved, that card both moved and did not:
    it is still open at its own URL and still holds its name, its pins and its
    saved recipes. ``key`` is the one to follow; the list is what to check a
    key against before deciding it is gone.
    """

    key: str
    moved: dict[str, list[str]] = Field(default_factory=dict)


def _stored_value(value: bool | int | float | str) -> str:
    """An override as the hub keeps it. ``workflow_default_override.value`` is
    TEXT and the read side hands it back verbatim, so a bool is written the way
    a graph writes one rather than as Python's ``True``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _require_hash(value: str, name: str) -> str:
    if not _HASH_RE.match(value):
        raise HTTPException(
            status_code=422, detail=f"Invalid {name}: expected a SHA-256 hex digest."
        )
    return value


def _iso(value) -> str | None:
    """Render a vault timestamp, which is a ``datetime``, as the API's string."""
    return value.isoformat() if value is not None else None


def _assets(rows) -> list[WorkflowAsset]:
    return [
        WorkflowAsset(widget=row["widget_name"], name=row["normalized_filename"])
        for row in rows
    ]


def _cover_urls(covers) -> list[str]:
    """Thumbnail URLs for a card's cover strip, cache-busted per bitmap.

    The same URL and the same cache key the grid's own tiles use
    (``routes/pictures/_thumbnails.py``), so a cover the browser already holds
    is not fetched twice and a regenerated bitmap is not served stale.
    """
    urls = []
    for cover in covers:
        version = ImageUtils.thumbnail_cache_version(
            cover.thumbnail_width, cover.thumbnail_height, cover.orientation
        )
        urls.append(f"/pictures/thumbnails/{cover.picture_id}.webp?v={version}")
    return urls


# What a card is called when the owner has not named it. The card's name row
# is its only identifying text and the ⓘ panel puts it in an `aria-label`, so
# this may not be null: `{{ card.name }}` renders empty and the label reads
# "About null". The fallback is the workflow FILE that runs it, because that is
# what a person calls their workflow and it is the one identifying string not
# already on the card (the checkpoint has its own row, the type its own chip).
UNNAMED_CARD = "Untitled workflow"


def _display_name(card) -> str:
    """The owner's name for a card, else the file that runs it, else a stand-in."""
    if card.name:
        return card.name
    if card.file_name:
        stem = card.file_name.rsplit("/", 1)[-1]
        return stem[: -len(".json")] if stem.lower().endswith(".json") else stem
    return UNNAMED_CARD


def _slot_models(slots) -> list[WorkflowSlotModel]:
    return [
        WorkflowSlotModel(name=slot.name, kind=slot.kind, mark=slot.mark)
        for slot in slots
    ]


def _card(figure, defaults=()) -> WorkflowCard:
    """Render one card's figures in the shape ``workflowCard.js`` documents."""
    return WorkflowCard(
        key=figure.card.workflow_key,
        name=_display_name(figure.card),
        type=figure.card.workflow_type,
        imported=figure.card.imported,
        models=_slot_models(figure.models),
        loras=_slot_models(figure.loras),
        differs_by=figure.differs_by,
        picture_count=figure.pictures,
        rating=figure.rating,
        covers=_cover_urls(figure.covers),
        stack_size=figure.stack_size,
        saved_recipe_count=figure.saved_recipes,
        defaults=[
            WorkflowDefault(
                label=default.label,
                slot_label=default.slot_label,
                input_name=default.input_name,
                value=default.value,
                provenance=default.provenance,
            )
            for default in defaults
        ],
        topology_hash=figure.card.topology_hash,
        variant_count=len(figure.card.variants),
        rank=figure.rank,
        member_keys=[
            key for key in figure.member_keys if key != figure.card.workflow_key
        ],
    )


def _card_variants(hub, vault, card) -> list[WorkflowVariant]:
    """The stored graphs one card is made of, with what each one made.

    The card's variants are a subset of its topology's recipes - a topology can
    carry several cards, one per set of models - so the topology-wide hub reads
    are filtered rather than re-queried per variant.
    """
    wanted = set(card.variants)
    recipes = [
        row
        for row in recipes_for_topology(hub, card.topology_hash)
        if row["structural_hash"] in wanted
    ]
    activity = read_recipe_activity(vault, [row["structural_hash"] for row in recipes])
    assets = assets_for_topology_recipes(hub, card.topology_hash)
    forgotten = forgotten_asset_counts(hub, card.topology_hash).get(
        card.topology_hash, {}
    )
    variants = []
    for row in recipes:
        seen = activity.get(row["structural_hash"])
        variants.append(
            WorkflowVariant(
                structural_hash=row["structural_hash"],
                node_count=row["node_count"],
                first_seen_at=row["first_seen_at"],
                pictures=seen.pictures if seen else 0,
                last_used=_iso(seen.last_used) if seen else None,
                assets=_assets(assets.get(row["structural_hash"], [])),
                forgotten_models=forgotten.get(row["structural_hash"], 0),
            )
        )
    return variants


def create_router(server) -> APIRouter:
    """Create the workflow-library router.

    Args:
        server: The Server instance, for ``hub`` (the workflow rows) and
            ``vault`` (the pictures made with them).

    Returns:
        The configured router.
    """
    router = APIRouter(tags=["workflows"])

    def _hub():
        hub = getattr(server, "hub", None)
        if hub is None:
            # A vault opened without a hub has no workflow library at all, which
            # is a configuration state rather than a fault. Say so instead of
            # raising an AttributeError out of a read.
            raise HTTPException(
                status_code=503,
                detail="No hub is attached, so this machine has no workflow library.",
            )
        return hub

    @router.get(
        "/workflows",
        summary="List workflows",
        description=(
            "Every workflow topology this machine knows, with how many of the "
            "current library's pictures each accounts for. Opens at topology "
            "level; the recipes under one topology are its variants."
        ),
        response_model=WorkflowLibrary,
    )
    def list_workflows(request: Request):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        topologies = topology_index(hub)
        assets = assets_by_topology(hub)
        slots = adapter_slots_by_topology(hub)
        forgotten = forgotten_asset_counts(hub)
        ghost_names = model_ghost_names(hub)
        library_uuid = getattr(server.vault, "library_uuid", None)
        ghosts = picture_ghosts_by_topology(hub, library_uuid) if library_uuid else {}
        activity, progress = read_library(server.vault)

        workflows = []
        for row in topologies:
            seen = activity.get(row["topology_hash"])
            row_assets = assets.get(row["topology_hash"], [])
            workflows.append(
                WorkflowSummary(
                    topology_hash=row["topology_hash"],
                    hash_version=row["hash_version"],
                    node_count=row["node_count"],
                    first_seen_at=row["first_seen_at"],
                    variants=row["variant_count"],
                    pictures=seen.pictures if seen else 0,
                    last_used=_iso(seen.last_used) if seen else None,
                    assets=_assets(row_assets),
                    adapter_slots=slots.get(row["topology_hash"], 0),
                    forgotten_models=max(
                        forgotten.get(row["topology_hash"], {}).values(), default=0
                    ),
                    ghosts=ghosts.get(row["topology_hash"], 0),
                    model_ghosts=len(
                        {a["normalized_filename"] for a in row_assets} & ghost_names
                    ),
                )
            )
        return WorkflowLibrary(
            scan=WorkflowScan(pictures=progress.pictures, scanned=progress.scanned),
            workflows=workflows,
        )

    @router.get(
        "/workflows/{topology_hash}/variants",
        summary="List a workflow's variants",
        description=(
            "The recipes filed under one topology — the same graph bound to "
            "different models. This is the list row's expansion."
        ),
        response_model=list[WorkflowVariant],
        responses={404: {"description": "This machine has no such topology."}},
    )
    def list_variants(request: Request, topology_hash: str):
        server.auth.ensure_secure_when_required(request)
        _require_hash(topology_hash, "topology_hash")
        hub = _hub()
        if not topology_exists(hub, topology_hash):
            raise HTTPException(status_code=404, detail="Unknown workflow.")
        recipes = recipes_for_topology(hub, topology_hash)
        hashes = [row["structural_hash"] for row in recipes]
        activity = read_recipe_activity(server.vault, hashes)
        assets = assets_for_topology_recipes(hub, topology_hash)
        forgotten = forgotten_asset_counts(hub, topology_hash).get(topology_hash, {})
        variants = []
        for row in recipes:
            seen = activity.get(row["structural_hash"])
            variants.append(
                WorkflowVariant(
                    structural_hash=row["structural_hash"],
                    node_count=row["node_count"],
                    first_seen_at=row["first_seen_at"],
                    pictures=seen.pictures if seen else 0,
                    last_used=_iso(seen.last_used) if seen else None,
                    assets=_assets(assets.get(row["structural_hash"], [])),
                    forgotten_models=forgotten.get(row["structural_hash"], 0),
                )
            )
        return variants

    @router.get(
        "/workflows/{topology_hash}/pictures",
        summary="Pictures made with a workflow",
        description=(
            "The newest kept pictures this library made with one topology, "
            "newest first. Ids only: the caller already has the thumbnail route."
        ),
        response_model=list[int],
    )
    def list_workflow_pictures(
        request: Request,
        topology_hash: str,
        limit: int = Query(
            6,
            ge=1,
            le=MAX_SAMPLE_PICTURES,
            description="How many ids to return, newest first.",
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        _require_hash(topology_hash, "topology_hash")
        return read_topology_picture_ids(server.vault, topology_hash, limit)

    @router.get(
        "/workflows/recipes/{structural_hash}/graph",
        summary="A recipe's stored graph",
        description=(
            "The structural document for one recipe: the graph with its "
            "parameters, seeds and prompts nulled and its assets named by an "
            "opaque reference. Describes the workflow; does not run it."
        ),
        response_model=WorkflowGraph,
        responses={404: {"description": "This machine has no such recipe."}},
    )
    def get_recipe_graph(request: Request, structural_hash: str):
        server.auth.ensure_secure_when_required(request)
        _require_hash(structural_hash, "structural_hash")
        hub = _hub()
        if not recipe_exists(hub, structural_hash):
            raise HTTPException(status_code=404, detail="Unknown workflow variant.")
        document = get_document(hub, structural_hash)
        if document is None:
            # The row is there and its document would not parse. The store has
            # already logged the hash and the decode error; answering 404 here
            # would report a corrupt row as a workflow this machine never had.
            raise HTTPException(
                status_code=500,
                detail="This workflow's stored graph could not be read.",
            )
        return WorkflowGraph(structural_hash=structural_hash, document=document)

    # ── The cards (v1.12 B3) ────────────────────────────────────────────────
    # Under `/workflows/cards` rather than on `/workflows` itself, because the
    # shipped topology list keeps working until F1b swaps the route. The detail
    # and picture routes take the same prefix rather than `/workflows/{key}`:
    # `/workflows/{key}/pictures` would be permanently shadowed by the
    # `{topology_hash}/pictures` route above it, and splitting the three across
    # two prefixes to save one segment on the one that would have fitted reads
    # worse than keeping them together. B9 moves the prefix, not the shape.
    #
    # The payload is `frontend/src/utils/workflowCard.js`'s documented card,
    # which is already merged and already has components reading it; see the
    # `WorkflowCard` model.

    @router.get(
        "/workflows/cards",
        summary="The Workflows grid",
        description=(
            "Every workflow card this machine holds, in cover-rank order, one "
            "card per stack. Hidden cards and one-offs are counted rather than "
            "listed."
        ),
        response_model=WorkflowCards,
    )
    def list_cards(request: Request):
        server.auth.ensure_secure_when_required(request)
        grid = read_grid(_hub(), server.vault)
        return WorkflowCards(
            cards=[_card(figure) for figure in grid.cards],
            one_offs=grid.one_offs,
            hidden=grid.hidden,
        )

    @router.get(
        "/workflows/cards/{workflow_key}",
        summary="One workflow card",
        description=(
            "A card opened: its variants, and the value each featured "
            "parameter starts from with where that value came from."
        ),
        response_model=WorkflowCardDetail,
        responses={404: {"description": "This machine has no such card."}},
    )
    def get_card(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        _require_hash(workflow_key, "workflow_key")
        return _read_detail(_hub(), workflow_key)

    def _read_detail(hub, workflow_key: str) -> WorkflowCardDetail:
        """One card opened, for the detail route and for what a write answers.

        The whole grid for one card, because its rank is Bayesian: the prior is
        the library's own mean rating, which cannot be read off one card. A
        write answers with this so the caller sees the card it just changed
        rather than an echo of its own request - and pays the grid read once,
        on a gesture a person made, rather than per card.
        """
        figure = read_grid(hub, server.vault).figure(workflow_key)
        if figure is None:
            raise HTTPException(status_code=404, detail="Unknown workflow card.")
        card = figure.card
        return WorkflowCardDetail(
            card=_card(figure, card_defaults(hub, server.vault, card)),
            notes=card.notes,
            hidden=card.hidden,
            variants=_card_variants(hub, server.vault, card),
        )

    @router.get(
        "/workflows/cards/{workflow_key}/pictures",
        summary="Pictures made with a card",
        description=(
            "The newest kept pictures made by any variant of one card, newest "
            "first. Ids only: the caller already has the thumbnail route."
        ),
        response_model=list[int],
        responses={404: {"description": "This machine has no such card."}},
    )
    def list_card_pictures(
        request: Request,
        workflow_key: str,
        limit: int = Query(
            6,
            ge=1,
            le=MAX_SAMPLE_PICTURES,
            description="How many ids to return, newest first.",
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        _require_hash(workflow_key, "workflow_key")
        card = find_card(_hub(), workflow_key)
        if card is None:
            raise HTTPException(status_code=404, detail="Unknown workflow card.")
        return read_card_picture_ids(server.vault, card.variants, limit)

    # ── The writes (v1.12 B4) ───────────────────────────────────────────────
    # The stack routes are declared BEFORE the templated card routes below:
    # FastAPI matches in declaration order, and `/workflows/stacks/{id}/order`
    # would otherwise sit behind nothing today but behind the first
    # `/workflows/{key}/{anything}` somebody adds.
    #
    # Every one of these emits `CHANGED_WORKFLOWS`, which is a "look again"
    # signal and not a card: the counts, covers and stacks a card shows are
    # computed per request over the whole vault, so the client re-reads
    # `GET /workflows/cards` rather than trusting what a write carried back.

    def _announce(request: Request, keys, reason: str) -> None:
        """Tell every other tab which cards to look at again, and why."""
        announce_changed_workflows(
            server,
            keys,
            reason,
            origin_client_id=getattr(request.state, "origin_client_id", None),
        )

    def _require_card(hub, workflow_key: str):
        """One card by key, or a 404 - never an attribute row on a dead key."""
        _require_hash(workflow_key, "workflow_key")
        card = find_card(hub, workflow_key)
        if card is None:
            raise HTTPException(status_code=404, detail="Unknown workflow card.")
        return card

    def _known_keys(hub, keys: list[str]) -> list[str]:
        """The keys, checked whole: one unknown card refuses the request.

        Refused whole rather than filtered, for `PUT /recipes/order`'s reason:
        a half-applied stack is worse than a rejected one, and a filtered list
        would silently stack fewer cards than the owner selected.
        """
        if len(set(keys)) != len(keys):
            raise HTTPException(status_code=400, detail="keys must be unique")
        known = {card.workflow_key for card in card_index(hub)}
        for key in keys:
            _require_hash(key, "workflow_key")
            if key not in known:
                raise HTTPException(status_code=404, detail="Unknown workflow card.")
        return keys

    def _stack_id(stack_id: str) -> str:
        if not _STACK_ID_RE.match(stack_id):
            raise HTTPException(
                status_code=422,
                detail="Invalid stack_id: expected a stack id or auto:<core hash>.",
            )
        return stack_id

    @router.post(
        "/workflows/stacks",
        summary="Stack workflows together",
        description=(
            "Put the cards named in one stack, in the order given. A card "
            "already in a stack brings its whole stack with it, and the first "
            "key stays the cover — so merging two stacks keeps the "
            "first-selected one's cover and the name that goes with it."
        ),
        response_model=StackResult,
        status_code=201,
        responses={404: {"description": "One of the cards does not exist."}},
    )
    def stack_workflows(request: Request, payload: StackKeys = Body(...)):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        selected = _known_keys(hub, payload.keys)
        if len(selected) < 2:
            raise HTTPException(
                status_code=400, detail="A stack needs at least two cards."
            )
        # Each selection expands to the stack it is already in, in selection
        # order, so stacking two stacks merges them rather than pulling one
        # card out of each.
        members: list[str] = []
        for key in selected:
            for member in effective_stack_keys(hub, key):
                if member not in members:
                    members.append(member)
        if len(members) > MAX_STACK_KEYS:
            raise HTTPException(
                status_code=400, detail="That would make a stack too large to order."
            )
        stack_id = stack_together(hub, members)
        _announce(request, members, "stacks")
        return StackResult(stack_id=stack_id, keys=members)

    @router.put(
        "/workflows/stacks/{stack_id}/order",
        summary="Reorder a stack",
        description=(
            "Set a stack's member order by a complete ordered key list; the "
            "first key is the cover. Ordering an automatic grouping is what "
            "makes it a stack of its own, so the order survives a regrouping."
        ),
        response_model=StackResult,
        responses={404: {"description": "One of the cards does not exist."}},
    )
    def reorder_stack(request: Request, stack_id: str, payload: StackKeys = Body(...)):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _stack_id(stack_id)
        keys = _known_keys(hub, payload.keys)
        if len(keys) < 2:
            raise HTTPException(
                status_code=400, detail="A stack needs at least two cards."
            )
        members = keys_in_stack(hub, stack_id)
        if not members:
            # A shape this hub does not hold, checked like its `unstack`
            # sibling rather than written blind: a well-formed id naming no
            # stack would otherwise mint one, and an `auto:` id naming no
            # group would mint one with a `core_hash` no topology has.
            raise HTTPException(status_code=404, detail="Unknown workflow stack.")
        if set(keys) != set(members):
            # A complete ordered list of what is in the stack, and refused
            # whole when it is not. A key left out would be deleted from the
            # stack by the write - with no record that it left, so it would
            # rejoin its automatic group on the next read with nothing said -
            # and a key added is `POST /workflows/stacks`' gesture, not this
            # one.
            raise HTTPException(
                status_code=400,
                detail="keys must name every card in the stack, and no other.",
            )
        set_stack_order(hub, stack_id, keys)
        _announce(request, keys, "stacks")
        return StackResult(stack_id=stack_id, keys=keys)

    @router.post(
        "/workflows/stacks/{stack_id}/unstack",
        summary="Dissolve a stack",
        description=(
            "Take a whole stack apart: every member stands on its own "
            "afterwards and stays out of the automatic grouping it came from."
        ),
        response_model=StackResult,
        responses={404: {"description": "This machine has no such stack."}},
    )
    def dissolve_stack(request: Request, stack_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _stack_id(stack_id)
        keys = keys_in_stack(hub, stack_id)
        if not keys:
            raise HTTPException(status_code=404, detail="Unknown workflow stack.")
        unstack_stack(hub, stack_id, keys)
        _announce(request, keys, "stacks")
        return StackResult(stack_id=None, keys=keys)

    @router.patch(
        "/workflows/{workflow_key}",
        summary="Edit a workflow card",
        description=(
            "Write the fields the request carries; the rest stand. A null name "
            "or notes clears it. Hiding a card takes it out of the grid — it "
            "still opens by its own URL, and nothing about it is deleted."
        ),
        response_model=WorkflowCardDetail,
        responses={404: {"description": "This machine has no such card."}},
    )
    def edit_card(request: Request, workflow_key: str, payload: WorkflowCardEdit):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_card(hub, workflow_key)
        changes = payload.model_dump(exclude_unset=True)
        if "hidden" in changes and changes["hidden"] is None:
            # The column is NOT NULL and has no "unset" state: a null there is
            # a caller meaning "not hidden", which the hub is told plainly
            # rather than left to coerce.
            changes["hidden"] = False
        set_attributes(hub, workflow_key, **changes)
        _announce(request, [workflow_key], "changed")
        return _read_detail(hub, workflow_key)

    @router.put(
        "/workflows/{workflow_key}/slots",
        summary="Mark a card's LoRA slots",
        description=(
            "Say which of this workflow's LoRA slots are part of the workflow "
            "(structural) and which are part of the look (recipe). This "
            "re-keys every card of the topology: a card may split into "
            "several or several may merge into one, and the response says "
            "where this card went."
        ),
        response_model=SlotMarkResult,
        responses={404: {"description": "This machine has no such card."}},
    )
    def mark_slots(request: Request, workflow_key: str, payload: SlotMarks = Body(...)):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        lora_labels = {slot.get("label") for slot in card.slots if slot.get("is_lora")}
        unknown = sorted(set(payload.marks) - lora_labels)
        if unknown:
            # Named rather than ignored: a mark on a label this topology has no
            # LoRA slot for is a row every reader skips, so accepting it would
            # answer 200 to a flip that cannot have happened.
            raise HTTPException(
                status_code=422,
                detail=f"This workflow has no LoRA slot called {unknown[0]!r}.",
            )
        if getattr(server.vault, "library_uuid", None) is None:
            # The flip decides its merge winner on the vault's picture counts
            # and has to move the vault's saved recipes afterwards. With no
            # library open it could do neither, and a re-key that skipped both
            # would pick an arbitrary winner and orphan the recipes.
            raise HTTPException(
                status_code=503,
                detail="No library is open, so a workflow cannot be re-keyed.",
            )
        moved = flip_slot_marks(
            hub,
            card.topology_hash,
            payload.marks,
            read_variant_picture_counts(server.vault),
        )
        # The migration `db_models/saved_recipe.py` says a re-keying owes this
        # table. A second database, so it cannot be in the hub's transaction;
        # it is the first thing after it, and it is logged.
        try:
            rekeyed = saved_recipe_service.rekey_recipes(server.vault, moved)
        except Exception:
            # The hub transaction has already committed, so the cards have
            # moved and the recipes have not. Re-running the flip will not
            # repair it - the marks are now the marks in force, so a second
            # PUT re-keys nothing and returns an empty `moved` - which is
            # exactly why the map goes in the log rather than only the count:
            # it is the only record of which key each recipe set belongs on.
            # Raised rather than answered 200, because a saved recipe is
            # authored and silently stranding one is the failure
            # `rekey_in_session` exists to close.
            logger.exception(
                "A slot-mark flip on topology %s re-keyed its cards but could "
                "not move the saved recipes with them. The recipes are still "
                "on their old keys; the cards moved as %r.",
                card.topology_hash,
                moved,
            )
            raise
        if rekeyed:
            logger.info(
                "A slot-mark flip on topology %s moved %d saved recipe(s) onto "
                "the cards their workflows were re-keyed to.",
                card.topology_hash,
                rekeyed,
            )
        successors = moved.get(workflow_key) or [workflow_key]
        touched = {workflow_key, *moved}
        touched.update(key for keys in moved.values() for key in keys)
        _announce(request, sorted(touched), "changed")
        return SlotMarkResult(key=successors[0], moved=moved)

    @router.put(
        "/workflows/{workflow_key}/defaults",
        summary="Set a card's parameter defaults",
        description=(
            "Replace this card's whole set of parameter overrides. An empty "
            "list clears them, and the card's defaults then come from the "
            "pictures it has made again."
        ),
        response_model=WorkflowCardDetail,
        responses={404: {"description": "This machine has no such card."}},
    )
    def set_defaults(
        request: Request, workflow_key: str, payload: CardDefaults = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_card(hub, workflow_key)
        replace_defaults(
            hub,
            workflow_key,
            [
                (default.slot_label, default.input_name, _stored_value(default.value))
                for default in payload.defaults
            ],
        )
        _announce(request, [workflow_key], "changed")
        return _read_detail(hub, workflow_key)

    @router.put(
        "/workflows/{workflow_key}/pins",
        summary="Set a card's pinned parameters",
        description=(
            "Which parameters this card's form shows before 'All N'. An empty "
            "list is everything unpinned; null forgets the choice, so the "
            "default pins apply again."
        ),
        response_model=CardPins,
        responses={404: {"description": "This machine has no such card."}},
    )
    def set_pins(request: Request, workflow_key: str, payload: CardPins = Body(...)):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_card(hub, workflow_key)
        replace_pins(
            hub,
            workflow_key,
            None
            if payload.pins is None
            else [(pin.slot_label, pin.input_name) for pin in payload.pins],
        )
        _announce(request, [workflow_key], "changed")
        return payload

    @router.put(
        "/workflows/{workflow_key}/inputs",
        summary="Set a card's picture inputs",
        description=(
            "How each picture input of this card is filled: from the "
            "selection, from a picker, or from one fixed picture. Kept per "
            "library, because a picture is a picture in one library."
        ),
        response_model=CardPictureInputs,
        responses={
            404: {"description": "This machine has no such card."},
            503: {"description": "No library is open, so there is nothing to set up."},
        },
    )
    def set_picture_inputs(
        request: Request, workflow_key: str, payload: CardPictureInputs = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_card(hub, workflow_key)
        library_uuid = getattr(server.vault, "library_uuid", None)
        if not library_uuid:
            raise HTTPException(
                status_code=503,
                detail="No library is open, so a picture input cannot be set up.",
            )
        replace_picture_inputs(
            hub,
            library_uuid,
            workflow_key,
            [
                (entry.slot_label, entry.input_name, entry.mode, entry.pixel_sha)
                for entry in payload.inputs
            ],
        )
        _announce(request, [workflow_key], "changed")
        return payload

    @router.post(
        "/workflows/{workflow_key}/unstack",
        summary="Take a card out of its stack",
        description=(
            "Stand this card on its own. The rest of its stack stays as it "
            "was unless one card is left, in which case that stack dissolves."
        ),
        response_model=StackResult,
        responses={404: {"description": "This machine has no such card."}},
    )
    def unstack_workflow(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_card(hub, workflow_key)
        before = effective_stack_keys(hub, workflow_key)
        unstack_card(hub, workflow_key)
        _announce(request, before, "stacks")
        return StackResult(stack_id=None, keys=[workflow_key])

    return router

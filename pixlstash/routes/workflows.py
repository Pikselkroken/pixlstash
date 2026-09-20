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
import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from pixlstash.hub.workflow_card_reads import (
    asset_names,
    card_index,
    find_card,
    instance_documents,
    key_pins,
    keys_in_stack,
    picture_inputs,
)
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
from pixlstash.services.a1111_recipe import reduce_a1111
from pixlstash.services.comfyui_recipe_service import (
    MAX_SEED_64,
    apply_adapter,
    apply_seeds,
    detect_lora_targets,
    detect_seed_targets,
)
from pixlstash.services.comfyui_service import (
    _extract_output_node_ids,
    _process_comfyui_outputs,
    _submit_comfyui_prompt,
)
from pixlstash.services import workflow_run_service as run_service
from pixlstash.services.workflow_card_service import (
    BEST_SCORE,
    card_defaults,
    read_grid,
)
from pixlstash.services import saved_recipe_service
from pixlstash.services.workflow_events import announce_changed_workflows
from pixlstash.routes.comfyui import (
    MAX_RUNS_PER_REQUEST,
    _comfyui_url,
    _load_embedded_api_prompt,
    _load_workflow_json,
    _picture_workflow_key,
    _read_embedded_metadata,
    _read_object_info,
    _resolve_workflow_path,
    _shelf_adapter,
)
from pixlstash.services.workflow_identity import RECIPE, STRUCTURAL
from pixlstash.services.workflow_hash import (
    MODEL_EXTENSIONS,
    WorkflowGraphError,
    structural_document,
)
from pixlstash.services.workflow_identity import topology_node_labels
from pixlstash.services.workflow_io import api_graph, detect_workflow_io
from pixlstash.services.workflow_library_service import (
    read_best_picture_ids,
    read_card_picture_ids,
    read_instance_hashes,
    read_kept_pixel_shas,
    read_library,
    read_recipe_activity,
    read_topology_picture_ids,
    read_variant_picture_counts,
    stack_for_picture,
)
from pixlstash.utils.comfyui_utilities import collect_seed_inputs
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
    slot_label: str | None = Field(
        None,
        description=(
            "The slot's address, as `PUT /workflows/{key}/slots` marks it. "
            "Null for a slot the cached list gave no label."
        ),
    )


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
    last_used: str | None = Field(
        None,
        description=(
            "When a kept picture was last made by any variant of this card, "
            "as the same ISO string `/workflows` serves. Null when the card "
            "has no kept pictures. The Workflows grid's *Recently used* sort "
            "reads it; `rank` cannot stand in, being a rating."
        ),
    )
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
    pins: list[ParameterAddress] | None = Field(
        None,
        description=(
            "The parameters the owner pinned, addressed as `PUT "
            "/workflows/{key}/pins` takes them. `null` is a card nobody has "
            "pinned on, so the client's own default pins apply; `[]` is "
            "somebody who unpinned everything. Without this the pins were "
            "write-only and the Workflow tab could not draw the pin it sets."
        ),
    )


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

# A run request's own ceilings. The picture list is capped at the same place
# the shipped run route caps a selection, because it is the same gesture.
MAX_RUN_PICTURES = MAX_RUNS_PER_REQUEST
MAX_RUN_LORAS = 32
MAX_PROMPT_LENGTH = 20000

# How deep the runnable-source resolver looks for a picture or an instance to
# run. The tiers want the card's BEST, and one candidate is not enough: the
# best-rated picture may be a JPEG carrying no graph at all.
#
# Kept small because tier 2 OPENS each candidate to read its embedded metadata,
# and the pre-flight is a route a selection panel calls on every change: the
# cost is (groups x this), and `picture_ids` admits 200 pictures, so a large
# number here is thousands of synchronous file reads per keystroke-ish gesture.
# `fetch_object_info` is not cached either, so a panel calling the pre-flight
# per keystroke also hits ComfyUI once per call; both costs want the same fix.
# ponytail: a constant; a per-request cache of "this picture carries no graph"
# and of `object_info` would let it grow if a card is ever found whose best
# five are all JPEGs.
BEST_PICTURE_DEPTH = 5

# How a saved recipe spells a parameter address in its free-form overrides map
# (``saved_recipe.overrides``, B6). A slot label never contains a slash and
# neither does a widget name, so the last one separates the two.
OVERRIDE_ADDRESS_SEPARATOR = "/"

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


class RunLora(BaseModel):
    """One LoRA slot a run fills, addressed the way #1377 addresses a slot.

    A slot is a **node and a field**, never just a node: a stacker holds
    ``lora_name_1`` and ``lora_name_2`` on one node and they are two slots. The
    strengths are this run's, applied over whatever the graph carried.
    """

    node_id: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    field: str = Field("lora_name", min_length=1, max_length=MAX_LABEL_LENGTH)
    sha256: str = Field(min_length=1, max_length=64)
    strength_model: float | None = Field(None, ge=-10.0, le=10.0)
    strength_clip: float | None = Field(None, ge=-10.0, le=10.0)


class RunValue(ParameterAddress):
    """One parameter this run sets, over the card's defaults."""

    value: bool | int | float | str


class RunDestination(BaseModel):
    """Where a run with no source picture files its output."""

    set_id: int | None = None
    project_id: int | None = None
    character_id: int | None = None


class RunRequest(BaseModel):
    """``POST /workflows/run`` and its dry run, which take the same body.

    **Exactly one source**, and the three are different questions: pictures
    ask "run what made these", a saved recipe asks "run this look", and a key
    asks "run this card". ``target`` overrides which card actually runs, which
    is how a stack's other member is chosen.

    **Edited defaults are overrides applied here**, never written back into a
    graph: the stored document is content-addressed and rewriting it would
    change the identity of the very card being run.
    """

    picture_ids: list[int] = Field(default_factory=list, max_length=MAX_RUN_PICTURES)
    saved_recipe_id: int | None = None
    workflow_key: str | None = None

    target: str | None = None

    prompt: str | None = Field(None, max_length=MAX_PROMPT_LENGTH)
    negative: str | None = Field(None, max_length=MAX_PROMPT_LENGTH)
    loras: list[RunLora] = Field(default_factory=list, max_length=MAX_RUN_LORAS)
    values: list[RunValue] = Field(default_factory=list, max_length=MAX_DEFAULTS)

    count: int = Field(1, ge=1, le=MAX_RUNS_PER_REQUEST)
    seed_mode: Literal["new", "keep", "fixed"] = "new"
    seed: int | None = Field(None, ge=0, le=MAX_SEED_64)
    destination: RunDestination | None = None
    # NO `inputs` field. A card's picture-input setup is READ here - a fixed
    # input whose picture has gone is `fixed_input_deleted` - but nothing
    # FILLS one yet, because filling it means uploading pictures into
    # ComfyUI's input folder, which is the whole i2i path the shipped
    # `/comfyui/workflows/{name}/run` already owns. Taking the field and
    # ignoring it would be worse than not offering it: a caller would send a
    # picture and get a run that never read it.

    # A new run is a new picture, NOT a variant of the one it was made from
    # (v1.12 B7). The shipped run routes stack by default and this one does
    # not: those replay one picture's own recipe, where the output genuinely
    # is another take of that picture, while this runs a card and the pictures
    # that named it are its source rather than its subject.
    stack: bool = False
    allow_unchecked: bool = False
    client_id: str | None = Field(None, max_length=MAX_LABEL_LENGTH)


class RunGroup(BaseModel):
    """One card a request resolved to, and whether it would run.

    ``reasons`` empty is the only thing that means "this would run". Each
    reason is a code and its payload, so a panel can act on it rather than
    print it.
    """

    workflow_key: str
    source: str | None = None
    source_picture_id: int | None = None
    picture_ids: list[int] = Field(default_factory=list)
    runs: int = 0
    reasons: list[dict] = Field(default_factory=list)


class RunPreflight(BaseModel):
    """``POST /workflows/run/preflight``: what a run would do, doing none of it."""

    ok: bool = False
    runs: int = 0
    groups: list[RunGroup] = Field(default_factory=list)


class RunResult(BaseModel):
    """What was submitted. ``prompts`` is one entry per submission."""

    status: str = "success"
    runs: int = 0
    groups: list[RunGroup] = Field(default_factory=list)
    prompts: list[dict] = Field(default_factory=list)


@dataclass
class Plan:
    """One run request, resolved: what it would do and what it asked ComfyUI.

    Not a response model - it carries the graphs and the ``object_info`` map,
    which are megabytes and are nobody's business outside the two handlers.
    """

    body: RunRequest
    groups: list[RunGroup]
    submittable: list[tuple[dict, RunGroup]]
    comfyui_url: str
    object_info: dict | None
    object_info_error: str | None


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


# The last resort, when a card has nothing identifying at all.
#
# **It is a last resort and not the ordinary answer.** It used to be reached by
# every card that was not imported from a file - which is most of a library
# built from pictures - so a grid of forty workflows read "Untitled workflow"
# forty times, and the name row, which is the card's only identifying text,
# identified nothing. Duplicating the checkpoint onto the name row was avoided
# on the grounds that it has a row of its own; a constant is worse than a
# duplicate, because a duplicate at least tells two cards apart.
UNNAMED_CARD = "Untitled workflow"


def _model_stem(name: str) -> str:
    """A model filename as a person says it: no folders, no extension.

    Against ``MODEL_EXTENSIONS`` rather than "whatever follows the last dot":
    a version in the name (``juggernaut_v9.1``) is not an extension, and a
    guess by length gets ``.safetensors`` -- eleven characters, and the one
    that matters -- exactly wrong.
    """
    stem = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    lowered = stem.lower()
    for ext in MODEL_EXTENSIONS:
        if lowered.endswith(ext):
            return stem[: -len(ext)]
    return stem


def _display_name(card, models=()) -> str:
    """What a card is called: the owner's name, else its file, else its models.

    That order is how much the name is *theirs*: one they typed, then the file
    they dropped, then a description built here. The built one is
    ``type · checkpoint`` (``txt2img · juggernautXL``). It is deliberately not
    unique - two cards differing only by a post-processing node share it - but
    it is the difference between forty identical rows and a grid that can be
    scanned, and the ⓘ panel carries what actually separates them.
    """
    if card.name:
        return card.name
    if card.file_name:
        stem = card.file_name.rsplit("/", 1)[-1]
        return stem[: -len(".json")] if stem.lower().endswith(".json") else stem
    base = next(
        (slot.name for slot in models if slot.kind == "checkpoint" and slot.name),
        None,
    ) or next((slot.name for slot in models if slot.name), None)
    if not base:
        # Every model name forgotten, or a graph that loads none.
        return UNNAMED_CARD
    stem = _model_stem(base)
    return f"{card.workflow_type} · {stem}" if card.workflow_type else stem


def _slot_models(slots) -> list[WorkflowSlotModel]:
    return [
        WorkflowSlotModel(
            name=slot.name, kind=slot.kind, mark=slot.mark, slot_label=slot.label
        )
        for slot in slots
    ]


def _card(figure, defaults=()) -> WorkflowCard:
    """Render one card's figures in the shape ``workflowCard.js`` documents."""
    return WorkflowCard(
        key=figure.card.workflow_key,
        name=_display_name(figure.card, figure.models),
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
        last_used=_iso(figure.last_used),
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
        pins = key_pins(hub, workflow_key)
        return WorkflowCardDetail(
            card=_card(figure, card_defaults(hub, server.vault, card)),
            notes=card.notes,
            hidden=card.hidden,
            variants=_card_variants(hub, server.vault, card),
            pins=None
            if pins is None
            else [
                ParameterAddress(slot_label=slot_label, input_name=input_name)
                for slot_label, input_name in pins
            ],
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

    # ── Running a card (v1.12 B7) ─────────────────────────────────────────
    #
    # One route, three sources and a dry run that shares its body. The refusals
    # are `services/workflow_run_service`'s codes rather than sentences: the
    # same batch mixes sources, and a panel grouping "these four are missing
    # the same model" cannot do that from prose.

    def _user(request: Request):
        return server.auth.get_user_for_request(request)

    def _library_uuid() -> str | None:
        return getattr(server.vault, "library_uuid", None)

    def _embedded_graph(picture_id: int) -> dict | None:
        """This picture's embedded API graph, or ``None`` if it has none.

        A picture whose file has been moved off the disk is not a source and
        must not be an error: the resolver is walking candidates, and the best
        picture of a card being unreadable is exactly when the next tier is
        wanted. ``_load_embedded_api_prompt`` raises a 404 for a missing file
        and a 500 for an unreadable one, both of which are caught here and
        logged - the run says ``no_runnable_source`` if nothing else answers.
        """
        try:
            return _load_embedded_api_prompt(server, picture_id)
        except HTTPException as exc:
            logger.info(
                "Picture %s cannot be read for a runnable graph (%s), so the "
                "resolver moves on: %s",
                picture_id,
                exc.status_code,
                exc.detail,
            )
            return None

    def _source_graph_for(
        card,
    ) -> tuple[run_service.Source | None, run_service.Reason | None]:
        """Resolve one card's runnable source, doing the reads the tiers need.

        The reads are done here and the decision in the service, so the order
        the tiers are tried in is testable without a vault: this function only
        stops early because reading the next tier costs a file or a query.
        """
        file_document = None
        if card.file_name:
            path, _source = _resolve_workflow_path(card.file_name)
            if path:
                try:
                    file_document = _load_workflow_json(path)
                except (OSError, ValueError) as exc:
                    logger.warning(
                        "Card %s names workflow file %s, which will not load, so "
                        "the run falls back to a picture or a stored recipe: %s",
                        card.workflow_key,
                        card.file_name,
                        exc,
                    )
        picture_graph = None
        picture_id = None
        if not (file_document and api_graph(file_document)):
            for candidate in read_best_picture_ids(
                server.vault, card.variants, BEST_PICTURE_DEPTH
            ):
                graph = _embedded_graph(candidate)
                if graph:
                    picture_graph, picture_id = graph, candidate
                    break
        hub = _hub()
        instances: list[tuple[str, dict]] = []
        names: dict[str, list[tuple[str, str]]] = {}
        library_uuid = _library_uuid()
        if not file_document and not picture_graph and library_uuid:
            hashes = read_instance_hashes(
                server.vault, card.variants, BEST_SCORE, BEST_PICTURE_DEPTH
            ) or read_instance_hashes(
                server.vault, card.variants, None, BEST_PICTURE_DEPTH
            )
            instances = instance_documents(hub, library_uuid, hashes)
            names = asset_names(hub, [h for h, _ in instances])
        return run_service.resolve_source(
            card,
            file_document=file_document,
            picture_graph=picture_graph,
            picture_id=picture_id,
            instance_documents=instances,
            asset_names=names,
        )

    def _apply_addressed(graph: dict, values: list[RunValue]) -> None:
        """Write each ``(slot label, input name)`` value into the graph.

        Addressed by label because that is how a card's defaults are addressed:
        node ids are renumbered by every re-serialisation and a card's variants
        do not agree about them. A wired input is left alone - overwriting one
        drops the link - and an input the graph does not have is not invented.
        """
        if not values:
            return
        try:
            labels = topology_node_labels(structural_document(graph))
        except WorkflowGraphError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"This workflow will not reduce, so its parameters cannot be addressed: {exc}",
            ) from exc
        wanted = {(item.slot_label, item.input_name): item.value for item in values}
        for node_id, label in labels.items():
            node = graph.get(node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            for name in list(inputs):
                if (label, name) in wanted and not isinstance(inputs[name], list):
                    inputs[name] = wanted[(label, name)]

    def _apply_prompts(graph: dict, prompt: str | None, negative: str | None) -> None:
        """Put this run's prompts into the detected text nodes."""
        if prompt is None and negative is None:
            return
        try:
            detected = detect_workflow_io(graph)
        except WorkflowGraphError as exc:
            logger.info("Prompts not applied, the graph will not reduce: %s", exc)
            return
        for node_ids, text in (
            (detected.positive_prompts, prompt),
            (detected.negative_prompts, negative),
        ):
            if text is None:
                continue
            for node_id in node_ids:
                inputs = (graph.get(node_id) or {}).get("inputs")
                if isinstance(inputs, dict) and isinstance(inputs.get("text"), str):
                    inputs["text"] = text

    def _apply_loras(
        graph: dict, loras: list[RunLora], object_info: dict | None
    ) -> list[run_service.Reason]:
        """Fill each named slot with its shelf adapter, then its strengths.

        Each entry is applied to its own slot alone rather than to every slot
        the graph has, which is the whole difference between this and the
        shipped ``adapter_sha256``: a stacker's three slots are three LoRAs.

        Returns the reasons that stop this card, empty when every slot was
        written. **Naming a slot the graph does not have is still a 400**, and
        that split is the module's rule: a request that cannot be interpreted
        against this card is a request error, while a card that will not run is
        a reason code.
        """
        hub = getattr(server, "hub", None)
        targets = {
            (str(t.get("node_id")), str(t.get("field"))): t
            for t in detect_lora_targets(graph)
        }
        for item in loras:
            target = targets.get((item.node_id, item.field))
            if target is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This workflow has no LoRA slot {item.field} on node "
                        f"{item.node_id}."
                    ),
                )
            adapter = _shelf_adapter(hub, item.sha256)
            if object_info is None and target.get("by") != "digest":
                # A filename slot is resolved against what THIS ComfyUI lists,
                # and with no `object_info` there is no list. Refused rather
                # than skipped: the caller asked for LoRA X and consented to
                # running uninspected, not to running with whatever LoRA the
                # stored graph happened to name. A digest slot needs no list -
                # that node resolves the file itself - so it falls through.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "PixlStash could not reach ComfyUI, so it cannot tell "
                        f"which file to write into LoRA slot {item.field} on "
                        f"node {item.node_id}. Start ComfyUI, or run without "
                        "the LoRA."
                    ),
                )
            try:
                apply_adapter(graph, [target], adapter, object_info or {})
            except LookupError as exc:
                # The adapter is on the shelf and not on this ComfyUI, which is
                # the same fact as any other model the graph names and must not
                # be a different kind of answer: a dry run that 400s instead of
                # reporting `missing_models` is not a dry run.
                logger.info(
                    "LoRA %s cannot be placed in slot %s %s: %s",
                    item.sha256,
                    item.node_id,
                    item.field,
                    exc,
                )
                return [
                    run_service.Reason(
                        run_service.MISSING_MODELS,
                        {
                            "models": [
                                {
                                    "file": (adapter.get("filenames") or [""])[-1],
                                    "folder": "loras",
                                }
                            ]
                        },
                    )
                ]
            inputs = (graph.get(item.node_id) or {}).get("inputs")
            if not isinstance(inputs, dict):
                continue
            # ``strength`` is the model-only loaders' single widget, so a model
            # strength fills it when the two-widget spelling is absent rather
            # than being silently dropped.
            if item.strength_model is not None:
                for name in ("strength_model", "strength"):
                    if name in inputs and not isinstance(inputs[name], list):
                        inputs[name] = item.strength_model
                        break
            if item.strength_clip is not None and not isinstance(
                inputs.get("strength_clip"), list
            ):
                if "strength_clip" in inputs:
                    inputs["strength_clip"] = item.strength_clip
        return []

    def _fixed_input_reasons(hub, workflow_key: str) -> list[run_service.Reason]:
        """Every ``fixed`` picture input of this card whose picture has gone."""
        library_uuid = _library_uuid()
        if not library_uuid:
            return []
        fixed = [
            row
            for row in picture_inputs(hub, library_uuid, workflow_key)
            if row["mode"] == "fixed" and row["pixel_sha"]
        ]
        if not fixed:
            return []
        alive = read_kept_pixel_shas(server.vault, [row["pixel_sha"] for row in fixed])
        gone = [row for row in fixed if row["pixel_sha"] not in alive]
        return (
            [
                run_service.Reason(
                    run_service.FIXED_INPUT_DELETED,
                    {
                        "inputs": [
                            {
                                "slot_label": r["slot_label"],
                                "input_name": r["input_name"],
                            }
                            for r in gone
                        ]
                    },
                )
            ]
            if gone
            else []
        )

    def _require_one_source(body: RunRequest) -> None:
        """Exactly one of the three sources, checked before anything is read.

        First, and not inside the grouping: a body naming two sources must be
        told that, and looking the saved recipe up first would answer 404 about
        a recipe id the caller never meant to use on its own.
        """
        chosen = [
            name
            for name, given in (
                ("picture_ids", bool(body.picture_ids)),
                ("saved_recipe_id", body.saved_recipe_id is not None),
                ("workflow_key", bool(body.workflow_key)),
            )
            if given
        ]
        if len(chosen) != 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Name exactly one source: picture_ids, saved_recipe_id or "
                    "workflow_key."
                ),
            )

    def _saved_recipe(body: RunRequest):
        """The saved recipe this run starts from, or a 404."""
        recipe = saved_recipe_service.read_recipe(server.vault, body.saved_recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="Unknown saved recipe.")
        return recipe

    def _revalidated(body: RunRequest, changes: dict) -> RunRequest:
        """``body`` with *changes* applied, through the field constraints again.

        NOT ``model_copy(update=…)``, which assigns without validating: the
        values here come out of a stored row, so a saved seed above
        ``MAX_SEED_64`` or an overrides map longer than ``MAX_DEFAULTS`` would
        walk straight past the ceilings the request body declares.

        Raises:
            HTTPException: 422 when the merged body breaks one, naming the
                recipe - the request was fine and the row is what is wrong.
        """
        try:
            return RunRequest.model_validate({**body.model_dump(), **changes})
        except ValidationError as exc:
            logger.warning(
                "Saved recipe %s merges into a body that will not validate: %s",
                body.saved_recipe_id,
                exc,
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "This saved recipe holds a value this run cannot take: "
                    f"{exc.errors()[0].get('msg', 'invalid value')}."
                ),
            ) from exc

    def _float_or_none(value) -> float | None:
        """A saved LoRA's strength as a number, or ``None`` when it is not one."""
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            logger.warning(
                "Saved LoRA strength %r is not a number; slot left as is", value
            )
            return None

    def _with_recipe(
        body: RunRequest,
    ) -> tuple[RunRequest, str | None, list[dict]]:
        """The body with a saved recipe's own look filled in underneath it.

        The row is what somebody pressed Save on, so it supplies the prompt,
        the LoRAs, the overrides and the seed - and the request still wins over
        every one of them, because the panel that sends both is showing the
        recipe with the owner's edits on top.
        """
        if body.saved_recipe_id is None:
            return body, None, []
        stored = run_service.saved_recipe_body(_saved_recipe(body))
        addressed = []
        for address, value in (stored["overrides"] or {}).items():
            slot_label, _, input_name = str(address).rpartition(
                OVERRIDE_ADDRESS_SEPARATOR
            )
            if not slot_label or not input_name:
                logger.warning(
                    "Saved recipe %s holds override address %r, which names no "
                    "slot and input, so it is not applied.",
                    body.saved_recipe_id,
                    address,
                )
                continue
            addressed.append(
                RunValue(slot_label=slot_label, input_name=input_name, value=value)
            )
        given = {(v.slot_label, v.input_name) for v in body.values}
        merged = _revalidated(
            body,
            {
                "prompt": body.prompt if body.prompt is not None else stored["prompt"],
                "negative": (
                    body.negative if body.negative is not None else stored["negative"]
                ),
                "values": [
                    v.model_dump()
                    for v in addressed
                    if (v.slot_label, v.input_name) not in given
                ]
                + [v.model_dump() for v in body.values],
            },
        )
        # The seed, and ONLY when the caller left the choice open. A request
        # that says `seed_mode` meant it: the recipe filling in a mode nobody
        # asked for is the row winning over the gesture, which is the wrong way
        # round and what this function's own contract says it does not do.
        # `seed` is compared against None, never truthiness: a kept seed of 0 is
        # a seed somebody kept.
        asked = "seed_mode" in (body.model_fields_set or set())
        if body.seed is None and not asked and stored["keep_seed"]:
            try:
                merged = _revalidated(
                    merged, {"seed": int(stored["seed"]), "seed_mode": "fixed"}
                )
            except (TypeError, ValueError):
                logger.warning(
                    "Saved recipe %s keeps seed %r, which is not an integer, so "
                    "the run draws a new one.",
                    body.saved_recipe_id,
                    stored["seed"],
                )
        # The LoRAs go back with the body rather than into it: a saved one names
        # a file and a strength but no slot, and a slot only exists once the
        # graph has been resolved.
        return merged, stored["workflow_key"], stored["loras"] if not body.loras else []

    def _is_a1111(picture_id: int) -> bool:
        """Whether this picture's recipe is A1111 infotext rather than a graph.

        Asked only of a picture that resolved to no card, to tell the two
        honest "nothing to run" states apart. Unreadable for any reason is
        answered ``False``: this distinguishes one refusal from another and
        must not become a second way for the request to fail.
        """
        try:
            return reduce_a1111(_read_embedded_metadata(server, picture_id)) is not None
        except HTTPException as exc:
            logger.info(
                "Picture %s cannot be read, so its refusal stays "
                "no_runnable_source rather than a1111: %s",
                picture_id,
                exc.detail,
            )
            return False

    def _groups_for(
        body: RunRequest, recipe_key: str | None
    ) -> list[tuple[str | None, list[int], list[run_service.Reason]]]:
        """``(workflow_key, picture_ids, reasons)`` per card this request runs.

        With several pictures and no target the server groups them by each
        picture's recipe, which is the whole reason this is not one key: a
        selection spanning three cards is three different graphs, and running
        the first one over all of them would be silently wrong.
        """
        if body.workflow_key:
            return [(_require_hash(body.workflow_key, "workflow_key"), [], [])]
        if body.saved_recipe_id is not None:
            return [(recipe_key, [], [])]

        grouped: dict[str, list[int]] = {}
        orphans: list[tuple[str | None, list[int], list[run_service.Reason]]] = []
        for picture_id in body.picture_ids:
            key = _picture_workflow_key(server, picture_id)
            if key:
                grouped.setdefault(key, []).append(picture_id)
                continue
            # No card: either the picture's recipe is A1111 infotext, which no
            # ComfyUI graph can be made of, or there is nothing to run at all.
            reason = (
                run_service.A1111
                if _is_a1111(picture_id)
                else run_service.NO_RUNNABLE_SOURCE
            )
            orphans.append(
                (
                    None,
                    [picture_id],
                    [run_service.Reason(reason, {"picture_id": picture_id})],
                )
            )
        return [(key, ids, []) for key, ids in sorted(grouped.items())] + orphans

    def _plan(request: Request, body: RunRequest) -> Plan:
        """Resolve, judge and prepare every run this body asks for.

        ComfyUI is asked for its ``object_info`` **once** and the answer is
        carried in the plan: the pre-flight, the LoRA resolution, the seed
        detection and the run all need it, and re-fetching it would also let
        the run submit against a different answer than the one it was judged
        against.
        """
        hub = _hub()
        _require_one_source(body)
        body, recipe_key, recipe_loras = _with_recipe(body)
        user = _user(request)
        configured = bool(getattr(user, "comfyui_url", None))
        comfyui_url = _comfyui_url(user)
        object_info, object_info_error = _read_object_info(comfyui_url)

        if body.seed_mode == "fixed" and body.seed is None:
            raise HTTPException(
                status_code=400, detail="seed_mode 'fixed' needs a seed."
            )
        groups = _groups_for(body, recipe_key)
        if body.target:
            # One target replaces every group's card, keeping the pictures that
            # chose it: "run this stack member over what I selected".
            target = _require_hash(body.target, "target")
            pictures = [pid for _, ids, _ in groups for pid in ids]
            groups = [(target, pictures, [])]

        planned: list[RunGroup] = []
        submittable: list[tuple[dict, RunGroup]] = []
        for workflow_key, picture_ids, reasons in groups:
            group = RunGroup(
                workflow_key=workflow_key or "",
                picture_ids=picture_ids,
                reasons=[r.as_dict() for r in reasons],
            )
            if not workflow_key:
                # Every keyless group converges here, so the invariant lives
                # here and not at each producer: `reasons` empty is the only
                # thing that means a group would run, and one with no card
                # cannot. Today every producer already attaches a reason (a
                # picture on no card gets `a1111` or `no_runnable_source`, and
                # `POST /recipes` refuses an empty `workflow_key` outright), so
                # the fallback is the guard on the next one.
                group.reasons = (
                    reasons
                    and [r.as_dict() for r in reasons]
                    or [run_service.Reason(run_service.NO_RUNNABLE_SOURCE).as_dict()]
                )
                planned.append(group)
                continue
            if reasons:
                planned.append(group)
                continue
            card = find_card(hub, workflow_key)
            if card is None:
                group.reasons = [
                    run_service.Reason(
                        run_service.NO_RUNNABLE_SOURCE, {"workflow_key": workflow_key}
                    ).as_dict()
                ]
                planned.append(group)
                continue
            source, failure = _source_graph_for(card)
            if source is None:
                group.reasons = [failure.as_dict()]
                planned.append(group)
                continue
            group.source = source.origin
            group.source_picture_id = source.picture_id

            graph = source.graph
            _apply_addressed(graph, body.values)
            _apply_prompts(graph, body.prompt, body.negative)
            slots_in_graph = detect_lora_targets(graph)
            # Applied only when it CAN be, and after the two questions that
            # would otherwise be answered as the wrong failure: a graph with no
            # LoRA slot at all is `no_lora_loader` rather than a 400 about one
            # slot, and an unreachable ComfyUI cannot resolve a filename slot,
            # which `apply_adapter` would report as a missing node class.
            found: list[run_service.Reason] = []
            if body.loras and slots_in_graph:
                # NOT gated on `object_info`: skipping the application when
                # ComfyUI could not be asked is how a consented run silently
                # kept the stored graph's LoRA instead of the one that was
                # asked for. `_apply_loras` answers for that state itself.
                found += _apply_loras(graph, body.loras, object_info)
            elif not body.loras and recipe_loras and slots_in_graph:
                # "Run this saved look" has to place the look's own LoRAs. A
                # saved one names a file and a strength but no slot, so they
                # fill the graph's slots in order - which is exact for the one
                # slot a card usually has, and is why the request's own
                # addressed form exists for the rest.
                found += _apply_loras(
                    graph,
                    [
                        RunLora(
                            node_id=str(target["node_id"]),
                            field=str(target["field"]),
                            sha256=str(saved.get("sha256") or ""),
                            strength_model=_float_or_none(saved.get("strength")),
                        )
                        for target, saved in zip(slots_in_graph, recipe_loras)
                        if saved.get("sha256")
                    ],
                    object_info,
                )
            if body.seed_mode == "keep" and source.seedless:
                # There is nothing to keep: a stored instance document nulls its
                # seeds by design, so every one of `count` runs would submit
                # zero and produce the identical image. Refused rather than
                # quietly re-read as "new", which would be answering a different
                # question than the one asked.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "This card is being run from its stored recipe, which "
                        "keeps no seed, so there is none to keep. Use "
                        "seed_mode 'new' or 'fixed'."
                    ),
                )

            judged, _preflight = run_service.judge(
                graph,
                object_info,
                object_info_error,
                wants_lora=bool(body.loras),
                lora_slots=slots_in_graph,
            )
            found += judged
            if object_info is None and not configured:
                # The URL is the guessed default and nothing answered on it:
                # "set your ComfyUI address" is the actionable half of that.
                found = [
                    run_service.Reason(run_service.COMFYUI_NOT_CONFIGURED)
                    if r.code == run_service.COMFYUI_UNREACHABLE
                    else r
                    for r in found
                ]
            found += _fixed_input_reasons(hub, workflow_key)
            group.reasons = [r.as_dict() for r in found]
            if run_service.blocks_group(found, allow_unchecked=body.allow_unchecked):
                planned.append(group)
                continue
            if found:
                # The only reasons that survive here are ones the owner has
                # consented to, so say in the log what is being run blind.
                logger.warning(
                    "[workflows] Running card %s UNINSPECTED on the owner's "
                    "explicit acknowledgement: %s",
                    workflow_key,
                    ", ".join(r.code for r in found),
                )
            group.runs = body.count
            planned.append(group)
            submittable.append((graph, group))

        blocking = any(
            run_service.blocks_batch(
                [run_service.Reason(r["code"]) for r in group.reasons],
                allow_unchecked=body.allow_unchecked,
            )
            for group in planned
        )
        if blocking:
            # A missing model blocks the WHOLE batch, mixed or not: installing
            # it is a trip away from the keyboard, and queueing the rest would
            # leave the owner repeating the gesture to catch what was skipped.
            for group in planned:
                group.runs = 0
            submittable = []
        total = sum(group.runs for group in planned)
        if total > MAX_RUNS_PER_REQUEST:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{total} runs is more than one request starts; at most "
                    f"{MAX_RUNS_PER_REQUEST}."
                ),
            )
        return Plan(
            body=body,
            groups=planned,
            submittable=submittable,
            comfyui_url=comfyui_url,
            object_info=object_info,
            object_info_error=object_info_error,
        )

    @router.post(
        "/workflows/run/preflight",
        summary="Check what a run would do",
        description=(
            "The same body as POST /workflows/run, submitting nothing. Every "
            "card the request resolves to comes back with the reasons it would "
            "not run: comfyui_not_configured, comfyui_unreachable, ui_format, "
            "missing_nodes, missing_models, a1111, fixed_input_deleted, "
            "no_lora_loader, pixlstash_nodes, no_save_node, no_runnable_source. "
            "A group runs when its reasons are empty - or when the only ones "
            "left are an uninspectable ComfyUI the body said allow_unchecked "
            "to. A body that cannot be interpreted against the card answers "
            "400/404/422 here exactly as it does on the run, so the two never "
            "disagree."
        ),
        response_model=RunPreflight,
    )
    def preflight_run(request: Request, body: RunRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        plan = _plan(request, body)
        return RunPreflight(
            # `ok` is "the run would submit everything this resolved to", which
            # is not "no group has a reason": a group the owner consented to
            # running unchecked carries its reason AND runs.
            ok=bool(plan.submittable) and len(plan.submittable) == len(plan.groups),
            runs=sum(g.runs for g in plan.groups),
            groups=plan.groups,
        )

    @router.post(
        "/workflows/run",
        summary="Run a workflow card",
        description=(
            "Runs the card named by picture_ids, saved_recipe_id or "
            "workflow_key; target runs another card instead, which is how a "
            "stack's other member is chosen. With several pictures and no "
            "target the server groups them by each picture's recipe. count "
            "submits that many runs of each, seed_mode is new, keep or fixed, "
            "and prompt/negative/loras/values are overrides applied to the "
            "graph at run time and never written back into it. New runs are "
            "NOT stacked with their source unless stack: true. A missing model "
            "blocks the whole batch. See /workflows/run/preflight for the "
            "reason codes."
        ),
        response_model=RunResult,
    )
    def run_workflow(request: Request, body: RunRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        plan = _plan(request, body)
        body, groups = plan.body, plan.groups
        if not plan.submittable:
            return RunResult(status="refused", runs=0, groups=groups, prompts=[])

        # The consent rule is enforced in `_plan` and nowhere else, so the dry
        # run and the run answer identically: without `allow_unchecked` an
        # uninspectable ComfyUI blocks the batch and nothing reaches here.
        comfyui_url = plan.comfyui_url
        object_info = plan.object_info

        destination = (
            body.destination.model_dump(exclude_none=True) if body.destination else None
        ) or None
        prompts: list[dict] = []
        try:
            _submit_every(
                request, plan, body, comfyui_url, object_info, destination, prompts
            )
        except Exception as exc:
            if not prompts:
                raise
            # Earlier runs are already queued in ComfyUI and importing, so they
            # are returned for the client to follow rather than lost behind a
            # 500. The same choice the shipped run route makes, and for the
            # same reason: a prompt id nobody was told about is a generation
            # the owner cannot find, cancel or attribute.
            #
            # `Exception` and not `HTTPException`: what the queued work costs
            # does not depend on which layer failed, and a prompt lost to an
            # unexpected error is lost just as thoroughly. Logged with the
            # traceback, because swallowing the type is exactly how a real bug
            # would hide here.
            logger.exception(
                "[workflows] Run stopped after %d of its submissions: %s",
                len(prompts),
                exc,
            )
            return RunResult(
                status="partial",
                runs=len(prompts),
                groups=groups,
                prompts=prompts,
            )
        return RunResult(
            status="success", runs=len(prompts), groups=groups, prompts=prompts
        )

    def _submit_every(
        request: Request,
        plan: Plan,
        body: RunRequest,
        comfyui_url: str,
        object_info: dict | None,
        destination: dict | None,
        prompts: list[dict],
    ) -> None:
        """Queue every planned run, appending each prompt id AS it is accepted.

        ``prompts`` is the caller's list and is written to in place on purpose:
        a failure half way through has already put work into ComfyUI's queue,
        and the caller needs to know which.
        """
        for graph, group in plan.submittable:
            output_node_ids = _extract_output_node_ids(graph, {})
            seed_targets = detect_seed_targets(
                graph, object_info or {}
            ) or collect_seed_inputs(graph)
            # Hoisted out of the loop below: it is a WRITE task, idempotent,
            # and `count` runs of one group all land in the one stack.
            stack_id = (
                stack_for_picture(server.vault, group.picture_ids[0])
                if body.stack and group.picture_ids
                else None
            )
            for _ in range(body.count):
                instance = deepcopy(graph)
                if body.seed_mode == "fixed":
                    apply_seeds(instance, seed_targets, body.seed)
                elif body.seed_mode == "new":
                    apply_seeds(instance, seed_targets, None)
                submitted = _submit_comfyui_prompt(
                    comfyui_url, instance, body.client_id
                )
                prompt_id = submitted.get("prompt_id") or submitted.get("id")
                if prompt_id:
                    lease = request.state.library_lease
                    threading.Thread(
                        target=_process_comfyui_outputs,
                        args=(
                            server,
                            comfyui_url,
                            str(prompt_id),
                            output_node_ids,
                            stack_id,
                            group.picture_ids[0]
                            if body.stack and group.picture_ids
                            else None,
                        ),
                        kwargs={
                            "view_context": destination,
                            "origin_generation": lease.generation,
                            "origin_library_uuid": lease.library_uuid,
                        },
                        daemon=True,
                    ).start()
                prompts.append(
                    {"workflow_key": group.workflow_key, "prompt_id": prompt_id}
                )

    return router

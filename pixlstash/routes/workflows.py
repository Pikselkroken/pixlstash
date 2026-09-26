"""The Workflows view: the library list, the cards, and what the owner writes.

**The view opens at card level** (workflow implementation plan §F1, design
`DECISIONS.md`). A card is one workflow as the owner thinks of it; the recipes
filed under it are the same graph bound to different models, and they are the
card's *variants* rather than cards of their own. On the owner's library that
is ~192 cards instead of ~617 rows. B9 (#1410) retired the topology list that
used to hold `GET /workflows`, and the grid took the route.

**Two databases, no join.** The rows live in the hub and are content-addressed;
the counts live in whichever vault is attached. Nothing here crosses that
boundary — the hub answers "which workflows exist", the vault answers "how many
of my pictures came from each", and a hash the hub has never heard of is simply
a workflow this machine does not have. That is the arrangement
``pixlstash/hub/schema.py`` chose content addressing for, and it is why a
detached library still lists correctly against a hub that has the recipes.

**Every route here is ``OWNER_ONLY``, and that is not the default speaking.**
The card counts are read across every kept picture in the vault, so handing
one to a picture-, set- or project-scoped token would disclose the size of the
whole library one workflow at a time. The same goes for the picture ids the rail's
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

import functools
import os
import re
import threading
from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

from pixlstash.hub.workflow_card_reads import (
    asset_names,
    card_index,
    default_overrides,
    find_card,
    instance_documents,
    key_pins,
    keys_in_stack,
    picture_inputs,
    slot_marks,
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
    assets_for_topology_recipes,
    forgotten_asset_counts,
    get_document,
    recipe_exists,
    recipes_for_topology,
    unvouched_model_values,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.a1111_recipe import reduce_a1111
from pixlstash.services.comfyui_recipe_service import (
    LORA_FILENAME_FIELD_RE,
    MAX_SEED_64,
    apply_adapter,
    apply_filename_swap,
    apply_lora_chain,
    apply_model_swap,
    apply_seeds,
    detect_lora_targets,
    detect_model_targets,
    insert_adapter,
    listed_options,
    lora_display_name,
    plan_lora_chain,
    plan_lora_insertion,
    read_lora_chain,
    read_lora_chain_untyped,
)
from pixlstash.services.comfyui_service import (
    PIXLSTASH_PICTURE_LOADER,
    _extract_output_node_ids,
    _process_comfyui_outputs,
    _submit_comfyui_prompt,
    _upload_image_to_comfyui,
    library_ids_named,
    swap_pixlstash_savers,
    unfed_picture_loaders,
)
from pixlstash.services import workflow_bindings
from pixlstash.services import workflow_run_service as run_service
from pixlstash.services.workflow_card_service import (
    BASE_MODEL_KINDS,
    BEST_SCORE,
    by_key,
    card_defaults,
    read_grid,
    slot_kind,
)
from pixlstash.services import saved_recipe_service
from pixlstash.services.model_shelf_service import (
    adapter_digest_index,
    known_base_model,
    model_name_aliases,
    propose_companions,
    recipe_asset_index,
)
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
    runnable_document,
    store_workflow_copy,
    trash_user_workflow,
)
from pixlstash.services.workflow_export import download_stem, scrub_for_export
from pixlstash.services.workflow_identity import (
    CHECKPOINT_WIDGETS,
    RECIPE,
    STRUCTURAL,
)
from pixlstash.services.workflow_hash import (
    MODEL_EXTENSIONS,
    SECRET_FIELD_RE,
    WorkflowGraphError,
    normalized_filename,
    structural_document,
)
from pixlstash.services.workflow_identity import topology_node_labels
from pixlstash.services.workflow_inputs import (
    CardInput,
    card_input_modes,
    resolve_fills,
)
from pixlstash.services.workflow_io import api_graph, detect_workflow_io
from pixlstash.services.workflow_library_service import (
    read_best_picture_ids,
    read_card_picture_ids,
    read_instance_hashes,
    read_kept_picture_files,
    read_library_ids,
    read_oldest_kept_by_pixel_sha,
    read_recipe_activity,
    read_variant_picture_counts,
    stack_for_picture,
)
from pixlstash.services.workflow_bindings import BINDINGS_KEY
from pixlstash.utils.comfyui_utilities import (
    iter_model_fields_api,
    loaded_model_widgets,
)
from pixlstash.stacking import build_stack_filename_prefix
from pixlstash.utils.adapter_header import (
    FILE_CHECKPOINT,
    FILE_ENGINE,
    FILE_TEXT_ENCODER,
    FILE_VAE,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.known_base_models import family_of, modality_of

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

    On a card with **no recipe** (``variant_count: 0``, #1466) these are not
    read off a stored slot list at all: they are recovered from the workflow
    file itself, which is best effort and comes back empty on a document the
    recovery cannot read. An empty ``models`` on such a card therefore means
    *nobody has read this workflow's models*, never *it has none*.
    """

    name: str | None = Field(
        None,
        description=(
            "The model's file as a card names it: no folder, no extension, "
            "and no quant postfix (`t5xxl`, not "
            "`t5xxl_fp8_e4m3fn.safetensors`). Null for a slot whose name the "
            "recipe never recorded — never an empty string, which reads as a "
            "model called nothing. A name that is *nothing but* its quant "
            "falls back to the file's own string, the way a model shelf row "
            "does."
        ),
    )
    title: str | None = Field(
        None,
        description=(
            "What the model shelf calls this file — the trainer's own name, "
            "or the one the owner typed — or null where the shelf does not "
            "know it. **A client showing the model shows this in preference "
            "to `name`**, because the card's generated `name` was built from "
            "it: a chip reading `realvisxl.safetensors` under a name row "
            "reading `Krea 2` is one model described twice, which is the "
            "drift #1416 already cost this pair once."
        ),
    )
    icon: str | None = Field(
        None,
        description=(
            "The `sha256` of the picture the owner chose for this model on "
            "the shelf, for `GET /model-icons/{sha256}`, or null - which is "
            "the ordinary case, since PixlStash generates no sample for a "
            "model it registers in place. A client drawing the model falls "
            "back the way the shelf's own rows do: this picture, else "
            "initials off the name."
        ),
    )
    base_model: str | None = Field(
        None,
        description=(
            "The shelf's raw `base_model` for this file, or null where the "
            "shelf does not hold the model."
        ),
    )
    base_model_folded: str | None = Field(
        None,
        description=(
            "The same value folded to its canonical label, or null where "
            "`known_base_models` does not recognise it — **the two spellings "
            "the model shelf serves, under the same names**. A client hashes "
            "a generated mark's colour out of `base_model_folded or "
            "base_model`, so serving one of them would give the same model "
            "two colours in two places."
        ),
    )
    kind: str
    quant: str | None = Field(
        None,
        description=(
            "The precision the file was stored at, as one canonical id "
            "(`bf16`, `fp8_e4m3`, `q4_k_m`, `int8`, `mixed`), or null where "
            "nothing records it — which is the ordinary case. **`name` has "
            "had this stripped off it**, so two quant variants of one model "
            "read as one name and this is what keeps them apart; a client "
            "showing the name shows this beside it.\n\n"
            "One vocabulary from two sources: the model shelf's own column, "
            "read from the safetensors header, where this machine has scanned "
            "the file, and the filename postfix otherwise — which is the only "
            "source a `.gguf` has. The id is served rather than a label, "
            "because `FP8 E4M3` is display copy and belongs in the client."
        ),
    )
    mark: str | None = None
    slot_label: str | None = Field(
        None,
        description=(
            "The slot's address, as `PUT /workflows/{key}/slots` marks it. "
            "Null for a slot the cached list gave no label — which is every "
            "slot of a card that has no recipe (#1466), since a label is an "
            "address inside a stored topology and such a card has none."
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


class WorkflowRecipeLora(BaseModel):
    """One LoRA that has filled the card's recipe slots in some variant.

    ``character_id`` is null for a LoRA attached to no character in this
    library (or to several), and the card then draws a LoRA glyph for it.
    """

    name: str
    recipes: int
    character_id: int | None = None
    character_name: str | None = None


class WorkflowCover(BaseModel):
    """One picture of a card's cover strip: where to load it, and how to crop it.

    ``url`` is API-relative (``/pictures/thumbnails/{id}.webp?v=…``), so a
    consumer prefixes the API base and appends the share token itself.

    The rest is the stored face-weighted SQUARE rectangle within that bitmap,
    under the same names ``GET /pictures/thumbnails/batch`` serves it by, so
    ``utils/squareCrop.js`` reads a cover with no mapping layer. A cover cell
    is not square (6:5, 4:5, 3:5 on a card), so the client fits the cell's
    ratio around this rectangle's centre rather than using it verbatim.

    **Every crop field is nullable**, because a picture keeps them NULL until
    it has been processed. A cover with no rectangle is cropped the way it is
    today, ``object-fit: cover`` anchored top centre — the fallback is a
    requirement, not a nicety (#1465).

    They are five independent columns rather than one optional block, so a
    client decides on ``square_crop_x``/``_y``: ``render_thumbnail`` writes the
    three crop values together, but nothing here enforces that, and a row
    carrying an origin without a ``side`` is answered by deriving
    ``min(width, height)`` — which is what the square-mode grid already does
    (``squareCropParams``).
    """

    url: str
    picture_id: int = Field(
        description=(
            "The picture this cover draws, so a client can OPEN it (#1455). "
            "The id is inside `url` and parsing it back out is a path shape "
            "rather than an interface. It sits on the cover rather than in a "
            "`cover_ids` list beside `covers` - which is what it was before "
            "this model existed - because two lists paired by position are "
            "two lists that can come apart, and nothing but their "
            "construction would keep them in step."
        )
    )
    thumbnail_width: int | None = None
    thumbnail_height: int | None = None
    square_crop_x: int | None = None
    square_crop_y: int | None = None
    square_crop_side: int | None = None


class WorkflowStackMember(BaseModel):
    """One card of a stack, as a picker lists it without reading its card.

    Members of one stack usually share a base model and a type, so their
    generated names differ only by a number (:func:`_display_names`);
    ``sets_apart`` says what this one loads that not every member does, and ``differs_by`` is its chips against the cover
    for the difference that is not a model (a step added, nodes rewired).
    """

    key: str
    name: str
    sets_apart: list[str] = Field(
        default_factory=list,
        description=(
            "The models and structural LoRAs this member loads that some "
            "other member of the stack does not, as the shelf names them. "
            "Recipe LoRAs are left out: they vary inside one card."
        ),
    )
    differs_by: list[str] = Field(
        default_factory=list,
        description="This member's chips against the cover; empty on the cover.",
    )


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
    type_label: str | None = Field(
        None,
        description=(
            "`type` spelled the way ComfyUI spells it on its own templates "
            "(`txt2img` -> `Text to Image`). The card's name row and its type "
            "chip both read this, so the two cannot say the same fact in two "
            "vocabularies. Null exactly when `type` is."
        ),
    )
    imported: bool = Field(
        False, description="A workflow file on this machine runs this card."
    )
    hidden: bool = Field(
        False,
        description=(
            "The owner has hidden this card. **On the GRID** it is true only "
            "for a card `include_hidden` let in, so a client that did not ask "
            "never sees it set - but one that did has to mark those cards, or "
            "the checkbox silently mixes them into the grid they were kept "
            "out of. **On the detail route it is always the card's own "
            "state**, with no flag involved: `GET /workflows/{key}` "
            "opens a hidden card by design, which is how it can be unhidden. "
            "`WorkflowCardDetail.hidden` is the same fact beside it."
        ),
    )
    models: list[WorkflowSlotModel] = Field(default_factory=list)
    loras: list[WorkflowSlotModel] = Field(default_factory=list)
    recipe_loras: list[WorkflowRecipeLora] = Field(
        default_factory=list,
        description=(
            "Every LoRA the card's variants loaded into its recipe slots, "
            "most-used first. Empty on a card with no recipe slot."
        ),
    )
    differs_by: list[str] = Field(default_factory=list)
    picture_count: int = 0
    rating: float | None = Field(
        None, description="Mean of the stars this card has; null when it has none."
    )
    covers: list[WorkflowCover] = Field(
        default_factory=list,
        description="Up to three cover pictures, the cover first.",
    )
    stack_size: int = 1
    saved_recipe_count: int = 0
    defaults: list[WorkflowDefault] = Field(default_factory=list)
    # Beyond the shared shape, and additive: a caller that only knows
    # `workflowCard.js` ignores these and needs no translation for the rest.
    topology_hash: str
    specials: list[str] | None = Field(
        None,
        description=(
            "The post-processing this workflow carries, from `upscale` and "
            "`face_detailer`. **Null and `[]` are different answers**: null "
            "means the card's document has not been read for it yet, `[]` "
            "means it was read and the graph has none. The generated `name` "
            "says `+ FaceDetailer` only on `[]`'s side of that line, so a "
            "consumer drawing its own chip has to keep the two apart too."
        ),
    )
    variant_count: int = 0
    member_keys: list[str] = Field(default_factory=list)
    members: list[WorkflowStackMember] = Field(
        default_factory=list,
        description=(
            "The whole stack in its order, the cover first and this card "
            "included, each with its name and what sets it apart. Empty "
            "outside a stack."
        ),
    )
    stack_id: str | None = Field(
        None,
        description=(
            "The stack this card sits in, or null when it stands alone. "
            "Either a stored stack's id or `auto:<core hash>` for an "
            "automatic grouping nobody has ordered yet - the two are what "
            "`PUT /workflows/stacks/{stack_id}/order` and "
            "`POST /workflows/stacks/{stack_id}/unstack` are addressed by, "
            "and neither can be derived from anything else the card carries."
        ),
    )
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
    ghosts: int = Field(
        0,
        description=(
            "Picture ghosts this card's variants keep for the active library: "
            "the thumbnail and prompt of a picture the library no longer has."
        ),
    )
    model_ghosts: int = Field(
        0,
        description=(
            "How many VALUES this card's variants name that the shelf does "
            "not hold - a model filename, or a `*_sha256` digest, which is "
            "what the shelf judges. A card naming one missing model by both "
            "counts 2, so this is not a count of models: read it as "
            "'something here is gone'. With `ghosts`, what the Filters "
            "panel's Ghosts row asks about."
        ),
    )


class WorkflowCards(BaseModel):
    """``GET /workflows``: the grid, and what it left out.

    ``one_offs`` and ``hidden`` are counts rather than rows on purpose: both
    sets are excluded from ``cards``, and the view offers them as a way back in
    rather than as clutter. Both still open on the detail route.
    """

    cards: list[WorkflowCard]
    one_offs: int = 0
    hidden: int = 0


class WorkflowCardDetail(BaseModel):
    """``GET /workflows/{workflow_key}``: one card opened."""

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
    graph_base_models: list[str] | None = Field(
        None,
        description=(
            "The base-model files the graph a run would submit names, as the "
            "graph spells them (folders included), for a card whose own "
            "`models` name no base model: its name was never recorded or was "
            "forgotten, but the workflow file or a picture's embedded graph "
            "still says which file it loads. `[]` is a graph that was read and "
            "loads no base model (an upscaler); `null` is one that was not "
            "read - the card names its base model, or has no graph to read."
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
# SQLite's INTEGER ceiling. An id past it is not a picture that is missing, it
# is a request no row could answer, and the driver says so with a 500.
MAX_PICTURE_ID = 2**63 - 1
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

# The extension a picture keeps when it is uploaded into ComfyUI's input folder.
# Anything else is dropped rather than carried into a name another program
# resolves as a path.
_UPLOAD_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
_PIXEL_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


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
    because SQLite reuses a vault id the moment the next import lands. A
    client may pin by ``picture_id`` instead and the server stores that
    picture's content (#1457): the grid projection a picker hands back carries
    no ``pixel_sha``, and fetching one per pin is a round trip to learn a value
    the client never needs to hold.
    """

    mode: Literal["selection", "picker", "fixed"]
    pixel_sha: str | None = Field(None, max_length=64)
    picture_id: int | None = Field(None, ge=1, le=MAX_PICTURE_ID)


class CardPictureInputs(BaseModel):
    """``PUT /workflows/{key}/inputs``: this card's whole picture-input setup."""

    inputs: list[CardPictureInput] = Field(default_factory=list, max_length=MAX_INPUTS)

    @field_validator("inputs")
    @classmethod
    def _fixed_names_a_picture(
        cls, value: list[CardPictureInput]
    ) -> list[CardPictureInput]:
        for entry in value:
            if entry.mode == "fixed" and not (
                entry.pixel_sha or entry.picture_id is not None
            ):
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


class RunLoraSlot(BaseModel):
    """One LoRA slot a run skips (#1478), addressed as :class:`RunLora` addresses it.

    A skip is for THIS run: the loader is bypassed on the run's own copy of the
    graph and the stored workflow keeps it. Editing the workflow for good is
    ``PUT /workflows/{key}/lora-chain``.
    """

    node_id: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    field: str = Field("lora_name", min_length=1, max_length=MAX_LABEL_LENGTH)


class RunValue(ParameterAddress):
    """One parameter this run sets, over the card's defaults."""

    value: bool | int | float | str


class RunInput(ParameterAddress):
    """One picture input this run fills, addressed the way a card addresses.

    ``picture_id`` is the picture it gets on every submission of this run;
    ``null`` says "my selection goes here", which is how a caller picks the
    input a selection feeds when more than one is open.
    """

    picture_id: int | None = Field(None, ge=1, le=MAX_PICTURE_ID)


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

    picture_ids: list[Annotated[int, Field(le=MAX_PICTURE_ID)]] = Field(
        default_factory=list, max_length=MAX_RUN_PICTURES
    )
    saved_recipe_id: int | None = None
    workflow_key: str | None = None

    target: str | None = None

    prompt: str | None = Field(None, max_length=MAX_PROMPT_LENGTH)
    negative: str | None = Field(None, max_length=MAX_PROMPT_LENGTH)
    loras: list[RunLora] = Field(default_factory=list, max_length=MAX_RUN_LORAS)
    # LoRA slots this run goes without (#1478): each loader is bypassed on the
    # run's copy, its consumers reading its inputs. Applied to every group of
    # the run whose graph has that slot; a slot no graph has is a 400.
    skip_loras: list[RunLoraSlot] = Field(
        default_factory=list, max_length=MAX_RUN_LORAS
    )
    values: list[RunValue] = Field(default_factory=list, max_length=MAX_DEFAULTS)

    count: int = Field(1, ge=1, le=MAX_RUNS_PER_REQUEST)
    seed_mode: Literal["new", "keep", "fixed"] = "new"
    seed: int | None = Field(None, ge=0, le=MAX_SEED_64)
    destination: RunDestination | None = None
    # What fills each picture input of the card (#1457), first answer wins:
    # an entry here, a `fixed` pin whose picture is still kept, a stored
    # `selection` fed from `picture_ids`, and - once those have been applied
    # to every input - the one input still open when exactly one is, which the
    # selection fills with nothing here saying so. That last rule is why this
    # field is optional: it is needed only where two or more inputs are open,
    # and for a picture picked for one run. See `workflow_inputs.resolve_fills`.
    inputs: list[RunInput] = Field(default_factory=list, max_length=MAX_INPUTS)

    @field_validator("inputs")
    @classmethod
    def _one_fill_per_input(cls, value: list[RunInput]) -> list[RunInput]:
        """One entry per address, and the selection sent to one input at most.

        A selection is the run's repeat axis, so two inputs both taking it
        would have to agree on which picture each submission reads - which is
        two selections, and the request has one.
        """
        _one_row_per_address(value)
        if sum(entry.picture_id is None for entry in value) > 1:
            raise ValueError("At most one input can take the selection.")
        return value

    # A new run is a new picture, NOT a variant of the one it was made from
    # (v1.12 B7). The shipped run routes stack by default and this one does
    # not: those replay one picture's own recipe, where the output genuinely
    # is another take of that picture, while this runs a card and the pictures
    # that named it are its source rather than its subject.
    @field_validator("picture_ids")
    @classmethod
    def _one_run_per_picture(cls, value: list[int]) -> list[int]:
        """Drop repeats, keeping first-seen order.

        The retired `run_i2i` de-duplicated its `picture_ids` (`_int_list`) and
        this route grouped whatever it was handed, so `[5, 5]` put picture 5 in
        a group twice. It never multiplied the submissions - `count` governs
        those - but the group REPORTED covering a picture twice, which is a
        wrong answer to "what would this run", and the pre-flight and the run
        share this body.
        """
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _a_slot_is_filled_or_skipped(self) -> "RunRequest":
        """A slot named both in ``loras`` and in ``skip_loras`` is refused (422).

        Filling a slot and skipping it are opposite answers to one question,
        and picking either would be answering a request nobody made.
        """
        filled = {(item.node_id, item.field) for item in self.loras}
        both = [
            f"{item.field} on node {item.node_id}"
            for item in self.skip_loras
            if (item.node_id, item.field) in filled
        ]
        if both:
            raise ValueError(
                f"LoRA slot {', '.join(both)} is both set and skipped; name it in "
                "loras or in skip_loras, not both."
            )
        return self

    stack: bool = False
    # `StrictBool`, not `bool`: consent to running a graph nobody could inspect
    # is the CWE-829 control (review finding R3b), and a lax cast reads `"yes"`,
    # `"on"`, `"y"` and `1` as an acknowledgement the owner never gave - `"false"`
    # included, since every non-empty string is truthy. The route #1410 retired
    # required the literal JSON `true` (`payload.get(...) is True`); this is the
    # same rule, declared instead of hand-written.
    allow_unchecked: StrictBool = False
    client_id: str | None = Field(None, max_length=MAX_LABEL_LENGTH)


class RunPictureInput(ParameterAddress):
    """One picture input of the card a group runs, and what fills it.

    ``fill`` is the server's answer and the client's to show, never to
    re-derive: ``request`` (this body's entry), ``fixed`` (the card's pin),
    ``selection`` (the group's pictures, one per submission), ``graph`` (open,
    and the file the graph already names is on this ComfyUI) or ``null`` (open
    and unfilled, which ``picture_input_unfilled`` names).

    ``picture_id`` is the one picture a ``request`` or ``fixed`` fill feeds; a
    pin whose content no kept picture holds any more has it ``null`` and
    ``picture_missing`` true, which is an empty slot to choose again.
    """

    title: str
    mode: Literal["selection", "picker", "fixed"]
    pixel_sha: str | None = None
    picture_id: int | None = None
    picture_missing: bool = False
    fill: Literal["request", "fixed", "selection", "graph"] | None = None


class RunGroup(BaseModel):
    """One card a request resolved to, and whether it would run.

    ``reasons`` empty is the only thing that means "this would run". Each
    reason is a code and its payload, so a panel can act on it rather than
    print it. ``substitutions``, ``bypassed_loras`` and ``unplaced_loras`` are
    not reasons: they say what this run will do differently from what the graph
    or the recipe says, which is a fact to report rather than a refusal to act
    on.
    """

    workflow_key: str
    source: str | None = None
    source_picture_id: int | None = None
    picture_ids: list[int] = Field(default_factory=list)
    runs: int = 0
    reasons: list[dict] = Field(default_factory=list)
    # A model loaded from a different file than the graph names, because the copy
    # it names is gone and another copy of the same bytes is not (#1439). Never
    # silent: a run that quietly loaded a different file makes its own lineage a
    # lie, so it is reported here on the pre-flight and on the run alike, and
    # logged. The stored recipe keeps the name it recorded.
    substitutions: list[dict] = Field(default_factory=list)
    # A LoRA loader taken out of the graph because this ComfyUI does not have
    # its file (#1463). A LoRA is optional - the graph runs without it - so it
    # is bypassed rather than refused the way a missing checkpoint is. Reported
    # for the same reason a substitution is: a picture made without the
    # character LoRA the owner expected, with nothing said, is worse than a
    # refusal, and the pre-flight is where they are told BEFORE the run.
    # A slot the owner asked this run to skip (`skip_loras`, #1478) is reported
    # here too, marked `"requested": true`; the automatic ones are `false`.
    bypassed_loras: list[dict] = Field(default_factory=list)
    # A saved recipe's LoRA this run does not apply (#1478): the workflow has no
    # slot left for it, or the shelf cannot identify its file. Matched to slots
    # by digest, then basename, then any free slot, and only a LoRA with nowhere
    # to go lands here - reported rather than dropped, because the credit
    # matcher that said "matches your saved recipe" counted it. Not a reason:
    # the run goes ahead. Unlike `bypassed_loras` it is NOT cleared on a group
    # that is refused, because it is a fact about the recipe and the graph and
    # holds whatever ComfyUI says: `[{filename, sha256, node_id, reason}]`.
    unplaced_loras: list[dict] = Field(default_factory=list)
    # Every picture input of the card, enumerated from the graph this run
    # resolved with the card's stored setup laid over it (#1457). It is the
    # card's WHOLE set, which is what makes the whole-set
    # `PUT /workflows/{key}/inputs` safe to call after reading it: a client
    # never writes back a set it has not seen.
    picture_inputs: list[RunPictureInput] = Field(default_factory=list)
    # A custom node this ComfyUI lacks, replaced by what PixlStash already does
    # (#1463): a seed node, whose link becomes a literal the run's
    # own seed pass then writes, or a text node, whose string is inlined.
    # Reported on the same terms as a bypass: the
    # graph that runs is not the one the card names, and the owner is told so
    # before the run rather than after.
    replaced_nodes: list[dict] = Field(default_factory=list)


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


class WorkflowExport(BaseModel):
    """``GET /workflows/{key}/export``: a ComfyUI file, minus every run of it.

    ``removed`` names the CATEGORIES that were taken out and never the values:
    the point of the route is that those values do not travel, and an answer
    listing the prompt it withheld would be the leak the scrub exists to stop.
    """

    filename: str = Field(description="What to call the file when it is saved.")
    workflow: dict = Field(description="The ComfyUI API-format graph.")
    removed: list[str] = Field(
        default_factory=list,
        description="What the export left out, as categories, never values.",
    )
    source: str = Field(
        description="Where the graph was resolved from: file, picture or instance."
    )


class WorkflowRunnableGraph(BaseModel):
    """``GET /workflows/{key}/graph``: the graph as it runs, for this owner's ComfyUI.

    The unscrubbed sibling of :class:`WorkflowExport`. Duplicate writes the
    same graph into a file; this hands it to the ComfyUI-PixlStash node, which
    opens it in the ComfyUI editor when the Workflow tab's *Open in ComfyUI*
    sends ComfyUI there with ``?pixlstash_workflow=<key>``.
    """

    name: str = Field(description="What to call the workflow in ComfyUI.")
    workflow: dict = Field(description="The ComfyUI API-format graph.")
    source: str = Field(
        description="Where the graph was resolved from: file, picture or instance."
    )
    seedless: bool = Field(
        False,
        description=(
            "True when the graph came from a stored recipe, whose seeds are "
            "null by design: set one before queueing it."
        ),
    )
    forgotten: int = Field(
        0,
        description="How many model names the library could no longer name.",
    )


class WorkflowFile(BaseModel):
    """One workflow file this machine now holds, and the card it landed on."""

    name: str = Field(description="What the file is called in the user folder.")
    workflow_key: str | None = Field(
        None,
        description="The card it was filed on, or null when it could not be filed.",
    )


class InsertedLoader(WorkflowFile):
    """The file written by ``POST /workflows/{key}/insert-lora-loader``."""

    node_id: str = Field(description="The id the new loader has in the graph.")
    class_type: str = Field(description="Which loader node was added.")


class LoraChainSource(BaseModel):
    """The top rail: the output the first LoRA loader reads."""

    node_id: str
    class_type: str | None = None
    outputs: list[str] = Field(
        default_factory=list,
        description="What this node hands the chain: MODEL, and CLIP when it is "
        "the CLIP source too.",
    )


class LoraChainClipSource(BaseModel):
    """Where the chain's CLIP comes from, when that is not the model source."""

    node_id: str
    class_type: str | None = None


class LoraChainConsumer(BaseModel):
    """One input reading the end of the chain."""

    node_id: str
    class_type: str | None = None
    field: str
    type: str


class LoraChainSink(BaseModel):
    """The bottom rail: what reads the chain's result."""

    summary: str | None = Field(
        None, description="`KSampler #7 reads model · 2 text encoders read clip`."
    )
    consumers: list[LoraChainConsumer] = Field(default_factory=list)


class LoraChainLoader(BaseModel):
    """One LoRA slot of the chain, in the order a run applies it."""

    node_id: str
    class_type: str | None = None
    field: str = Field(
        description="The slot's widget; a stacker read untyped lists one row per "
        "slot on the same node."
    )
    filename: str = Field(description="The raw widget value, or a digest.")
    name: str = Field(description="The basename without its extension.")
    strength: float | None = None
    strength_clip: float | None = None
    sha256: str | None = Field(
        None, description="The shelf LoRA this slot loads, when exactly one matches."
    )
    on_shelf: bool = False


class LoraChain(BaseModel):
    """``GET /workflows/{key}/lora-chain``: the LoRA chain as the editor shows it."""

    workflow_key: str
    editable: bool = False
    refusal: str | None = Field(
        None, description="Why the chain can only be looked at, when it can."
    )
    source: LoraChainSource | None = None
    clip_source: LoraChainClipSource | None = None
    sink: LoraChainSink = Field(default_factory=LoraChainSink)
    loaders: list[LoraChainLoader] = Field(default_factory=list)
    added_loader_class: str | None = Field(
        None, description="The loader class Add a LoRA would insert."
    )
    branch_note: str | None = Field(
        None,
        description=(
            "Why the chain stops before some of the workflow's loaders: the "
            "model branches at its end, so those loaders are left as they are."
        ),
    )


class LoraChainEntry(BaseModel):
    """One loader of the chain as the owner left it.

    An existing loader by ``node_id``, or a new one by the shelf ``sha256`` of
    the LoRA it loads. An existing loader cannot be re-pointed at another LoRA:
    a ``sha256`` beside a ``node_id`` must be the one it already loads.
    """

    node_id: str | None = Field(None, min_length=1, max_length=MAX_LABEL_LENGTH)
    sha256: str | None = Field(None, min_length=1, max_length=64)
    strength: float | None = Field(None, ge=-10.0, le=10.0)

    @model_validator(mode="after")
    def _names_a_loader(self) -> "LoraChainEntry":
        if self.node_id is None and self.sha256 is None:
            raise ValueError("an entry names an existing node_id or a shelf sha256")
        return self


class LoraChainEdit(BaseModel):
    """``PUT /workflows/{key}/lora-chain``: the whole chain, in apply order."""

    entries: list[LoraChainEntry] = Field(
        default_factory=list, max_length=MAX_RUN_LORAS
    )
    name: str | None = Field(None, max_length=MAX_NAME_LENGTH)
    dry_run: StrictBool = False


class LoraChainChange(BaseModel):
    """One line of what a chain edit changes, in the owner's words."""

    kind: Literal["deleted", "added", "moved", "strength", "rewired"]
    node_id: str
    text: str


class LoraChainSaved(BaseModel):
    """What a chain edit changed, and the new card a write filed it on."""

    dry_run: bool = False
    name: str | None = Field(None, description="The file written; null on a dry run.")
    workflow_key: str | None = Field(
        None, description="The NEW card; null on a dry run."
    )
    changes: list[LoraChainChange] = Field(default_factory=list)


class SwapModel(BaseModel):
    """One shelf row the clone dialog can offer or has resolved a file to."""

    id: int
    filename: str
    display_name: str | None = None
    base_model: str | None = Field(
        None,
        description=(
            "The known base model the clone reasons about: the shelf's identified "
            "label unless it is a fuzzy guess, else the stored one."
        ),
    )
    file_kind: str
    family: str | None = Field(
        None,
        description="The file's own layout or architecture (clip_l, t5_xxl, flux1).",
    )


class SwapSlot(BaseModel):
    """One model file the card's graph names, and the shelf row it is, if any."""

    filename: str = Field(description="The name exactly as the graph holds it.")
    kind: str = Field(
        description="checkpoint, unet, vae, clip, lora, controlnet, or the widget."
    )
    model: SwapModel | None = Field(
        None, description="The one shelf row of that name, or null."
    )


class SwapProposal(BaseModel):
    """A support file recipes on this machine have run beside the new checkpoint."""

    id: int
    filename: str
    display_name: str | None = None
    family: str | None = None
    via: str = Field(
        description=(
            "Which step answered: checkpoint (ran with this checkpoint), "
            "base_model (with another of the same base model), family "
            "(with another of the same architecture) or declared (nothing "
            "has run with it: its file layout is one the architecture "
            "declares, which is not evidence it suits)."
        )
    )
    recipes: int = Field(description="How many recipes name the two together.")
    history_runs: int = Field(
        default=0,
        description=(
            "How many runs in ComfyUI's own history, as of the last workflow "
            "pull, loaded the two together."
        ),
    )


class SwapFlag(BaseModel):
    """A LoRA or ControlNet trained on another family than the new checkpoint."""

    filename: str
    kind: str
    base_model: str
    family: str
    modality: str | None = None


class ModelSwapOptions(BaseModel):
    """``GET /workflows/{key}/model-swap``: what the clone dialog draws."""

    slots: list[SwapSlot]
    checkpoints: list[SwapModel]
    vaes: list[SwapModel]
    text_encoders: list[SwapModel]
    checkpoint_family: str | None = None
    checkpoint_modality: str | None = None
    proposals: dict[str, list[SwapProposal]] = Field(
        default_factory=dict,
        description="Per support kind (vae, text_encoder); empty without a checkpoint.",
    )
    flags: list[SwapFlag] = Field(default_factory=list)


# Ceiling on one clone's swap map. A graph names a handful of model files; the
# bound is here so a hand-made request cannot post an unbounded map.
MAX_SWAPS = 64


class CloneWithModels(BaseModel):
    """``POST /workflows/{key}/clone-with-models``."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    swaps: dict[str, str] = Field(
        description="The graph's filename -> the filename to load instead."
    )

    @field_validator("swaps")
    @classmethod
    def _model_files_only(cls, swaps: dict[str, str]) -> dict[str, str]:
        # A replacement without a model extension would be nulled out of the
        # structural hash, so the clone would fold onto the original's card.
        if not swaps or len(swaps) > MAX_SWAPS:
            raise ValueError(f"swaps must name 1 to {MAX_SWAPS} files")
        for was, now in swaps.items():
            if not was.strip() or not now.strip() or len(now) > 1024:
                raise ValueError("swaps must map a filename to a filename")
            if not now.lower().endswith(MODEL_EXTENSIONS):
                raise ValueError(f"{now!r} is not a model file")
        return swaps


class ClonedWorkflow(WorkflowFile):
    """The file ``POST /workflows/{key}/clone-with-models`` wrote."""

    swapped: list[dict] = Field(description="One entry per loader field rewritten.")
    unswapped: list[dict] = Field(
        description="Swaps that did not land: not_in_graph or not_on_comfyui."
    )
    verified: bool = Field(
        description=(
            "Whether ComfyUI listed every name written; false means at least "
            "one went in unchecked."
        )
    )


class WorkflowDeleted(BaseModel):
    """Which file went to the trash, and which card it came off."""

    deleted: str
    workflow_key: str


@dataclass
class Feed:
    """Where one filled picture input goes in a graph, and what feeds it.

    ``picture_id`` ``None`` is the group's selection: one of its pictures per
    submission, which is what makes the group repeat per picture.

    ``by_id`` is a ComfyUI-PixlStash picture loader, which is handed the
    picture's id rather than an uploaded file: it fetches the picture itself.
    """

    targets: list[dict]
    picture_id: int | None
    by_id: bool = False


@dataclass
class Plan:
    """One run request, resolved: what it would do and what it asked ComfyUI.

    Not a response model - it carries the graphs and the ``object_info`` map,
    which are megabytes and are nobody's business outside the two handlers.

    ``files`` is every picture a submission uploads, ``{picture_id: (path,
    upload name)}``, resolved here so a picture that cannot be handed over is
    a refusal before the first byte leaves - never half way through a batch.
    """

    body: RunRequest
    groups: list[RunGroup]
    submittable: list[tuple[dict, RunGroup, list[Feed]]]
    comfyui_url: str
    object_info: dict | None
    object_info_error: str | None
    files: dict[int, tuple[str, str]] = dataclass_field(default_factory=dict)


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


def _covers(covers) -> list[WorkflowCover]:
    """A card's cover strip: a cache-busted URL plus its stored crop rectangle.

    The same URL and the same cache key the grid's own tiles use
    (``routes/pictures/_thumbnails.py``), so a cover the browser already holds
    is not fetched twice and a regenerated bitmap is not served stale.

    The crop rectangle rides along rather than being fetched per cover: the
    ranked query already reads this row, so the three columns cost nothing
    beyond the bytes (#1465).
    """
    strip = []
    for cover in covers:
        version = ImageUtils.thumbnail_cache_version(
            cover.thumbnail_width, cover.thumbnail_height, cover.orientation
        )
        strip.append(
            WorkflowCover(
                url=f"/pictures/thumbnails/{cover.picture_id}.webp?v={version}",
                picture_id=cover.picture_id,
                thumbnail_width=cover.thumbnail_width,
                thumbnail_height=cover.thumbnail_height,
                square_crop_x=cover.square_crop_x,
                square_crop_y=cover.square_crop_y,
                square_crop_side=cover.square_crop_side,
            )
        )
    return strip


# The last resort, when a card has no base model to be named after and no type
# to say what it does. Never shown bare where two cards reach it: the grid
# numbers every generated name it would otherwise print twice
# (:func:`_display_names`), so the name row, the card's only identifying text,
# always tells cards apart. It used to be "Untitled workflow", printed forty
# times on a grid of forty such cards.
UNNAMED_CARD = "Workflow"


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


# ComfyUI's own template names read "Krea 2: Text to Image", and a workflow
# library is read beside ComfyUI rather than instead of it, so the type is
# spelled the way the person already sees it spelled.
#
# **One map, and it is served rather than mirrored.** The card shows its type
# twice - in a generated name and in its own chip - and a second copy of these
# labels on the client is the drift this file has already been bitten by once
# (`CHECKPOINT_WIDGETS`). `WorkflowCard.type_label` carries the answer, so the
# chip and the name are the same string by construction.
_TYPE_LABELS = {
    "txt2img": "Text to Image",
    "img2img": "Image to Image",
    "inpaint": "Inpaint",
    "outpaint": "Outpaint",
    "upscale": "Upscale",
}


# What a post-processing group is called in a generated name. ComfyUI's own
# node name for the detailer, because that is what the person sees in the graph;
# `upscale` has no single node to be named after and reads as the plain word.
#
# A group this build does not know - a value written by a newer PixlStash and
# read back here - is left out rather than printed raw: a name is the card's
# only identifying text, and an unexplained token in it is worse than a shorter
# name.
_SPECIAL_LABELS = {
    "upscale": "Upscale",
    "face_detailer": "FaceDetailer",
}


def _base_model_slot(models):
    """The slot a card is named after, or ``None`` if it loads no base model."""
    for kind in BASE_MODEL_KINDS:
        for slot in models:
            if slot.kind == kind and slot.name:
                return slot
    return None


def _specials_suffix(card) -> str:
    """`` + FaceDetailer`` and friends, or the empty string.

    Empty for a card whose document has not been read for its groups
    (``specials`` is ``None``) as well as for one that genuinely has none. The
    two are not the same fact and the payload keeps them apart, but a name has
    nowhere to say "not known yet" and claiming the shorter name is the honest
    thing to do with an unknown.

    A group that IS the workflow's type is left off: an upscale workflow is
    already called ``Upscale`` by ``_TYPE_LABELS``, and "Upscale + Upscale" says
    one fact twice.
    """
    return "".join(
        f" + {_SPECIAL_LABELS[group]}"
        for group in card.specials or ()
        if group in _SPECIAL_LABELS and group != card.workflow_type
    )


def _display_name(card, models=()) -> str:
    """What a card is called: the owner's name, else its file, else its models.

    That order is how much the name is *theirs*: one they typed, then the file
    they dropped, then a description built here.

    The built one is **the model, then what the workflow does, then what it
    does extra** - ``Krea 2: Text to Image + FaceDetailer`` - because the model
    is what a person calls the workflow, the verb only tells two of them apart
    once the model already has, and the post-processing is what separates two
    cards that agree on both. The card contract, this fallback chain included,
    is ``docs/integration_architecture.md`` §2.

    **The model is named as the shelf names it, not as the file is spelled.**
    ``realvisxl`` is a filename stem; ``Krea 2`` is what the trainer wrote in
    the header or what the owner typed, and it is in the same database as the
    card. A model this machine has never scanned still falls back to its stem.

    The suffix is on the generated name only. A card named after its workflow
    FILE keeps the owner's spelling untouched: appending to a name somebody
    chose is inventing, not describing.
    """
    if card.name:
        return card.name
    if card.file_name:
        stem = card.file_name.rsplit("/", 1)[-1]
        return stem[: -len(".json")] if stem.lower().endswith(".json") else stem
    slot = _base_model_slot(models)
    # The shelf's name first. Stripped, because `display_name` is free text off
    # a safetensors header or a text field, and a name of three spaces renders
    # the row blank exactly as the empty stem below would.
    stem = ((slot.title or "").strip() or _model_stem(slot.name)) if slot else ""
    label = _TYPE_LABELS.get(card.workflow_type)
    if not stem:
        # No base model: every name forgotten, a graph that loads none, or a
        # stem that came back empty (these are third-party widget values, so
        # the whole name can be an extension or end in a separator). Named for
        # what it does rather than after its VAE; the grid numbers the
        # duplicates this makes.
        return (label or UNNAMED_CARD) + _specials_suffix(card)
    named = f"{stem}: {label}" if label else stem
    return named + _specials_suffix(card)


def _display_names(figures) -> dict[str, str]:
    """Every card's name, by key, with no generated name printed twice.

    A name the owner typed or a workflow file's is left alone however many
    cards share it: renaming what somebody chose is inventing. Generated names
    that collide are numbered ``Text to Image (2)``, ``(3)``, in key order so a
    card keeps its number from one read to the next.
    """
    # ponytail: key order is stable across reads but a new card can shift the
    # numbers after it; store a sequence on the card if that ever matters.
    names = {}
    generated = {}
    for figure in sorted(figures, key=lambda f: f.card.workflow_key):
        card = figure.card
        name = _display_name(card, figure.models)
        names[card.workflow_key] = name
        if not card.name and not card.file_name:
            generated.setdefault(name, []).append(card.workflow_key)
    for name, keys in generated.items():
        for number, key in enumerate(keys[1:], start=2):
            names[key] = f"{name} ({number})"
    return names


# What a workflow file may be before the grid declines to parse it. A real one
# is tens to hundreds of kilobytes; the largest in this repo's own fixtures is
# under 300 KB, and the watched folder is a place anything can be dropped. The
# size is already in hand from the ``stat`` the cache key needs, so refusing
# costs nothing - and this read now happens on the grid and on every workflow
# write, where it used to happen on neither.
# ponytail: one entry per file version; stale versions age out of the LRU.
@functools.lru_cache(maxsize=256)
def _file_model_widgets(path: str, mtime_ns: int, size: int) -> tuple:
    """``((widget, filename), ...)`` read off one stored workflow file (#1466).

    Keyed on mtime and size exactly as ``comfyui._describe_workflow`` is, so
    the grid parses each file once per version of it rather than once per
    request, and a file that will not read is logged once rather than on every
    open of the view.
    """
    try:
        document = _load_workflow_json(path)
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning(
            "Workflow file %s will not load, so the card it is the whole of "
            "is described with no models: %s",
            path,
            exc,
        )
        return ()
    try:
        return tuple(loaded_model_widgets(document))
    except Exception as exc:
        # The reader indexes into whatever the file holds, so a malformed one
        # (a `nodes` entry that is not a dict, a non-list `widgets_values`)
        # raises something other than a ValueError. A card described without
        # its models is the failure this whole function exists to soften; it
        # must not be one that takes the grid down.
        logger.warning(
            "Could not read the models out of workflow file %s, which failed "
            "with %s; its card is described with none: %s",
            path,
            type(exc).__name__,
            exc,
        )
        return ()


def _file_models(file_name: str) -> tuple:
    """:func:`read_grid`'s reader: the models one stored file loads.

    The I/O half of #1466, here rather than in the service because the folder
    a workflow file lives in is this layer's - the same split
    :func:`_source_graph_for` already keeps, where the route does the reads
    and the service decides what they mean.
    """
    path, _source = _resolve_workflow_path(file_name)
    if not path:
        return ()
    try:
        stat = os.stat(path)
    except OSError as exc:
        logger.warning(
            "Could not stat workflow file %s, so the card it is the whole of "
            "is described with no models: %s",
            path,
            exc,
        )
        return ()
    return _file_model_widgets(path, stat.st_mtime_ns, stat.st_size)


def _slot_models(slots) -> list[WorkflowSlotModel]:
    return [
        WorkflowSlotModel(
            name=slot.name,
            title=slot.title,
            quant=slot.quant,
            icon=slot.icon,
            base_model=slot.base_model,
            base_model_folded=slot.base_model_folded,
            kind=slot.kind,
            mark=slot.mark,
            slot_label=slot.label,
        )
        for slot in slots
    ]


def _slot_names(figure) -> list[str]:
    """What a card loads, as :func:`_stack_members` compares cards by."""
    names = []
    for slot in [*figure.models, *figure.loras]:
        if not slot.name or (slot.kind == "lora" and slot.mark == RECIPE):
            continue
        named = (slot.title or "").strip() or slot.name
        names.append(f"{named} {slot.quant}" if slot.quant else named)
    return names


def _stack_members(
    figure, figures_by_key, card_names=None
) -> list[WorkflowStackMember]:
    """The stack *figure* is in, each member named and told apart.

    *card_names* is :func:`_display_names` over the grid, so a member reads
    as its own card does; left out, each is named alone. A model the member's own
    name already says (the checkpoint a generated name starts with) is not
    said again.
    """
    if not figures_by_key or figure.stack_size < 2:
        return []
    figures = [figures_by_key.get(key) for key in figure.member_keys]
    if any(member is None for member in figures):
        logger.warning(
            "Stack of card %s names a member the grid has no figures for; "
            "its members are not listed: %s",
            figure.card.workflow_key,
            figure.member_keys,
        )
        return []
    loads = [_slot_names(member) for member in figures]
    shared = set.intersection(*(set(names) for names in loads))
    members = []
    for position, (member, names) in enumerate(zip(figures, loads)):
        name = (card_names or {}).get(member.card.workflow_key) or _display_name(
            member.card, member.models
        )
        members.append(
            WorkflowStackMember(
                key=member.card.workflow_key,
                name=name,
                sets_apart=list(
                    dict.fromkeys(n for n in names if n not in shared and n not in name)
                ),
                differs_by=member.differs_by if position else [],
            )
        )
    return members


def _card(figure, defaults=(), figures_by_key=None, names=None) -> WorkflowCard:
    """Render one card's figures in the shape ``workflowCard.js`` documents.

    *figures_by_key* (every card of the grid, by key) is what names the other
    members of its stack (``members``); left out, the card lists none.
    *names* is :func:`_display_names` over the same grid, which is what keeps
    two generated names apart; left out, the card is named alone.
    """
    return WorkflowCard(
        key=figure.card.workflow_key,
        name=(names or {}).get(figure.card.workflow_key)
        or _display_name(figure.card, figure.models),
        type=figure.card.workflow_type,
        type_label=_TYPE_LABELS.get(figure.card.workflow_type),
        imported=figure.card.imported,
        hidden=figure.card.hidden,
        models=_slot_models(figure.models),
        loras=_slot_models(figure.loras),
        recipe_loras=[
            WorkflowRecipeLora(
                name=lora.name,
                recipes=lora.recipes,
                character_id=lora.character_id,
                character_name=lora.character_name,
            )
            for lora in figure.recipe_loras
        ],
        specials=None if figure.card.specials is None else list(figure.card.specials),
        differs_by=figure.differs_by,
        picture_count=figure.pictures,
        rating=figure.rating,
        covers=_covers(figure.covers),
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
        members=_stack_members(figure, figures_by_key, names),
        stack_id=figure.stack_id,
        ghosts=figure.ghosts,
        model_ghosts=figure.model_ghosts,
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
    # On `/workflows` itself since B9 (#1410) retired the topology list that
    # used to hold the prefix. The detail and picture routes are
    # `/workflows/{workflow_key}` and `/workflows/{workflow_key}/pictures`.
    #
    # The payload is `frontend/src/utils/workflowCard.js`'s documented card,
    # which is already merged and already has components reading it; see the
    # `WorkflowCard` model.

    @router.get(
        "/workflows",
        summary="The Workflows grid",
        description=(
            "Every workflow card this machine holds, in cover-rank order, one "
            "card per stack. Hidden cards and one-offs are counted rather than "
            "listed, unless the two flags ask for them."
        ),
        response_model=WorkflowCards,
    )
    def list_cards(
        request: Request,
        include_hidden: bool = Query(
            False,
            description=(
                "List hidden cards too - the Filters panel's *Show hidden "
                "workflows*. `hidden` still counts them either way."
            ),
        ),
        include_one_offs: bool = Query(
            False,
            description=(
                "List one-offs too - *Hide one-offs* unticked. `one_offs` "
                "still counts them either way."
            ),
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        grid = read_grid(
            _hub(),
            server.vault,
            include_hidden=include_hidden,
            include_one_offs=include_one_offs,
            file_models=_file_models,
        )
        figures_by_key = by_key(grid.figures)
        names = _display_names(grid.figures)
        return WorkflowCards(
            cards=[
                _card(figure, figures_by_key=figures_by_key, names=names)
                for figure in grid.cards
            ],
            one_offs=grid.one_offs,
            hidden=grid.hidden,
        )

    @router.get(
        "/workflows/{workflow_key}",
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
        grid = read_grid(hub, server.vault, file_models=_file_models)
        figure = grid.figure(workflow_key)
        if figure is None:
            raise HTTPException(status_code=404, detail="Unknown workflow card.")
        card = figure.card
        pins = key_pins(hub, workflow_key)
        graph_models = (
            None if _base_model_slot(figure.models) else _graph_base_models(card)
        )
        return WorkflowCardDetail(
            card=_card(
                figure,
                card_defaults(hub, server.vault, card),
                by_key(grid.figures),
                _display_names(grid.figures),
            ),
            notes=card.notes,
            hidden=card.hidden,
            variants=_card_variants(hub, server.vault, card),
            pins=None
            if pins is None
            else [
                ParameterAddress(slot_label=slot_label, input_name=input_name)
                for slot_label, input_name in pins
            ],
            graph_base_models=graph_models,
        )

    def _graph_base_models(card) -> list[str] | None:
        """The base-model files the card's runnable graph names, in order.

        Read off the same source a run would submit (:func:`_source_graph_for`:
        the workflow file, then the best picture's embedded graph, then a stored
        instance), because that is the graph whose missing file matters. Only
        asked when the card has no name for its base model, so the grid never
        pays for it and an opened card pays once. A name the hub forgot reads
        back as :data:`~run_service.FORGOTTEN_MODEL` and is left out: it names
        nothing a person could look for. ``None`` when there is no graph to
        read, which is not the same answer as a graph that loads none.
        """
        try:
            source, _reason = _source_graph_for(card)
        except (RecursionError, WorkflowGraphError, OSError, ValueError) as exc:
            logger.warning(
                "Card %s: could not read its runnable graph for the base model "
                "it names; the Workflow tab shows none: %s",
                card.workflow_key,
                exc,
            )
            return None
        if source is None:
            return None
        found = []
        for widget, value in loaded_model_widgets(source.graph):
            if (
                widget in CHECKPOINT_WIDGETS
                and value != run_service.FORGOTTEN_MODEL
                and value not in found
            ):
                found.append(value)
        return found

    @router.get(
        "/workflows/{workflow_key}/pictures",
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
    # `GET /workflows` rather than trusting what a write carried back.

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
            "library, because a picture is a picture in one library. A fixed "
            "input names its picture by pixel_sha or by picture_id, and one "
            "given by id is stored as that picture's content. Replaces the "
            "card's whole set, so read it first: every run pre-flight returns "
            "it as picture_inputs."
        ),
        response_model=CardPictureInputs,
        responses={
            400: {"description": "A pinned picture_id is not a kept picture."},
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
        # A pin by id is stored as that picture's CONTENT, so it survives the
        # id being reused. Refused rather than stored empty when the picture is
        # not a kept one: a pin that silently names nothing is a run that later
        # asks for a picture the owner thinks they already chose.
        by_id = [entry.picture_id for entry in payload.inputs if entry.picture_id]
        kept = read_kept_picture_files(server.vault, by_id) if by_id else {}
        entries = []
        for entry in payload.inputs:
            pixel_sha = entry.pixel_sha
            if entry.mode == "fixed" and entry.picture_id is not None:
                pixel_sha = (kept.get(entry.picture_id) or (None, None))[1]
                if not pixel_sha:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Picture {entry.picture_id} is not a kept picture "
                            "of this library, so it cannot be pinned."
                        ),
                    )
            entries.append(
                entry.model_copy(update={"pixel_sha": pixel_sha, "picture_id": None})
            )
        replace_picture_inputs(
            hub,
            library_uuid,
            workflow_key,
            [
                (entry.slot_label, entry.input_name, entry.mode, entry.pixel_sha)
                for entry in entries
            ],
        )
        _announce(request, [workflow_key], "changed")
        return CardPictureInputs(inputs=entries)

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

    def _embedded_graph(
        picture_id: int, object_info: dict | None = None
    ) -> dict | None:
        """This picture's runnable graph, or ``None`` if it has none.

        A picture whose file has been moved off the disk is not a source and
        must not be an error: the resolver is walking candidates, and the best
        picture of a card being unreadable is exactly when the next tier is
        wanted. ``_load_embedded_api_prompt`` raises a 404 for a missing file
        and a 500 for an unreadable one, both of which are caught here and
        logged - the run says ``no_runnable_source`` if nothing else answers.

        ``object_info`` lets it rebuild a picture that carries only ComfyUI's
        editor graph. **A refusal returns ``None`` and does not raise**: this
        walks candidates, so "this one cannot be rebuilt" means take the next,
        exactly as "this one has no graph" does. The reasons are logged rather
        than raised for the same reason - they are about a candidate, not about
        the run.
        """
        # The `try` covers the READ and nothing else. Wrapping the refusal
        # branch in it too would make "this candidate will not rebuild" and
        # "somebody raised here by mistake" the same event, and the guarantee
        # below would hold by accident rather than by construction.
        try:
            graph, problems = _load_embedded_api_prompt(server, picture_id, object_info)
        except HTTPException as exc:
            logger.info(
                "Picture %s cannot be read for a runnable graph (%s), so the "
                "resolver moves on: %s",
                picture_id,
                exc.status_code,
                exc.detail,
            )
            return None
        if graph is None and problems:
            logger.info(
                "Picture %s carries an editor graph that will not rebuild, so "
                "the resolver moves on: %s",
                picture_id,
                "; ".join(problems),
            )
        return graph

    def _source_graph_for(
        card, object_info: dict | None = None
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
                    file_document = runnable_document(path, _load_workflow_json(path))
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
                graph = _embedded_graph(candidate, object_info)
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

    def _slot_digests(slots: list[dict], shelf_index) -> dict:
        """``{(node_id, field): sha256 or None}``: which shelf LoRA each slot loads.

        A digest slot names its LoRA exactly, and counts when the shelf holds
        that digest. A filename slot is matched on its case-folded basename,
        and a name two shelf LoRAs share names neither - the rule
        ``resolveRecipeLoras`` and ``_resolve_against_shelf`` already apply.
        """
        by_name, digests = shelf_index or ({}, set())
        answer: dict[tuple[str, str], str | None] = {}
        for slot in slots:
            value = str(slot.get("value") or "")
            if slot.get("by") == "digest":
                digest = value.strip().lower()
                found = digest if digest in digests else None
            else:
                matched = by_name.get(normalized_filename(value.strip()), set())
                found = next(iter(matched)) if len(matched) == 1 else None
            answer[(str(slot.get("node_id")), str(slot.get("field")))] = found
        return answer

    def _card_inputs(
        hub,
        workflow_key: str,
        graph: dict,
        addressed: bool,
        bindings: list | None = None,
    ) -> list[CardInput]:
        """This card's picture inputs in *graph*, with its stored setup over them.

        A graph that will not reduce has nothing addressable in it. That is a
        400 only when the request ADDRESSED an input (``_apply_addressed``'s
        rule and wording); otherwise the card runs exactly as it did before
        picture inputs were filled, rather than a runnable card starting to
        refuse over a question nobody asked.
        """
        library_uuid = _library_uuid()
        stored = picture_inputs(hub, library_uuid, workflow_key) if library_uuid else []
        try:
            inputs = card_input_modes(graph, stored)
        except WorkflowGraphError as exc:
            if addressed:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "This workflow will not reduce, so its picture inputs "
                        f"cannot be addressed: {exc}"
                    ),
                ) from exc
            logger.info(
                "Card %s's graph will not reduce, so its picture inputs are "
                "left as the graph has them: %s",
                workflow_key,
                exc,
            )
            return []
        if bindings is None:
            return inputs
        # A file the old import dialog stored says which inputs a run fills,
        # and `[]` is one that took no picture at all: detection must not opt
        # an input back in that the owner opted out of (`workflow_bindings`).
        # An input left out here is not addressable and runs as authored.
        bound = {
            workflow_bindings.target_node(graph, binding.get("path"))
            for binding in bindings
            if isinstance(binding, dict)
            and binding.get("role") == workflow_bindings.IMAGE
        }
        return [item for item in inputs if bound.intersection(item.node_ids)]

    def _fill_inputs(
        graph: dict,
        card_inputs: list[CardInput],
        requested: dict[tuple[str, str], int | None],
        selection: list[int],
        preflight: dict,
    ) -> tuple[list[RunPictureInput], list[Feed], list[run_service.Reason]]:
        """Answer every picture input, and refuse the ones nothing answers.

        Run AFTER ``judge``, on the graph ``judge`` saw, because an open input
        is only a refusal when the file the graph already names is not on this
        ComfyUI - and that is the pre-flight's ``missing_input_images``, which
        ``judge`` itself never reads. A loader naming a mask or a reference
        the owner keeps in ComfyUI's input folder runs as it always did.

        Returns:
            ``(described, feeds, reasons)``: every input as the response shows
            it, where each filled one goes, and ``picture_input_unfilled``
            naming the open ones, if any.
        """
        pinned = read_oldest_kept_by_pixel_sha(
            server.vault,
            sorted(
                {i.pixel_sha for i in card_inputs if i.mode == "fixed" and i.pixel_sha}
            ),
        )
        fills = resolve_fills(card_inputs, requested, pinned, bool(selection))
        missing = {
            str(item.get("node_id"))
            for item in preflight.get("missing_input_images") or []
            if item
        }
        described: list[RunPictureInput] = []
        feeds: list[Feed] = []
        unfilled: list[dict] = []
        for fill in fills:
            item = fill.input
            how = fill.how
            if how is not None:
                targets = [
                    workflow_bindings.picture_target(graph, node_id, item.class_type)
                    for node_id in item.node_ids
                ]
                if all(targets):
                    feeds.append(
                        Feed(
                            targets,
                            fill.picture_id,
                            by_id=item.class_type == PIXLSTASH_PICTURE_LOADER,
                        )
                    )
                else:
                    # A loader PixlStash cannot hand an uploaded file to. Named
                    # rather than filled somewhere else, which would be a run
                    # that never read the picture it was given.
                    how = None
            elif (
                not (item.mode == "fixed" and item.pixel_sha)
                # A PixlStash picture loader's own ids are frozen, and empty
                # it picks pictures by its own sort (#1521): never run as is.
                and item.class_type != PIXLSTASH_PICTURE_LOADER
            ) and all(
                _graph_names_a_live_file(graph, node_id, item.input_name, missing)
                for node_id in item.node_ids
            ):
                # Never for a pin whose picture has gone: the owner chose a
                # picture for this input, and quietly running the file the
                # graph was authored with instead is a run that read neither.
                # It is an empty slot, and says so (decision 7).
                how = "graph"
            if how is None:
                # `title` beside the address: a slot label is a topology hash,
                # and a refusal a person reads has to name the input they see.
                unfilled.append(
                    {
                        "slot_label": item.slot_label,
                        "input_name": item.input_name,
                        "title": item.title,
                    }
                )
            pin_gone = bool(
                item.mode == "fixed" and item.pixel_sha and item.pixel_sha not in pinned
            )
            described.append(
                RunPictureInput(
                    slot_label=item.slot_label,
                    input_name=item.input_name,
                    title=item.title,
                    mode=item.mode,
                    pixel_sha=item.pixel_sha,
                    picture_id=(
                        fill.picture_id
                        if fill.picture_id is not None
                        else pinned.get(item.pixel_sha)
                        if item.mode == "fixed"
                        else None
                    ),
                    picture_missing=pin_gone,
                    fill=how,
                )
            )
        reasons = (
            [
                run_service.Reason(
                    run_service.PICTURE_INPUT_UNFILLED, {"inputs": unfilled}
                )
            ]
            if unfilled
            else []
        )
        return described, feeds, reasons

    def _graph_names_a_live_file(
        graph: dict, node_id: str, input_name: str, missing: set[str]
    ) -> bool:
        """Whether an input nobody filled can run on what the graph says.

        A link is computed at run time and not ours to judge. A literal is live
        unless the pre-flight found it missing; an empty one is nothing at all.
        """
        inputs = (graph.get(node_id) or {}).get("inputs") or {}
        value = inputs.get(input_name)
        if isinstance(value, list):
            return True
        return isinstance(value, str) and bool(value) and node_id not in missing

    def _upload_files(submittable) -> dict[int, tuple[str, str]]:
        """``{picture_id: (path, upload name)}`` for every picture a run feeds.

        Resolved in ``_plan`` and not at upload time, so a picture that has
        been binned or has lost its file refuses the request before anything
        is uploaded - the rule that keeps a bad request out of the owner's
        ComfyUI input folder.

        The name carries the id and the content, never the picture's own file
        name: ComfyUI's upload overwrites by name, so two pictures both called
        ``image.png`` in one batch would each load whichever landed last by
        the time the queue reached them.

        A picture only a PixlStash picture loader reads (``Feed.by_id``) is
        checked the same way and not uploaded: that loader fetches it itself.
        """
        fed = [
            (picture_id, feed.by_id)
            for _graph, group, feeds in submittable
            for feed in feeds
            for picture_id in (
                [feed.picture_id] if feed.picture_id is not None else group.picture_ids
            )
        ]
        wanted = sorted({picture_id for picture_id, _by_id in fed})
        uploads = {picture_id for picture_id, by_id in fed if not by_id}
        if not wanted:
            return {}
        kept = read_kept_picture_files(server.vault, wanted)
        library = re.sub(r"[^0-9a-zA-Z]", "", _library_uuid() or "")[:8] or "library"
        files: dict[int, tuple[str, str]] = {}
        for picture_id in wanted:
            if picture_id not in kept:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Picture {picture_id} is not a kept picture of this "
                        "library, so it cannot fill a picture input."
                    ),
                )
            file_path, pixel_sha = kept[picture_id]
            path = ImageUtils.resolve_picture_path(server.vault.image_root, file_path)
            if not path or not os.path.isfile(path):
                logger.warning(
                    "Picture %s cannot fill a picture input: its file %r "
                    "(resolved %r) is not on disk.",
                    picture_id,
                    file_path,
                    path,
                )
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Picture {picture_id}'s file is not on disk, so it "
                        "cannot fill a picture input."
                    ),
                )
            extension = os.path.splitext(path)[1].lower()
            if not _UPLOAD_EXTENSION_RE.match(extension):
                extension = ""
            # The content, so a name never outlives the bytes it named: a vault
            # id is reused after a delete and one ComfyUI may serve two
            # libraries. A picture the hashing task has not reached yet has no
            # `pixel_sha`, and its file's size and mtime stand in for one.
            if pixel_sha and _PIXEL_SHA_RE.match(pixel_sha):
                content = pixel_sha
            else:
                stat = os.stat(path)
                content = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
            if picture_id in uploads:
                files[picture_id] = (
                    path,
                    f"pixlstash-{library}-{picture_id}-{content}{extension}",
                )
        return files

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
        if not body.picture_ids and any(e.picture_id is None for e in body.inputs):
            # "My selection goes here", on a run with no selection: honouring
            # it is impossible and ignoring it would run a graph that never
            # read the pictures the caller thinks it sent.
            raise HTTPException(
                status_code=400,
                detail=(
                    "An input was sent the selection, and this run has no "
                    "selection: name a picture_id for it."
                ),
            )
        groups = _groups_for(body, recipe_key)
        if body.target:
            # One target replaces every group's card, keeping the pictures that
            # chose it: "run this stack member over what I selected".
            target = _require_hash(body.target, "target")
            pictures = [pid for _, ids, _ in groups for pid in ids]
            groups = [(target, pictures, [])]
        if body.skip_loras and len({key for key, _, _ in groups if key}) > 1:
            # A skip names a loader by its node id, which only means one thing
            # in one graph: across cards it could skip an unrelated LoRA and
            # report it as the owner's choice.
            raise HTTPException(
                status_code=400,
                detail=(
                    "skip_loras names loaders by node id, which only identifies "
                    "a loader on one workflow; this run spans several. Run one "
                    "workflow at a time, or name it as target."
                ),
            )

        # Read once for the whole request, and only when there is a ComfyUI to
        # verify a swap against: two shelf scans per group would be two scans of
        # `model` and `model_file` for an answer that cannot change mid-request.
        aliases = model_name_aliases(hub) if object_info is not None else {}
        # Which shelf LoRA each graph slot already loads, read once and only when
        # a saved recipe's LoRAs have to be matched to slots.
        shelf_index = (
            adapter_digest_index(hub) if recipe_loras and not body.loras else None
        )

        requested = {
            (entry.slot_label, entry.input_name): entry.picture_id
            for entry in body.inputs
        }
        # Addresses some resolved card actually has; see the check after the loop.
        addressed: set[tuple[str, str]] = set()
        reached_inputs = False

        planned: list[RunGroup] = []
        submittable: list[tuple[dict, RunGroup, list[Feed]]] = []
        # Which requested skips some graph of this run holds, and whether any
        # graph was resolved to look in: a skip no graph has is refused below.
        skips_found: set[tuple[str, str]] = set()
        skips_checked = False
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
            source, failure = _source_graph_for(card, object_info)
            if source is None:
                group.reasons = [failure.as_dict()]
                planned.append(group)
                continue
            group.source = source.origin
            group.source_picture_id = source.picture_id

            graph = source.graph
            # Enumerated from the graph as it was resolved, BEFORE anything
            # below rewires it: a slot label is derived from the topology, and
            # #1463's bypass takes a LoRA loader out and changes the topology.
            # The labels a card's setup was written against are these. The two
            # steps commute otherwise - a LoRA loader has no picture input, a
            # picture loader is never a model or clip consumer, and a bypass
            # keeps every other node's id - so the fill below still finds its
            # nodes in the graph the bypass left.
            card_inputs = _card_inputs(
                hub, workflow_key, graph, bool(requested), source.bindings
            )
            reached_inputs = True
            addressed.update(item.address for item in card_inputs)
            _apply_addressed(graph, body.values)
            _apply_prompts(graph, body.prompt, body.negative)
            # A saved recipe's LoRAs are matched against the graph as it stood
            # BEFORE the skip: matched after it, the LoRA a skipped loader held
            # moved on to the next free slot and replaced a LoRA the owner had
            # not named, while the notice still said it was skipped.
            slots_before_skip = detect_lora_targets(graph)
            # The slots the owner asked this run to go without, next: before
            # the saved recipe's LoRAs are applied, before the missing-LoRA
            # bypass and before `judge`, so the graph judged is the graph
            # submitted.
            skipped, skip_reasons, skip_seen = run_service.skip_requested_loras(
                graph,
                [(item.node_id, item.field) for item in body.skip_loras],
                object_info,
            )
            skips_found |= skip_seen
            skips_checked = True
            slots_in_graph = detect_lora_targets(graph)
            # Applied only when it CAN be, and after the two questions that
            # would otherwise be answered as the wrong failure: a graph with no
            # LoRA slot at all is `no_lora_loader` rather than a 400 about one
            # slot, and an unreachable ComfyUI cannot resolve a filename slot,
            # which `apply_adapter` would report as a missing node class.
            found: list[run_service.Reason] = list(skip_reasons)
            # What the repair registry changed in this graph, by `RunGroup`
            # field; put on the group only if it ends up being submitted. See
            # the assignment below.
            repaired: dict[str, list[dict]] = {}
            if body.loras and slots_in_graph:
                # NOT gated on `object_info`: skipping the application when
                # ComfyUI could not be asked is how a consented run silently
                # kept the stored graph's LoRA instead of the one that was
                # asked for. `_apply_loras` answers for that state itself.
                found += _apply_loras(graph, body.loras, object_info)
            elif not body.loras and recipe_loras:
                # "Run this saved look" has to place the look's own LoRAs. A
                # saved one names a file and a digest but no slot, so each is
                # MATCHED to one - digest, then basename, then a free slot in
                # order (#1478) - and one with nowhere to go is reported in
                # `unplaced_loras` rather than dropped. A positional zip put a
                # recipe stored in the other order onto the wrong loaders.
                placements, group.unplaced_loras = run_service.place_recipe_loras(
                    slots_before_skip,
                    recipe_loras,
                    _slot_digests(slots_before_skip, shelf_index)
                    if slots_before_skip
                    else {},
                )
                # A recipe LoRA matched to a skipped slot is not applied: the
                # owner skipped that loader for this run, and the skip is
                # already reported in `bypassed_loras`.
                skipped_slots = {(item["node_id"], item["field"]) for item in skipped}
                placements = [
                    (target, saved)
                    for target, saved in placements
                    if (str(target["node_id"]), str(target["field"]))
                    not in skipped_slots
                ]
                for unplaced in group.unplaced_loras:
                    logger.info(
                        "[workflows] Card %s runs without saved LoRA %s: %s",
                        workflow_key,
                        unplaced["filename"] or unplaced["sha256"],
                        unplaced["reason"],
                    )
                if placements:
                    found += _apply_loras(
                        graph,
                        [
                            RunLora(
                                node_id=str(target["node_id"]),
                                field=str(target["field"]),
                                sha256=str(saved["sha256"]).strip().lower(),
                                strength_model=_float_or_none(saved.get("strength")),
                            )
                            for target, saved in placements
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

            # Before `judge`, because the whole point is that the graph it
            # judges is the graph that will be submitted: a swap applied after it
            # would be a substitution nothing verified, and one reported as a
            # missing model the owner then cannot find (#1439). Applied to the
            # copy being submitted and never written back to the stored recipe,
            # whose filenames are the picture's provenance.
            if object_info is not None:
                group.substitutions = apply_model_swap(
                    graph,
                    detect_model_targets(graph, object_info),
                    aliases,
                    object_info,
                )
                for swap in group.substitutions:
                    logger.info(
                        "[workflows] Card %s loads %s in place of %s on node %s "
                        "(%s.%s): the same model, from the copy this shelf still "
                        "has.",
                        workflow_key,
                        swap["now"],
                        swap["was"],
                        swap["node_id"],
                        swap["class_type"],
                        swap["field"],
                    )

            # The ComfyUI-PixlStash policy (#1521): a saver runs as SaveImage,
            # so the import below is the only one, and a loader's frozen
            # project, set or character id is looked up in this library.
            swap_pixlstash_savers(graph)
            node_policy = {
                "library_ids": read_library_ids(server.vault, library_ids_named(graph)),
                "picture_loader": True,
                "from_file": source.origin == run_service.FROM_FILE,
            }
            judged, preflight = run_service.judge(
                graph,
                object_info,
                object_info_error,
                wants_lora=bool(body.loras),
                lora_slots=slots_in_graph,
                **node_policy,
            )
            if object_info is not None and not found:
                # Judge, repair what the registry knows how to, judge AGAIN
                # (#1463): a repair can leave its refusal standing, and only
                # the second verdict says whether this graph now runs. After
                # the swap, so a LoRA the shelf still holds under another name
                # is loaded rather than dropped; skipped when `_apply_loras`
                # has already refused, because that run is not happening and a
                # LoRA the request asked for is never the graph's to drop.
                #
                # Held in a local and NOT put on the group here. What it says
                # is "the run goes ahead with this changed", which is a lie on
                # a group about to be refused for some other reason.
                repaired = run_service.repair(graph, object_info, judged)
                if any(repaired.values()):
                    # Both halves: `_fill_inputs` reads this preflight, and it
                    # must describe the graph that will be submitted.
                    judged, preflight = run_service.judge(
                        graph,
                        object_info,
                        object_info_error,
                        wants_lora=bool(body.loras),
                        lora_slots=slots_in_graph,
                        **node_policy,
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
            # After `judge` and after the bypass, on the graph that will be
            # submitted, and before anything is uploaded: every refusal is
            # decided in this function, so a request that refuses leaves
            # nothing in ComfyUI's input folder. It blocks this card and not
            # the batch - "Make more like these" runs the rest.
            described, feeds, unfilled = _fill_inputs(
                graph, card_inputs, requested, picture_ids, preflight
            )
            group.picture_inputs = described
            found += unfilled
            # `judge` allowed the PixlStash picture loader because a run feeds
            # it; one that is not fed and is not an input the owner can fill
            # (that one is `picture_input_unfilled` already) is refused here.
            unfed = unfed_picture_loaders(
                graph,
                {
                    workflow_bindings.target_node(graph, target.get("path"))
                    for feed in feeds
                    for target in feed.targets
                }
                | {node_id for item in card_inputs for node_id in item.node_ids},
            )
            if unfed:
                found = run_service.with_pixlstash_refusals(found, unfed)
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
            # A selection feeding an input repeats the run per picture, and
            # `count` multiplies that: 40 pictures at count 5 is 200 runs, and
            # the cap below has to see 200, not 5.
            per_picture = any(feed.picture_id is None for feed in feeds)
            group.runs = body.count * (len(picture_ids) if per_picture else 1)
            # Reported only now, when this group really is being submitted:
            # every refusal is in, and what the notice claims is true.
            for report, entries in repaired.items():
                setattr(group, report, entries)
            # The owner's own skips ride in the same field, marked requested.
            group.bypassed_loras = skipped + group.bypassed_loras
            planned.append(group)
            submittable.append((graph, group, feeds))

        unknown = sorted(set(requested) - addressed)
        if unknown and reached_inputs:
            # The rule `_apply_loras` states: a request that cannot be read
            # against the card is a request error, not a reason. A picture sent
            # to an input no card here has would otherwise be a run that never
            # read it.
            raise HTTPException(
                status_code=400,
                detail=(
                    "This workflow has no picture input "
                    + ", ".join(f"{label}/{name}" for label, name in unknown)
                    + "."
                ),
            )

        unknown_skips = [
            f"{item.field} on node {item.node_id}"
            for item in body.skip_loras
            if (item.node_id, item.field) not in skips_found
        ]
        if skips_checked and unknown_skips:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This workflow has no LoRA slot "
                    f"{', '.join(unknown_skips)} to skip."
                ),
            )
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
                # Including the groups that WOULD have run: nothing is
                # submitted now, so "the run goes ahead without this LoRA" is
                # no longer true of any of them.
                for entry in run_service.REPAIRS:
                    setattr(group, entry.report, [])
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
            files=_upload_files(submittable),
        )

    @router.post(
        "/workflows/run/preflight",
        summary="Check what a run would do",
        description=(
            "The same body as POST /workflows/run, submitting nothing. Every "
            "card the request resolves to comes back with the reasons it would "
            "not run: comfyui_not_configured, comfyui_unreachable, ui_format, "
            "missing_nodes, missing_models, a1111, picture_input_unfilled, "
            "no_lora_loader, pixlstash_nodes, no_save_node, no_runnable_source, "
            "lora_not_skippable. "
            "A group runs when its reasons are empty - or when the only ones "
            "left are an uninspectable ComfyUI the body said allow_unchecked "
            "to. A LoRA this ComfyUI does not have is NOT among them: its "
            "loader is taken out of the graph and named in bypassed_loras, "
            "which is a fact about the run rather than a reason against it; "
            "so is unplaced_loras, a saved recipe's LoRA the workflow has no "
            "slot for or the shelf cannot identify. skip_loras names slots "
            "this run goes without: each loader is bypassed on the run's copy "
            "and reported in bypassed_loras with requested true, and one that "
            "cannot be skipped without dropping another LoRA is "
            "lora_not_skippable. Likewise a custom seed node this ComfyUI "
            "lacks (rgthree's Seed and its kin) is replaced by the run's own "
            "seed and named in replaced_nodes, where every input it fed is one "
            "the seed pass writes. A "
            "body that cannot be interpreted against the card answers "
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
            "graph at run time and never written back into it. inputs fills "
            "the card's picture inputs; with exactly one left open by the "
            "card's pins and stored setup, the selection fills it unasked and "
            "the run repeats once per selected picture. Pictures are uploaded "
            "into ComfyUI's input folder only after every refusal is decided. "
            "New runs are NOT stacked with their source unless stack: true, "
            "and a run over a selection stacks each output with the picture it "
            "read. A missing model "
            "blocks the whole batch. See /workflows/run/preflight for the "
            "reason codes. allow_unchecked consents to running a graph the "
            "server could not inspect and must be the literal JSON true: any "
            "other spelling is a 422, and the camelCase allowUnchecked the "
            "retired run_recipe also took is not a field here, so sending it "
            "consents to nothing."
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

        **Uploads happen here and nowhere else**, after every refusal in
        ``_plan`` is in, and all of them before the first submission: each
        distinct picture once per request however many runs read it, so an
        upload that fails has queued nothing.
        """
        uploaded = {
            picture_id: _upload_image_to_comfyui(comfyui_url, path, upload_name)
            for picture_id, (path, upload_name) in plan.files.items()
        }
        for graph, group, feeds in plan.submittable:
            output_node_ids = _extract_output_node_ids(graph, {})
            # The same finder `replace_missing_seed_nodes` checked its literals
            # against: a replaced seed node is only safe because this writes
            # every input it inlined.
            seed_targets = run_service.run_seed_targets(graph, object_info)
            # A selection feeding an input is the run's repeat axis: one pass
            # per picture, each its own source and, with `stack`, its own
            # stack. Otherwise one pass, and the group's first picture is the
            # source, as a run of a card has always stacked.
            per_picture = any(feed.picture_id is None for feed in feeds)
            passes = group.picture_ids if per_picture else group.picture_ids[:1]
            for selected in passes or [None]:
                # Hoisted out of the loop below: it is a WRITE task, idempotent,
                # and `count` runs of one pass all land in the one stack.
                source_id = selected if body.stack else None
                stack_id = (
                    stack_for_picture(server.vault, source_id)
                    if source_id is not None
                    else None
                )
                filled = deepcopy(graph)
                if stack_id:
                    _tag_for_stack(filled, stack_id, source_id)
                for feed in feeds:
                    picture_id = (
                        feed.picture_id if feed.picture_id is not None else selected
                    )
                    value = str(picture_id) if feed.by_id else uploaded[picture_id]
                    for target in feed.targets:
                        workflow_bindings.fill(
                            filled,
                            {workflow_bindings.IMAGE: [target]},
                            {workflow_bindings.IMAGE: value},
                        )
                for _ in range(body.count):
                    instance = deepcopy(filled)
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
                                source_id,
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

    def _tag_for_stack(graph: dict, stack_id: int, source_id: int) -> None:
        """Tag the save node so its outputs join the source's stack however they
        arrive.

        ``_process_comfyui_outputs`` stacks what IT imports; a ComfyUI output
        folder the owner also watches can import the file first, and then
        only the tag in its name says where it belongs. The retired run route
        did both for the same reason.

        Each save node keeps its OWN prefix under the tag, so a graph saving
        `out` and `preview` still saves two sets of files. A prefix that is
        wired from another node is left alone: overwriting it drops the link.
        """
        tagged = False
        for node in graph.values():
            if not isinstance(node, dict) or node.get("class_type") != "SaveImage":
                continue
            inputs = node.setdefault("inputs", {})
            own = inputs.get("filename_prefix")
            if isinstance(own, list):
                continue
            inputs["filename_prefix"] = build_stack_filename_prefix(
                str(own or ""), stack_id, source_id
            )
            tagged = True
        if not tagged:
            logger.warning(
                "[workflows] No SaveImage node to tag for stack %s (source %s); "
                "its outputs join the stack only if this run imports them.",
                stack_id,
                source_id,
            )

    # ── The file gestures (v1.12 B8) ──────────────────────────────────────
    #
    # Export, Duplicate, Clone with new models, Insert loader and Delete: the
    # five gestures that write or read a FILE, against a card that may never
    # have had one. Each starts from `_source_graph_for`, so a card the library
    # only knows from its pictures exports and duplicates like any other - that
    # is most of what makes them worth having.
    #
    # Edited defaults are NOT applied to any of these. A default is an override
    # the run path puts in at submit time (implementation plan rule 1), and
    # writing one into a graph would make the workflow and the override the
    # same thing.

    def _card_source(card, object_info: dict | None = None):
        """One card's runnable graph, or the 409 that says why there is none.

        ``RecursionError`` is caught here rather than at each gesture because
        all three resolve through this one function and share the exposure: an
        embedded graph comes out of a picture that arrived from somewhere else,
        so its nesting depth is not ours to trust, and ``sanitize_prompt_graph``
        deep-copies it before anything of ours has looked at it.
        ``_store_workflow`` and ``_trash_stored_workflow`` already name this
        class for the same reason.
        """
        try:
            source, reason = _source_graph_for(card, object_info)
        except RecursionError as exc:
            logger.warning(
                "Card %s has a source graph too deeply nested to read: %s",
                card.workflow_key,
                exc,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "PixlStash cannot read this workflow: its graph is nested "
                    "too deeply to walk."
                ),
            ) from exc
        if source is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "PixlStash has no graph for this workflow "
                    f"({reason.code if reason else run_service.NO_RUNNABLE_SOURCE})."
                ),
            )
        return source

    def _file_stem(card) -> str:
        """What a file written for this card should be called, without .json.

        Sanitised, because a card's name is the owner's own text and it goes
        both into a path under the user folder and into a download name the
        CLIENT writes with — so the cleaning has to hold on the client's
        platform too, which ``os.path.basename`` on Linux does not do for a
        Windows separator. ``resolve_path_within`` is still the backstop on
        this side (``routes/comfyui.py``); this is the half that leaves.
        """
        stem = os.path.splitext(card.file_name)[0] if card.file_name else ""
        return download_stem(stem or _display_name(card)) or "workflow"

    # Which of the two cleanings is the authority: for a file written on THIS
    # machine it is `_normalize_workflow_name` + `resolve_path_within` in
    # `routes/comfyui.py`, and `download_stem` above is advisory. For the
    # export's `filename` there is no server-side backstop at all, because the
    # client writes that file — so there `download_stem` is the authority.

    def _structural_lora_slots(card, graph: dict) -> set[tuple[str, str]]:
        """``(node id, widget)`` of every LoRA slot the owner marked structural.

        Everything else the export empties, which is why this reads the
        STRUCTURAL marks rather than the recipe ones: an unmarked slot, a
        topology the backfill has not frozen yet and a loader the label map
        does not reach all then fall on the side that publishes nothing.

        A mark is keyed by ``<node label>/<widget>`` (``workflow_identity``'s
        own slot label), so the widget is carried through rather than the node
        alone — a stacker's three slots are three marks on one node.
        """
        keep = {
            label
            for (topology_hash, label), mark in slot_marks(
                _hub(), [card.topology_hash]
            ).items()
            if topology_hash == card.topology_hash and mark == STRUCTURAL
        }
        if not keep:
            return set()
        try:
            labels = topology_node_labels(structural_document(graph))
        except WorkflowGraphError as exc:
            # No labels means no node is known to be structural, so every LoRA
            # slot is emptied. Logged rather than raised: a less useful export
            # is the right failure here, and `scrub_for_export` refuses the
            # graph on its own if the reduction is what it needed.
            logger.info(
                "Card %s exports with every LoRA slot emptied, its graph will "
                "not reduce to slot labels: %s",
                card.workflow_key,
                exc,
            )
            return set()
        return {
            (node_id, widget)
            for node_id, label in labels.items()
            for widget in ((graph.get(node_id) or {}).get("inputs") or {})
            if f"{label}/{widget}" in keep
        }

    @router.get(
        "/workflows/{workflow_key}/export",
        summary="Export a workflow",
        description=(
            "This workflow as a ComfyUI file somebody else can open: prompts "
            "and caption targets blank, seeds nulled, the LoRA slots that are "
            "part of the look emptied, node titles stripped, picture file "
            "names blanked, and any model name this machine does not hold "
            "left out. Export a recipe instead to share what was actually run."
        ),
        response_model=WorkflowExport,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "There is no graph to export, or it will not read."},
        },
    )
    def export_workflow(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        source = _card_source(card)
        try:
            document, removed = scrub_for_export(
                source.graph,
                structural_lora_slots=_structural_lora_slots(card, source.graph),
                unvouched=unvouched_model_values(hub),
            )
        except (WorkflowGraphError, RecursionError) as exc:
            # Refused rather than exported unscrubbed. A graph PixlStash cannot
            # read is one it can promise nothing about, and the promise is the
            # route. `RecursionError` is the same answer and the same class of
            # input: an embedded graph comes out of a picture that arrived from
            # somewhere else, which is why `_store_workflow` and
            # `_trash_stored_workflow` both name it too.
            raise HTTPException(
                status_code=409,
                detail=(
                    "PixlStash cannot read this workflow well enough to make it "
                    f"safe to share: {exc}"
                ),
            ) from exc
        return WorkflowExport(
            filename=f"{_file_stem(card)}.json",
            workflow=document,
            removed=removed,
            source=source.origin,
        )

    @router.get(
        "/workflows/{workflow_key}/graph",
        summary="A workflow's runnable graph",
        description=(
            "This workflow as Run would submit it, prompt and seed kept, for "
            "opening in the owner's own ComfyUI: resolved against what that "
            "ComfyUI lists, with model names swapped to the copy it loads. "
            "Credential widgets are blanked. A graph from a stored recipe has "
            "no seeds (`seedless`) and may name models the library forgot "
            "(`forgotten`). The ComfyUI-PixlStash node reads it when ComfyUI "
            "is opened with `?pixlstash_workflow=<key>`. Export instead to "
            "give it away."
        ),
        response_model=WorkflowRunnableGraph,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "There is no graph for this card."},
        },
    )
    def get_runnable_graph(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        # Resolved the way Run… resolves it: with ComfyUI's own model list, so
        # an editor-only picture graph is rebuilt and a renamed model loads
        # (#1439). Without ComfyUI the file and picture tiers still answer.
        object_info, _error = _read_object_info(_comfyui_url(_user(request)))
        source = _card_source(card, object_info)
        graph = source.graph
        if object_info is not None:
            apply_model_swap(
                graph,
                detect_model_targets(graph, object_info),
                model_name_aliases(hub),
                object_info,
            )
        # Otherwise unscrubbed, for Duplicate's reason: it stays with the owner
        # and is meant to RUN. But it travels over the network into ComfyUI's
        # page, whose own save and share would keep a key, so credentials go.
        # ponytail: top-level widgets only; export's nested walk if one hides.
        for node in graph.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            for name, value in inputs.items():
                if isinstance(value, str) and SECRET_FIELD_RE.search(name):
                    inputs[name] = ""
        return WorkflowRunnableGraph(
            name=_file_stem(card),
            workflow=graph,
            source=source.origin,
            seedless=source.seedless,
            forgotten=source.forgotten,
        )

    @router.post(
        "/workflows/{workflow_key}/duplicate",
        summary="Duplicate a workflow",
        description=(
            "Write this workflow into the user's workflow folder under a free "
            "name, so it can be opened and changed in ComfyUI without touching "
            "the original. A card the library only knows from its pictures "
            "gets a file this way for the first time."
        ),
        response_model=WorkflowFile,
        status_code=201,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "There is no graph to duplicate."},
            500: {"description": "The copy could not be written."},
        },
    )
    def duplicate_workflow(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        source = _card_source(card)
        # Unscrubbed on purpose: this file stays on the owner's machine and is
        # meant to RUN, and a copy with its models blanked would not.
        name, key = _store_copy(
            hub, f"{_file_stem(card)} (copy)", source.graph, source.bindings
        )
        _announce(request, sorted({workflow_key, key} - {None}), "imported")
        return WorkflowFile(name=name, workflow_key=key)

    @router.post(
        "/workflows/{workflow_key}/insert-lora-loader",
        summary="Add a LoRA loader to a workflow",
        description=(
            "Write a copy of this workflow with a LoRA loader spliced in right "
            "after the model source, so a workflow that had nowhere to put a "
            "LoRA now has a slot to swap into. The loader starts at ComfyUI's "
            "own widget defaults — no LoRA is chosen here — so pick one before "
            "running the copy as it is. The original file is not changed; the "
            "copy is a card of its own."
        ),
        response_model=InsertedLoader,
        status_code=201,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "No graph, or nowhere a loader can honestly go."},
            503: {"description": "ComfyUI could not be reached."},
        },
    )
    def insert_lora_loader(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        source = _card_source(card)
        # ComfyUI types the links (#1376): an API-format link carries no type,
        # so without `object_info` an input reading the model could be missed
        # and that branch would run without the LoRA, silently.
        object_info, error = _read_object_info(_comfyui_url(_user(request)))
        if object_info is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "PixlStash could not ask ComfyUI what its nodes hand on, so "
                    f"it cannot tell where a LoRA loader would go: {error}"
                ),
            )
        graph = deepcopy(source.graph)
        try:
            plan = plan_lora_insertion(graph, object_info)
            loader = insert_adapter(graph, plan, None, object_info)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        name, key = _store_copy(
            hub, f"{_file_stem(card)} (LoRA)", graph, source.bindings
        )
        _announce(request, sorted({workflow_key, key} - {None}), "imported")
        return InsertedLoader(
            name=name,
            workflow_key=key,
            node_id=loader["node_id"],
            class_type=loader["class_type"],
        )

    # ── The LoRA chain (#1478) ──────────────────────────────────────────────
    # Read and written whole: the editor lists the loaders in the order a run
    # applies them, and one save is one new card however many gestures made
    # it. `insert-lora-loader` above is the empty-list case of the write.

    def _shelf_chain(hub, chain: dict) -> None:
        """Mark each loader of *chain* with the shelf LoRA it loads, in place.

        ``sha256`` is set when exactly one shelf LoRA matches - by digest for a
        digest slot, by case-folded basename otherwise - and a digest slot's
        ``name`` becomes the shelf's filename, since a hash names nothing to a
        reader.
        """
        by_name, digests = adapter_digest_index(hub)
        found = _slot_digests(chain["loaders"], (by_name, digests))
        for loader in chain["loaders"]:
            digest = found.get((str(loader["node_id"]), str(loader["field"])))
            loader["sha256"] = digest
            if loader.get("by") != "digest":
                continue
            if digest is None:
                loader["name"] = str(loader["value"])[:12]
                continue
            try:
                filenames = _shelf_adapter(hub, digest)["filenames"]
            except HTTPException as exc:
                logger.info(
                    "Digest loader #%s names shelf LoRA %s, whose name cannot be "
                    "read, so it is shown by its digest: %s",
                    loader["node_id"],
                    digest,
                    exc.detail,
                )
                loader["name"] = digest[:12]
                continue
            loader["name"] = lora_display_name(filenames[-1]) if filenames else digest

    def _chain_payload(
        workflow_key: str, chain: dict, refusal: str | None, object_info
    ) -> LoraChain:
        model = chain.get("model_source")
        clip = chain.get("clip_source")
        same_node = bool(model and clip and clip["node_id"] == model["node_id"])
        added = None
        if refusal is None:
            added = "LoraLoader" if clip is not None else "LoraLoaderModelOnly"
            if added not in (object_info or {}):
                added = None
        return LoraChain(
            workflow_key=workflow_key,
            editable=refusal is None,
            refusal=refusal,
            source=None
            if model is None
            else LoraChainSource(
                node_id=str(model["node_id"]),
                class_type=model.get("class_type"),
                outputs=["MODEL", "CLIP"] if same_node else ["MODEL"],
            ),
            clip_source=None
            if clip is None or same_node
            else LoraChainClipSource(
                node_id=str(clip["node_id"]), class_type=clip.get("class_type")
            ),
            sink=LoraChainSink(
                summary=chain.get("sink_summary"),
                consumers=[LoraChainConsumer(**sink) for sink in chain["sinks"]],
            ),
            loaders=[
                LoraChainLoader(
                    node_id=str(loader["node_id"]),
                    class_type=loader.get("class_type"),
                    field=str(loader["field"]),
                    filename=str(loader["value"]),
                    name=loader["name"],
                    strength=loader["strengths"].get("model"),
                    # A model-only loader has no CLIP strength to show, and a
                    # loader read untyped says nothing about its wiring either.
                    strength_clip=loader["strengths"].get("clip"),
                    sha256=loader.get("sha256"),
                    on_shelf=loader.get("sha256") is not None,
                )
                for loader in chain["loaders"]
            ],
            added_loader_class=added,
            branch_note=chain.get("branch_note"),
        )

    @router.get(
        "/workflows/{workflow_key}/lora-chain",
        summary="A workflow's LoRA chain",
        description=(
            "The LoRA loaders between this workflow's model source and what "
            "reads the model, in the order a run applies them, each with its "
            "strength and the shelf LoRA it loads. Typed from ComfyUI's "
            "object_info: when ComfyUI cannot be reached, or the chain is one "
            "PixlStash cannot edit honestly (two model sources, a stacker, a "
            "branching chain), editable is false, refusal says why, and the "
            "loaders are still listed as read from the graph."
        ),
        response_model=LoraChain,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "There is no graph for this card."},
        },
    )
    def get_lora_chain(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        graph = _card_source(card).graph
        # Cached: the inspector asks on every card it selects, the map is
        # megabytes, and an unreachable ComfyUI would otherwise cost a full
        # timeout per click. This read only DRAWS the chain; the PUT that
        # rewires it reads a fresh map.
        object_info, error = _read_object_info(
            _comfyui_url(_user(request)), cached=True
        )
        chain = None
        if object_info is None:
            refusal = (
                "PixlStash could not reach ComfyUI, so it cannot tell how this "
                f"workflow's LoRAs are wired; they can only be looked at: {error}"
            )
        else:
            try:
                chain = read_lora_chain(graph, object_info)
                refusal = None
            except LookupError as exc:
                logger.info(
                    "Card %s's LoRA chain is shown read-only: %s", workflow_key, exc
                )
                refusal = str(exc)
        if chain is None:
            chain = read_lora_chain_untyped(graph, object_info)
        _shelf_chain(hub, chain)
        return _chain_payload(workflow_key, chain, refusal, object_info)

    @router.put(
        "/workflows/{workflow_key}/lora-chain",
        summary="Edit a workflow's LoRA chain",
        description=(
            "Write a copy of this workflow with its LoRA chain as the owner "
            "left it: entries in apply order, an existing loader by node_id "
            "(moved and re-weighted, its id kept), a new one by the shelf "
            "sha256 of its LoRA, and every loader left out deleted. One call "
            "is one new card; the original file is never changed. dry_run "
            "answers the list of changes and writes nothing."
        ),
        response_model=LoraChainSaved,
        status_code=201,
        responses={
            200: {
                "model": LoraChainSaved,
                "description": "A dry run: the changes, nothing written.",
            },
            404: {"description": "This machine has no such card."},
            409: {
                "description": (
                    "No graph, nothing changed, an unknown or repeated loader, a "
                    "LoRA not on the shelf or not on this ComfyUI, or a chain "
                    "PixlStash cannot edit honestly."
                )
            },
            503: {"description": "ComfyUI could not be reached."},
        },
    )
    def edit_lora_chain(
        request: Request,
        response: Response,
        workflow_key: str,
        payload: LoraChainEdit = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        source = _card_source(card)
        object_info, error = _read_object_info(_comfyui_url(_user(request)))
        if object_info is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "PixlStash could not ask ComfyUI what its nodes hand on, so "
                    f"it cannot rewire this workflow's LoRAs: {error}"
                ),
            )
        graph = deepcopy(source.graph)
        try:
            chain = read_lora_chain(graph, object_info)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _shelf_chain(hub, chain)
        loaded = {
            loader["node_id"]: loader.get("sha256") for loader in chain["loaders"]
        }
        entries: list[dict] = []
        for entry in payload.entries:
            if entry.node_id is not None:
                wanted = entry.sha256.strip().lower() if entry.sha256 else None
                if (
                    wanted
                    and entry.node_id in loaded
                    and loaded[entry.node_id] != wanted
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Loader #{entry.node_id} loads another LoRA, and a "
                            "loader cannot be pointed at a different one here. "
                            "Delete it and add the new one."
                        ),
                    )
                entries.append({"node_id": entry.node_id, "strength": entry.strength})
                continue
            try:
                adapter = _shelf_adapter(hub, entry.sha256)
            except HTTPException as exc:
                if exc.status_code == 503:
                    raise
                # Not on the shelf, or not a LoRA: the chain the owner asked for
                # cannot be built, which is this route's conflict and not a
                # malformed request.
                raise HTTPException(status_code=409, detail=exc.detail) from exc
            entries.append(
                {
                    "node_id": None,
                    "adapter": adapter,
                    "strength": entry.strength,
                    "name": lora_display_name(
                        (adapter["filenames"] or [adapter["sha256"]])[-1]
                    ),
                }
            )
        try:
            plan = plan_lora_chain(graph, chain, entries, object_info)
            if not plan["changes"]:
                raise HTTPException(
                    status_code=409,
                    detail="Nothing changed: this is the chain the workflow has.",
                )
            if payload.dry_run:
                response.status_code = 200
                return LoraChainSaved(dry_run=True, changes=plan["changes"])
            apply_lora_chain(graph, plan, object_info)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        asked = re.sub(r"\.json$", "", (payload.name or "").strip(), flags=re.I)
        stem = download_stem(asked) if asked else ""
        name, key = _store_copy(
            hub, stem or f"{_file_stem(card)} (edited)", graph, source.bindings
        )
        _announce(request, sorted({workflow_key, key} - {None}), "imported")
        return LoraChainSaved(
            dry_run=False, name=name, workflow_key=key, changes=plan["changes"]
        )

    def _swap_models(hub) -> dict[int, SwapModel]:
        """Every shelf row a clone could load, by id. Engines are PixlStash's own."""
        return {
            int(row["id"]): SwapModel(
                id=row["id"],
                filename=row["filename"],
                display_name=row["display_name"],
                base_model=known_base_model(row),
                file_kind=row["file_kind"],
                family=row["family"],
            )
            for row in hub.fetchall(
                "SELECT id, filename, display_name, base_model, "
                "base_model_canonical, base_model_source, file_kind, family "
                "FROM model WHERE filename IS NOT NULL AND file_kind <> ?",
                (FILE_ENGINE,),
            )
        }

    def _swap_slots(
        graph: dict, models: dict[int, SwapModel], index: tuple
    ) -> list[tuple[str, str, SwapSlot]]:
        """``(class_type, widget, slot)`` per model file *graph* names, once each.

        Read through ``iter_model_fields_api``, the walk ``apply_filename_swap``
        rewrites through, so a file the dialog offers is one the swap reaches.
        Only values ending in a model extension: anything else (PixlStash's own
        node naming a shelf row by id) is not a filename a clone can swap.
        """
        by_name, _by_digest, _filenames, _names = index
        slots: dict[str, tuple[str, str, SwapSlot]] = {}
        for _node_id, class_type, widget, value in iter_model_fields_api(graph):
            if value in slots or not value.lower().endswith(MODEL_EXTENSIONS):
                continue
            if LORA_FILENAME_FIELD_RE.match(widget):
                kind = "lora"
            elif "CLIPVision" in class_type:
                # `clip_name` on a vision loader is an image encoder, not a
                # text encoder: a row offering text encoders would write T5
                # into an IPAdapter's vision slot.
                kind = "clip_vision"
            else:
                kind = slot_kind(widget)
            ids = by_name.get(normalized_filename(value), set())
            slots[value] = (
                class_type,
                widget,
                SwapSlot(
                    filename=value,
                    kind=kind,
                    model=models.get(next(iter(ids))) if len(ids) == 1 else None,
                ),
            )
        return list(slots.values())

    def _base_candidates(
        request: Request, found: list[tuple[str, str, SwapSlot]], checkpoints: list
    ) -> list[SwapModel]:
        """The checkpoints the workflow's first base loader could load.

        The shelf keeps a diffusion-only UNET and an all-in-one checkpoint under
        one kind, so without this a UNETLoader graph is offered SDXL
        checkpoints. Narrowed by the loader's own file type (a GGUF loader is
        offered GGUF files) and, when ComfyUI answers, by what that loader
        lists. ComfyUI down keeps every file of the right type: the clone's own
        check refuses the rest.
        """
        base = next(
            (entry for entry in found if entry[2].kind in BASE_MODEL_KINDS), None
        )
        if base is None:
            return checkpoints
        class_type, widget, slot = base
        extension = os.path.splitext(slot.filename)[1].lower()
        kept = [
            m
            for m in checkpoints
            if os.path.splitext(m.filename)[1].lower() == extension
        ]
        object_info, error = _read_object_info(_comfyui_url(_user(request)))
        options = listed_options(object_info, class_type, widget)
        if not options:
            logger.info(
                "Offering every %s checkpoint for %s unchecked, ComfyUI could "
                "not say what it lists: %s",
                extension,
                class_type,
                error or "the field is not enumerated",
            )
            return kept
        listed = {normalized_filename(option) for option in options}
        return [m for m in kept if normalized_filename(m.filename) in listed]

    @router.get(
        "/workflows/{workflow_key}/model-swap",
        summary="What a workflow could be cloned onto",
        description=(
            "The model files this workflow loads, the shelf's checkpoints, VAEs "
            "and text encoders to choose from, and - once a checkpoint is "
            "chosen - the VAEs and text encoders recipes on this machine, or "
            "runs in ComfyUI's history as of the last workflow pull, have run "
            "beside it, and the LoRAs and ControlNets trained on another "
            "family. When no recipe or run answers, support files whose layout "
            "the checkpoint's architecture declares are proposed as "
            "`declared`, never as evidence."
        ),
        response_model=ModelSwapOptions,
        responses={
            404: {"description": "No such card, or no such checkpoint."},
            409: {"description": "There is no graph to clone."},
        },
    )
    def read_model_swap(
        request: Request, workflow_key: str, checkpoint_id: int | None = None
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        # The graph is read on the proposal call too: the LoRA flags are about
        # the files it names.
        source = _card_source(card)
        models = _swap_models(hub)
        index = recipe_asset_index(hub)
        found = _swap_slots(source.graph, models, index)

        def of_kind(kind: str) -> list[SwapModel]:
            return sorted(
                (m for m in models.values() if m.file_kind == kind),
                key=lambda m: (m.display_name or m.filename).lower(),
            )

        options = ModelSwapOptions(
            slots=[slot for _cls, _widget, slot in found],
            checkpoints=[],
            vaes=of_kind(FILE_VAE),
            text_encoders=of_kind(FILE_TEXT_ENCODER),
        )
        if checkpoint_id is None:
            # Asked once, on open: the choice lists are what the dialog draws
            # then, and it does not re-read them per checkpoint.
            options.checkpoints = _base_candidates(
                request, found, of_kind(FILE_CHECKPOINT)
            )
            return options
        chosen = models.get(checkpoint_id)
        if chosen is None or chosen.file_kind != FILE_CHECKPOINT:
            raise HTTPException(status_code=404, detail="Unknown checkpoint.")
        options.checkpoint_family = family_of(chosen.base_model)
        options.checkpoint_modality = modality_of(chosen.base_model)
        options.proposals = {
            kind: [SwapProposal(**entry) for entry in entries]
            for kind, entries in propose_companions(hub, checkpoint_id, index).items()
        }
        # Only where both sides fold to a known family and differ in family
        # or modality: an unknown family is never a flag, and a VAE or text
        # encoder carries no base model to compare (evidence answers for
        # those, not a table).
        for slot in options.slots:
            if slot.kind not in ("lora", "controlnet") or slot.model is None:
                continue
            family = family_of(slot.model.base_model)
            modality = modality_of(slot.model.base_model)
            if (
                family
                and options.checkpoint_family
                and (
                    family != options.checkpoint_family
                    or modality != options.checkpoint_modality
                )
            ):
                options.flags.append(
                    SwapFlag(
                        filename=slot.filename,
                        kind=slot.kind,
                        base_model=slot.model.base_model,
                        family=family,
                        modality=modality,
                    )
                )
        return options

    @router.post(
        "/workflows/{workflow_key}/clone-with-models",
        summary="Clone a workflow onto other models",
        description=(
            "Write a copy of this workflow with some of its model files "
            "replaced, named as asked, beside the original. Every loader "
            "naming a replaced file is rewritten. When ComfyUI answers, each "
            "new name is written as ComfyUI lists it and a name it does not "
            "list is left out; when it does not, the names are written "
            "unchecked. The original file is not changed; the clone is a card "
            "of its own."
        ),
        response_model=ClonedWorkflow,
        status_code=201,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "No graph to clone, or a swap could not be made."},
            500: {"description": "The copy could not be written."},
        },
    )
    def clone_with_models(request: Request, workflow_key: str, body: CloneWithModels):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        source = _card_source(card)
        graph = deepcopy(source.graph)
        # Not `insert_lora_loader`'s 503: nothing here depends on ComfyUI's link
        # types, so an unreachable ComfyUI means "write the names unchecked".
        object_info, error = _read_object_info(_comfyui_url(_user(request)))
        if object_info is None:
            logger.info(
                "Cloning card %s with its model names unchecked, ComfyUI did "
                "not answer: %s",
                workflow_key,
                error,
            )
        swapped, unswapped = apply_filename_swap(graph, body.swaps, object_info)
        if not swapped and not unswapped:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The clone was not written: every model asked for is the "
                    "file this workflow already loads, so it would be a copy."
                ),
            )
        if unswapped:
            # All or nothing. A clone whose checkpoint swap failed while its
            # VAE swap landed is the old model with the new model's VAE - a
            # broken workflow saved as a success.
            raise HTTPException(
                status_code=409,
                detail=(
                    "The clone was not written, because not every new model "
                    "could go in: "
                    + "; ".join(f"{u['now']} ({u['reason']})" for u in unswapped)
                ),
            )
        name, key = _store_copy(
            hub, download_stem(body.name) or "workflow", graph, source.bindings
        )
        landed = find_card(hub, key) if key else None
        if landed is not None and key != workflow_key:
            _carry_to_clone(hub, workflow_key, landed, body.name, graph, swapped)
        _announce(request, sorted({workflow_key, key} - {None}), "imported")
        return ClonedWorkflow(
            name=name,
            workflow_key=key,
            swapped=swapped,
            unswapped=unswapped,
            verified=all(entry["verified"] for entry in swapped),
        )

    def _carry_to_clone(
        hub, source_key: str, landed, name: str, graph: dict, swapped: list
    ):
        """Give a clone the name typed for it and the original's pins and defaults.

        A new key is a blank card, and pins and defaults are addressed by
        ``(slot label, input name)``, which a filename swap does not move, so
        the owner's choices on the original fit the clone as they stand. Each is
        carried only where the card has none of its own: the clone may land on
        a card that already exists (a second clone, or the same workflow built
        by hand), and its owner's choices stand. Notes are not carried: they
        describe the original's history, which the clone does not share.

        A default on a loader field the swap rewrote is dropped, whatever its
        value, or it would put some other model back on the clone's first run.
        """
        if landed.name is None:
            set_attributes(hub, landed.workflow_key, name=name.strip())
        if key_pins(hub, landed.workflow_key) is None:
            pins = key_pins(hub, source_key)
            if pins is not None:
                replace_pins(hub, landed.workflow_key, pins)
        overrides = default_overrides(hub, source_key)
        if not overrides or default_overrides(hub, landed.workflow_key):
            return
        try:
            labels = topology_node_labels(structural_document(graph))
        except WorkflowGraphError as exc:
            logger.warning(
                "Clone %s of card %s will not reduce, so its swapped loader "
                "fields cannot be told apart and the original's defaults are "
                "not carried: %s",
                landed.workflow_key,
                source_key,
                exc,
            )
            return
        rewritten = {
            (labels.get(entry["node_id"]), entry["field"]) for entry in swapped
        }
        defaults = [
            (slot_label, input_name, value)
            for (slot_label, input_name), value in overrides.items()
            if (slot_label, input_name) not in rewritten
        ]
        if defaults:
            replace_defaults(hub, landed.workflow_key, defaults)

    def _store_copy(
        hub, stem: str, graph: dict, bindings: list | None = None
    ) -> tuple[str, str | None]:
        """Write one graph into the user folder, or raise the 500 that says why.

        *bindings* are the source file's ``pixlstash_bindings``, which the
        resolved graph has lost (``Source.bindings``). They go back in, or a
        copy of a file that opted out of a picture input would opt back in on
        its first run.
        """
        if bindings is not None:
            graph = {**graph, BINDINGS_KEY: bindings}
        try:
            return store_workflow_copy(hub, f"{stem}.json", graph)
        except (OSError, ValueError) as exc:
            logger.error(
                "A workflow copy named %r could not be written to the user folder: %s",
                stem,
                exc,
            )
            raise HTTPException(
                status_code=500,
                detail="PixlStash could not write the workflow file.",
            ) from exc

    @router.delete(
        "/workflows/{workflow_key}",
        summary="Delete an imported workflow",
        description=(
            "Send this card's workflow file to the system trash and take it "
            "off the card. Only a file this machine holds can be deleted: a "
            "workflow the library knows from its pictures has no file, and is "
            "hidden rather than deleted. The card itself and its pictures stay."
        ),
        response_model=WorkflowDeleted,
        responses={
            404: {
                "description": (
                    "This machine has no such card, or the card names a file "
                    "the user folder does not hold — a built-in, or one "
                    "already gone from disk."
                )
            },
            409: {"description": "This card has no workflow file to delete."},
            500: {"description": "The file could not be moved to the trash."},
        },
    )
    def delete_workflow(request: Request, workflow_key: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        card = _require_card(hub, workflow_key)
        if not card.file_name:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This workflow has no file on this machine — the library "
                    "knows it from its pictures. Hide it instead."
                ),
            )
        deleted = trash_user_workflow(hub, card.file_name)
        _announce(request, [workflow_key], "changed")
        return WorkflowDeleted(deleted=deleted, workflow_key=workflow_key)

    return router

"""The Workflows view: the grid of workflows, one opened, and what the owner writes.

**One entry per workflow** (#1623). A workflow is a group of topologies:
``auto:<core and families digest>`` for the automatic group, or a uuid hex for one the owner
merged or split (``hub/workflow_card_reads.workflow_index``). Its variants are
every recipe filed under those topologies, and checkpoint, LoRAs and values are
its *recipe* - a default recipe read off its best pictures, which the owner can
edit, and whatever a run or a saved recipe puts over it. The card key
(``workflow_key``) is internal storage: no path, body or answer here names it.
Everything that acts on ONE graph (the graph, export, the LoRA chain, model
swap and fix, duplicate, clone, delete) acts on the workflow's **base card**,
the busiest card of its base topology.

**Two databases, no join.** The rows live in the hub and are content-addressed;
the counts live in whichever vault is attached. Nothing here crosses that
boundary — the hub answers "which workflows exist", the vault answers "how many
of my pictures came from each", and a hash the hub has never heard of is simply
a workflow this machine does not have.

**Every route here is ``OWNER_ONLY``, and that is not the default speaking.**
The counts are read across every kept picture in the vault, so handing one to
a picture-, set- or project-scoped token would disclose the size of the whole
library one workflow at a time. The same goes for the picture ids the rail's
tiles are made of. Declared in ``pixlstash/authz/registry.py``, never inline.
They also refuse remote plaintext under ``require_ssl``, like the model-shelf
reads that name the same model files.

**The writes are the owner editing their own library**: a workflow's name,
notes and hidden flag, its default-recipe parameters, pins and picture inputs
(``hub/workflow_group_writes``). Each says "look again" on the way out
(``EventType.CHANGED_WORKFLOWS``, naming workflow ids).
"""

from __future__ import annotations

import functools
import json
import sqlite3
import os
import re
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field as dataclass_field
from difflib import SequenceMatcher
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

from pixlstash.hub import workflow_versions
from pixlstash.hub.workflow_card_reads import (
    Workflow,
    asset_names,
    card_index,
    find_card,
    find_workflow,
    group_picture_inputs,
    group_pins,
    instance_documents,
    manual_workflow_ids,
    model_fix_labels,
    model_fixes,
    workflow_of_variant,
)
from pixlstash.hub.workflow_card_writes import (
    record_loader_swaps,
    set_model_fix,
)
from pixlstash.hub.workflow_cards import STRIP_LORAS_FOR_STACKS
from pixlstash.hub.workflow_group_writes import (
    delete_manual_workflow,
    is_parameter_address,
    replace_group_picture_inputs,
    replace_group_pins,
    replace_parameter_defaults,
    set_default_lora,
    set_group_attributes,
)
from pixlstash.hub.workflows import (
    assets_for_topology_recipes,
    forgotten_asset_counts,
    recipes_for_topology,
    unvouched_model_values,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.routes._helpers import require_hub
from pixlstash.services.a1111_recipe import reduce_a1111
from pixlstash.services.comfyui_recipe_service import (
    CLIP_TYPE_BY_FAMILY,
    LORA_FILENAME_FIELD_RE,
    MAX_SEED_64,
    PIXLSTASH_ADAPTER_LOADER,
    apply_adapter,
    apply_filename_swap,
    apply_loader_rewrites,
    apply_lora_chain,
    apply_model_swap,
    apply_seeds,
    detect_lora_targets,
    detect_model_targets,
    format_prompt_rejection,
    insert_adapter,
    listed_as,
    listed_options,
    live_lora_targets,
    live_node_ids,
    lora_display_name,
    model_filename_fields,
    plan_loader_rewrites,
    plan_lora_chain,
    plan_lora_insertion,
    read_lora_chain,
    read_lora_chain_untyped,
    retype_text_encoders,
    swap_target,
    swap_to_adapter_loader,
)
from pixlstash.services.comfyui_service import (
    FILE_SAVE_NODE_CLASSES,
    PIXLSTASH_PICTURE_LOADER,
    _extract_output_node_ids,
    _process_comfyui_outputs,
    _submit_comfyui_prompt,
    _upload_image_to_comfyui,
    library_ids_named,
    swap_pixlstash_savers,
    unfed_picture_loaders,
)
from pixlstash.services import workflow_bindings, workflow_inbox
from pixlstash.services import workflow_run_service as run_service
from pixlstash.services.workflow_card_service import (
    BASE_MODEL_KINDS,
    BEST_SCORE,
    EDITED,
    LORA_OFF,
    SHELF_MODEL_GONE,
    SHELF_MODEL_UNNAMED,
    DefaultRecipe,
    converted_manual_document,
    read_grid,
    slot_kind,
    lora_modal_strength,
    shelf_filenames,
    workflow_defaults,
    workflow_lora_summary,
)
from pixlstash.services import saved_recipe_service
from pixlstash.services.model_shelf_service import (
    adapter_digest_index,
    families_clash,
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
    WorkflowChanged,
    WorkflowFileTooLarge,
    runnable_document,
    store_manual_workflow,
    store_over_workflow,
    trash_user_workflow,
    user_workflow_exists,
)
from pixlstash.services.workflow_export import (
    LORA_SLOTS,
    download_stem,
    scrub_for_export,
)
from pixlstash.services.workflow_identity import (
    CHECKPOINT_WIDGETS,
    CORE_ADDRESS_PREFIX,
    LIKENESS_GATE,
    UPSCALE_LATENT,
    UPSCALE_MODEL,
    UPSCALE_RESIZE,
    UPSCALE_ULTIMATE_SD,
    MODEL_PER_PASS,
    REFERENCES_PREFIX,
    REFINE,
    TWO_PASS,
    loader_swaps,
    core_node_labels,
    model_fix_kind,
)
from pixlstash.services.workflow_hash import (
    MODEL_EXTENSIONS,
    SECRET_FIELD_RE,
    SHELF_ID_FIELD,
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
from pixlstash.services.comfyui_ui_graph import convert_ui_graph_to_api
from pixlstash.services.workflow_io import (
    api_graph,
    with_converted_graph,
)
from pixlstash.services.workflow_parameters import set_latent_size, size_inputs
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
    find_comfy_workflow,
    iter_model_fields_api,
    loaded_model_widgets,
)
from pixlstash.stacking import (
    build_stack_filename_prefix,
    build_workflow_filename_prefix,
    strip_workflow_tags,
)
from pixlstash.utils.adapter_header import (
    FILE_CHECKPOINT,
    FILE_ENGINE,
    FILE_TEXT_ENCODER,
    FILE_UNKNOWN,
    FILE_VAE,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.known_base_models import family_of, modality_of
from pixlstash.utils.comfyui_utilities import NotAWorkflowError
from pixlstash.hub.workflow_origin import FILE_ORIGIN, INBOX_ORIGIN, live_file
from pixlstash.utils.workflow_ids import MANUAL_PREFIX, WORKFLOW_ID_PATTERN
from send2trash import TrashPermissionError

logger = get_logger(__name__)

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
    shelf_filename: str | None = Field(
        None,
        description=(
            "On a shelf loader (`checkpoint_id`), whose `name` is a shelf row "
            "id, the file that id names, or `(model no longer on the shelf)`. "
            "Null otherwise."
        ),
    )


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


class WorkflowSlotModel(BaseModel):
    """One model a workflow's base card names, in the slot it sits in.

    ``kind`` is the slot rather than the file (``checkpoint``, ``unet``,
    ``vae``, ``clip``, ``lora``…), because that is what a card row shows: it
    names the checkpoint only, and ⓘ lists the rest.

    ``name`` is ``None`` for a LoRA slot, which is a slot rather than a file —
    which LoRA went in it is the recipe's business (``recipe_values``,
    ``default_recipe``) — and for a model whose name was forgotten.

    On a **manual** workflow (``variant_count: 0``) these are not read off a
    stored slot list at all: they are recovered from its own document, which
    is best effort and comes back empty on a document the recovery cannot
    read. An empty ``models`` on such a card therefore means
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
    sha256: str | None = Field(
        None,
        description=(
            "The shelf file this slot loads, or null where the shelf does not "
            "hold it or the name could be more than one file. A LoRA slot "
            "carries one only where it carries a `name`: on a manual workflow, "
            "read off its own document."
        ),
    )
    base_model_family: str | None = Field(
        None,
        description=(
            "The family of the base model the shelf identified this file as "
            "(`sdxl`, `flux1`, `krea2`, …), filename guesses included: the "
            "same value as `base_model_family` on its `GET /adapters` or "
            "`GET /checkpoints` row, or null where the shelf cannot say. What "
            "a client compares a LoRA's `base_model_family` against to narrow "
            "the workflows it fits."
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
    slot_label: str | None = Field(
        None,
        description=(
            "The slot's label on the base topology. "
            "Null for a slot the cached list gave no label — which is every "
            "slot of a manual workflow, whose models are read off its "
            "document rather than a stored topology."
        ),
    )
    filename: str | None = Field(
        None,
        description=(
            "The value the recipe recorded for the slot, spelled as "
            "`default_recipe.models[].filename` spells it (a shelf loader's "
            "row id included), so a client matches a recipe model to its slot "
            "by identity. Null on a recipe's LoRA slot and where the recipe "
            "recorded no value. **Not** null where only `name` is: a shelf "
            "loader whose id the shelf no longer holds serves `name: null` "
            "and keeps the recorded id here."
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

    ``exposed`` is a parameter the owner added (``GET …/form-inputs`` lists
    what can be): its pictures do not vote on it, so a ``PUT …/defaults``
    without it removes the row rather than putting a computed value back.
    """

    label: str
    slot_label: str
    input_name: str
    value: bool | int | float | str
    provenance: str
    exposed: bool = False


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
    superseded: bool = Field(
        False,
        description=(
            "Made with a model the owner has since replaced in this workflow "
            "(`PUT /workflows/{workflow_id}/model-fix`). Such a picture covers only "
            "where no picture made with the workflow as it now stands can."
        ),
    )


class RecipeValue(BaseModel):
    """One value a workflow's kept pictures used, and on how many of them."""

    name: str = Field(
        description=(
            "The value as the picture grid's `comfyui_model` / `comfyui_lora` "
            "filters take it, so it filters to the pictures it counts."
        )
    )
    pictures: int


class RecipeValues(BaseModel):
    """The checkpoints and LoRAs a workflow's kept pictures used, most first."""

    checkpoints: list[RecipeValue] = Field(default_factory=list)
    loras: list[RecipeValue] = Field(default_factory=list)


class DefaultRecipeModel(BaseModel):
    """One model the default recipe loads, at its loader's address."""

    address: str
    kind: str = Field(description="The shelf file kind the loader takes.")
    filename: str | None = Field(
        None, description="The file, or null where its name was forgotten."
    )
    provenance: str
    shelf_filename: str | None = Field(
        None,
        description=(
            "On a shelf loader, whose `filename` is a shelf row id, the file "
            "that id names, or `(model no longer on the shelf)`. Null "
            "otherwise."
        ),
    )


class DefaultRecipeLora(BaseModel):
    """One LoRA of the default recipe."""

    asset: str = Field(
        "",
        description=(
            "The file's reference in the stored graphs (`asset:<sha256>` of "
            "its name), as `lora-summary` names it; empty where neither the "
            "pictures nor the shelf can name the file."
        ),
    )
    filename: str | None = None
    sha256: str | None = Field(
        None, description="The shelf's digest, or null where it cannot name it."
    )
    strength: float | None = None
    provenance: str


class DefaultRecipePayload(BaseModel):
    """What a workflow runs with when nobody says otherwise (#1622).

    Read off the newest instances of its 4★+ pictures (else of every picture),
    with the owner's edits (`provenance: edited`) over it. `stages` names each
    optional stage the base graph has and whether the recipe runs it, and
    `stage_details` says, for a stage that can be more than one thing, which
    this graph's is (`upscale`: "Upscale model (4x-ultrasharp)").
    """

    sampled: int = 0
    models: list[DefaultRecipeModel] = Field(default_factory=list)
    loras: list[DefaultRecipeLora] = Field(default_factory=list)
    values: list[WorkflowDefault] = Field(default_factory=list)
    stages: dict[str, bool] = Field(default_factory=dict)
    stage_details: dict[str, str] = Field(default_factory=dict)


class WorkflowCard(BaseModel):
    """One workflow on the Workflows grid (#1623).

    A **workflow** is a group of topologies; its variants are every recipe
    filed under them, and the checkpoint and LoRAs a picture used are its
    recipe, not its identity (`recipe_values` lists them). `models` and
    `loras` are its base card's slots, the graph a run starts from.

    **This shape is read by** `frontend/src/utils/workflowCard.js`, so the
    field names are snake_case and need no mapping layer.
    """

    id: str = Field(
        description="The workflow: `auto:<core and families digest>`, or `manual:<uuid hex>`."
    )
    name: str | None = Field(
        None,
        description=(
            "What the workflow is called: the owner's name, else its file's, "
            "else one generated from its base model and type."
        ),
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
        False,
        description=(
            "A workflow document the owner holds runs this workflow: a manual "
            "one, or an automatic one with a legacy workflow file."
        ),
    )
    manual: bool = Field(
        False,
        description=(
            "A manual workflow: its own stored document, imported, pulled, "
            "duplicated or extracted, never grouped with another. Deletable."
        ),
    )
    from_name: str | None = Field(
        None,
        description=(
            "The name of the workflow or recipe a manual workflow was made "
            "from (duplicate, fixed copy, clone, LoRA edit, extract), as it "
            "was called then; null for an imported or pulled one."
        ),
    )
    origin_category: Literal["comfyui", "pictures", "own"] = Field(
        "pictures",
        description=(
            "Where the workflow came from, for the Workflows view's filter: "
            "`comfyui` for one pulled from ComfyUI's saved workflows, "
            "`pictures` for an automatic one (`auto:`, known from pictures), "
            "`own` for every other manual one (imported, dropped in the "
            "inbox, a built-in, a duplicate, a fixed copy, a clone, a chain "
            "edit, an extract)."
        ),
    )
    versions: int = Field(
        1,
        description=(
            "How many versions of the workflow's document are kept: a ComfyUI "
            "file that changed, or a LoRA chain edit saved over the workflow, "
            "is a new version of it, and at most "
            "50 are kept (version 1 and the newest 49). 1 for an automatic "
            "workflow never saved over; one saved over keeps the graph its "
            "pictures held as version 1."
        ),
    )
    version: int = Field(
        1,
        description=(
            "The current version's number. Versions are numbered for good, "
            "so past the 50 kept it is more than `versions`. 1 for an "
            "automatic workflow never saved over."
        ),
    )
    version_at: str | None = Field(
        None,
        description=(
            "When the current version was stored (ISO 8601). Null for an "
            "automatic workflow never saved over."
        ),
    )
    hidden: bool = Field(
        False,
        description=(
            "The owner has hidden this workflow. **On the GRID** it is true "
            "only for one `include_hidden` let in. **On the detail route it is "
            "always the workflow's own state**: `GET /workflows/{workflow_id}` "
            "opens a hidden workflow by design, which is how it can be "
            "unhidden."
        ),
    )
    models: list[WorkflowSlotModel] = Field(default_factory=list)
    loras: list[WorkflowSlotModel] = Field(default_factory=list)
    picture_count: int = 0
    rating: float | None = Field(
        None,
        description="Mean of the stars this workflow has; null when it has none.",
    )
    rating_counts: list[int] = Field(
        default_factory=lambda: [0] * 5,
        description="How many of its pictures carry each star, 1 star first.",
    )
    covers: list[WorkflowCover] = Field(
        default_factory=list,
        description="Up to three cover pictures, the cover first.",
    )
    saved_recipe_count: int = 0
    defaults: list[WorkflowDefault] = Field(
        default_factory=list,
        description=(
            "The featured values it starts from: empty on the grid, the "
            "default recipe's `values` on the detail route."
        ),
    )
    base_topology: str | None = Field(
        None,
        description=(
            "The topology a run starts from and an export writes: the one "
            "with the most stages, then LoRA loaders, then kept pictures."
        ),
    )
    topologies: list[str] = Field(
        default_factory=list,
        description="Every topology of the workflow, sorted.",
    )
    specials: list[str] | None = Field(
        None,
        description=(
            "The post-processing the base graph carries, from `upscale`, "
            "`face_detailer`, `seed_variance` and `intermediate_save`. **Null and `[]` are different answers**: null "
            "means the graph has not been read for it yet, `[]` means it was "
            "read and has none."
        ),
    )
    variant_count: int = 0
    last_used: str | None = Field(
        None,
        description=(
            "When a kept picture was last made by any variant of this "
            "workflow. Null when it has no kept pictures."
        ),
    )
    created_at: str | None = Field(
        None,
        description=(
            "When this machine first had the workflow (ISO 8601): a manual "
            "one's storing, an automatic one's first recipe."
        ),
    )
    changed_at: str | None = Field(
        None,
        description=(
            "When the workflow last changed (ISO 8601): a manual one's newest "
            "version, an automatic one's newest recipe. The owner's own edits "
            "to name or defaults do not count."
        ),
    )
    rank: float = Field(
        0.0,
        description=(
            "The Bayesian cover rank the grid is ordered by. Not `rating`: "
            "it is smoothed towards the library's mean so workflows can be "
            "ordered against each other, and is meaningless on its own."
        ),
    )
    ghosts: int = Field(
        0,
        description=(
            "Picture ghosts this workflow's variants keep for the active "
            "library: the thumbnail and prompt of a picture the library no "
            "longer has."
        ),
    )
    model_ghosts: int = Field(
        0,
        description=(
            "How many VALUES this workflow's variants name that the shelf does "
            "not hold - a model filename, or a `*_sha256` digest. Read it as "
            "'something here is gone', not as a count of models."
        ),
    )
    recipe_values: RecipeValues = Field(
        default_factory=RecipeValues,
        description=(
            "The checkpoints and LoRAs its kept pictures used, with how many "
            "pictures each, most used first: the input to the picture filter."
        ),
    )
    default_recipe: DefaultRecipePayload | None = Field(
        None,
        description=(
            "Null on the grid (it samples stored runs per workflow); filled "
            "on the detail route and on every write's answer."
        ),
    )


class WorkflowCards(BaseModel):
    """``GET /workflows``: the grid, one entry per workflow, and what it left out.

    ``one_offs`` and ``hidden`` are counts rather than rows on purpose: both
    sets are excluded from ``cards``, and the view offers them as a way back in
    rather than as clutter. Both still open on the detail route.
    """

    cards: list[WorkflowCard]
    one_offs: int = 0
    hidden: int = 0


class ModelFixRow(BaseModel):
    """One model replaced in a workflow, as the Workflow tab shows it."""

    slot_label: str
    was: str = Field(description="The file the workflow originally loaded.")
    now: str = Field(description="The file it loads instead.")
    slot_kind: Literal["checkpoint", "vae", "text_encoder"] = Field(
        description=(
            "The kind of model the slot takes, which is also the shelf kind "
            "of `now`: the Workflow tab row the fix belongs to."
        )
    )


class WorkflowCardDetail(BaseModel):
    """``GET /workflows/{workflow_id}``: one workflow opened."""

    card: WorkflowCard
    notes: str | None = None
    hidden: bool = False
    variants: list[WorkflowVariant] = Field(default_factory=list)
    pins: list[ParameterAddress] | None = Field(
        None,
        description=(
            "The parameters the owner pinned, addressed as `PUT "
            "/workflows/{workflow_id}/pins` takes them. `null` is a workflow nobody has "
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
            "still says which file it loads. A PixlStash shelf loader's id is "
            "served as the file that shelf row names, `(model no longer on the "
            "shelf)` or `(unnamed shelf model)`. `[]` is a graph that was read and "
            "loads no base model (an upscaler); `null` is one that was not "
            "read - the card names its base model, or has no graph to read."
        ),
    )
    model_fixes: list[ModelFixRow] = Field(
        default_factory=list,
        description=(
            "The models the owner replaced in this workflow's base graph "
            "because the original is gone (`PUT /workflows/{workflow_id}/"
            "model-fix`). A run loads `now` wherever the graph names `was`."
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
# A pin list and a parameter form are small.
MAX_DEFAULTS = 200
MAX_PINS = 200
MAX_INPUTS = 200

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

# A workflow is named either by ``auto:`` and the core hash that IS the
# automatic group, or by ``manual:`` and the uuid hex of a manual one. Checked
# rather than trusted, so a malformed id is a 422 naming the parameter instead
# of a write against a workflow nothing will ever read. Checked with
# `fullmatch`: `$` also matches before a trailing newline, so `.match` let
# `<id>\n` through.
_WORKFLOW_ID_RE = re.compile(WORKFLOW_ID_PATTERN)

# The extension a picture keeps when it is uploaded into ComfyUI's input folder.
# Anything else is dropped rather than carried into a name another program
# resolves as a path.
_UPLOAD_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
_PIXEL_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class WorkflowCardEdit(BaseModel):
    """``PATCH /workflows/{workflow_id}``: the fields the request carries, and no more.

    ``null`` for ``name`` or ``notes`` clears it, which is the workflow's own
    default and not the same as leaving the field out - so the handler reads
    ``exclude_unset`` rather than testing for ``None``.
    """

    name: str | None = Field(None, max_length=MAX_NAME_LENGTH)
    notes: str | None = Field(None, max_length=MAX_NOTES_LENGTH)
    hidden: bool | None = None


def _one_row_per_address(entries) -> None:
    """Refuse a whole-set write that names one parameter twice.

    Both tables these feed are keyed on the address ``slot_label/input_name``, so a
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
    """One parameter of a workflow, by address: ``<slot_label>/<input_name>``.

    ``slot_label`` is a ``core:<label>`` shared by every topology of the
    workflow, or a label on its base topology; never a node id, which every
    re-serialisation renumbers.
    """

    slot_label: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    input_name: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)


class CardDefault(ParameterAddress):
    """One parameter the owner has set this workflow to start from."""

    value: bool | int | float | str


class CardDefaults(BaseModel):
    """``PUT /workflows/{workflow_id}/defaults``: the whole parameter edit set.

    Whole rather than per parameter, because the form shows every featured
    parameter at once - so an empty list is somebody clearing them all, which
    is a state and not a no-op. Parameters only: an address naming a model
    loader's file or a ``lora:`` is refused, because those rows are the
    default recipe's models and LoRAs, which this form does not show.
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
            address = (
                f"{default.slot_label}{OVERRIDE_ADDRESS_SEPARATOR}{default.input_name}"
            )
            if not is_parameter_address(address):
                raise ValueError(
                    f"{address!r} names a model or a LoRA, not a parameter."
                )
        _one_row_per_address(value)
        return value


class DefaultLoraEdit(BaseModel):
    """``PUT /workflows/{workflow_id}/default-lora``: one LoRA in or out.

    ``asset`` names one of the workflow's own LoRAs as ``lora-summary`` does.
    ``include`` true puts it in the default recipe (at ``strength``, else the
    strength its pictures used most, else 1), false keeps it out, and null
    drops the edit so its pictures decide again.
    """

    asset: str | None = Field(None, pattern=r"^asset:[0-9a-f]{64}$")
    include: bool | None
    strength: float | None = Field(None, ge=-100, le=100)
    # Clearing only: the edit's own digest, as `default_recipe.loras[].sha256`
    # has it, which names an edit whose file has since left the shelf.
    sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _names_one_lora(self) -> "DefaultLoraEdit":
        if self.include is not None and self.asset is None:
            raise ValueError("Adding or excluding a LoRA names it by `asset`.")
        if self.include is None and self.asset is None and self.sha256 is None:
            raise ValueError("Clearing an edit names it by `asset` or `sha256`.")
        return self


# A model fix's slot kinds as its refusals say them.
_FIX_KIND_NAMES = {
    FILE_CHECKPOINT: "checkpoint",
    FILE_VAE: "VAE",
    FILE_TEXT_ENCODER: "text encoder",
}


class ModelFix(BaseModel):
    """``PUT /workflows/{workflow_id}/model-fix``: replace a model, or undo that.

    ``was`` is the file the workflow names, as the graph spells it; ``now`` a
    file on the model shelf, or ``null`` to load the original again.
    ``slot_kind`` names the kind of slot being fixed; the shelf kind of
    ``now`` decides it when left out, and an undo without it undoes every
    kind.
    """

    was: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)
    now: str | None = Field(None, min_length=1, max_length=MAX_VALUE_LENGTH)
    slot_kind: Literal["checkpoint", "vae", "text_encoder"] | None = None


class CardPins(BaseModel):
    """``PUT /workflows/{workflow_id}/pins``: which parameters the form shows first.

    ``null`` forgets the workflow's pins, so the defaults apply again; ``[]`` is
    somebody who unpinned everything, which the hub keeps as a row.
    """

    pins: list[ParameterAddress] | None = Field(None, max_length=MAX_PINS)


class FormInput(BaseModel):
    """One input of a workflow's graph a parameter row can set."""

    slot_label: str
    input_name: str
    value: bool | int | float | str = Field(
        description="What the workflow's graph holds, which a run uses unasked."
    )
    kind: Literal["number", "text", "boolean", "choice"] | None = Field(
        None,
        description=(
            "What ComfyUI's `object_info` declares it as. Null where ComfyUI "
            "did not answer or does not know the node; the type of `value` is "
            "then the only clue."
        ),
    )
    options: list[str] | None = Field(
        None, description="What a `choice` may be set to, as ComfyUI lists it."
    )
    exposed: bool = Field(
        description="Whether it is one of the workflow's parameters already."
    )


class FormNode(BaseModel):
    """One node of a workflow's graph, with the inputs a row can set."""

    node_id: str = Field(
        description=(
            "The node's id in the graph read, for telling two nodes of one "
            "class apart on screen. Not an address: every re-serialisation "
            "renumbers it."
        )
    )
    title: str
    class_type: str
    inputs: list[FormInput]


class WorkflowFormInputs(BaseModel):
    """``GET /workflows/{workflow_id}/form-inputs``."""

    nodes: list[FormNode] = Field(default_factory=list)


class CardPictureInput(ParameterAddress):
    """How one picture input of a workflow is filled.

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
    """``PUT /workflows/{workflow_id}/inputs``: its whole picture-input setup."""

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


class WorkflowLoraUse(BaseModel):
    """One LoRA file across a workflow, as the inspector's pile lists it."""

    asset: str = Field(
        description=(
            "The file's reference in the stored graphs (`asset:<sha256>`): "
            "what the picture listing's `workflow_lora` takes. Empty on the "
            "row for pictures that loaded none of the LoRAs that change."
        )
    )
    filename: str | None = Field(
        None, description="The file, or null where its name was forgotten."
    )
    name: str | None = Field(
        None,
        description=(
            "What to call it: the model shelf's title where exactly one shelf "
            "model answers to the file, else the filename without folders, "
            "extension or quant. Null where the name was forgotten."
        ),
    )
    on_shelf: bool = False
    sha256: str | None = Field(
        None,
        description=(
            "The one shelf LoRA file this is, by content digest (not the "
            "`asset` hash, which is of its name), or null when the shelf "
            "cannot say. `PUT …/default-lora` accepts exactly these."
        ),
    )
    pictures: int = 0
    picture_ids: list[int] = Field(
        default_factory=list,
        description="Its best kept pictures, best first, for a strip. At most 3.",
    )


class WorkflowLoraSummary(BaseModel):
    """``GET /workflows/{workflow_id}/lora-summary``: its LoRAs in two parts.

    ``shared`` is the LoRAs every kept picture of the workflow loaded;
    ``varying`` is the rest, most pictures first; ``without`` is the pictures
    that loaded none of ``varying``. ``pictures`` is what both are counted
    against, and leaves out pictures whose graph could not be read.
    """

    workflow_id: str
    pictures: int = 0
    shared: list[WorkflowLoraUse] = Field(default_factory=list)
    varying: list[WorkflowLoraUse] = Field(default_factory=list)
    without: WorkflowLoraUse | None = None
    cover_asset: str | None = Field(
        None,
        description=(
            "Which of `varying` the `cover` picture loaded, for the top of "
            "the pile. Null without `cover`, or when it loaded none of them."
        ),
    )


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


class RunAddedLora(BaseModel):
    """One shelf LoRA a run ADDS, in a loader of its own, over whatever the graph loads.

    No slot is named: a new loader is spliced in right after the model source
    on the run's own copy (``plan_lora_insertion`` / ``insert_adapter``), so the
    workflow keeps every LoRA it already loads and a graph with no loader at
    all can still take one. A LoRA the graph already loads is not added a
    second time; its strengths are set on the loader that has it.
    """

    sha256: str = Field(min_length=1, max_length=64)
    strength_model: float | None = Field(None, ge=-10.0, le=10.0)
    strength_clip: float | None = Field(None, ge=-10.0, le=10.0)


class RunLoraSlot(BaseModel):
    """One LoRA slot a run skips (#1478), addressed as :class:`RunLora` addresses it.

    A skip is for THIS run: the loader is bypassed on the run's own copy of the
    graph and the stored workflow keeps it. Editing the workflow for good is
    ``PUT /workflows/{workflow_id}/lora-chain``.
    """

    node_id: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    field: str = Field("lora_name", min_length=1, max_length=MAX_LABEL_LENGTH)


class RunChoice(BaseModel):
    """A sampler or scheduler this run uses in place of the graph's.

    The answer to ``missing_choices``, addressed by node as that reason names
    it. For THIS run only; the workflow keeps its own value.
    """

    node_id: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    field: Literal["sampler_name", "scheduler"]
    value: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)


class RunModel(BaseModel):
    """One model a run loads at one loader, over the default recipe (#1622).

    ``address`` is ``core:<label>/<widget>`` (or a base topology's
    ``<slot label>/<widget>``), the address a workflow's default recipe names
    its models by. Exactly one of ``filename`` (as ComfyUI or the shelf spells
    it) and ``sha256`` (a shelf model).
    """

    address: str = Field(min_length=3, max_length=MAX_LABEL_LENGTH)
    filename: str | None = Field(None, min_length=1, max_length=MAX_VALUE_LENGTH)
    sha256: str | None = Field(None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def _one_way_to_name_it(self) -> "RunModel":
        if (self.filename is None) == (self.sha256 is None):
            raise ValueError("Name the model by filename or by sha256, not both.")
        if OVERRIDE_ADDRESS_SEPARATOR not in self.address:
            raise ValueError("A model address is <slot>/<widget>.")
        return self


class RunValue(ParameterAddress):
    """One parameter this run sets, over the workflow's default recipe."""

    value: bool | int | float | str


class RunInput(ParameterAddress):
    """One picture input this run fills, by the address the pre-flight gave.

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
    ask "run what made these", a saved recipe asks "run this look", and a
    ``workflow_id`` asks "run this workflow". ``target`` names the workflow
    that actually runs instead, over the pictures selected.

    **The default recipe and the request's values are applied here**, never
    written back into a graph: the stored document is content-addressed and
    rewriting it would change the identity of what is being run.
    """

    picture_ids: list[Annotated[int, Field(le=MAX_PICTURE_ID)]] = Field(
        default_factory=list, max_length=MAX_RUN_PICTURES
    )
    saved_recipe_id: int | None = None
    # A workflow (#1622): `auto:<core and families digest>` or an owner's group. It runs its
    # base topology's graph with its DEFAULT RECIPE applied by the server, and
    # this body's values, models, LoRAs and skipped stages over that. Only this
    # source goes without a LoRA attached to a person: who a picture is of is
    # a recipe's business, and `add_loras` is how such a run names somebody.
    workflow_id: str | None = Field(None, max_length=MAX_LABEL_LENGTH)

    # A workflow to run instead of the source's, over its pictures; with its
    # default recipe applied, as a `workflow_id` source is.
    target: str | None = Field(None, max_length=MAX_LABEL_LENGTH)

    prompt: str | None = Field(None, max_length=MAX_PROMPT_LENGTH)
    negative: str | None = Field(None, max_length=MAX_PROMPT_LENGTH)
    loras: list[RunLora] = Field(default_factory=list, max_length=MAX_RUN_LORAS)
    # LoRAs this run adds in new loaders of their own, after `loras` and the
    # recipe's LoRAs are placed: nothing the graph loads is replaced, so a
    # workflow's own LoRAs keep running (Create with LoRA…). Needs ComfyUI's
    # node types, so an unreachable ComfyUI is a 400 rather than a run that
    # quietly leaves the LoRA out.
    add_loras: list[RunAddedLora] = Field(
        default_factory=list, max_length=MAX_RUN_LORAS
    )
    # LoRA slots this run goes without (#1478): each loader is bypassed on the
    # run's copy, its consumers reading its inputs. Applied to every group of
    # the run whose graph has that slot; a slot no graph has is a 400.
    skip_loras: list[RunLoraSlot] = Field(
        default_factory=list, max_length=MAX_RUN_LORAS
    )
    # Optional stages this run goes without (#1621): each is bypassed on the
    # run's copy, what the stage alone read is pruned, and a card whose stage
    # cannot be taken out is refused with `stage_not_skippable`, never run whole.
    skip_stages: list[
        Literal["upscale", "face_detailer", "seed_variance", "intermediate_save"]
    ] = Field(default_factory=list, max_length=4)
    values: list[RunValue] = Field(default_factory=list, max_length=MAX_DEFAULTS)
    # Samplers and schedulers this run swaps in for ones this ComfyUI does not
    # list (`missing_choices`), applied last so they win over `values`.
    choices: list[RunChoice] = Field(default_factory=list, max_length=MAX_DEFAULTS)
    # The models this run loads, by loader address (#1622). A model from
    # another family than the one it replaces is flagged on the group
    # (`family_mismatch`) and still runs.
    models: list[RunModel] = Field(default_factory=list, max_length=MAX_DEFAULTS)

    count: int = Field(1, ge=1, le=MAX_RUNS_PER_REQUEST)
    seed_mode: Literal["new", "keep", "fixed"] = "new"
    seed: int | None = Field(None, ge=0, le=MAX_SEED_64)
    destination: RunDestination | None = None
    # What fills each picture input of the workflow (#1457), first answer wins:
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
    """One picture input of the graph a group runs, and what fills it.

    ``fill`` is the server's answer and the client's to show, never to
    re-derive: ``request`` (this body's entry), ``fixed`` (the workflow's pin),
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


class RunPrompt(BaseModel):
    """Where one group's graph takes a run's prompts, and what it holds (#1832)."""

    # False where a prompt sent with the run has nowhere to go: no prompt node
    # was found, or it keeps its text in a field PixlStash does not write.
    positive_settable: bool = False
    negative_settable: bool = False
    # The graph's own positive prompt, before this request's was written. Null
    # where it is not a literal or the graph's prompt nodes disagree.
    positive_text: str | None = None


class RunGroup(BaseModel):
    """One graph a request resolved to, and whether it would run.

    ``reasons`` empty is the only thing that means "this would run". Each
    reason is a code and its payload, so a panel can act on it rather than
    print it. ``substitutions``, ``bypassed_loras`` and ``unplaced_loras`` are
    not reasons: they say what this run will do differently from what the graph
    or the recipe says, which is a fact to report rather than a refusal to act
    on.
    """

    # The workflow this group runs: the one named, or for a picture-sourced
    # run the workflow of the picture's variant. Null for a picture in none.
    workflow_id: str | None = None
    # The version of a manual workflow's document this group runs, after Run
    # and Open's check against ComfyUI; what its pictures record
    # (`picture.run_workflow_version`). For an automatic workflow the owner
    # saved over, the version of its graph (recorded on no picture); null
    # for one never saved over.
    workflow_version: int | None = None
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
    # Every picture input of the graph, enumerated from the graph this run
    # resolved with the workflow's stored setup laid over it (#1457). It is
    # the WHOLE set, which is what makes the whole-set
    # `PUT /workflows/{workflow_id}/inputs` safe to call after reading it: a
    # client never writes back a set it has not seen.
    picture_inputs: list[RunPictureInput] = Field(default_factory=list)
    # A custom node this ComfyUI lacks, replaced by what PixlStash already does
    # (#1463): a seed node, whose link becomes a literal the run's
    # own seed pass then writes, or a text node, whose string is inlined.
    # Reported on the same terms as a bypass: the
    # graph that runs is not the one the card names, and the owner is told so
    # before the run rather than after.
    replaced_nodes: list[dict] = Field(default_factory=list)
    # A core LoRA loader whose file this ComfyUI does not have, loading the
    # shelf LoRA through the ComfyUI-PixlStash loader instead, by its hash:
    # `[{node_id, class_type, file, sha256, requested}]`. A repair like
    # `replaced_nodes`: the graph that runs is not the one the card names.
    # `requested: false` is a swap of the workflow's own LoRA, which "Save
    # fixed workflow" (`POST /workflows/{workflow_id}/fixed-copy`) keeps;
    # `true` is one for a LoRA this request named, which it does not.
    swapped_loaders: list[dict] = Field(default_factory=list)
    # What this run does that the owner may not expect, and runs anyway
    # (#1620 Q3): `family_mismatch` for a model loaded in place of one made for
    # another family or modality, `model_not_applied` for one this ComfyUI
    # cannot load where it was asked for, `prompt_not_applied` (with its
    # `side`) for a prompt this request sent that the graph has no place for
    # (#1832). Not reasons: nothing here refuses.
    flags: list[dict] = Field(default_factory=list)
    # Null for a group refused before its graph was resolved.
    prompt: RunPrompt | None = None


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
    """``GET /workflows/{workflow_id}/export``: a ComfyUI file, minus every run of it.

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
        description="Where the graph was resolved from: edit, file, picture or instance."
    )


class WorkflowRunnableGraph(BaseModel):
    """``GET /workflows/{workflow_id}/graph``: the graph as it runs, for this owner's ComfyUI.

    The unscrubbed sibling of :class:`WorkflowExport`, built by Run's own plan
    so it is the graph Run would submit. This hands it to the ComfyUI-PixlStash
    node, which
    opens it in the ComfyUI editor when the Workflow tab's *Open in ComfyUI*
    sends ComfyUI there with ``?pixlstash_workflow=<workflow_id>``.
    """

    name: str = Field(description="What to call the workflow in ComfyUI.")
    workflow: dict | None = Field(
        description=(
            "The ComfyUI API-format graph, or null when `needs_conversion`: "
            "there is only an editor file, for the node to open and convert."
        )
    )
    needs_conversion: bool = Field(
        False,
        description=(
            "True when PixlStash could not build a runnable graph but the "
            "workflow has a live ComfyUI file (`comfyui_file`): the node opens "
            "that file and converts it in ComfyUI. `detail` says why."
        ),
    )
    detail: str | None = Field(
        None, description="With `needs_conversion`: why the file did not convert."
    )
    source: str | None = Field(
        description="Where the graph was resolved from: edit, file, picture or instance."
    )
    seedless: bool = Field(
        False,
        description=(
            "Always false: a stored recipe's nulled seeds are filled before "
            "the graph is sent. Kept for ComfyUI-PixlStash nodes that read it."
        ),
    )
    forgotten: int = Field(
        0,
        description="How many model names the library could no longer name.",
    )
    comfyui_file: str | None = Field(
        None,
        description=(
            "The ComfyUI file this workflow was pulled from, relative to "
            "ComfyUI's `workflows/` user folder (`portraits/flux.json`), "
            "when it has one there that is neither gone nor deleted here, at "
            "the owner's ComfyUI address. The node opens that file, so Save "
            "writes back to it, and falls back to `workflow` without it. "
            "Checked against ComfyUI before this answer, so it is the version "
            "`workflow` was built from."
        ),
    )


class WorkflowFile(BaseModel):
    """One workflow file this machine now holds, and the workflow it is in."""

    name: str = Field(description="What the file is called in the user folder.")
    workflow_id: str | None = Field(
        None,
        description=(
            "The workflow it was filed in, or null when it could not be filed "
            "or its graph is in no workflow yet."
        ),
    )


class FixedWorkflowCopy(WorkflowFile):
    """The file written by ``POST /workflows/{workflow_id}/fixed-copy``."""

    changes: list[str] = Field(
        default_factory=list,
        description="What the copy does differently from the original, one line each.",
    )


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


class LoraChainPass(BaseModel):
    """The node a lane's model ends in: its sampler, as the owner names it."""

    node_id: str
    class_type: str | None = None
    title: str | None = Field(
        None, description="The node's ComfyUI title, when it is not just its class."
    )


class LoraChainLane(BaseModel):
    """One pass past the fork: the loaders only it reads, and what reads them."""

    source: LoraChainSource | None = Field(
        None,
        description="The lane's own model, when the workflow loads one per pass; "
        "null for a lane off the shared trunk.",
    )
    sampler: LoraChainPass
    sink: LoraChainSink = Field(default_factory=LoraChainSink)
    loaders: list[LoraChainLoader] = Field(default_factory=list)
    added_loader_class: str | None = Field(
        None, description="The loader class Add would insert in this lane."
    )


class LoraChain(BaseModel):
    """``GET /workflows/{workflow_id}/lora-chain``: the LoRA chain as the editor shows it.

    A straight chain is ``loaders`` between ``source`` and ``sink``. Where the
    model forks, ``loaders`` is the trunk every pass reads and ``lanes`` holds
    one entry per pass; a workflow loading a model per pass has no ``source``
    and every lane names its own.
    """

    workflow_id: str
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
    lanes: list[LoraChainLane] = Field(
        default_factory=list,
        description="One per pass where the model forks, in run order; empty "
        "for a straight chain.",
    )
    branch_note: str | None = Field(
        None,
        description=(
            "Why the chain stops before some of the workflow's loaders: a node "
            "that is not a loader, or a further branch, so those loaders are "
            "left as they are."
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
    """``PUT /workflows/{workflow_id}/lora-chain``: the whole chain, in apply order.

    ``entries`` is the trunk (the whole chain when it is straight); ``lanes``
    one list per lane of the chain as read, in its order. A loader may land in
    any of them, which is how it crosses the fork. ``lanes`` left out keeps
    every pass as it is.
    """

    entries: list[LoraChainEntry] = Field(
        default_factory=list, max_length=MAX_RUN_LORAS
    )
    lanes: list[list[LoraChainEntry]] | None = Field(None, max_length=MAX_RUN_LORAS)

    @model_validator(mode="after")
    def _within_the_cap(self) -> "LoraChainEdit":
        # The cap is on the whole tree, not per list: every entry may cost a
        # shelf lookup before the plan is checked.
        total = len(self.entries) + sum(len(lane) for lane in self.lanes or [])
        if total > MAX_RUN_LORAS:
            raise ValueError(f"at most {MAX_RUN_LORAS} loaders in one chain")
        return self

    name: str | None = Field(None, max_length=MAX_NAME_LENGTH)
    dry_run: StrictBool = False
    # Save over this workflow instead of as a copy: `name` is then not read.
    overwrite: StrictBool = False


class LoraChainChange(BaseModel):
    """One line of what a chain edit changes, in the owner's words."""

    kind: Literal["deleted", "added", "moved", "strength", "rewired"]
    node_id: str
    text: str


class LoraChainSaved(BaseModel):
    """What a chain edit changed, and the workflow the edited graph is in."""

    dry_run: bool = False
    name: str | None = Field(
        None,
        description=(
            "What the new workflow is called; null on a dry run and on an "
            "overwrite, which names nothing."
        ),
    )
    workflow_id: str | None = Field(
        None,
        description=(
            "The workflow the edited graph is in: the new one, or this one "
            "on an overwrite; null on a dry run."
        ),
    )
    overwritten: bool = Field(
        False, description="Whether the edit was saved over this workflow."
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
    """One model file the workflow's graph names, and the shelf row it is, if any."""

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
            "Which step answered: grouped (a hand-made workflow set pairs it "
            "with this checkpoint; listed first), checkpoint (ran with this "
            "checkpoint), base_model (with another of the same base model), "
            "family (with another of the same architecture) or declared "
            "(nothing has run with it: its file layout is one the "
            "architecture declares, which is not evidence it suits)."
        )
    )
    recipes: int = Field(
        description="How many recipes name the two together; 0 for grouped."
    )
    history_runs: int = Field(
        default=0,
        description=(
            "How many runs in ComfyUI's own history, as of the last workflow "
            "pull, loaded the two together; 0 for grouped."
        ),
    )
    set_name: str | None = Field(
        None,
        description=(
            "grouped only: the name of the newest workflow set grouping it, "
            "null when that set has no name."
        ),
    )
    prepick: bool = Field(
        True,
        description=(
            "Whether the dialog may select it for the owner. False for a "
            "grouped file when the matching sets group several of that kind."
        ),
    )


class SwapFlag(BaseModel):
    """A LoRA or ControlNet trained on another family than the new checkpoint."""

    filename: str
    kind: str
    base_model: str
    family: str
    modality: str | None = None


class ModelFixCandidate(BaseModel):
    """A shelf model that can replace a missing one (``?replacing=``)."""

    id: int
    filename: str
    display_name: str | None = None
    via: str | None = Field(
        None,
        description=(
            "For a VAE or text encoder, the evidence it goes with the "
            "workflow's checkpoint (`SwapProposal.via`; `declared` is "
            "untested). Null for a checkpoint."
        ),
    )
    loader: str | None = Field(
        None,
        description=(
            "Set when the workflow's own loader cannot load this file and a "
            "run loads it through this ComfyUI-PixlStash node instead "
            "(`PixlStashVAELoader`, `PixlStashCLIPLoader`), swapped in for "
            "the original. Null when the original loader loads it."
        ),
    )


class ModelSwapOptions(BaseModel):
    """``GET /workflows/{workflow_id}/model-swap``: what the clone dialog draws."""

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
    replacements: list[ModelFixCandidate] | None = Field(
        None,
        description=(
            "Only with `?replacing=`: the shelf models the Workflow tab may "
            "offer in place of that file (`PUT …/model-fix`). A VAE or text "
            "encoder must go with the workflow's checkpoint (a workflow set "
            "grouping them, or recipes and ComfyUI runs that loaded them "
            "together), a checkpoint must have the missing one's base model "
            "(the shelf's, else the one the graph's LoRAs and ControlNets "
            "agree on) unless no loadable one does, and every kind must be "
            "one the loader naming the file can load: listed by it when "
            "ComfyUI answers, of the same file type when it does not."
        ),
    )
    replacements_narrowed: bool | None = Field(
        None,
        description=(
            "Only with `?replacing=` a checkpoint: true when `replacements` "
            "were held to the missing one's base model, false when nothing "
            "said which it was, or no loadable checkpoint has it, and every "
            "loadable checkpoint is offered."
        ),
    )
    replacements_reason: (
        Literal[
            "no_checkpoint",
            "none_go_with_it",
            "none_loadable",
            "needs_pixlstash_nodes",
        ]
        | None
    ) = Field(
        None,
        description=(
            "Why `replacements` is empty: the checkpoint is not on the shelf, "
            "so nothing says what goes with it; nothing does; "
            "nothing that does can be loaded by this loader; or something "
            "could, through a PixlStash loader, and ComfyUI-PixlStash is not "
            "installed."
        ),
    )


# Ceiling on one clone's swap map. A graph names a handful of model files; the
# bound is here so a hand-made request cannot post an unbounded map.
MAX_SWAPS = 64
# Workflow sets one clone-plan read may ask about, and models per set: a
# shelf's worth, since one refusal blanks the whole dialog.
MAX_SET_PLANS = 2000
MAX_SET_MODELS = 1000


class CloneLoras(BaseModel):
    """The LoRA chain a clone is written with, in ``PUT …/lora-chain``'s shape.

    Read against the ORIGINAL workflow's chain: a kept loader by ``node_id``,
    a new one by shelf ``sha256``, and every loader left out deleted, so
    ``entries: []`` clears them.
    """

    entries: list[LoraChainEntry] = Field(
        default_factory=list, max_length=MAX_RUN_LORAS
    )
    lanes: list[list[LoraChainEntry]] | None = Field(None, max_length=MAX_RUN_LORAS)

    @model_validator(mode="after")
    def _within_the_cap(self) -> "CloneLoras":
        total = len(self.entries) + sum(len(lane) for lane in self.lanes or [])
        if total > MAX_RUN_LORAS:
            raise ValueError(f"at most {MAX_RUN_LORAS} loaders in one chain")
        return self


class CloneWithModels(BaseModel):
    """``POST /workflows/{workflow_id}/clone-with-models``."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    swaps: dict[str, str] = Field(
        description="The graph's filename -> the filename to load instead."
    )
    loras: CloneLoras | None = Field(
        None,
        description=(
            "The LoRA chain to write the clone with; null keeps the workflow's own."
        ),
    )

    @field_validator("swaps")
    @classmethod
    def _model_files_only(cls, swaps: dict[str, str]) -> dict[str, str]:
        # A replacement without a model extension would be nulled out of the
        # structural hash, so the clone would fold onto the original's variant.
        if not swaps or len(swaps) > MAX_SWAPS:
            raise ValueError(f"swaps must name 1 to {MAX_SWAPS} files")
        for was, now in swaps.items():
            if not was.strip() or not now.strip() or len(now) > 1024:
                raise ValueError("swaps must map a filename to a filename")
            if not now.lower().endswith(MODEL_EXTENSIONS):
                raise ValueError(f"{now!r} is not a model file")
        return swaps


class SetCloneAsk(BaseModel):
    """One workflow set to plan a clone onto: a key of the caller's, its models."""

    key: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    checkpoint_ids: list[int] = Field(
        default_factory=list,
        max_length=MAX_SET_MODELS,
        description=(
            "The set's checkpoints (or diffusion files): one per base loader "
            "of a two-model graph, empty when none."
        ),
    )
    model_ids: list[int] = Field(
        max_length=MAX_SET_MODELS,
        description="Its other models: VAEs and text encoders are used.",
    )
    picks: dict[str, int] = Field(
        default_factory=dict,
        max_length=MAX_SET_MODELS,
        description=(
            "The owner's own pairing, a VAE or text-encoder file of the graph "
            "-> the model of `model_ids` its loaders take: applied before "
            "anything is paired for them. One for a file the graph has no "
            "loader of that kind for is ignored."
        ),
    )

    @model_validator(mode="after")
    def _picks_are_members(self) -> "SetCloneAsk":
        # One set file is never written over two loaders.
        ids = list(self.picks.values())
        if len(ids) != len(set(ids)) or not set(ids) <= set(self.model_ids):
            raise ValueError("each pick must name a different model of model_ids")
        return self


class SetClonePlansRequest(BaseModel):
    """``POST /workflows/{workflow_id}/set-clone-plans``."""

    sets: list[SetCloneAsk] = Field(max_length=MAX_SET_PLANS)

    @model_validator(mode="after")
    def _keys_unique(self) -> "SetClonePlansRequest":
        # The plans come back by key: two sets sharing one would lose one.
        keys = [ask.key for ask in self.sets]
        if len(keys) != len(set(keys)):
            raise ValueError("each set needs a key of its own")
        return self


class LoaderDiff(BaseModel):
    """One model loader of the graph, before and after the clone."""

    node_id: str
    kind: str = Field(description="checkpoint, unet, vae or clip.")
    was_class: str
    now_class: str
    was: list[str] = Field(description="The files it loads now, in field order.")
    now: list[str] = Field(description="The files the clone loads there.")
    pack: str | None = Field(
        None, description="The node pack now_class comes from, when it changed."
    )
    installed: bool | None = Field(
        None, description="Whether ComfyUI has now_class; null when not asked."
    )
    was_type: str | None = Field(
        None, description="A CLIP loader's `type` before the clone, else null."
    )
    now_type: str | None = Field(
        None, description="Its `type` in the clone: the new model's, when known."
    )


class SetClonePlan(BaseModel):
    """What cloning the workflow onto one set would write."""

    key: str
    fit: Literal["same_base_model", "same_family", "other", "wont_load"]
    reason: str | None = Field(None, description="Why it will not load.")
    base_model: str | None = Field(None, description="The set checkpoint's.")
    keeps_loras: bool = Field(
        description=(
            "True when the set's checkpoint has the workflow's base model, so "
            "its LoRAs come along; an unknown base model on either side is "
            "not the same."
        )
    )
    swaps: dict[str, str] = Field(
        description="The graph's filename -> the set's file, as the clone takes it."
    )
    unpaired_bases: list[str] = Field(
        default_factory=list,
        description=(
            "Base-model files of the graph no set checkpoint was paired with "
            "(the set holds fewer than the graph loads): they keep their file."
        ),
    )
    takes: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Each VAE or text-encoder file of the graph that takes one of the "
            "set's models -> that model, changed or not: what `picks` "
            "re-pairs. A file the set has nothing for is absent."
        ),
    )
    choices: dict[str, list[int]] = Field(
        default_factory=dict,
        description="The set's models by the loader kind taking them (vae, clip).",
    )
    maps_cleanly: bool = Field(
        False,
        description=(
            "True when the set's checkpoints, VAEs and text encoders fill the "
            "graph's loaders of those kinds one for one, whatever the base "
            "model: no file of the original is left and none of the set's is "
            "unused. Onto another family, every CLIP loader must also take "
            "the type the new model loads as. Never true for a set that will "
            "not load."
        ),
    )
    loaders: list[LoaderDiff]


class SetClonePlans(BaseModel):
    """The workflow's own base model, and one plan per set asked."""

    base_filename: str | None = None
    base_model: str | None = None
    plans: list[SetClonePlan]


class ClonedWorkflow(WorkflowFile):
    """The file ``POST /workflows/{workflow_id}/clone-with-models`` wrote."""

    swapped: list[dict] = Field(description="One entry per loader field rewritten.")
    loaders: list[dict] = Field(
        default_factory=list,
        description=(
            "One {node_id, was, now, pack, installed} per loader whose node "
            "class changed for a file of another type (GGUF)."
        ),
    )
    unswapped: list[dict] = Field(
        description="Swaps that did not land: not_in_graph or not_on_comfyui."
    )
    verified: bool = Field(
        description=(
            "Whether ComfyUI listed every name written; false means at least "
            "one went in unchecked."
        )
    )


class ExtractedWorkflow(BaseModel):
    """``POST /recipes/{recipe_id}/extract-workflow``: the manual workflow made."""

    workflow_id: str = Field(description="The new manual workflow's id.")
    name: str = Field(description="What it is called: the recipe's name.")


class WorkflowDeleted(BaseModel):
    """Which manual workflow was deleted, by name and id."""

    deleted: str = Field(description="The deleted workflow's name.")
    workflow_id: str


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
    # ``(card, graph, swapped)`` per submittable graph a model fix swapped a
    # PixlStash loader into (#1605), recorded when the graph is submitted.
    loader_swaps: list[tuple] = dataclass_field(default_factory=list)
    # ``(graph, source, card, swapped)`` per graph built, refused or not: what
    # Open in ComfyUI opens, since a refusal is often the thing to fix there.
    built: list[tuple] = dataclass_field(default_factory=list)


def _shelf_digest(hub, kind: str):
    """A filename -> its shelf SHA-256 among models of *kind*, or ``None``.

    Only a model with a copy present: a PixlStash loader fetches the bytes,
    and one whose copies are gone would move the failure to ComfyUI's queue.
    ``None`` too for a name the shelf holds under two digests: which one the
    graph meant is a guess.
    """

    def digest_of(filename: str) -> str | None:
        rows = hub.fetchall(
            "SELECT DISTINCT m.sha256 FROM model m "
            "JOIN model_file f ON f.model_id = m.id AND f.state = 'present' "
            "WHERE lower(m.filename) = ? AND m.file_kind = ? "
            "AND m.sha256 IS NOT NULL",
            (normalized_filename(filename), kind),
        )
        return rows[0][0] if len(rows) == 1 else None

    return digest_of


def _stored_value(value: bool | int | float | str) -> str:
    """An override as the hub keeps it. ``workflow_default_override.value`` is
    TEXT and the read side hands it back verbatim, so a bool is written the way
    a graph writes one rather than as Python's ``True``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _iso(value) -> str | None:
    """Render a vault timestamp, which is a ``datetime``, as the API's string."""
    return value.isoformat() if value is not None else None


def _assets(rows, shelf_files: dict[str, str | None]) -> list[WorkflowAsset]:
    return [
        WorkflowAsset(
            widget=row["widget_name"],
            name=row["normalized_filename"],
            shelf_filename=(
                shelf_files.get(row["normalized_filename"])
                if row["widget_name"] == SHELF_ID_FIELD
                else None
            ),
        )
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
            cover.thumbnail_width,
            cover.thumbnail_height,
            cover.orientation,
            file_path=cover.file_path,
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
                superseded=cover.superseded,
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
    "video": "Video",
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


# What a core trait is called where a generated name needs it to tell two
# workflows apart (:func:`_display_names`). A trait this build does not know is
# left out, as an unknown special is.
_TRAIT_LABELS = {
    TWO_PASS: "Two-Pass",
    REFINE: "Refine",
    MODEL_PER_PASS: "Model per Pass",
    LIKENESS_GATE: "Likeness Gate",
}


# How an upscale stage upscales, as its row says it. One checkbox covers a
# resize, a model pass and a tiled re-diffusion, which cost seconds to minutes
# apart, so the row names which this graph's is. Served, as `_TYPE_LABELS` is.
_UPSCALE_LABELS = {
    UPSCALE_ULTIMATE_SD: "Ultimate SD Upscale",
    UPSCALE_MODEL: "Upscale model",
    UPSCALE_LATENT: "Latent upscale and second pass",
    UPSCALE_RESIZE: "Resize",
}


def _stage_details(recipe) -> dict[str, str]:
    """What each stage of the default recipe is, where a stage can be several.

    Only ``upscale`` for now: its kinds joined, the upscale model's name after
    the kinds that load one. A kind this build does not know is left out, as
    an unknown special is.
    """
    model = _model_stem(recipe.upscale_model) if recipe.upscale_model else ""
    parts = []
    for kind in recipe.upscale:
        label = _UPSCALE_LABELS.get(kind)
        if not label:
            continue
        uses_model = kind in (UPSCALE_ULTIMATE_SD, UPSCALE_MODEL)
        parts.append(f"{label} ({model})" if model and uses_model else label)
    return {"upscale": " + ".join(parts)} if parts else {}


def _trait_label(trait: str) -> str | None:
    if trait.startswith(REFERENCES_PREFIX):
        count = trait[len(REFERENCES_PREFIX) :]
        if not count.isdigit():
            return None
        return f"{count} Reference" + ("" if count == "1" else "s")
    return _TRAIT_LABELS.get(trait)


def _base_model_slots(models) -> list:
    """The slots a card is named after: every named one of its first base kind.

    A Wan 2.2 graph loads two UNETs (high and low noise) and is both of them,
    so every one is named, never the first, each by the file wired into its
    loader where the stored graph says (``_wired_names``, #1691). Empty if the
    graph loads no base model.
    """
    for kind in BASE_MODEL_KINDS:
        found = [slot for slot in models if slot.kind == kind and slot.name]
        if found:
            return found
    return []


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
    """What a workflow is called: the owner's name, else its file, else its models.

    That order is how much the name is *theirs*: one they typed, then the file
    they dropped, then a description built here.

    The built one is **the model, then what the workflow does, then what it
    does extra** - ``Krea 2: Text to Image + FaceDetailer`` - because the model
    is what a person calls the workflow, the verb only tells two of them apart
    once the model already has, and the post-processing is what separates two
    cards that agree on both. A graph loading two base models names both,
    ``Wan 2.2 High + Wan 2.2 Low: Video`` (:func:`_base_model_slots`). The card contract, this fallback chain included,
    is ``docs/integration_architecture.md`` §2.

    **The model is named as the shelf names it, not as the file is spelled.**
    ``realvisxl`` is a filename stem; ``Krea 2`` is what the trainer wrote in
    the header or what the owner typed, and it is in the same database as the
    card. A model this machine has never scanned still falls back to its stem.

    The suffix is on the generated name only. A workflow named after its
    FILE keeps the owner's spelling untouched: appending to a name somebody
    chose is inventing, not describing.
    """
    if card.name:
        return card.name
    if card.file_name:
        stem = card.file_name.rsplit("/", 1)[-1]
        return stem[: -len(".json")] if stem.lower().endswith(".json") else stem
    # The shelf's name first. Stripped, because `display_name` is free text off
    # a safetensors header or a text field, and a name of three spaces renders
    # the row blank exactly as the empty stem below would. Two loaders of one
    # file read once (`dict.fromkeys` keeps the order and drops repeats).
    stems = dict.fromkeys(
        (slot.title or "").strip() or _model_stem(slot.name)
        for slot in _base_model_slots(models)
    )
    stem = " + ".join(filter(None, stems))
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


def _distinguishing_traits(cards) -> list[str]:
    """Each card's trait suffix, saying only what not all of *cards* share.

    *cards* share one generated name. A trait every one of them has tells none
    of them apart, so it is left off: a negative prompt is not news on a grid
    of SDXL workflows that all have one. A card whose traits are not known yet
    (``None``) gets no suffix and does not count towards what is shared.
    """
    known = [set(card.traits) for card in cards if card.traits is not None]
    shared = set.intersection(*known) if known else set()
    suffixes = []
    for card in cards:
        labels = (
            _trait_label(trait) for trait in card.traits or () if trait not in shared
        )
        suffixes.append("".join(f" + {label}" for label in labels if label))
    return suffixes


def _display_names(figures) -> dict[str, str]:
    """Every workflow's name, by id, with no generated name printed twice.

    A name the owner typed or a workflow file's is left alone however many
    workflows share it: renaming what somebody chose is inventing. Generated
    names that collide first say what the core does differently
    (``... + Two-Pass``, :func:`_distinguishing_traits`). **Two names that
    differ only by their stages collide**: a stage is switched on or off per
    run, so ``Text to Image`` and ``Text to Image + Upscale`` read as one
    workflow run two ways, and a second sampling pass between them is what
    actually tells them apart. Whatever still collides is numbered ``Text to Image (2)``, ``(3)``: the workflows the
    grid draws by default before the hidden ones and the one-offs, so the one
    on screen is not "(2)" of a sibling nobody sees, then in id order so a
    workflow keeps its number from one read to the next.
    """
    # ponytail: id order is stable across reads but a new workflow can shift
    # the numbers after it; store a sequence if that ever matters.
    names = {}
    generated = {}
    for figure in sorted(
        figures, key=lambda f: (f.card.hidden, f.one_off, f.workflow_id)
    ):
        card = figure.card
        name = _display_name(card, figure.models)
        names[figure.workflow_id] = name
        if not card.name and not card.file_name:
            # Grouped by the name without its stages, which end it.
            stem = name.removesuffix(_specials_suffix(card))
            generated.setdefault(stem, []).append(figure)
    renamed = {}
    for members in generated.values():
        # A lone name shares every trait it has with itself, so it gets none.
        suffixes = _distinguishing_traits([member.card for member in members])
        for member, suffix in zip(members, suffixes):
            name = names[member.workflow_id] + suffix
            names[member.workflow_id] = name
            renamed.setdefault(name, []).append(member.workflow_id)
    for name, keys in renamed.items():
        for number, key in enumerate(keys[1:], start=2):
            names[key] = f"{name} ({number})"
    return names


def _manual_model_widgets(hub, workflow_id: str) -> tuple:
    """``((widget, filename), ...)`` read off one manual workflow's document.

    Cached per version, so the grid parses each document once rather than
    once per request, a document that will not read is logged once, and the
    document itself is read only when its version is not cached.
    """
    row = hub.fetchone(
        "SELECT v.version, COALESCE(v.created_at, d.created_at) AS version_at "
        "FROM workflow_document d LEFT JOIN workflow_version v "
        "ON v.workflow_id = d.workflow_id AND v.version = (SELECT MAX(version) "
        "FROM workflow_version WHERE workflow_id = d.workflow_id) "
        "WHERE d.workflow_id = ?",
        (workflow_id,),
    )
    if row is None:
        return ()
    return _model_widgets_at(hub, workflow_id, row["version"], row["version_at"])


# Bounded, so a deleted workflow's entry ages out. Keyed on when the version
# was stored as well as its number: a workflow made from a file reuses its id
# (`hub/workflow_group_convert.py`), so a deleted one's version 1 and its
# successor's must not share an entry.
@functools.lru_cache(maxsize=512)
def _model_widgets_at(
    hub, workflow_id: str, version: int | None, version_at: str | None
) -> tuple:
    """:func:`_manual_model_widgets` of the version *version* stored at *version_at*."""
    document = hub.fetchone(
        "SELECT document FROM workflow_document WHERE workflow_id = ?",
        (workflow_id,),
    )
    if document is None:
        return ()
    return _model_widgets_of(workflow_id, document["document"])


def _model_widgets_of(workflow_id: str, document: str) -> tuple:
    """The models one stored *document* loads, as :func:`_manual_model_widgets`.

    In the automatic path's order (``asset_names``: widget, then lowercased
    filename), and a model two loaders spell in two cases read once, so a
    pair reads the same on a manual card as on an automatic one. LoRAs keep
    every loader: two holding one file are two slots here.
    """
    try:
        loaded = loaded_model_widgets(json.loads(document))
    except Exception as exc:
        # The reader indexes into whatever the document holds, so a malformed
        # one raises something other than a ValueError. A workflow described
        # without its models must not take the grid down.
        logger.warning(
            "Could not read the models out of manual workflow %s, which failed "
            "with %s; it is described with none: %s",
            workflow_id,
            type(exc).__name__,
            exc,
        )
        return ()
    found: dict[tuple, tuple[str, str]] = {}
    for index, (widget, filename) in enumerate(
        sorted(loaded, key=lambda pair: (pair[0], str(pair[1]).lower()))
    ):
        repeat = index if widget == "lora_name" else None
        found.setdefault((widget, str(filename).lower(), repeat), (widget, filename))
    return tuple(found.values())


def _slot_models(slots) -> list[WorkflowSlotModel]:
    return [
        WorkflowSlotModel(
            name=slot.name,
            title=slot.title,
            quant=slot.quant,
            icon=slot.icon,
            base_model=slot.base_model,
            base_model_folded=slot.base_model_folded,
            sha256=slot.sha256,
            base_model_family=slot.base_model_family,
            kind=slot.kind,
            slot_label=slot.label,
            filename=slot.filename,
        )
        for slot in slots
    ]


def _entry(figure, recipe=None, names=None) -> WorkflowCard:
    """Render one workflow's figures in the shape ``workflowCard.js`` reads.

    *recipe* is its :class:`DefaultRecipe`, read on the detail route and the
    write answers only; *names* is :func:`_display_names` over the same grid,
    which keeps two generated names apart.
    """
    workflow = figure.workflow
    return WorkflowCard(
        id=workflow.workflow_id,
        name=(names or {}).get(workflow.workflow_id)
        or _display_name(figure.card, figure.models),
        type=figure.card.workflow_type,
        type_label=_TYPE_LABELS.get(figure.card.workflow_type),
        imported=figure.card.imported,
        manual=figure.card.manual,
        from_name=figure.card.from_name,
        origin_category=_origin_category(figure.card),
        versions=figure.card.versions,
        version=figure.card.version,
        version_at=figure.card.version_at,
        hidden=figure.card.hidden,
        models=_slot_models(figure.models),
        loras=_slot_models(figure.loras),
        specials=None if figure.card.specials is None else list(figure.card.specials),
        picture_count=figure.pictures,
        rating=figure.rating,
        rating_counts=list(figure.stars),
        covers=_covers(figure.covers),
        saved_recipe_count=figure.saved_recipes,
        defaults=_defaults_payload(recipe.values) if recipe else [],
        base_topology=workflow.base_topology,
        topologies=list(workflow.topologies),
        variant_count=len(workflow.variants),
        rank=figure.rank,
        last_used=_iso(figure.last_used),
        created_at=figure.created_at,
        changed_at=figure.changed_at,
        ghosts=figure.ghosts,
        model_ghosts=figure.model_ghosts,
        recipe_values=RecipeValues(
            **{
                kind: [
                    RecipeValue(name=name, pictures=pictures)
                    for name, pictures in values
                ]
                for kind, values in figure.recipe_values.items()
            }
        ),
        default_recipe=_recipe_payload(recipe) if recipe else None,
    )


def _origin_category(card) -> str:
    """``WorkflowCard.origin_category``: ``comfyui``, ``pictures`` or ``own``."""
    if not card.manual:
        return "pictures"
    return "comfyui" if card.origin == "pull" else "own"


def _defaults_payload(values) -> list[WorkflowDefault]:
    return [
        WorkflowDefault(
            label=default.label,
            slot_label=default.slot_label,
            input_name=default.input_name,
            value=default.value,
            provenance=default.provenance,
            exposed=default.exposed,
        )
        for default in values
    ]


def _recipe_payload(recipe: DefaultRecipe) -> DefaultRecipePayload:
    return DefaultRecipePayload(
        sampled=recipe.sampled,
        models=[
            DefaultRecipeModel(
                address=model.address,
                kind=model.kind,
                filename=model.filename,
                provenance=model.provenance,
                shelf_filename=model.shelf_filename,
            )
            for model in recipe.models
        ],
        loras=[
            DefaultRecipeLora(
                asset=lora.asset,
                filename=lora.filename,
                sha256=lora.sha256,
                strength=lora.strength,
                provenance=lora.provenance,
            )
            for lora in recipe.loras
        ],
        values=_defaults_payload(recipe.values),
        stages=dict(recipe.stages),
        stage_details=_stage_details(recipe),
    )


def _workflow_variants(hub, vault, workflow: Workflow) -> list[WorkflowVariant]:
    """The stored graphs a workflow is made of, with what each one made.

    Its variants are a subset of its topologies' recipes, so the
    topology-wide hub reads are filtered rather than re-queried per variant.
    """
    wanted = set(workflow.variants)
    # A variant a model fix swapped a PixlStash loader into is filed under the
    # swapped topology and carded under the original one (#1605). Two
    # originals can swap to one graph, so each topology is read once.
    topologies = list(workflow.topologies)
    for topology_hash in workflow.topologies:
        topologies += [
            row[0]
            for row in hub.fetchall(
                "SELECT DISTINCT swapped_topology_hash FROM workflow_loader_swap "
                "WHERE topology_hash = ?",
                (topology_hash,),
            )
            if row[0] not in topologies
        ]
    recipes, assets, forgotten = [], {}, {}
    for topology in topologies:
        recipes += [
            row
            for row in recipes_for_topology(hub, topology)
            if row["structural_hash"] in wanted
        ]
        assets.update(assets_for_topology_recipes(hub, topology))
        forgotten.update(forgotten_asset_counts(hub, topology).get(topology, {}))
    recipes.sort(key=lambda row: row["first_seen_at"] or "")
    activity = read_recipe_activity(vault, [row["structural_hash"] for row in recipes])
    shelf_files = shelf_filenames(
        hub,
        sorted(
            {
                row["normalized_filename"]
                for rows in assets.values()
                for row in rows
                if row["widget_name"] == SHELF_ID_FIELD
            }
        ),
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
                assets=_assets(assets.get(row["structural_hash"], []), shelf_files),
                forgotten_models=forgotten.get(row["structural_hash"], 0),
            )
        )
    return variants


def _require_object_info(read: tuple[dict | None, str | None], why: str) -> dict:
    """The map from :func:`_read_object_info`, or a 503 saying *why* it is needed."""
    object_info, error = read
    if object_info is None:
        raise HTTPException(
            status_code=503, detail=f"PixlStash could not ask ComfyUI {why}: {error}"
        )
    return object_info


def create_router(server) -> APIRouter:
    """Create the workflow-library router.

    Args:
        server: The Server instance, for ``hub`` (the workflow rows) and
            ``vault`` (the pictures made with them).

    Returns:
        The configured router.
    """
    router = APIRouter(tags=["workflows"])

    _hub = functools.partial(require_hub, server)

    # ── The workflows (#1623) ───────────────────────────────────────────────
    # The grid is `/workflows` itself, one entry per workflow. The payload is
    # `frontend/src/utils/workflowCard.js`'s documented card; see
    # `WorkflowCard`.

    def _counts() -> dict[str, int] | None:
        """``{structural_hash: kept pictures}``, or ``None`` with no library.

        What a workflow's base card is chosen on (its last tie-break), read
        the same way the default recipe reads it so the two agree.
        """
        if _library_uuid() is None:
            return None
        return read_variant_picture_counts(server.vault)

    def _manual_models(workflow_id: str) -> tuple:
        return _manual_model_widgets(_hub(), workflow_id)

    def _owner_object_info() -> dict | None:
        """The owner's ComfyUI ``object_info`` (cached), or ``None`` (logged).

        What a read of a workflow's defaults converts a stored editor document
        with the first time it needs its graph
        (``workflow_card_service.converted_manual_document``), which stores the
        conversion so no later read asks again.
        """
        return _read_object_info(_comfyui_url(server.auth.user), cached=True)[0]

    def _defaults(hub, workflow_id: str) -> DefaultRecipe | None:
        """``workflow_defaults``, converting a manual editor document on first read."""
        return workflow_defaults(hub, server.vault, workflow_id, _owner_object_info)

    def _workflow_id(workflow_id: str) -> str:
        if not _WORKFLOW_ID_RE.fullmatch(workflow_id):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Invalid workflow_id: expected auto:<64 hex> or manual:<uuid hex>."
                ),
            )
        return workflow_id

    def _require_workflow(hub, workflow_id: str) -> Workflow:
        """One workflow by id, or a 404 - never a row written on a dead id."""
        workflow = find_workflow(hub, _workflow_id(workflow_id), _counts())
        if workflow is None:
            raise HTTPException(status_code=404, detail="Unknown workflow.")
        return workflow

    def _require_base(hub, workflow_id: str):
        """``(workflow, base card)``: what a one-graph gesture acts on, or a 404."""
        cards = card_index(hub)
        workflow = find_workflow(hub, _workflow_id(workflow_id), _counts(), cards)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Unknown workflow.")
        card = next((c for c in cards if c.workflow_key == workflow.base_card), None)
        if card is None:
            raise HTTPException(
                status_code=404,
                detail="This workflow has no graph filed to act on.",
            )
        return workflow, card

    @router.get(
        "/workflows",
        summary="The Workflows grid",
        description=(
            "Every workflow this machine holds, one entry each, in cover-rank "
            "order. Hidden workflows and one-offs are counted rather than "
            "listed, unless the two flags ask for them."
        ),
        response_model=WorkflowCards,
    )
    def list_cards(
        request: Request,
        include_hidden: bool = Query(
            False,
            description=(
                "List hidden workflows too - the Filters panel's *Show hidden "
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
            manual_models=_manual_models,
        )
        names = _display_names(grid.figures)
        return WorkflowCards(
            cards=[_entry(figure, names=names) for figure in grid.cards],
            one_offs=grid.one_offs,
            hidden=grid.hidden,
        )

    @router.get(
        "/workflows/{workflow_id}",
        summary="One workflow",
        description=(
            "A workflow opened: its variants, its default recipe, and the "
            "value each featured parameter starts from with where that value "
            "came from."
        ),
        response_model=WorkflowCardDetail,
        responses={404: {"description": "This machine has no such workflow."}},
    )
    def get_card(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        return _read_detail(_hub(), _workflow_id(workflow_id))

    def _read_detail(hub, workflow_id: str) -> WorkflowCardDetail:
        """One workflow opened, for the detail route and for what a write answers.

        The whole grid for one workflow, because its rank is Bayesian: the
        prior is the library's own mean rating, which cannot be read off one
        workflow. A write answers with this so the caller sees the workflow it
        just changed rather than an echo of its own request.
        """
        grid = read_grid(hub, server.vault, manual_models=_manual_models)
        figure = grid.figure(workflow_id)
        if figure is None:
            raise HTTPException(status_code=404, detail="Unknown workflow.")
        workflow = figure.workflow
        recipe = _defaults(hub, workflow_id)
        pins = group_pins(hub, workflow_id)
        graph_models = (
            None
            if _base_model_slots(figure.models) or figure.base is None
            else _graph_base_models(hub, figure.base)
        )
        return WorkflowCardDetail(
            card=_entry(figure, recipe, _display_names(grid.figures)),
            notes=workflow.notes,
            hidden=workflow.hidden,
            variants=_workflow_variants(hub, server.vault, workflow),
            pins=None
            if pins is None
            else [
                ParameterAddress(slot_label=slot_label, input_name=input_name)
                for slot_label, input_name in pins
            ],
            graph_base_models=graph_models,
            model_fixes=[
                ModelFixRow(slot_label=label, was=was, now=now, slot_kind=kind)
                for label, was, now, kind in (
                    model_fixes(hub, workflow.base_topology)
                    if workflow.base_topology
                    else []
                )
            ],
        )

    def _apply_model_fixes(
        card,
        graph: dict,
        object_info: dict | None,
        swapped: dict[str, tuple[dict, dict]] | None = None,
    ) -> list[dict]:
        """Load each model the owner replaced on this card's graph, in place.

        Matched on the file, not the folder the graph filed it under: a fix is
        keyed the way the card key is (``normalized_filename``), and the three
        source tiers do not agree on folders. Where the loader cannot load the
        replacement, a PixlStash loader is swapped in (#1605) and the node
        added to *swapped*, which the caller records with
        :func:`_record_loader_swaps` once the graph is final. Returns the
        substitutions made.
        """
        fixes = {
            (kind, normalized_filename(was)): now
            for _label, was, now, kind in model_fixes(_hub(), card.topology_hash)
        }
        if not fixes:
            return []
        # Fields of the kind each fix was recorded for (`model_fix_labels`):
        # a VAE holding a file of the same name as a replaced checkpoint
        # keeps it.
        done, missed = [], []
        # {node_id: (graph before, {digest widget: (widget, file)})}
        swapped = {} if swapped is None else swapped
        for kind in sorted({kind for kind, _was in fixes}):
            swaps = {
                value: fixes[(kind, normalized_filename(value))]
                for _node, cls, widget, value in iter_model_fields_api(graph)
                if model_fix_kind(cls, widget) == kind
                and (kind, normalized_filename(value)) in fixes
            }
            if not swaps:
                continue
            kind_done, kind_missed = apply_filename_swap(
                graph,
                swaps,
                object_info,
                fields=lambda cls, widget, kind=kind: (
                    model_fix_kind(cls, widget) == kind
                ),
            )
            done += kind_done
            missed += _swap_in_pixlstash_loaders(
                card, graph, kind, swaps, kind_missed, object_info, swapped
            )
        if swapped:
            # A rename on a node swapped afterwards (a Dual loader's other
            # file) names a node and field the graph no longer has: it is
            # reported as the swapped node's, from the name it renamed.
            renamed = {
                (swap["node_id"], swap["field"]): swap["was"]
                for swap in done
                if swap["node_id"] in swapped
            }
            done = [swap for swap in done if swap["node_id"] not in swapped]
            for node_id, (before, files) in swapped.items():
                for digest_widget, (widget, file) in files.items():
                    was = renamed.get(
                        (node_id, widget), before[node_id]["inputs"][widget]
                    )
                    if normalized_filename(file) != normalized_filename(was):
                        done.append(
                            {
                                "node_id": node_id,
                                "class_type": graph[node_id]["class_type"],
                                "field": digest_widget,
                                "was": was,
                                "now": file,
                                "verified": True,
                            }
                        )
        for swap in done:
            logger.info(
                "[workflows] Card %s loads %s in place of %s on node %s: the "
                "owner replaced that model.",
                card.workflow_key,
                swap["now"],
                swap["was"],
                swap["node_id"],
            )
        for miss in missed:
            logger.warning(
                "[workflows] Card %s: the owner's replacement %s for %s was not "
                "loaded (%s).",
                card.workflow_key,
                miss["now"],
                miss["was"],
                miss["reason"],
            )
        return done

    def _swap_in_pixlstash_loaders(
        card,
        graph: dict,
        kind: str,
        swaps: dict[str, str],
        missed: list[dict],
        object_info: dict | None,
        swapped: dict[str, tuple[dict, dict]],
    ) -> list[dict]:
        """Load what the rename could not through a PixlStash loader (#1605).

        A fix whose replacement ComfyUI does not list for the workflow's own
        loader (not in its model folders, or a file type that loader cannot
        read) swaps the loader node for ComfyUI-PixlStash's, which fetches the
        file by digest. Each swapped node is added to *swapped*; returns the
        misses that are still missed.
        """
        if kind not in run_service.PIXLSTASH_SWAP_LOADERS:
            # Checkpoints keep the rename only: nothing to try, nothing to say.
            return missed
        unlisted = {m["was"] for m in missed if m["reason"] == "not_on_comfyui"}
        nodes = {
            node_id
            for node_id, cls, widget, value in iter_model_fields_api(graph)
            if model_fix_kind(cls, widget) == kind and value in unlisted
        }
        for node_id in sorted(nodes):
            plan, refusal = run_service.plan_pixlstash_swap(
                graph, node_id, kind, swaps, object_info, _shelf_digest(_hub(), kind)
            )
            if plan is None:
                logger.warning(
                    "[workflows] Card %s: node %s (%s) cannot load the owner's "
                    "replacement, and a PixlStash loader cannot stand in for it "
                    "(%s).",
                    card.workflow_key,
                    node_id,
                    graph[node_id].get("class_type"),
                    refusal,
                )
                continue
            before = deepcopy(graph)
            run_service.apply_pixlstash_swap(graph, node_id, plan)
            swapped[node_id] = (
                before,
                {
                    digest_widget: (widget, file)
                    for digest_widget, widget, file, _d in plan["fields"]
                },
            )
        # Still missed while any loader of this kind names the file: two
        # nodes may load it and only one be swapped.
        still_named = {
            value
            for _node, cls, widget, value in iter_model_fields_api(graph)
            if model_fix_kind(cls, widget) == kind
        }
        return [m for m in missed if m["was"] in still_named]

    def _record_loader_swaps(card, graph: dict, swapped: dict) -> None:
        """Card the swapped graph's pictures as the original's (#1605).

        Called with the graph as it is submitted, after every other change to
        it (a PixlStash saver run as ``SaveImage``, a repair), since the
        recorded topology has to be the one its pictures come back with.
        """
        # Every node is swapped back at once: the topology to card as is the
        # graph with none of them swapped.
        original = deepcopy(graph)
        for node_id, (before, _files) in swapped.items():
            original[node_id] = before[node_id]
        try:
            swapped_topology, swaps = loader_swaps(
                original,
                graph,
                {node_id: files for node_id, (_before, files) in swapped.items()},
            )
        except WorkflowGraphError as exc:
            logger.warning(
                "[workflows] Card %s runs with a PixlStash loader swapped in, "
                "but its graph will not reduce, so its pictures will not card "
                "as this workflow's: %s",
                card.workflow_key,
                exc,
            )
            return
        try:
            record_loader_swaps(_hub(), swapped_topology, swaps)
        except sqlite3.Error as exc:
            # The run goes ahead: only where its pictures card is at stake.
            logger.warning(
                "[workflows] Card %s: could not record its swapped PixlStash "
                "loader, so this run's pictures will card apart: %s",
                card.workflow_key,
                exc,
            )

    def _graph_base_models(hub, card) -> list[str] | None:
        """The base-model files the card's runnable graph names, in order.

        Read off the same source a run would submit (:func:`_source_graph_for`:
        the owner's edit of the graph, else the workflow file, then the best
        picture's embedded graph, then a stored instance), because that is the graph whose missing file matters. Only
        asked when the card has no name for its base model, so the grid never
        pays for it and an opened card pays once. A name the hub forgot reads
        back as :data:`~run_service.FORGOTTEN_MODEL` and is left out: it names
        nothing a person could look for. ``None`` when there is no graph to
        read, which is not the same answer as a graph that loads none.

        A PixlStash shelf loader's ``checkpoint_id`` is a shelf row id, not a
        file, so it is read as the file that row names. Where the shelf no
        longer holds that row, or holds it without a file, a picture's editor
        graph may still name the file (:func:`_editor_names_for`); failing both, the shelf's own "gone"
        / "unnamed" words stand in. A bare ``77`` reads as a model called 77.
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
        loaded = [
            (widget, value)
            for widget, value in loaded_model_widgets(source.graph)
            if widget in CHECKPOINT_WIDGETS and value != run_service.FORGOTTEN_MODEL
        ]
        shelf_files = shelf_filenames(
            hub, [value for widget, value in loaded if widget == SHELF_ID_FIELD]
        )
        # Gone, or still on the shelf but naming no file: either way the
        # picture's editor graph may be the only thing left that names it.
        lost = [
            value
            for value, file in shelf_files.items()
            if not file or file == SHELF_MODEL_GONE
        ]
        if lost and source.picture_id is not None:
            shelf_files.update(_editor_names_for(source, lost))
        # Once per recorded value, before resolving: two gone ids are two
        # loaders, though both read "no longer on the shelf".
        first_widget: dict[str, str] = {}
        for widget, value in loaded:
            first_widget.setdefault(value, widget)
        return [
            shelf_files.get(value, SHELF_MODEL_GONE) or SHELF_MODEL_UNNAMED
            if widget == SHELF_ID_FIELD
            else value
            for value, widget in first_widget.items()
        ]

    def _editor_names_for(source, shelf_ids: list[str]) -> dict[str, str]:
        """``{shelf id: file}`` from the source picture's editor graph.

        A ComfyUI picture carries two graphs: the API ``prompt`` that ran,
        which the source is, and the editor ``workflow``. Where a shelf loader
        stands in the first, the same node in the second can be an ordinary
        loader that names the file, and that name outlives the shelf row.
        Paired by node id, which both graphs share; an id no editor node
        names a file for is left out.
        """
        wanted = set(shelf_ids)
        nodes = {
            node_id: value
            for node_id, _cls, widget, value in iter_model_fields_api(
                source.graph, shelf_ids_as_text=True
            )
            if widget == SHELF_ID_FIELD and value in wanted
        }
        try:
            editor = find_comfy_workflow(
                _read_embedded_metadata(server, source.picture_id)
            )
        except (HTTPException, OSError, ValueError) as exc:
            # `_read_embedded_metadata` raises only HTTPException today; the
            # other two keep this a "gone" rather than a 500 if that changes.
            logger.info(
                "Picture %s: could not read its editor graph for the file a "
                "gone shelf loader named, so it reads as gone: %s",
                source.picture_id,
                getattr(exc, "detail", exc),
            )
            return {}
        found = {}
        for node in (editor or {}).get("nodes") or []:
            if not isinstance(node, dict) or str(node.get("id")) not in nodes:
                continue
            try:
                names = [
                    value
                    for widget, value in loaded_model_widgets(
                        {"nodes": [node], "links": []}
                    )
                    if widget in CHECKPOINT_WIDGETS and widget != SHELF_ID_FIELD
                ]
            except Exception as exc:
                # The reader indexes into whatever the file holds, so a
                # malformed node raises something other than a ValueError
                # (`_manual_model_widgets` guards the same reader the same
                # way). Only this node's name is lost.
                logger.warning(
                    "Picture %s: editor node %s will not read for the file a "
                    "gone shelf loader named, so it reads as gone: %s",
                    source.picture_id,
                    node.get("id"),
                    exc,
                )
                continue
            if names:
                found[nodes[str(node["id"])]] = names[0]
        return found

    @router.get(
        "/workflows/{workflow_id}/pictures",
        summary="Pictures made with a workflow",
        description=(
            "The newest kept pictures made by any variant of one workflow, "
            "newest first. Ids only: the caller already has the thumbnail route."
        ),
        response_model=list[int],
        responses={404: {"description": "This machine has no such workflow."}},
    )
    def list_card_pictures(
        request: Request,
        workflow_id: str,
        limit: int = Query(
            6,
            ge=1,
            le=MAX_SAMPLE_PICTURES,
            description="How many ids to return, newest first.",
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow = _require_workflow(hub, workflow_id)
        live = manual_workflow_ids(hub)
        # A manual workflow's pictures are filed under its own id.
        keys = [workflow_id] if workflow_id in live else workflow.variants
        return read_card_picture_ids(server.vault, keys, limit, live)

    # ── The writes (#1623) ──────────────────────────────────────────────────
    # Every one of these emits `CHANGED_WORKFLOWS` naming workflow ids, which
    # is a "look again" signal and not a workflow: the counts and covers are
    # computed per request over the whole vault, so the client re-reads
    # `GET /workflows` rather than trusting what a write carried back.

    def _announce(request: Request, ids, reason: str) -> None:
        """Tell every other tab which workflows to look at again, and why."""
        announce_changed_workflows(
            server,
            ids,
            reason,
            origin_client_id=getattr(request.state, "origin_client_id", None),
        )

    @router.patch(
        "/workflows/{workflow_id}",
        summary="Edit a workflow",
        description=(
            "Write the fields the request carries; the rest stand. A null name "
            "or notes clears it. Hiding a workflow takes it off the grid — it "
            "still opens by its own URL, and nothing about it is deleted."
        ),
        response_model=WorkflowCardDetail,
        responses={404: {"description": "This machine has no such workflow."}},
    )
    def edit_card(request: Request, workflow_id: str, payload: WorkflowCardEdit):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_workflow(hub, workflow_id)
        changes = payload.model_dump(exclude_unset=True)
        if "hidden" in changes and changes["hidden"] is None:
            # The column is NOT NULL and has no "unset" state: a null there is
            # a caller meaning "not hidden", which the hub is told plainly
            # rather than left to coerce.
            changes["hidden"] = False
        set_group_attributes(hub, workflow_id, **changes)
        _announce(request, [workflow_id], "changed")
        return _read_detail(hub, workflow_id)

    @router.get(
        "/workflows/{workflow_id}/lora-summary",
        summary="The LoRAs of a workflow",
        description=(
            "Which LoRAs every kept picture of this workflow loaded, and which "
            "change between them: for each, how many pictures and its best "
            "pictures. `cover` names the picture on top of the workflow's "
            "cover, to say which changing LoRA it loaded."
        ),
        response_model=WorkflowLoraSummary,
        responses={404: {"description": "This machine has no such workflow."}},
    )
    def lora_summary(
        request: Request,
        workflow_id: str,
        cover: int | None = Query(
            None, ge=1, description="The picture on top of the workflow's cover."
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow = _require_workflow(hub, workflow_id)
        if _library_uuid() is None:
            # No pictures to count, so nothing is shared and nothing changes.
            return WorkflowLoraSummary(workflow_id=workflow_id)
        summary = workflow_lora_summary(hub, server.vault, workflow.variants, cover)
        return WorkflowLoraSummary(workflow_id=workflow_id, **asdict(summary))

    @router.put(
        "/workflows/{workflow_id}/model-fix",
        summary="Replace a missing model in a workflow",
        description=(
            "Load another model wherever this workflow names `was` in a slot "
            "of `now`'s kind (a checkpoint, a VAE or a text encoder) - the fix "
            "for a workflow whose model is gone - or, with `now: null`, the "
            "original again, in the workflow's base graph. The workflow keeps "
            "its pictures and its settings, and a picture made with the "
            "replacement is filed in it. Answers with the workflow."
        ),
        response_model=WorkflowCardDetail,
        responses={
            404: {"description": "No such workflow, or `now` is not on the shelf."},
            409: {"description": "The workflow does not load `was` there."},
            422: {
                "description": (
                    "`now` names the same file as `was`, or is not of `slot_kind`."
                )
            },
            503: {"description": "No library is open."},
        },
    )
    def fix_model(request: Request, workflow_id: str, payload: ModelFix = Body(...)):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _workflow, card = _require_base(hub, workflow_id)
        if card.manual:
            # A fix re-keys the cards of a topology; a manual workflow is on
            # none. Its own graph changes by making a new one.
            raise HTTPException(
                status_code=409,
                detail=(
                    "A manual workflow's models are changed by cloning it "
                    "with other models, not replaced in place."
                ),
            )
        was, now, kind = payload.was, payload.now, payload.slot_kind
        if now is not None:
            # A shelf model of a kind a slot can take, and nothing else,
            # written as the shelf spells it: the name goes into every graph
            # this workflow submits. Its kind is the kind of slot it fixes.
            rows = hub.fetchall(
                "SELECT filename, file_kind FROM model WHERE lower(filename) = ? "
                "AND file_kind IN (?, ?, ?) ORDER BY file_kind",
                (
                    normalized_filename(now),
                    FILE_CHECKPOINT,
                    FILE_VAE,
                    FILE_TEXT_ENCODER,
                ),
            )
            if not rows:
                raise HTTPException(
                    status_code=404,
                    detail="That model is not on the model shelf.",
                )
            if kind is None and len(rows) > 1:
                # One filename on the shelf under several kinds, and the caller
                # did not say which: the kind the workflow loads `was` as. Two
                # such kinds are a guess that would fix one slot and leave the
                # other missing behind a 200, so ask, as `?replacing=` does.
                matching = [
                    r
                    for r in rows
                    if model_fix_labels(hub, card.topology_hash, was, r["file_kind"])
                ]
                if not matching:
                    # `was` may be a replacement that has gone missing too: the
                    # stored graphs name the original, so the kind the
                    # workflow loads `was` as is the one its fix recorded.
                    chained_kinds = {
                        fix_kind
                        for _label, _was, fix_now, fix_kind in model_fixes(
                            hub, card.topology_hash
                        )
                        if normalized_filename(fix_now) == normalized_filename(was)
                    }
                    matching = [r for r in rows if r["file_kind"] in chained_kinds]
                if len(matching) > 1:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This workflow loads that model as a "
                            + " and a ".join(
                                _FIX_KIND_NAMES[r["file_kind"]] for r in matching
                            )
                            + "; say which kind to replace."
                        ),
                    )
                if not matching:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This workflow does not load that model as a "
                            + " or a ".join(
                                _FIX_KIND_NAMES[r["file_kind"]] for r in rows
                            )
                            + "."
                        ),
                    )
                row = matching[0]
            else:
                row = next((r for r in rows if kind in (None, r["file_kind"])), None)
            if row is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "That is a "
                        + " or a ".join(
                            _FIX_KIND_NAMES[k]
                            for k in sorted({r["file_kind"] for r in rows})
                        )
                        + f", not a {_FIX_KIND_NAMES[kind]}."
                    ),
                )
            now, kind = row["filename"], row["file_kind"]
        fixes = [
            fix
            for fix in model_fixes(hub, card.topology_hash)
            if kind in (None, fix[3])
        ]
        # A replacement that has gone missing too is fixed from the ORIGINAL:
        # fixes are one step on both the key side and the run side, so a
        # chain would leave the card keyed and run on the middle link.
        chained = [
            fix_was
            for _label, fix_was, fix_now, _kind in fixes
            if normalized_filename(fix_now) == normalized_filename(was)
        ]
        if now is not None and len(set(map(normalized_filename, chained))) > 1:
            # Two originals replaced by this one file in two slots: which of
            # them the owner means is a guess, and fixing one would leave the
            # other loading a missing file behind a 200. Undoing is not
            # ambiguous: every slot goes back to its own original, below.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Several models were replaced by that one; replace each "
                    f"original instead ({', '.join(sorted(set(chained)))})."
                ),
            )
        if chained and now is not None:
            was = chained[0]
        was_norm = normalized_filename(was)
        # (original, the slots it is replaced in), one per fix this request
        # writes or undoes.
        targets: list[tuple[str, list[str]]] = []
        if now is not None:
            if normalized_filename(now) == was_norm:
                raise HTTPException(
                    status_code=422, detail="That is the model it already loads."
                )
            labels = model_fix_labels(hub, card.topology_hash, was, kind)
            # A slot another original is already replaced by `now` in loads
            # `now` already: nothing to do there. Writing a second fix would
            # fold two originals onto one replacement in one slot and file the
            # replacement's pictures on whichever row SQLite read last.
            loaded = {
                label: fix_was
                for label, fix_was, fix_now, _kind in fixes
                if label in labels
                and normalized_filename(fix_now) == normalized_filename(now)
                and normalized_filename(fix_was) != was_norm
            }
            if labels and set(labels) <= loaded.keys():
                logger.info(
                    "Workflow %s already loads %s where it loaded %s (replacing "
                    "%s); nothing to change.",
                    workflow_id,
                    now,
                    was,
                    ", ".join(sorted(set(loaded.values()))),
                )
                return _read_detail(hub, workflow_id)
            labels = [label for label in labels if label not in loaded]
            targets = [(was, labels)] if labels else []
        else:
            for original in sorted(set(chained)) or [was]:
                labels = [
                    label
                    for label, fix_was, _now, _kind in fixes
                    if normalized_filename(fix_was) == normalized_filename(original)
                ]
                if labels:
                    targets.append((original, labels))
        if not targets:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This workflow does not load that model as a "
                    f"{_FIX_KIND_NAMES[kind]}."
                    if now is not None
                    else "That model was not replaced."
                ),
            )
        if getattr(server.vault, "library_uuid", None) is None:
            # The re-key below picks its merge winner on the vault's picture
            # counts and moves the vault's saved recipes, as a mark flip does.
            raise HTTPException(
                status_code=503,
                detail="No library is open, so a workflow cannot be re-keyed.",
            )
        # The base card's own key, unless a re-key moved it: then where its
        # pictures went. Internal: the workflow keeps its id either way.
        key = card.workflow_key
        for original, labels in targets:
            moved = set_model_fix(
                hub,
                card.topology_hash,
                labels,
                original,
                now,
                read_variant_picture_counts(server.vault),
                keep_key=key,
                kind=kind or FILE_CHECKPOINT,
            )
            try:
                saved_recipe_service.rekey_recipes(server.vault, moved)
            except Exception:
                # The mark flip's reason: the cards have moved and this map is
                # the only record of where each recipe set belongs.
                logger.exception(
                    "A model fix on topology %s re-keyed its cards but could "
                    "not move the saved recipes with them; the cards moved as %r.",
                    card.topology_hash,
                    moved,
                )
                raise
            key = (moved.get(key) or [key])[0]
        _announce(request, [workflow_id], "changed")
        return _read_detail(hub, workflow_id)

    @router.put(
        "/workflows/{workflow_id}/default-lora",
        summary="Put one LoRA in or out of a workflow's default recipe",
        description=(
            "Add one of this workflow's LoRAs (`asset`, as `lora-summary` names "
            "it) to its default recipe, keep it out, or drop that edit. Every "
            "other default stands. Only a LoRA `lora-summary` gives a `sha256` "
            "can go in: that digest is how the default recipe names it. Added "
            "without a `strength`, it takes the one its pictures used most, "
            "else 1."
        ),
        response_model=WorkflowCardDetail,
        responses={
            404: {"description": "No such workflow, or no such LoRA in it."},
            409: {"description": "The model shelf cannot name that LoRA."},
            503: {"description": "No library is open."},
        },
    )
    def set_default_lora_route(
        request: Request, workflow_id: str, payload: DefaultLoraEdit = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow = _require_workflow(hub, workflow_id)
        if _library_uuid() is None:
            raise HTTPException(status_code=503, detail="No library is open.")
        summary = workflow_lora_summary(hub, server.vault, workflow.variants)
        use = next(
            (
                use
                for use in (*summary.shared, *summary.varying)
                if use.asset == payload.asset
            ),
            None,
        )
        if payload.include is None:
            # Dropping an edit needs no shelf: the edit names its own digest,
            # so one stays removable after its file leaves the shelf.
            recipe = _defaults(hub, workflow_id)
            sha256 = next(
                (
                    lora.sha256
                    for lora in (recipe.loras if recipe else [])
                    if lora.provenance == EDITED
                    and lora.sha256
                    and (
                        lora.sha256.lower() == payload.sha256
                        if payload.sha256
                        else lora.asset == payload.asset
                    )
                ),
                None if payload.sha256 else (use.sha256 if use else None),
            )
            if sha256 is None:
                raise HTTPException(
                    status_code=404,
                    detail="This workflow's default recipe has no edit for that LoRA.",
                )
            set_default_lora(hub, workflow_id, sha256, None)
            _announce(request, [workflow_id], "changed")
            return _read_detail(hub, workflow_id)
        if use is None:
            raise HTTPException(
                status_code=404, detail="This workflow's pictures load no such LoRA."
            )
        # The summary's own resolution, the one the pile offers the button
        # by, so the two can never disagree about which LoRAs can go in.
        if use.sha256 is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "That LoRA is not on your model shelf."
                    if not use.on_shelf
                    else "Your model shelf cannot tell which LoRA file that is."
                ),
            )
        if not payload.include:
            value = LORA_OFF
        else:
            strength = payload.strength
            if strength is None:
                strength = lora_modal_strength(
                    hub, server.vault, workflow_id, payload.asset
                )
            value = _stored_value(float(1.0 if strength is None else strength))
        set_default_lora(hub, workflow_id, use.sha256, value)
        _announce(request, [workflow_id], "changed")
        return _read_detail(hub, workflow_id)

    @router.put(
        "/workflows/{workflow_id}/defaults",
        summary="Set a workflow's parameter defaults",
        description=(
            "Replace this workflow's whole set of parameter edits, each "
            "addressed `<slot_label>/<input_name>`. An empty list clears them, "
            "and its defaults then come from the pictures it has made again. "
            "The default recipe's models and LoRAs are not touched."
        ),
        response_model=WorkflowCardDetail,
        responses={404: {"description": "This machine has no such workflow."}},
    )
    def set_defaults(
        request: Request, workflow_id: str, payload: CardDefaults = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_workflow(hub, workflow_id)
        replace_parameter_defaults(
            hub,
            workflow_id,
            [
                (
                    f"{default.slot_label}{OVERRIDE_ADDRESS_SEPARATOR}"
                    f"{default.input_name}",
                    _stored_value(default.value),
                )
                for default in payload.defaults
            ],
        )
        _announce(request, [workflow_id], "changed")
        return _read_detail(hub, workflow_id)

    @router.put(
        "/workflows/{workflow_id}/pins",
        summary="Set a workflow's pinned parameters",
        description=(
            "Which parameters the Run form asks for each run; every other "
            "parameter is fixed at the workflow's value (a saved recipe or an "
            "API override still sets it). An empty list fixes everything; "
            "null forgets the choice, so the default pins apply again."
        ),
        response_model=CardPins,
        responses={404: {"description": "This machine has no such workflow."}},
    )
    def set_pins(request: Request, workflow_id: str, payload: CardPins = Body(...)):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_workflow(hub, workflow_id)
        replace_group_pins(
            hub,
            workflow_id,
            None
            if payload.pins is None
            else [
                f"{pin.slot_label}{OVERRIDE_ADDRESS_SEPARATOR}{pin.input_name}"
                for pin in payload.pins
            ],
        )
        _announce(request, [workflow_id], "changed")
        return payload

    @router.get(
        "/workflows/{workflow_id}/form-inputs",
        summary="The inputs of a workflow a parameter can be made of",
        description=(
            "Every literal input of this workflow's graph that a run can set "
            "by address, grouped by node, each with its value, what ComfyUI's "
            "object_info declares it as and, for a drop-down, its options. "
            "Left out: a wired input, and what PixlStash already has a control "
            "for or decides itself (the prompts, the seeds, a picture batch, a "
            "picture input, a model or LoRA loader's file and strengths, the "
            "save node, a credential). `exposed` marks the ones that are "
            "parameters already; any other becomes one by naming its address "
            "in `PUT /workflows/{workflow_id}/defaults`, and is asked for by "
            "the Run form once `PUT /workflows/{workflow_id}/pins` names it."
        ),
        response_model=WorkflowFormInputs,
        responses={
            400: {"description": "The workflow's graph will not reduce."},
            404: {"description": "This machine has no such workflow."},
            409: {"description": "There is no graph for this workflow."},
        },
    )
    def get_form_inputs(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow, card = _require_base(hub, workflow_id)
        # Cached, for the LoRA chain's reason: the inspector asks on every
        # workflow it selects.
        object_info, _error = _read_object_info(
            _comfyui_url(_user(request)), cached=True
        )
        graph = _card_source(card, object_info=object_info).graph
        labels, core = _graph_labels(graph, "parameters")
        recipe = _defaults(hub, workflow.workflow_id)
        return WorkflowFormInputs(
            nodes=run_service.form_inputs(
                graph,
                labels,
                # A manual workflow's parameters are addressed by slot label
                # alone (`_manual_sample`), so its new ones are too.
                {} if workflow.workflow_id.startswith(MANUAL_PREFIX) else core,
                object_info,
                {(d.slot_label, d.input_name) for d in recipe.values}
                if recipe
                else set(),
                MAX_VALUE_LENGTH,
            )
        )

    @router.put(
        "/workflows/{workflow_id}/inputs",
        summary="Set a workflow's picture inputs",
        description=(
            "How each picture input of this workflow is filled: from the "
            "selection, from a picker, or from one fixed picture. Kept per "
            "library, because a picture is a picture in one library. A fixed "
            "input names its picture by pixel_sha or by picture_id, and one "
            "given by id is stored as that picture's content. Replaces the "
            "whole set, so read it first: every run pre-flight returns it as "
            "picture_inputs."
        ),
        response_model=CardPictureInputs,
        responses={
            400: {"description": "A pinned picture_id is not a kept picture."},
            404: {"description": "This machine has no such workflow."},
            503: {"description": "No library is open, so there is nothing to set up."},
        },
    )
    def set_picture_inputs(
        request: Request, workflow_id: str, payload: CardPictureInputs = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _require_workflow(hub, workflow_id)
        library_uuid = _library_uuid()
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
        replace_group_picture_inputs(
            hub,
            library_uuid,
            workflow_id,
            [
                (
                    f"{entry.slot_label}{OVERRIDE_ADDRESS_SEPARATOR}{entry.input_name}",
                    entry.mode,
                    entry.pixel_sha,
                )
                for entry in entries
            ],
        )
        _announce(request, [workflow_id], "changed")
        return CardPictureInputs(inputs=entries)

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

    def _converted_document(
        card, document: dict | None, object_info: dict | None, comfyui_url
    ) -> tuple[dict | None, list[str]]:
        """*document*, with an editor graph converted to the API graph it runs as.

        For a workflow FILE, whose conversion is not stored. A manual
        workflow's document goes through ``converted_manual_document``
        instead, which stores the conversion on its version.

        A document ComfyUI's *Convert for PixlStash* already converted reads as
        that conversion and is returned as is. One that was never sent through
        it is converted here, the way a picture's editor graph is: with
        ComfyUI's ``object_info``, read (cached) from *comfyui_url* only when
        the caller had none and there is an editor graph to read. A refusal
        leaves the document as it was, so the resolver still reports
        ``ui_format``.

        Returns ``(document, problems)``: *problems* are the converter's
        sentences when it refused, so the reason can say why.
        """
        if not document or api_graph(document) is not None:
            return document, []
        if object_info is None and comfyui_url:
            object_info, _error = _read_object_info(comfyui_url, cached=True)
        if object_info is None:
            # Nothing to convert against; the failed read logged why.
            return document, []
        graph, problems = convert_ui_graph_to_api(document, object_info)
        if graph is None:
            logger.info(
                "Card %s holds an editor workflow that will not convert: %s",
                card.workflow_key,
                "; ".join(problems),
            )
            return document, problems
        return with_converted_graph(document, graph), []

    def _freshen(user, workflow_id: str) -> None:
        """Take a pulled workflow's ComfyUI file now if it changed there.

        Run and Open only, before the workflow is read: one listing of the
        file's folder, and a newer file becomes the workflow's next version,
        which the default recipe and the resolve then read. Not for an
        automatic workflow, one with no live ComfyUI file, or an owner who
        turned "Pull workflows from ComfyUI" off. Any failure leaves the stored
        version to run (``WorkflowPulls.freshen`` logs it and never raises).
        """
        pulls = getattr(server, "workflow_pulls", None)
        if pulls is None or not workflow_id.startswith(MANUAL_PREFIX):
            return
        if not getattr(user, "pull_comfyui_workflows", True):
            return
        pulls.freshen(_comfyui_url(user), workflow_id)

    def _edited_graph_for(card) -> tuple[dict | None, int | None]:
        """``(graph, version)`` the owner saved over the automatic workflow
        *card* is the base of.

        The workflow's highest version (``workflow_versions.edited_document``).
        ``(None, None)`` for a workflow never saved over, which is nearly
        every one, and for a card that is in such a workflow without being its
        base: a saved recipe runs on the card it was saved from, and that
        card's own graph is not the one that was edited.
        """
        if not card.variants:
            return None, None
        hub = _hub()
        workflow_id = workflow_of_variant(hub, card.variants[0])
        if not workflow_id:
            return None, None
        edited, version = workflow_versions.edited_document(hub, workflow_id)
        if version is None:
            return None, None
        workflow = find_workflow(hub, workflow_id, _counts())
        if workflow is None or workflow.base_card != card.workflow_key:
            return None, None
        if api_graph(edited) is None:
            # Only a row written from outside this route can be one: every
            # overwrite stores a checked API graph. The version still stands,
            # so the next overwrite is made on it and replaces it.
            logger.warning(
                "Workflow %s: version %d of its graph is not an API graph, so "
                "the graph its pictures hold is used instead.",
                workflow_id,
                version,
            )
            return None, version
        return edited, version

    def _source_graph_for(
        card, object_info: dict | None = None, comfyui_url: str | None = None
    ) -> tuple[run_service.Source | None, run_service.Reason | None]:
        """Resolve one card's runnable source, doing the reads the tiers need.

        The reads are done here and the decision in the service, so the order
        the tiers are tried in is testable without a vault: this function only
        stops early because reading the next tier costs a file or a query.

        An editor-format file or manual document is converted with
        ``object_info`` (or the map read from *comfyui_url*) before it is
        judged, so it runs without a trip through ComfyUI first.
        """
        if card.manual:
            # Its own row and nothing else: no file, no picture, no instance.
            # Converted, and the conversion stored, if it is an editor document
            # nothing has converted yet; ComfyUI is asked only then.
            def read_object_info() -> dict | None:
                if object_info is not None or not comfyui_url:
                    return object_info
                return _read_object_info(comfyui_url, cached=True)[0]

            converted, version, problems = converted_manual_document(
                _hub(), card.workflow_key, read_object_info
            )
            source, reason = run_service.resolve_source(
                card, file_document=converted, file_problems=problems
            )
            if source is not None:
                # The version read with the document, so the number the run
                # records is the graph it built.
                source.workflow_version = version
            return source, reason
        edited, edited_version = _edited_graph_for(card)
        if edited is not None:
            # What the owner saved over this workflow's graph: no file,
            # picture or instance is read, since none of them is its graph now.
            source, reason = run_service.resolve_source(card, edited_document=edited)
            if source is not None:
                source.workflow_version = edited_version
            return source, reason
        file_document = None
        file_problems: list[str] = []
        if card.file_name:
            path, _source = _resolve_workflow_path(card.file_name)
            if path:
                try:
                    file_document, file_problems = _converted_document(
                        card,
                        runnable_document(path, _load_workflow_json(path)),
                        object_info,
                        comfyui_url,
                    )
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
        source, reason = run_service.resolve_source(
            card,
            file_document=file_document,
            picture_graph=picture_graph,
            picture_id=picture_id,
            instance_documents=instances,
            asset_names=names,
            file_problems=file_problems,
        )
        if source is not None:
            # A stored version that would not read: the pictures' graph runs,
            # and an overwrite made on it is still made on that version.
            source.workflow_version = edited_version
        return source, reason

    def _apply_addressed(graph: dict, values: list[RunValue]) -> None:
        """Write each ``(slot label, input name)`` value into the graph.

        Addressed by label because that is how a card's defaults are addressed:
        node ids are renumbered by every re-serialisation and a card's variants
        do not agree about them. A wired input is left alone - overwriting one
        drops the link - except the run's size (``size_inputs``), which
        ``set_latent_size`` cuts only when the value differs from what the wire
        carries. An input the graph does not have is not invented.
        """
        if not values:
            return
        labels, core = _graph_labels(graph, "parameters")
        wanted = {(item.slot_label, item.input_name): item.value for item in values}
        # One input can be named two ways (slot label, core address); the entry
        # later in *values* wins, as a later one of the same address does.
        order = {
            (item.slot_label, item.input_name): index
            for index, item in enumerate(values)
        }
        found = set()
        sized = size_inputs(graph)
        for node_id, node in graph.items():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            addressed_as = [labels.get(node_id)]
            if node_id in core:
                addressed_as.append(CORE_ADDRESS_PREFIX + core[node_id])
            for name in list(inputs):
                # A wired input is left alone - overwriting drops the link -
                # so it counts as not applied and is logged below. Except the
                # run's size: the one wire a run cuts on purpose.
                latent_size = (str(node_id), name) in sized
                if isinstance(inputs[name], list) and not latent_size:
                    continue
                matches = [
                    (label, name) for label in addressed_as if (label, name) in wanted
                ]
                if matches:
                    found.update(matches)
                    value = wanted[max(matches, key=order.__getitem__)]
                    if latent_size:
                        set_latent_size(graph, node, name, value)
                    else:
                        inputs[name] = value
        for slot_label, input_name in sorted(set(wanted) - found):
            # Not an error: a card's defaults are read off every variant, and a
            # stage-node address goes stale when the base topology changes
            # (#1622). Logged, because it used to vanish without a word.
            logger.info(
                "[workflows] Parameter %s/%s matches no settable input on the "
                "run graph, so it is not applied.",
                slot_label,
                input_name,
            )

    def _graph_labels(graph: dict, what: str) -> tuple[dict, dict]:
        """``(slot labels, core labels)`` of *graph*, or the 400 saying why not.

        A graph whose core strip leaves nothing (every node plumbing, a stage
        or a LoRA loader) has no core addresses, and says so in the log rather
        than refusing a run addressed by slot label, which always worked.
        """
        try:
            document = structural_document(graph)
            labels = topology_node_labels(document)
        except WorkflowGraphError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"This workflow will not reduce, so its {what} cannot be addressed: {exc}",
            ) from exc
        try:
            core = core_node_labels(document, strip_loras=STRIP_LORAS_FOR_STACKS)
        except WorkflowGraphError as exc:
            logger.info(
                "[workflows] This graph has no core, so no core: address "
                "matches it: %s",
                exc,
            )
            core = {}
        return labels, core

    def _apply_models(
        hub, graph: dict, models: list[RunModel], object_info: dict | None
    ) -> list[dict]:
        """Load each asked-for model at its loader address; return the flags.

        Through ``apply_filename_swap``, so the file is written in ComfyUI's
        spelling. A model made for another family or modality than the one it
        replaces is flagged ``family_mismatch`` and still loaded (#1620 Q3): a
        family table not knowing a base model is no reason to refuse a run. One
        ComfyUI cannot load is flagged ``model_not_applied`` and the loader
        keeps its file, which ``judge`` then checks like any other.
        """
        if not models:
            return []
        labels, core = _graph_labels(graph, "models")
        shelf = _swap_models(hub)
        by_digest = {
            str(row["sha256"]).lower(): row["filename"]
            for row in hub.fetchall(
                "SELECT sha256, filename FROM model "
                "WHERE sha256 IS NOT NULL AND filename IS NOT NULL"
            )
        }
        base_models: dict[str, str | None] = {}
        for model in shelf.values():
            name = normalized_filename(model.filename)
            # A name two shelf models share is no answer about either.
            base_models[name] = None if name in base_models else model.base_model
        flags = []
        for model in models:
            slot_label, _, widget = model.address.rpartition(OVERRIDE_ADDRESS_SEPARATOR)
            now = model.filename or by_digest.get(str(model.sha256).lower())
            if now is None:
                # A saved recipe can outlive the model it pinned: the loader
                # keeps its file and the run goes ahead, as for any other miss.
                logger.warning(
                    "[workflows] No shelf model has sha256 %s, so %s keeps its file.",
                    model.sha256,
                    model.address,
                )
                flags.append(
                    {
                        "code": "model_not_applied",
                        "address": model.address,
                        "was": None,
                        "now": model.sha256,
                        "reason": "not_on_shelf",
                    }
                )
                continue
            node_ids = [
                node_id
                for node_id in graph
                if labels.get(node_id) == slot_label
                # Only a node the core kept has a core address: spelled with
                # an empty label, the prefix alone would name every other one.
                or (
                    node_id in core
                    and CORE_ADDRESS_PREFIX + core[node_id] == slot_label
                )
            ]
            loaders = [
                node_id
                for node_id in node_ids
                if isinstance(graph[node_id], dict)
                and isinstance(graph[node_id].get("inputs"), dict)
                and isinstance(graph[node_id]["inputs"].get(widget), str)
            ]
            if not loaders:
                logger.info(
                    "[workflows] Model address %s matches no loader on the run "
                    "graph, so %s is not loaded.",
                    model.address,
                    now,
                )
                flags.append(
                    {
                        "code": "model_not_applied",
                        "address": model.address,
                        "was": None,
                        "now": now,
                        "reason": "no_loader",
                    }
                )
            for node_id in loaders:
                was = graph[node_id]["inputs"][widget]
                if normalized_filename(was) == normalized_filename(now):
                    continue
                # Only this loader: another naming the same file (a refiner, a
                # stage's) is another address.
                done, missed = apply_filename_swap(
                    {node_id: graph[node_id]},
                    {was: now},
                    object_info,
                    fields=lambda _cls, field, widget=widget: field == widget,
                )
                for entry in missed:
                    logger.warning(
                        "[workflows] %s cannot be loaded at %s (%s), so the "
                        "loader keeps %s.",
                        now,
                        model.address,
                        entry["reason"],
                        was,
                    )
                    flags.append(
                        {
                            "code": "model_not_applied",
                            "address": model.address,
                            "was": was,
                            "now": now,
                            "reason": entry["reason"],
                        }
                    )
                for entry in done:
                    was_base = base_models.get(normalized_filename(entry["was"]))
                    now_base = base_models.get(normalized_filename(entry["now"]))
                    if families_clash(was_base, now_base):
                        logger.info(
                            "[workflows] %s (%s) is loaded in place of %s (%s): "
                            "another family, flagged and run.",
                            entry["now"],
                            now_base,
                            entry["was"],
                            was_base,
                        )
                        flags.append(
                            {
                                "code": "family_mismatch",
                                "address": model.address,
                                "was": entry["was"],
                                "now": entry["now"],
                                "was_base_model": was_base,
                                "now_base_model": now_base,
                            }
                        )
        return flags

    def _swapped_to_digest_loader(
        graph: dict,
        item: RunLora,
        adapter: dict,
        object_info: dict | None,
        swaps: list[dict] | None = None,
    ) -> bool:
        """Load *item*'s LoRA through the ComfyUI-PixlStash loader, if one can.

        The fallback for a filename slot this ComfyUI cannot fill with the
        shelf LoRA (its file is not there): the node becomes
        ``PixlStashAdapterLoader``, keyed by the adapter's digest, and keeps its
        wiring. False, with the graph untouched, where the pack is not
        installed or the node is not a core single-slot loader.
        """
        if not object_info or PIXLSTASH_ADAPTER_LOADER not in object_info:
            return False
        node = graph.get(item.node_id) or {}
        was = {
            "node_id": item.node_id,
            "class_type": node.get("class_type"),
            "file": str((node.get("inputs") or {}).get(item.field) or ""),
            "sha256": adapter["sha256"],
        }
        try:
            swap_to_adapter_loader(graph, item.node_id, adapter["sha256"], object_info)
        except LookupError as exc:
            logger.info(
                "LoRA loader %s cannot take %s through %s: %s",
                item.node_id,
                adapter["sha256"],
                PIXLSTASH_ADAPTER_LOADER,
                exc,
            )
            return False
        logger.info(
            "LoRA %s is not on this ComfyUI, so loader %s now loads it by its "
            "hash through %s.",
            adapter["sha256"],
            item.node_id,
            PIXLSTASH_ADAPTER_LOADER,
        )
        if swaps is not None:
            swaps.append(was)
        return True

    def _apply_loras(
        graph: dict,
        loras: list[RunLora],
        object_info: dict | None,
        swaps: list[dict] | None = None,
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
                # The adapter is on the shelf and not on this ComfyUI. Where
                # ComfyUI-PixlStash is installed, the loader is swapped to its
                # digest loader, which resolves the LoRA by hash and fetches it,
                # so the LoRA asked for is loaded rather than refused.
                if not _swapped_to_digest_loader(
                    graph, item, adapter, object_info, swaps
                ):
                    # The same fact as any other model the graph names and it
                    # must not be a different kind of answer: a dry run that
                    # 400s instead of reporting `missing_models` is not a dry
                    # run.
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

    def _add_loras(
        graph: dict,
        loras: list[RunAddedLora],
        object_info: dict | None,
        allow_unchecked: bool,
        swaps: list[dict] | None = None,
    ) -> list[run_service.Reason]:
        """Splice a loader per LoRA into *graph*, after the model source.

        A LoRA the graph already loads gets its strengths set on that loader
        instead of a second copy. Each new loader goes in after the previous
        one, so they stack in the order given. Returns the reasons that stop
        this card: ``lora_not_insertable`` with the splice's own sentence,
        which says why (no model source to splice after, or the LoRA is not on
        this ComfyUI and ComfyUI-PixlStash is not there to fetch it).
        """
        if object_info is None:
            # The splice is typed by ComfyUI (#1376): without its node list an
            # input reading the model could be missed, and that branch would
            # run without the LoRA. `judge` already refuses an uninspected
            # graph as `comfyui_unreachable`; a caller that consented to run
            # unchecked asked for the LoRA, not for a run without it.
            if not allow_unchecked:
                return []
            raise HTTPException(
                status_code=400,
                detail=(
                    "PixlStash could not reach ComfyUI, so it cannot tell where "
                    "to add the LoRA. Start ComfyUI, or run without it."
                ),
            )
        hub = getattr(server, "hub", None)
        shelf_index = adapter_digest_index(hub)
        # Every LoRA answers for itself: one that cannot be added does not
        # hide the next, so the owner sees them all rather than one per retry.
        refused: list[run_service.Reason] = []
        for item in loras:
            sha256 = item.sha256.strip().lower()
            adapter = _shelf_adapter(hub, sha256)
            slots = detect_lora_targets(graph)
            digests = _slot_digests(slots, shelf_index) if slots else {}
            present = [
                slot
                for slot in slots
                if digests.get((str(slot["node_id"]), str(slot["field"]))) == sha256
            ]
            if present:
                found = _apply_loras(
                    graph,
                    [
                        RunLora(
                            node_id=str(slot["node_id"]),
                            field=str(slot["field"]),
                            sha256=sha256,
                            strength_model=item.strength_model,
                            strength_clip=item.strength_clip,
                        )
                        for slot in present
                    ],
                    object_info,
                    swaps,
                )
                refused += found
                continue
            try:
                plan = plan_lora_insertion(graph, object_info)
                loader = insert_adapter(graph, plan, adapter, object_info)
            except LookupError as exc:
                logger.info("LoRA %s cannot be added to this workflow: %s", sha256, exc)
                refused.append(
                    run_service.Reason(
                        run_service.LORA_NOT_INSERTABLE, {"detail": str(exc)}
                    )
                )
                continue
            inputs = (graph.get(loader["node_id"]) or {}).get("inputs") or {}
            if item.strength_model is not None:
                for name in ("strength_model", "strength"):
                    if name in inputs:
                        inputs[name] = item.strength_model
                        break
            if item.strength_clip is not None and "strength_clip" in inputs:
                inputs["strength_clip"] = item.strength_clip
        return refused

    def _leave_out_character_loras(
        graph: dict,
        recipe: DefaultRecipe,
        body: RunRequest,
        object_info: dict | None,
        shelf_index,
    ) -> None:
        """Bypass the loaders of *graph* still holding a person's LoRA.

        A workflow run by itself is nobody's portrait: which person a picture
        is of belongs to a recipe, or to the LoRA this run adds
        (``add_loras``). A slot the request names itself (``loras``,
        ``skip_loras``) is the owner's and is left to them, and so is the
        loader of a LoRA this run adds: ``_add_loras`` sets its strengths where
        it is, rather than a splice the graph may refuse. Best effort, like
        the default recipe's other bypasses: a loader that cannot be taken out
        keeps its LoRA and the run goes ahead.
        """
        added = {item.sha256.strip().lower() for item in body.add_loras}
        left_out = recipe.character_loras - added
        if not left_out:
            return
        asked = {item.node_id for item in [*body.loras, *body.skip_loras]}
        digests = _slot_digests(
            live_lora_targets(graph, object_info),
            shelf_index or adapter_digest_index(_hub()),
        )
        slots = [
            slot
            for slot, digest in digests.items()
            if digest in left_out and slot[0] not in asked
        ]
        skipped, left_in, _found = run_service.skip_requested_loras(
            graph, slots, object_info
        )
        for entry in skipped:
            logger.info(
                "[workflows] Workflow %s runs without the character LoRA in "
                "node %s: who a picture is of is a recipe's, not the workflow's.",
                recipe.workflow_id,
                entry.get("node_id"),
            )
        for reason in left_in:
            logger.info(
                "[workflows] Workflow %s keeps a character LoRA that cannot be "
                "bypassed: %s",
                recipe.workflow_id,
                reason.as_dict(),
            )

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
        workflow_id: str | None,
        graph: dict,
        addressed: bool,
        bindings: list | None = None,
        object_info: dict | None = None,
    ) -> list[CardInput]:
        """The picture inputs in *graph*, with the workflow's stored setup over them.

        A graph that will not reduce has nothing addressable in it. That is a
        400 only when the request ADDRESSED an input (``_apply_addressed``'s
        rule and wording); otherwise the graph runs exactly as it did before
        picture inputs were filled, rather than a runnable one starting to
        refuse over a question nobody asked.
        """
        library_uuid = _library_uuid()
        stored = (
            group_picture_inputs(hub, library_uuid, workflow_id)
            if library_uuid and workflow_id
            else []
        )
        try:
            inputs = card_input_modes(
                graph,
                _on_graph_labels(graph, stored),
                live_node_ids(graph, object_info) if object_info else None,
            )
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
                "Workflow %s's graph will not reduce, so its picture inputs are "
                "left as the graph has them: %s",
                workflow_id,
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

    def _on_graph_labels(graph: dict, stored: list[dict]) -> list[dict]:
        """*stored* with each ``core:`` address put as the graph's own slot label.

        A workflow's setup may address an input by its core address, which
        every topology of the workflow shares (the conversion writes those);
        :func:`card_input_modes` reads slot labels. A graph with no core, or
        a core address it has no node for, leaves the row as it is, which
        then reads as an input the graph has lost.
        """
        if not any(row["slot_label"].startswith(CORE_ADDRESS_PREFIX) for row in stored):
            return stored
        try:
            labels, core = _graph_labels(graph, "picture inputs")
        except HTTPException as exc:
            logger.info(
                "Picture-input addresses left as stored, the graph will not reduce: %s",
                exc.detail,
            )
            return stored
        by_core = {
            CORE_ADDRESS_PREFIX + label: labels[node_id]
            for node_id, label in core.items()
            if node_id in labels
        }
        return [
            {**row, "slot_label": by_core.get(row["slot_label"], row["slot_label"])}
            for row in stored
        ]

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
                ("workflow_id", bool(body.workflow_id)),
            )
            if given
        ]
        if len(chosen) != 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Name exactly one source: picture_ids, saved_recipe_id or "
                    "workflow_id."
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
    ) -> tuple[RunRequest, str | None, list[dict], str | None]:
        """The body with a saved recipe's own look filled in underneath it.

        The row is what somebody pressed Save on, so it supplies the prompt,
        the LoRAs, the overrides and the seed - and the request still wins over
        every one of them, because the panel that sends both is showing the
        recipe with the owner's edits on top.
        """
        if body.saved_recipe_id is None:
            return body, None, [], None
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
                # A recipe's own models (#1622), unless the request names some.
                # Pins over the workflow's default recipe, address by address,
                # which is applied under this body later: NULL and [] pin nothing.
                # under this body later.
                "models": [m.model_dump() for m in body.models]
                or stored["models"]
                or [],
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
        return (
            merged,
            stored["workflow_key"],
            stored["loras"] if not body.loras else [],
            stored["workflow_id"],
        )

    def _workflow_recipe(workflow_id: str) -> DefaultRecipe:
        """A workflow's default recipe, or the 404 / 422 saying why not."""
        recipe = _defaults(_hub(), _workflow_id(workflow_id))
        if recipe is None or recipe.base_card is None:
            raise HTTPException(status_code=404, detail="Unknown workflow.")
        return recipe

    def _under_defaults(body: RunRequest, recipe: DefaultRecipe) -> RunRequest:
        """*body* with the default recipe filled in underneath it (#1622).

        Models only: the request wins address by address. Values are written
        in a pass of their own BEFORE the request's (``_recipe_values``),
        because one input can be addressed two ways - by slot label and by
        core address - and merging the two lists could let the recipe's win.
        The LoRAs and stages are applied after the graph resolves.
        """
        asked = {m.address for m in body.models}
        for model in recipe.models:
            if not model.filename and model.address not in asked:
                # A forgotten name: the loader keeps the base graph's own file.
                logger.info(
                    "[workflows] Workflow %s's default model at %s has no "
                    "readable name, so the loader keeps its file.",
                    recipe.workflow_id,
                    model.address,
                )
        return body.model_copy(
            update={
                "models": [
                    RunModel(address=m.address, filename=m.filename)
                    for m in recipe.models
                    if m.filename and m.address not in asked
                ]
                + list(body.models),
            }
        )

    def _recipe_values(recipe: DefaultRecipe) -> list[RunValue]:
        return [
            RunValue(slot_label=d.slot_label, input_name=d.input_name, value=d.value)
            for d in recipe.values
        ]

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
        body: RunRequest, recipe_key: str | None, base_card: str | None = None
    ) -> list[tuple[str | None, list[int], list[run_service.Reason]]]:
        """``(card key, picture_ids, reasons)`` per graph this request runs.

        The card key is internal: the base card of a workflow, or the card a
        picture's variant is on. With several pictures and no target the
        server groups them by each picture's recipe, which is the whole reason
        this is not one key: a selection spanning three cards is three
        different graphs, and running the first one over all of them would be
        silently wrong.
        """
        if base_card is not None:
            return [(base_card, [], [])]
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

    def _plan(
        request: Request,
        body: RunRequest,
        opening: bool = False,
        *,
        extract: bool = False,
    ) -> Plan:
        """Resolve, judge and prepare every run this body asks for.

        ``opening`` is Open in ComfyUI's plan: the owner's and the recipe's
        choices, none of Run's workarounds. The savers stay PixlStash ones,
        since a run queued by hand in ComfyUI has no import but theirs; the
        batch size is not pinned, since there is no count to stand in for
        it; and the repair registry does not run: a LoRA loader it bypassed
        would be gone from the graph ComfyUI saves, where the owner came to
        fix it.

        ComfyUI is asked for its ``object_info`` **once** and the answer is
        carried in the plan: the pre-flight, the LoRA resolution, the seed
        detection and the run all need it, and re-fetching it would also let
        the run submit against a different answer than the one it was judged
        against.

        *extract* is ``POST /recipes/{id}/extract-workflow``: the graph is to
        be stored, not submitted, so ComfyUI is not asked and every resolved
        graph is kept whatever would stop a run.
        """
        hub = _hub()
        _require_one_source(body)
        body, recipe_key, recipe_loras, recipe_workflow = _with_recipe(body)
        # A workflow run (#1622): the server applies the default recipe, and
        # the request - or the saved recipe it names - over it. A target is
        # the workflow that runs instead, over the source's pictures.
        workflow_id = body.target or body.workflow_id or recipe_workflow
        if (
            workflow_id == recipe_workflow
            and workflow_id
            and (
                not _WORKFLOW_ID_RE.fullmatch(workflow_id)
                or find_workflow(hub, workflow_id) is None
            )
        ):
            # The recipe's workflow is gone (deleted, or refiled by a rule
            # change): it runs on the card it was saved from, with no default
            # recipe under it, or not at all.
            logger.warning(
                "[workflows] Saved recipe %s names workflow %s, which this hub "
                "no longer holds; it runs on its own card.",
                body.saved_recipe_id,
                workflow_id,
            )
            workflow_id = None
        user = _user(request)
        if workflow_id and not extract:
            # Before the default recipe, which is read off the graph too.
            _freshen(user, workflow_id)
        recipe = _workflow_recipe(workflow_id) if workflow_id else None
        # A workflow run by itself is nobody's portrait: a person's LoRA is a
        # recipe's business, so only a run made from pictures places one the
        # default recipe set aside, and only this one bypasses the graph's own.
        alone = body.workflow_id is not None
        # The person this run names (`add_loras`) keeps the loader they already
        # have: bypassed and spliced back, a graph the splice refuses would
        # stop a run that only wanted another strength.
        added = {item.sha256.strip().lower() for item in body.add_loras}
        default_loras = (
            recipe.recipe_loras(people=added if alone else None)
            if recipe is not None
            else []
        )
        if recipe is not None:
            body = _under_defaults(body, recipe)
            if not body.loras and not recipe_loras:
                recipe_loras = default_loras
        configured = bool(getattr(user, "comfyui_url", None))
        comfyui_url = _comfyui_url(user)
        object_info, object_info_error = (
            (None, "not asked: the workflow is extracted, not run")
            if extract
            else _read_object_info(comfyui_url)
        )

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
        # A saved recipe runs on the card it was saved from, not on its
        # workflow's base card: its overrides and stages are that graph's, and a
        # stage-node override means nothing on another topology. Only a card
        # the hub no longer holds falls back to the base card.
        own_card = (
            body.saved_recipe_id is not None
            and not body.target
            and recipe_key is not None
            and find_card(hub, recipe_key) is not None
        )
        if (
            body.saved_recipe_id is not None
            and recipe_key
            and not body.target
            and not own_card
        ):
            # A target run leaves the card on purpose; only a missing card warns.
            logger.warning(
                "[workflows] Saved recipe %s names card %s, which this hub no "
                "longer holds; it runs on its workflow's base card.",
                body.saved_recipe_id,
                recipe_key,
            )
        groups = _groups_for(
            body,
            recipe_key,
            recipe.base_card
            if recipe is not None and not body.target and not own_card
            else None,
        )
        if body.target:
            # One target replaces every group's graph, keeping the pictures
            # that chose it: "run this workflow over what I selected".
            pictures = [pid for _, ids, _ in groups for pid in ids]
            groups = [(recipe.base_card, pictures, [])]
        if (body.skip_loras or body.choices) and len(
            {key for key, _, _ in groups if key}
        ) > 1:
            # A skip names a loader by its node id, which only means one thing
            # in one graph: across cards it could skip an unrelated LoRA and
            # report it as the owner's choice.
            raise HTTPException(
                status_code=400,
                detail=(
                    "skip_loras and choices name nodes by id, which only "
                    "identifies a node on one workflow; this run spans several. Run one "
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
            adapter_digest_index(hub)
            if (recipe_loras or recipe is not None) and not body.loras
            else None
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
        loader_swaps: list[tuple] = []
        built: list[tuple] = []
        # Which requested skips some graph of this run holds, and whether any
        # graph was resolved to look in: a skip no graph has is refused below.
        skips_found: set[tuple[str, str]] = set()
        skips_checked = False
        for card_key, picture_ids, reasons in groups:
            group = RunGroup(
                workflow_id=workflow_id,
                picture_ids=picture_ids,
                reasons=[r.as_dict() for r in reasons],
            )
            if not card_key:
                # Every keyless group converges here, so the invariant lives
                # here and not at each producer: `reasons` empty is the only
                # thing that means a group would run, and one with no card
                # cannot. Today every producer already attaches a reason (a
                # picture on no card gets `a1111` or `no_runnable_source`, and
                # `POST /recipes` refuses a recipe with no workflow), so
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
            card = find_card(hub, card_key)
            if card is None:
                group.reasons = [
                    run_service.Reason(
                        run_service.NO_RUNNABLE_SOURCE,
                        {"workflow_id": group.workflow_id},
                    ).as_dict()
                ]
                planned.append(group)
                continue
            if group.workflow_id is None:
                # A picture-sourced group runs the workflow its variant is in.
                group.workflow_id = (
                    workflow_of_variant(hub, card.variants[0])
                    if card.variants
                    else None
                )
            source, failure = _source_graph_for(card, object_info)
            if source is None:
                group.reasons = [
                    {**failure.as_dict(), "workflow_id": group.workflow_id}
                ]
                planned.append(group)
                continue
            group.source = source.origin
            group.source_picture_id = source.picture_id
            group.workflow_version = source.workflow_version

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
                hub,
                group.workflow_id,
                graph,
                bool(requested),
                source.bindings,
                object_info,
            )
            reached_inputs = True
            addressed.update(item.address for item in card_inputs)
            if recipe is not None:
                # The default recipe first, so every request value lands on top
                # of it however either addresses the input.
                _apply_addressed(graph, _recipe_values(recipe))
            _apply_addressed(graph, body.values)
            # After every value: `count` is the number of pictures, one per
            # submission, and a batch saved in the graph (or a recipe) would
            # multiply it.
            pinned = [] if opening else run_service.pin_batch_size(graph, object_info)
            if pinned:
                logger.info(
                    "[workflows] Run of %s: batch_size pinned to 1 on nodes %s so "
                    "count (%s) decides how many pictures come out.",
                    card_key,
                    pinned,
                    body.count,
                )
            group.flags = _apply_models(hub, graph, body.models, object_info)
            placed = run_service.apply_prompts(graph, body.prompt, body.negative)
            group.prompt = RunPrompt(
                positive_settable=placed["positive_settable"],
                negative_settable=placed["negative_settable"],
                positive_text=placed["positive_text"],
            )
            group.flags += [
                {"code": "prompt_not_applied", "side": side}
                for side in placed["unplaced"]
            ]
            if alone and recipe is not None:
                # First, so every step below reads the graph without them.
                _leave_out_character_loras(
                    graph, recipe, body, object_info, shelf_index
                )
            # A saved recipe's LoRAs are matched against the graph as it stood
            # BEFORE the skip: matched after it, the LoRA a skipped loader held
            # moved on to the next free slot and replaced a LoRA the owner had
            # not named, while the notice still said it was skipped.
            # Live loaders only: a recipe LoRA put in a loader no output reads
            # would be reported placed and never run.
            slots_before_skip = live_lora_targets(graph, object_info)
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
            if (
                recipe is not None
                and not body.loras
                and recipe.loras_decided
                # A LoRA with a digest is placed; one with only a filename
                # keeps the loader of that name. One with neither cannot be
                # told from the rest, so nothing is bypassed.
                and all(
                    saved.get("sha256") or saved.get("filename")
                    for saved in recipe_loras
                )
            ):
                # The recipe decides a workflow's LoRAs, so a loader it leaves
                # empty is bypassed (#1622). Best effort, unlike an owner's
                # skip: a loader that cannot be taken out keeps its LoRA and
                # the run goes ahead, rather than being refused for a choice
                # nobody made by hand.
                placed, kept = run_service.place_recipe_loras(
                    slots_before_skip,
                    recipe_loras,
                    _slot_digests(slots_before_skip, shelf_index)
                    if slots_before_skip
                    else {},
                )
                taken = {
                    (str(target["node_id"]), str(target["field"]))
                    for target, _ in placed
                } | {
                    (str(target["node_id"]), str(target["field"]))
                    for target in slots_before_skip
                    if any(entry["node_id"] == str(target["node_id"]) for entry in kept)
                }
                unfilled = [
                    (str(target["node_id"]), str(target["field"]))
                    for target in slots_before_skip
                    if (str(target["node_id"]), str(target["field"])) not in taken
                    and (str(target["node_id"]), str(target["field"])) not in skip_seen
                ]
                recipe_skipped, left_in, _ = run_service.skip_requested_loras(
                    graph, unfilled, object_info
                )
                for reason in left_in:
                    logger.info(
                        "[workflows] Workflow %s keeps a LoRA its recipe leaves "
                        "out: %s",
                        workflow_id,
                        reason.as_dict(),
                    )
                skipped += [{**entry, "requested": False} for entry in recipe_skipped]
            slots_in_graph = detect_lora_targets(graph)
            # Applied only when it CAN be, and after the two questions that
            # would otherwise be answered as the wrong failure: a graph with no
            # LoRA slot at all is `no_lora_loader` rather than a 400 about one
            # slot, and an unreachable ComfyUI cannot resolve a filename slot,
            # which `apply_adapter` would report as a missing node class.
            found: list[run_service.Reason] = list(skip_reasons)
            # LoRA loaders swapped to the ComfyUI-PixlStash loader on the way
            # (`swapped_loaders`); reported like the repairs, below. Apart by
            # cause: a swap for a LoRA this request named (`loras`,
            # `add_loras`) is the form's, and "Save fixed workflow" writes no
            # form, so only the recipe's own count towards it.
            lora_swaps: list[dict] = []
            recipe_swaps: list[dict] = []
            # What the repair registry changed in this graph, by `RunGroup`
            # field; put on the group only if it ends up being submitted. See
            # the assignment below.
            repaired: dict[str, list[dict]] = {}
            if body.loras and slots_in_graph:
                # NOT gated on `object_info`: skipping the application when
                # ComfyUI could not be asked is how a consented run silently
                # kept the stored graph's LoRA instead of the one that was
                # asked for. `_apply_loras` answers for that state itself.
                found += _apply_loras(graph, body.loras, object_info, lora_swaps)
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
                if recipe is not None and recipe_loras == default_loras:
                    # A default LoRA the shelf cannot name, which the loader
                    # still loads, is the graph as it was: nothing to report.
                    group.unplaced_loras = [
                        u for u in group.unplaced_loras if u["node_id"] is None
                    ]
                for unplaced in group.unplaced_loras:
                    logger.info(
                        "[workflows] Card %s runs without saved LoRA %s: %s",
                        card_key,
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
                        recipe_swaps,
                    )
            # Not gated on `found`: an added LoRA is applied, or its own reason
            # reported, whatever else refused. Skipped behind an earlier
            # refusal, a consented run would go ahead without it unsaid.
            if body.add_loras:
                found += _add_loras(
                    graph,
                    body.add_loras,
                    object_info,
                    body.allow_unchecked,
                    lora_swaps,
                )
            # The stages the owner switched off, after the LoRAs are placed:
            # the prune can take out a loader only the stage read, and a LoRA
            # addressed to it before then would be a 400 for a slot the graph
            # had when the request was made. Still before `judge`.
            found += run_service.skip_requested_stages(
                graph, body.skip_stages, object_info
            )
            # Not on a saved recipe's own card: that graph already has exactly
            # the stages the recipe ran with.
            off = [
                stage
                for stage, on in sorted(
                    recipe.stages.items() if recipe and not own_card else ()
                )
                if not on and stage not in body.skip_stages
            ]
            if off:
                # The recipe's own off-stages, best effort like its LoRAs: one
                # that cannot be taken out runs whole rather than refusing a
                # run over a choice nobody made by hand.
                for reason in run_service.skip_requested_stages(
                    graph, off, object_info
                ):
                    logger.info(
                        "[workflows] Workflow %s keeps a stage its recipe runs "
                        "without: %s",
                        workflow_id,
                        reason.as_dict(),
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
            # The owner's replacements for models that are gone, first: they
            # name what this workflow loads now, and the same-bytes swap below
            # only rescues a name still missing after them.
            swapped: dict[str, tuple[dict, dict]] = {}
            group.substitutions = _apply_model_fixes(card, graph, object_info, swapped)
            if object_info is not None:
                same_model = apply_model_swap(
                    graph,
                    detect_model_targets(graph, object_info),
                    aliases,
                    object_info,
                )
                group.substitutions += same_model
                for swap in same_model:
                    logger.info(
                        "[workflows] Card %s loads %s in place of %s on node %s "
                        "(%s.%s): the same model, from the copy this shelf still "
                        "has.",
                        card_key,
                        swap["now"],
                        swap["was"],
                        swap["node_id"],
                        swap["class_type"],
                        swap["field"],
                    )

            # After every value, so the replacement for a sampler or scheduler
            # this ComfyUI lacks wins over the default the form also sent.
            run_service.apply_choices(
                graph, [(c.node_id, c.field, c.value) for c in body.choices]
            )

            # The ComfyUI-PixlStash policy (#1521): a saver runs as SaveImage,
            # so the import below is the only one, and a loader's frozen
            # project, set or character id is looked up in this library.
            if not opening:
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
            if object_info is not None and not found and not opening:
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
            built.append((graph, source, card, swapped))
            if extract:
                # Stored, never submitted: nothing here stops it.
                planned.append(group)
                submittable.append((graph, group, feeds))
                continue
            if run_service.blocks_group(found, allow_unchecked=body.allow_unchecked):
                planned.append(group)
                continue
            if found:
                # The only reasons that survive here are ones the owner has
                # consented to, so say in the log what is being run blind.
                logger.warning(
                    "[workflows] Running card %s UNINSPECTED on the owner's "
                    "explicit acknowledgement: %s",
                    card_key,
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
            group.swapped_loaders = [
                {**entry, "requested": False} for entry in recipe_swaps
            ] + [{**entry, "requested": True} for entry in lora_swaps]
            # The owner's own skips ride in the same field, marked requested.
            group.bypassed_loras = skipped + group.bypassed_loras
            planned.append(group)
            submittable.append((graph, group, feeds))
            if swapped:
                loader_swaps.append((card, graph, swapped))

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
                group.swapped_loaders = []
            submittable = []
            loader_swaps = []
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
            loader_swaps=loader_swaps,
            built=built,
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
            "lora_not_skippable, stage_not_skippable, missing_choices. "
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
            "lora_not_skippable. skip_stages names optional stages (upscale, "
            "face_detailer, seed_variance, intermediate_save) this run goes without; a card whose stage cannot "
            "be taken out is stage_not_skippable. missing_choices names each "
            "sampler_name or scheduler this ComfyUI does not list, with its "
            "options and a replacement (euler, simple) to send back in choices. "
            "Likewise a custom seed node this ComfyUI "
            "lacks (rgthree's Seed and its kin) is replaced by the run's own "
            "seed and named in replaced_nodes, where every input it fed is one "
            "the seed pass writes; so is a plain text node (Text Multiline, "
            "CR Text, Textbox, PrimitiveStringMultiline), its string inlined. A "
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
        summary="Run a workflow",
        description=(
            "Runs what picture_ids, saved_recipe_id or workflow_id names; a "
            "workflow_id (or a target, which runs that workflow instead over "
            "the pictures selected) runs its base graph with its default "
            "recipe applied under the request. With several pictures and no "
            "target the server groups them by each picture's recipe. count "
            "submits that many runs of each, seed_mode is new, keep or fixed, "
            "and prompt/negative/loras/values are overrides applied to the "
            "graph at run time and never written back into it. inputs fills "
            "the graph's picture inputs; with exactly one left open by the "
            "workflow's pins and stored setup, the selection fills it unasked and "
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
        # Here, not in `_plan`: a preflight submits nothing, and the graphs
        # are final.
        for card, graph, swapped in plan.loader_swaps:
            _record_loader_swaps(card, graph, swapped)
        for graph, group, feeds in plan.submittable:
            output_node_ids = _extract_output_node_ids(graph, {})
            # The same finder `replace_missing_seed_nodes` checked its literals
            # against: a replaced seed node is only safe because this writes
            # every input it inlined.
            seed_targets = run_service.run_seed_targets(graph, object_info)
            # A manual workflow's run is filed on it.
            run_workflow_id = (
                group.workflow_id
                if (group.workflow_id or "").startswith(MANUAL_PREFIX)
                else None
            )
            run_workflow_version = group.workflow_version if run_workflow_id else None
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
                _tag_for_workflow(filled, run_workflow_id)
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
                    # ComfyUI accepts a prompt when ANY output validates and
                    # drops the rest, naming them in `node_errors`.
                    rejected = format_prompt_rejection(
                        {"node_errors": submitted.get("node_errors")}
                    )
                    if rejected:
                        logger.warning(
                            "[workflows] ComfyUI dropped outputs of prompt %s: %s",
                            prompt_id,
                            rejected,
                        )
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
                                "run_workflow_id": run_workflow_id,
                                "run_workflow_version": run_workflow_version,
                                "rejected": rejected,
                            },
                            daemon=True,
                        ).start()
                    prompts.append(
                        {"workflow_id": group.workflow_id, "prompt_id": prompt_id}
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
        if not _tag_save_nodes(
            graph, lambda own: build_stack_filename_prefix(own, stack_id, source_id)
        ):
            logger.warning(
                "[workflows] No save node to tag for stack %s (source %s); "
                "its outputs join the stack only if this run imports them.",
                stack_id,
                source_id,
            )

    def _tag_for_workflow(graph: dict, workflow_id: str | None) -> None:
        """Tag the save node so its outputs are filed on the manual workflow
        that ran however they arrive (#1688), as ``_tag_for_stack`` places them.

        Every run first drops a tag the graph inherited (one replayed from an
        earlier run's output carries it), so an automatic run's outputs, with
        *workflow_id* ``None``, are never filed on someone else's workflow.

        Not ``extra_pnginfo``: a run that sets it without a ``workflow`` key
        breaks custom nodes that read ``extra_pnginfo["workflow"]`` whenever it
        is present, and that key must stay empty (#628).
        """
        if workflow_id is None:
            for node in graph.values():
                if (
                    isinstance(node, dict)
                    and node.get("class_type") in FILE_SAVE_NODE_CLASSES
                ):
                    own = (node.get("inputs") or {}).get("filename_prefix")
                    if isinstance(own, str):
                        node["inputs"]["filename_prefix"] = strip_workflow_tags(own)
            return
        if not _tag_save_nodes(
            graph, lambda own: build_workflow_filename_prefix(own, workflow_id)
        ):
            logger.warning(
                "[workflows] No save node to tag for workflow %s; its "
                "outputs are filed on it only if this run imports them.",
                workflow_id,
            )

    def _tag_save_nodes(graph: dict, tagged_prefix) -> bool:
        """Rewrite each file saver's ``filename_prefix`` to ``tagged_prefix(own)``.

        ``SaveImage`` and the video savers (``FILE_SAVE_NODE_CLASSES``).

        Each save node keeps its OWN prefix under the tag; one wired from
        another node is left alone, since overwriting it drops the link.
        Whether any node was tagged.
        """
        tagged = False
        for node in graph.values():
            if (
                not isinstance(node, dict)
                or node.get("class_type") not in FILE_SAVE_NODE_CLASSES
            ):
                continue
            inputs = node.setdefault("inputs", {})
            own = inputs.get("filename_prefix")
            if isinstance(own, list):
                continue
            inputs["filename_prefix"] = tagged_prefix(str(own or ""))
            tagged = True
        return tagged

    # ── The file gestures (v1.12 B8) ──────────────────────────────────────
    #
    # Export, Duplicate, Clone with new models, Insert loader and Delete: the
    # five gestures that write or read a FILE, against a workflow that may
    # never have had one. Each acts on the workflow's base card and starts from
    # `_source_graph_for`, so a workflow the library only knows from its
    # pictures exports and duplicates like any other.
    #
    # Only the export applies the default recipe: it is the workflow as it is
    # meant to be run. A copy written for the owner (duplicate, clone, a chain
    # edit) is the graph as it is.

    def _no_graph_sentence(reason: dict | None) -> str:
        """The 409 sentence for a card with no graph: the code, and why if known."""
        code = (reason or {}).get("code") or run_service.NO_RUNNABLE_SOURCE
        why = (reason or {}).get("detail")
        return f"PixlStash has no graph for this workflow ({code})." + (
            f" {why}" if why else ""
        )

    def _card_source(
        card, *, object_info: dict | None = None, comfyui_url: str | None = None
    ):
        """One card's runnable graph, or the 409 that says why there is none.

        An editor-format document converts against *object_info*; a gesture
        that reads the map anyway passes it, so the map is fetched once and the
        conversion cannot disagree with the gesture about whether ComfyUI
        answered. One that does not need it passes *comfyui_url* instead, read
        (cached) only when there is an editor graph to convert.

        ``RecursionError`` is caught here rather than at each gesture because
        all three resolve through this one function and share the exposure: an
        embedded graph comes out of a picture that arrived from somewhere else,
        so its nesting depth is not ours to trust, and ``sanitize_prompt_graph``
        deep-copies it before anything of ours has looked at it.
        ``store_manual_workflow`` and ``_trash_stored_workflow`` already name
        this class for the same reason.
        """
        try:
            source, reason = _source_graph_for(card, object_info, comfyui_url)
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
                detail=_no_graph_sentence(reason.as_dict() if reason else None),
            )
        return source

    def _file_stem(card, name: str | None = None) -> str:
        """What a file written for this workflow should be called, without .json.

        *name* is the workflow's own name, which wins over the base card's
        file and the generated one.

        Sanitised, because a card's name is the owner's own text and it goes
        both into a path under the user folder and into a download name the
        CLIENT writes with — so the cleaning has to hold on the client's
        platform too, which ``os.path.basename`` on Linux does not do for a
        Windows separator. ``resolve_path_within`` is still the backstop on
        this side (``routes/comfyui.py``); this is the half that leaves.
        """
        stem = name or (os.path.splitext(card.file_name)[0] if card.file_name else "")
        return download_stem(stem or _display_name(card)) or "workflow"

    # Which of the two cleanings is the authority: for a file written on THIS
    # machine it is `_normalize_workflow_name` + `resolve_path_within` in
    # `routes/comfyui.py`, and `download_stem` above is advisory. For the
    # export's `filename` there is no server-side backstop at all, because the
    # client writes that file — so there `download_stem` is the authority.

    def _export_default_recipe(
        recipe: DefaultRecipe, graph: dict, object_info: dict | None
    ) -> tuple[set[tuple[str, str]], bool]:
        """Apply the default recipe to an export's graph.

        Returns the LoRA slots it keeps, and whether it took a loader out.

        Its values and models; its LoRAs where the graph already loads them,
        every other LoRA loader bypassed and each off stage switched off -
        both best effort, as a run does it: a loader that cannot be taken out
        is left for :func:`scrub_for_export` to empty, and a stage that
        cannot stays. With no majority among the LoRAs (``loras_decided``
        false) none is kept, so nothing that is the look leaves the machine.
        """
        _apply_addressed(graph, _recipe_values(recipe))
        _apply_models(
            _hub(),
            graph,
            [
                RunModel(address=m.address, filename=m.filename)
                for m in recipe.models
                if m.filename
            ],
            object_info,
        )
        targets = detect_lora_targets(graph)
        keep: set[tuple[str, str]] = set()
        if recipe.loras_decided and targets:
            digests = _slot_digests(targets, adapter_digest_index(_hub()))
            wanted_digests = {
                lora.sha256.lower() for lora in recipe.loras if lora.sha256
            }
            wanted_names = {
                normalized_filename(lora.filename)
                for lora in recipe.loras
                if lora.filename
            }
            for target in targets:
                slot = (str(target["node_id"]), str(target["field"]))
                if digests.get(slot) in wanted_digests or (
                    target.get("by") != "digest"
                    and normalized_filename(str(target.get("value") or ""))
                    in wanted_names
                ):
                    keep.add(slot)
        skip = [
            (str(target["node_id"]), str(target["field"]))
            for target in targets
            if (str(target["node_id"]), str(target["field"])) not in keep
        ]
        skipped, left_in, _found = run_service.skip_requested_loras(
            graph, skip, object_info
        )
        for reason in left_in:
            logger.info(
                "[workflows] Workflow %s exports with a LoRA loader its default "
                "recipe leaves out still in place (emptied): %s",
                recipe.workflow_id,
                reason.as_dict(),
            )
        off = [stage for stage, on in sorted(recipe.stages.items()) if not on]
        for reason in run_service.skip_requested_stages(graph, off, object_info):
            logger.info(
                "[workflows] Workflow %s exports with a stage its default "
                "recipe runs without: %s",
                recipe.workflow_id,
                reason.as_dict(),
            )
        return keep, bool(skipped)

    @router.get(
        "/workflows/{workflow_id}/export",
        summary="Export a workflow",
        description=(
            "This workflow as a ComfyUI file somebody else can open: its base "
            "graph with its default recipe applied (values and models set, "
            "LoRA loaders outside the recipe and stages it runs without "
            "bypassed where ComfyUI says how), then prompts and caption "
            "targets blank, seeds nulled, every LoRA slot not in the default "
            "recipe emptied, node titles stripped, picture file names blanked, "
            "and any model name this machine does not hold left out. Export a "
            "recipe instead to share what was actually run."
        ),
        response_model=WorkflowExport,
        responses={
            404: {"description": "This machine has no such workflow."},
            409: {"description": "There is no graph to export, or it will not read."},
        },
    )
    def export_workflow(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow, card = _require_base(hub, workflow_id)
        # Only a bypass and an editor-format document ask it: without ComfyUI
        # nothing is bypassed and the scrub empties every LoRA slot outside the
        # recipe instead.
        object_info, _error = _read_object_info(_comfyui_url(_user(request)))
        source = _card_source(card, object_info=object_info)
        recipe = _defaults(hub, workflow.workflow_id)
        graph = deepcopy(source.graph)
        try:
            keep, bypassed = (
                _export_default_recipe(recipe, graph, object_info)
                if recipe is not None
                else (set(), False)
            )
            document, removed = scrub_for_export(
                graph,
                kept_lora_slots=keep,
                unvouched=unvouched_model_values(hub),
            )
            if bypassed and LORA_SLOTS not in removed:
                # A loader taken out is the same fact as one emptied.
                removed = sorted([*removed, LORA_SLOTS])
        except HTTPException as exc:
            if exc.status_code != 400:
                raise
            # The recipe addresses a graph that will not reduce, which is
            # the scrub's own refusal below, and answered the same way.
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        except (WorkflowGraphError, RecursionError) as exc:
            # Refused rather than exported unscrubbed. A graph PixlStash cannot
            # read is one it can promise nothing about, and the promise is the
            # route. `RecursionError` is the same answer and the same class of
            # input: an embedded graph comes out of a picture that arrived from
            # somewhere else.
            raise HTTPException(
                status_code=409,
                detail=(
                    "PixlStash cannot read this workflow well enough to make it "
                    f"safe to share: {exc}"
                ),
            ) from exc
        # Named as the grid shows it. A generated name needs the models
        # (`Krea 2: Text to Image`), which `_file_stem`'s fallback has not got.
        shown = _display_names(
            read_grid(hub, server.vault, manual_models=_manual_models).figures
        ).get(workflow.workflow_id)
        return WorkflowExport(
            filename=f"{_file_stem(card, workflow.name or shown)}.json",
            workflow=document,
            removed=removed,
            source=source.origin,
        )

    @router.get(
        "/workflows/{workflow_id}/graph",
        summary="A workflow's runnable graph",
        description=(
            "This workflow as Run would submit it with nothing changed in the "
            "Run popup, for opening in the owner's own ComfyUI: the same graph "
            "Run picks, with the default recipe (values, LoRAs, stages, "
            "models), the owner's model fixes and the model-name swaps applied. "
            "The seed is a parameter like the rest: the default recipe's, else "
            "the graph's own; only a stored recipe's nulled seeds get a fresh "
            "one, so `seedless` is always false. Five "
            "differences from Run: a ComfyUI-PixlStash saver stays one, so a "
            "picture queued by hand still comes back; the batch size is not "
            "pinned to 1, since there is no count here; the picture inputs "
            "keep the graph's own values, since filling them uploads pictures "
            "into ComfyUI and a read must not; Run's repairs (a "
            "missing LoRA's loader bypassed, a missing seed node replaced) are "
            "not made, so ComfyUI shows what is missing; and a graph Run would "
            "refuse is answered anyway (its 409 names the reason only when no "
            "graph could be built at all), since ComfyUI is where a missing "
            "node or model is fixed. Credential widgets are blanked. A graph "
            "from a stored recipe may name models the library forgot "
            "(`forgotten`). The ComfyUI-PixlStash node reads it when ComfyUI "
            "is opened with `?pixlstash_workflow=<workflow_id>`. Export instead "
            "to give it away."
        ),
        response_model=WorkflowRunnableGraph,
        responses={
            404: {"description": "This machine has no such workflow."},
            409: {"description": "There is no graph for this workflow."},
        },
    )
    def get_runnable_graph(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow, _card = _require_base(hub, workflow_id)
        # Run's own plan, so what opens is what Run would submit (#1623 left a
        # workflow several graphs; this is the one Run picks).
        # `RecursionError` for `_card_source`'s reason: the source graph came
        # out of a picture from somewhere else, so its depth is not ours to trust.
        try:
            plan = _plan(
                request, RunRequest(workflow_id=workflow.workflow_id), opening=True
            )
        except RecursionError as exc:
            logger.warning(
                "Workflow %s has a source graph too deeply nested to read: %s",
                workflow_id,
                exc,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "PixlStash cannot read this workflow: its graph is nested "
                    "too deeply to walk."
                ),
            ) from exc
        # The workflow's ComfyUI file, when it was pulled from one.
        link = (
            live_file(hub, _comfyui_url(_user(request)), _card.workflow_key)
            if _card.manual
            else None
        )
        if not plan.built:
            reasons = [r for group in plan.groups for r in group.reasons]
            logger.info(
                "[workflows] Nothing to open for %s: no graph was built (%s).",
                workflow_id,
                [r["code"] for r in reasons],
            )
            first = reasons[0] if reasons else None
            # An editor file ComfyUI itself can open and convert is not a
            # dead end: answer with the file and let the node do it.
            if link:
                return WorkflowRunnableGraph(
                    name=_file_stem(_card, workflow.name),
                    workflow=None,
                    needs_conversion=True,
                    detail=(first or {}).get("detail") or _no_graph_sentence(first),
                    source=None,
                    comfyui_file=link["remote_path"],
                )
            raise HTTPException(status_code=409, detail=_no_graph_sentence(first))
        # One entry: a body naming a `workflow_id` resolves to exactly one
        # group (`_groups_for`), the workflow's base card, so there is no
        # second graph for Run to have preferred.
        graph, source, card, swapped = plan.built[0]
        if swapped:
            # What ComfyUI opens is what it runs: its pictures card here too.
            _record_loader_swaps(card, graph, swapped)
        # The seed is a parameter like any other, and the default recipe's is
        # already applied. A stored recipe keeps none by design, and
        # `resolve_references` stands a 0 in for each null it had: those get a
        # fresh seed, or every queue of the graph would make the same picture.
        # ponytail: a default recipe seed of exactly 0 is re-rolled with them.
        if source.seedless:
            placeholders = [
                target
                for target in run_service.run_seed_targets(graph, plan.object_info)
                if ((graph.get(target["node_id"]) or {}).get("inputs") or {}).get(
                    target["field"]
                )
                == 0
            ]
            apply_seeds(graph, placeholders, None)
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
            name=_file_stem(card, workflow.name),
            workflow=graph,
            source=source.origin,
            seedless=False,
            forgotten=source.forgotten,
            comfyui_file=link["remote_path"] if link else None,
        )

    @router.post(
        "/workflows/{workflow_id}/duplicate",
        summary="Duplicate a workflow",
        description=(
            "Write this workflow into the user's workflow folder under a free "
            "name, so it can be opened and changed in ComfyUI without touching "
            "the original. A workflow the library only knows from its pictures "
            "gets a file this way for the first time."
        ),
        response_model=WorkflowFile,
        status_code=201,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "There is no graph to duplicate."},
            413: {
                "description": "The graph is past the size a stored workflow may be."
            },
            500: {"description": "The copy could not be written."},
        },
    )
    def duplicate_workflow(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow, card = _require_base(hub, workflow_id)
        source = _card_source(card, comfyui_url=_comfyui_url(_user(request)))
        # Unscrubbed on purpose: this file stays on the owner's machine and is
        # meant to RUN, and a copy with its models blanked would not.
        name, landed = _store_copy(
            hub,
            f"{_file_stem(card, workflow.name)} (copy)",
            source.graph,
            source.bindings,
            "duplicate",
            workflow,
            card,
        )
        _announce(request, [landed], "imported")
        return WorkflowFile(name=name, workflow_id=landed)

    def _swap_missing_loras(graph: dict, object_info: dict) -> list[dict]:
        """Load each LoRA this ComfyUI lacks through the ComfyUI-PixlStash loader.

        Every core single-slot loader naming a file this ComfyUI does not list,
        whose digest the shelf can name, becomes ``PixlStashAdapterLoader``
        keyed by it (:func:`swap_to_adapter_loader`). Loaders the shelf cannot
        name, and every loader where the pack is not installed, are left as
        they are. Returns ``[{node_id, class_type, file, sha256}]``.
        """
        if PIXLSTASH_ADAPTER_LOADER not in object_info:
            return []
        missing = {
            str(item.get("node_id"))
            for item in detect_model_targets(graph, object_info)
            if item
            and run_service.model_folder(item.get("class_type"), item.get("field"))
            == "loras"
        }
        slots = [
            slot
            for slot in detect_lora_targets(graph)
            if str(slot["node_id"]) in missing and slot.get("by") != "digest"
        ]
        if not slots:
            return []
        digests = _slot_digests(slots, adapter_digest_index(_hub()))
        swapped = []
        for slot in slots:
            node_id, field = str(slot["node_id"]), str(slot["field"])
            sha256 = digests.get((node_id, field))
            if not sha256:
                continue
            node = graph[node_id]
            entry = {
                "node_id": node_id,
                "class_type": node.get("class_type"),
                "file": str((node.get("inputs") or {}).get(field) or ""),
                "sha256": sha256,
            }
            try:
                swap_to_adapter_loader(graph, node_id, sha256, object_info)
            except LookupError as exc:
                logger.info(
                    "LoRA loader %s keeps its file, which this ComfyUI lacks: %s",
                    node_id,
                    exc,
                )
                continue
            swapped.append(entry)
        return swapped

    @router.post(
        "/workflows/{workflow_id}/fixed-copy",
        summary="Save a workflow with this ComfyUI's repairs applied",
        description=(
            "Write a copy of this workflow with the repairs a run on this "
            "ComfyUI makes on the fly: the owner's model replacements, a model "
            "loaded under the name this ComfyUI has for the same file, a LoRA "
            "this ComfyUI lacks loaded through the ComfyUI-PixlStash loader by "
            "its hash, and a missing seed or text node replaced. A LoRA that "
            "cannot be loaded is kept, not removed, and nothing a run's own "
            "form sets (prompt, parameters, added LoRAs) is written. The "
            "original file is not changed; the copy is a workflow of its own."
        ),
        response_model=FixedWorkflowCopy,
        status_code=201,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "No graph, or nothing this ComfyUI needs fixed."},
            413: {
                "description": "The graph is past the size a stored workflow may be."
            },
            503: {"description": "ComfyUI could not be reached."},
        },
    )
    def save_fixed_workflow(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow, card = _require_base(hub, workflow_id)
        # What needs fixing is what THIS ComfyUI lacks, so without its node
        # list there is nothing to decide it from.
        object_info = _require_object_info(
            _read_object_info(_comfyui_url(_user(request))),
            "what it has, so it cannot tell what this workflow needs fixed",
        )
        source = _card_source(card, object_info=object_info)
        graph = deepcopy(source.graph)
        changes: list[str] = []
        for entry in _apply_model_fixes(card, graph, object_info, {}):
            changes.append(f"Loads {entry.get('now')} in place of {entry.get('was')}.")
        for entry in apply_model_swap(
            graph,
            detect_model_targets(graph, object_info),
            model_name_aliases(hub),
            object_info,
        ):
            changes.append(f"Loads {entry['now']} in place of {entry['was']}.")
        for entry in _swap_missing_loras(graph, object_info):
            changes.append(
                f"Loads {entry['file'] or 'its LoRA'} through the ComfyUI-PixlStash "
                "LoRA loader, which fetches it by its hash."
            )
        # The node repairs a run makes (#1463), and not the LoRA bypass: a copy
        # saved without a LoRA would lose it for good on every later run.
        for entry in run_service.replace_missing_text_nodes(
            graph, object_info
        ) + run_service.replace_missing_seed_nodes(graph, object_info):
            changes.append(
                f"Replaces {entry['class_type']} (node {entry['node_id']}), which "
                "this ComfyUI does not have."
            )
        if not changes:
            raise HTTPException(
                status_code=409,
                detail="Nothing in this workflow needs fixing on this ComfyUI.",
            )
        name, landed = _store_copy(
            hub,
            f"{_file_stem(card, workflow.name)} (fixed)",
            graph,
            source.bindings,
            "fixed",
            workflow,
            card,
        )
        _announce(request, [landed], "imported")
        logger.info(
            "Workflow %s saved as %s with this ComfyUI's repairs: %s",
            workflow_id,
            name,
            " ".join(changes),
        )
        return FixedWorkflowCopy(name=name, workflow_id=landed, changes=changes)

    # ── The LoRA chain (#1478) ──────────────────────────────────────────────
    # Read and written whole: the editor lists the loaders in the order a run
    # applies them, and one save is one new card however many gestures made
    # it.

    def _shelf_chain(hub, chain: dict) -> None:
        """Mark each loader of *chain* with the shelf LoRA it loads, in place.

        ``sha256`` is set when exactly one shelf LoRA matches - by digest for a
        digest slot, by case-folded basename otherwise - and a digest slot's
        ``name`` becomes the shelf's filename, since a hash names nothing to a
        reader.
        """
        every = _chain_loaders(chain)
        by_name, digests = adapter_digest_index(hub)
        found = _slot_digests(every, (by_name, digests))
        for loader in every:
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

    def _chain_loaders(chain: dict) -> list[dict]:
        """Every loader of *chain*: the trunk's, then each lane's."""
        return chain["loaders"] + [
            loader for lane in chain.get("lanes") or [] for loader in lane["loaders"]
        ]

    def _loader_payload(loader: dict) -> LoraChainLoader:
        return LoraChainLoader(
            node_id=str(loader["node_id"]),
            class_type=loader.get("class_type"),
            field=str(loader["field"]),
            filename=str(loader["value"]),
            name=loader["name"],
            strength=loader["strengths"].get("model"),
            # A model-only loader has no CLIP strength to show, and a loader
            # read untyped says nothing about its wiring either.
            strength_clip=loader["strengths"].get("clip"),
            sha256=loader.get("sha256"),
            on_shelf=loader.get("sha256") is not None,
        )

    def _sink_payload(sinks: list[dict], summary: str | None) -> LoraChainSink:
        return LoraChainSink(
            summary=summary,
            consumers=[LoraChainConsumer(**sink) for sink in sinks],
        )

    def _chain_payload(
        workflow_id: str, chain: dict, refusal: str | None, object_info
    ) -> LoraChain:
        model = chain.get("model_source")
        clip = chain.get("clip_source")
        same_node = bool(model and clip and clip["node_id"] == model["node_id"])

        def added_class(with_clip: bool) -> str | None:
            # The class plan_lora_chain puts in, named before the owner picks.
            if refusal is not None:
                return None
            added = "LoraLoader" if with_clip else "LoraLoaderModelOnly"
            return added if added in (object_info or {}) else None

        lanes = []
        for lane in chain.get("lanes") or []:
            source = lane["source"]
            lanes.append(
                LoraChainLane(
                    source=None
                    if source is None
                    else LoraChainSource(
                        node_id=str(source["node_id"]),
                        class_type=source.get("class_type"),
                        outputs=["MODEL"],
                    ),
                    sampler=LoraChainPass(**lane["pass"]),
                    sink=_sink_payload(lane["sinks"], lane.get("sink_summary")),
                    loaders=[_loader_payload(loader) for loader in lane["loaders"]],
                    added_loader_class=added_class(
                        any(sink["type"] == "CLIP" for sink in lane["sinks"])
                    ),
                )
            )
        return LoraChain(
            workflow_id=workflow_id,
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
            sink=_sink_payload(chain["sinks"], chain.get("sink_summary")),
            loaders=[_loader_payload(loader) for loader in chain["loaders"]],
            # No trunk to add to when every pass loads its own model.
            added_loader_class=added_class(clip is not None) if model else None,
            lanes=lanes,
            branch_note=chain.get("branch_note"),
        )

    @router.get(
        "/workflows/{workflow_id}/lora-chain",
        summary="A workflow's LoRA chain",
        description=(
            "The LoRA loaders between this workflow's model source and what "
            "reads the model, in the order a run applies them, each with its "
            "strength and the shelf LoRA it loads. Typed from ComfyUI's "
            "object_info. Where the model forks, loaders is the trunk every "
            "pass reads and lanes holds one entry per pass. When ComfyUI "
            "cannot be reached, or the chain is one PixlStash cannot edit "
            "honestly (loaders that start from different places, CLIP passed "
            "in another order than the model), editable is false, refusal says "
            "why, and the loaders are still listed as read from the graph."
        ),
        response_model=LoraChain,
        responses={
            404: {"description": "This machine has no such card."},
            409: {"description": "There is no graph for this card."},
        },
    )
    def get_lora_chain(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _workflow, card = _require_base(hub, workflow_id)
        # Cached: the inspector asks on every card it selects, the map is
        # megabytes, and an unreachable ComfyUI would otherwise cost a full
        # timeout per click. This read only DRAWS the chain; the PUT that
        # rewires it reads a fresh map.
        object_info, error = _read_object_info(
            _comfyui_url(_user(request)), cached=True
        )
        graph = _card_source(card, object_info=object_info).graph
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
                    "Workflow %s's LoRA chain is shown read-only: %s", workflow_id, exc
                )
                refusal = str(exc)
        if chain is None:
            chain = read_lora_chain_untyped(graph, object_info)
        _shelf_chain(hub, chain)
        return _chain_payload(workflow_id, chain, refusal, object_info)

    def _chain_plan(
        hub,
        graph: dict,
        object_info: dict,
        asked_entries: list[LoraChainEntry],
        asked_lanes: list[list[LoraChainEntry]] | None,
    ) -> dict:
        """``plan_lora_chain`` for a chain asked in ``LoraChainEdit``'s shape.

        Shared by Edit LoRAs and the clone that carries a chain. Raises the
        409 an unknown loader, a LoRA not on the shelf or a chain PixlStash
        cannot edit honestly answers.
        """
        try:
            chain = read_lora_chain(graph, object_info)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _shelf_chain(hub, chain)
        loaded = {
            loader["node_id"]: loader.get("sha256") for loader in _chain_loaders(chain)
        }

        def planned(asked: list[LoraChainEntry]) -> list[dict]:
            entries: list[dict] = []
            for entry in asked:
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
                    entries.append(
                        {"node_id": entry.node_id, "strength": entry.strength}
                    )
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
            return entries

        entries = planned(asked_entries)
        lanes = None if asked_lanes is None else [planned(lane) for lane in asked_lanes]
        try:
            return plan_lora_chain(graph, chain, entries, object_info, lanes)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.put(
        "/workflows/{workflow_id}/lora-chain",
        summary="Edit a workflow's LoRA chain",
        description=(
            "Write a copy of this workflow with its LoRA chain as the owner "
            "left it: entries in apply order (and, for a forked chain, lanes: "
            "one list per pass), an existing loader by node_id "
            "(moved and re-weighted, its id kept), a new one by the shelf "
            "sha256 of its LoRA, and every loader left out deleted. One call "
            "is one new workflow and this one is not changed, unless "
            "overwrite asks for the edit to be saved over this one. That is "
            "its next version: the versions before are kept and its graph is "
            "the newest (an automatic workflow's first overwrite keeps the "
            "graph read off its pictures as version 1). Its name, pictures "
            "and recipes stay. dry_run answers the list of changes and writes "
            "nothing."
        ),
        response_model=LoraChainSaved,
        status_code=201,
        responses={
            200: {
                "model": LoraChainSaved,
                "description": (
                    "A dry run (the changes, nothing written), or an "
                    "overwrite (this workflow, changed)."
                ),
            },
            404: {"description": "This machine has no such card."},
            409: {
                "description": (
                    "No graph, nothing changed, an unknown or repeated loader, a "
                    "LoRA not on the shelf or not on this ComfyUI, a chain "
                    "PixlStash cannot edit honestly, or an overwrite of a "
                    "workflow that got another version meanwhile."
                )
            },
            413: {
                "description": "The graph is past the size a stored workflow may be."
            },
            503: {"description": "ComfyUI could not be reached."},
        },
    )
    def edit_lora_chain(
        request: Request,
        response: Response,
        workflow_id: str,
        payload: LoraChainEdit = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow, card = _require_base(hub, workflow_id)
        # Read before the source so an editor document converts against the
        # same map; required after it, so a card with no graph is still a 409.
        read = _read_object_info(_comfyui_url(_user(request)))
        source = _card_source(card, object_info=read[0])
        object_info = _require_object_info(
            read,
            "what its nodes hand on, so it cannot rewire this workflow's LoRAs",
        )
        graph = deepcopy(source.graph)
        plan = _chain_plan(hub, graph, object_info, payload.entries, payload.lanes)
        try:
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
        if payload.overwrite:
            if not card.manual and (
                not card.variants
                or workflow_of_variant(hub, card.variants[0]) != workflow.workflow_id
            ):
                # `_edited_graph_for` finds a workflow's versions through the
                # card's first variant: a card without one would answer 200
                # here and never be read back. Its other condition, that the
                # card is the workflow's base card, is what `_require_base`
                # handed us.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "PixlStash cannot save over this workflow: its graph is "
                        "not read off its pictures. Save it as a new workflow."
                    ),
                )
            _store_over(
                hub,
                workflow,
                graph,
                source.bindings,
                "chain",
                source.workflow_version,
                original=source.graph,
            )
            response.status_code = 200
            _announce(request, [workflow.workflow_id], "changed")
            return LoraChainSaved(
                dry_run=False,
                workflow_id=workflow.workflow_id,
                overwritten=True,
                changes=plan["changes"],
            )
        asked = re.sub(r"\.json$", "", (payload.name or "").strip(), flags=re.I)
        stem = download_stem(asked) if asked else ""
        name, landed = _store_copy(
            hub,
            stem or f"{_file_stem(card, workflow.name)} (edited)",
            graph,
            source.bindings,
            "chain",
            workflow,
            card,
        )
        _announce(request, [landed], "imported")
        return LoraChainSaved(
            dry_run=False, name=name, workflow_id=landed, changes=plan["changes"]
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

    def _base_key(base_model: str | None) -> str | None:
        """*base_model* as two spellings of one base model compare, or None."""
        return (base_model or "").strip().casefold() or None

    def _replaced_base_model(
        graph: dict, models: dict[int, SwapModel], index: tuple, wanted: str
    ) -> str | None:
        """The base model a checkpoint replacing *wanted* should share (``_base_key``).

        The shelf's own for the file, where the shelf still holds it; else the
        one base model the graph's LoRAs and ControlNets agree on, since they
        are what a replacement of another base model would not match, when the
        graph loads only this one base model. None when neither says, or they
        disagree: the offer is then not narrowed. A shelf checkpoint of no
        known base model is not offered while one that matches is: nothing
        says it does.
        """
        slots = [slot for _cls, _widget, slot in _swap_slots(graph, models, index)]
        own = next(
            (
                slot.model.base_model
                for slot in slots
                if slot.model is not None
                and normalized_filename(slot.filename) == wanted
            ),
            None,
        )
        if _base_key(own):
            return _base_key(own)
        if sum(slot.kind in BASE_MODEL_KINDS for slot in slots) > 1:
            # ponytail: which LoRAs feed which base model is the lane walk's
            # (`lora-chain`); a graph with two says nothing here instead.
            return None
        adapters = {
            _base_key(slot.model.base_model)
            for slot in slots
            if slot.kind in ("lora", "controlnet") and slot.model is not None
        } - {None}
        if len(adapters) == 1:
            return adapters.pop()
        if adapters:
            logger.info(
                "Offering every checkpoint in place of %s: its LoRAs and "
                "ControlNets name several base models (%s)",
                wanted,
                ", ".join(sorted(adapters)),
            )
        return None

    def _fix_replacements(
        request: Request,
        card,
        graph: dict,
        models: dict[int, SwapModel],
        index: tuple,
        replacing: str,
        kind: str | None,
    ) -> tuple[list[ModelFixCandidate], str | None, bool | None]:
        """What the Workflow tab may offer in place of *replacing*.

        Read off the graph a run submits, with the owner's fixes applied, so a
        replacement that has gone missing too is answered for the loader it
        sits in. Every filter below is required, a checkpoint's base model
        only while a loadable one has it:

        * **It goes with the checkpoint** (VAEs and text encoders):
          :func:`propose_companions` for the graph's base model, which is the
          owner's workflow sets first, then the recipes and ComfyUI runs that
          loaded the two together. ``declared`` entries are the cold case and
          are marked by their ``via``.
        * **It has the missing one's base model** (checkpoints):
          :func:`_replaced_base_model`, so it matches the LoRAs around it.
          When no loadable checkpoint has it, every loadable one is offered
          instead, as when nothing says which it was, and the answer says it
          is not narrowed. A missing checkpoint with nothing to pick is a
          dead end, and the base model of a file that is gone is often a
          guess off its LoRAs that the owner cannot correct.
        * **The loader can load it**: listed by every loader naming the file,
          by the rule the rewrite writes it with (:func:`listed_as`), when
          ComfyUI answers; of the same file type when it cannot be asked. A
          GGUF loader lists safetensors too; a core one never lists GGUF.
          When ComfyUI answers, a file the loader does not list passes too
          where a run would swap a PixlStash loader in for it
          (``run_service.plan_pixlstash_swap``, #1605), marked ``loader``.

        Returns:
            ``(candidates, reason, narrowed)``, *reason* set only when there
            are none, *narrowed* whether a checkpoint's were held to a base
            model (None for any other kind).

        Raises:
            HTTPException: 409 when the graph loads *replacing* in no slot a
                fix can fill (of *kind*, when given).
        """
        _apply_model_fixes(card, graph, None)
        wanted = normalized_filename(replacing)
        every_loader = [
            (cls, widget, value, fix_kind, node_id)
            for node_id, cls, widget, value in iter_model_fields_api(graph)
            if normalized_filename(value) == wanted
            and (fix_kind := model_fix_kind(cls, widget)) is not None
        ]
        loaders = [entry for entry in every_loader if kind in (None, entry[3])]
        if not loaders and every_loader:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This workflow loads that model as a "
                    + " and a ".join(
                        _FIX_KIND_NAMES[k] for k in sorted({e[3] for e in every_loader})
                    )
                    + f", not a {_FIX_KIND_NAMES[kind]}."
                ),
            )
        if not loaders:
            raise HTTPException(
                status_code=409, detail="This workflow does not load that model."
            )
        kinds = {entry[3] for entry in loaders}
        if len(kinds) > 1:
            # One file in slots of two kinds: which the owner means is a
            # guess, and serving one would hide the other's answer.
            raise HTTPException(
                status_code=409,
                detail=(
                    "This workflow loads that model as a "
                    + " and a ".join(_FIX_KIND_NAMES[k] for k in sorted(kinds))
                    + "; say which kind to replace."
                ),
            )
        kind = kinds.pop()
        base_model = None
        # `wider`: the checkpoints of another base model, offered only when
        # none in `candidates` can be loaded.
        candidates, wider = [], []
        if kind == FILE_CHECKPOINT:
            base_model = _replaced_base_model(graph, models, index, wanted)
            shelf = sorted(
                (
                    m
                    for m in models.values()
                    if m.file_kind == FILE_CHECKPOINT
                    and normalized_filename(m.filename) != wanted
                ),
                key=lambda m: (m.display_name or m.filename).lower(),
            )
            for m in shelf:
                same = base_model is None or _base_key(m.base_model) == base_model
                (candidates if same else wider).append(
                    ModelFixCandidate(
                        id=m.id, filename=m.filename, display_name=m.display_name
                    )
                )
        else:
            base = next(
                (
                    slot.model
                    for _cls, _widget, slot in _swap_slots(graph, models, index)
                    if slot.kind in BASE_MODEL_KINDS and slot.model is not None
                ),
                None,
            ) or next(
                (
                    models.get(int(value))
                    for _node, _cls, widget, value in iter_model_fields_api(graph)
                    if widget == SHELF_ID_FIELD and value.isdigit()
                ),
                None,
            )
            if base is None or base.file_kind != FILE_CHECKPOINT:
                return [], "no_checkpoint", None
            candidates = [
                ModelFixCandidate(
                    id=entry["id"],
                    filename=entry["filename"],
                    display_name=entry["display_name"],
                    via=entry["via"],
                )
                for entry in propose_companions(_hub(), base.id, index)[kind]
            ]
        candidates = [
            c for c in candidates if normalized_filename(c.filename) != wanted
        ]
        narrowed = base_model is not None if kind == FILE_CHECKPOINT else None
        if not candidates and not wider:
            return [], "none_go_with_it", narrowed
        object_info, error = _read_object_info(_comfyui_url(_user(request)))

        def loadable(candidates, info, log=True):
            """The candidates every loader naming the file can load, given *info*."""
            for cls, widget, value, fix_kind, node_id in loaders:
                options = listed_options(info, cls, widget)
                if options is not None:
                    # Listed by this loader, or loadable through a PixlStash
                    # one swapped in for it (#1605), by the run's own rule.
                    kept = []
                    for c in candidates:
                        if listed_as(c.filename, options):
                            kept.append(c)
                            continue
                        plan, _refusal = run_service.plan_pixlstash_swap(
                            graph,
                            node_id,
                            fix_kind,
                            {value: c.filename},
                            info,
                            _shelf_digest(_hub(), fix_kind),
                        )
                        if plan is not None:
                            kept.append(
                                c.model_copy(update={"loader": plan["class_type"]})
                            )
                    candidates = kept
                else:
                    if log:
                        logger.info(
                            "Offering %s replacements for %s by file type, "
                            "ComfyUI could not say what it lists: %s",
                            cls,
                            value,
                            error or "the field is not enumerated",
                        )
                    extension = os.path.splitext(value)[1].lower()
                    candidates = [
                        c
                        for c in candidates
                        if os.path.splitext(c.filename)[1].lower() == extension
                    ]
            return candidates

        found = loadable(candidates, object_info)
        if found:
            return found, None, narrowed
        if wider:
            logger.info(
                "No loadable checkpoint of base model %s to replace %s with: "
                "trying every checkpoint instead",
                base_model,
                wanted,
            )
            candidates, narrowed = wider, False
            found = loadable(candidates, object_info, log=False)
            if found:
                return found, None, narrowed
        pack = [cls for cls, _widgets in run_service.PIXLSTASH_SWAP_LOADERS.values()]
        if object_info is not None and any(cls not in object_info for cls in pack):
            # Would installing ComfyUI-PixlStash make one loadable in EVERY
            # loader naming the file? Asked by the same filter, pack declared.
            with_pack = {**{cls: {} for cls in pack}, **object_info}
            if loadable(candidates, with_pack, log=False):
                return [], "needs_pixlstash_nodes", narrowed
        return [], "none_loadable", narrowed

    @router.get(
        "/workflows/{workflow_id}/model-swap",
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
            404: {"description": "No such workflow, or no such checkpoint."},
            409: {"description": "There is no graph to clone."},
        },
    )
    def read_model_swap(
        request: Request,
        workflow_id: str,
        checkpoint_id: int | None = None,
        replacing: str | None = Query(None, min_length=1, max_length=MAX_VALUE_LENGTH),
        slot_kind: Literal["checkpoint", "vae", "text_encoder"] | None = None,
    ):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _workflow, card = _require_base(hub, workflow_id)
        # The graph is read on the proposal call too: the LoRA flags are about
        # the files it names.
        source = _card_source(card, comfyui_url=_comfyui_url(_user(request)))
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
        if replacing is not None:
            # The Workflow tab's "Replace with…", not the clone dialog.
            (
                options.replacements,
                options.replacements_reason,
                options.replacements_narrowed,
            ) = _fix_replacements(
                request,
                card,
                deepcopy(source.graph),
                models,
                index,
                replacing,
                slot_kind,
            )
            return options
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
            if families_clash(slot.model.base_model, chosen.base_model):
                options.flags.append(
                    SwapFlag(
                        filename=slot.filename,
                        kind=slot.kind,
                        base_model=slot.model.base_model,
                        family=family_of(slot.model.base_model),
                        modality=modality_of(slot.model.base_model),
                    )
                )
        return options

    def _closest_pairs(
        slots: list[SwapSlot],
        models: list[SwapModel],
        fits: Callable[[SwapSlot, SwapModel], bool] | None = None,
    ) -> list[tuple[SwapSlot, SwapModel]]:
        """*slots* paired with *models* by filename, the closest pair first.

        Each slot and each model is paired at most once, and only where
        *fits* (``(slot, model) -> bool``, when given) allows.
        """
        # ponytail: name similarity, the only thing telling two files of one
        # kind apart (two experts of a base model, a video and an audio VAE);
        # a shelf role per file would replace it.
        ranked = sorted(
            (
                -SequenceMatcher(
                    None,
                    normalized_filename(slot.filename),
                    normalized_filename(model.filename),
                ).ratio(),
                i,
                j,
            )
            for i, slot in enumerate(slots)
            for j, model in enumerate(models)
            if fits is None or fits(slot, model)
        )
        paired: dict[int, int] = {}
        for _ratio, i, j in ranked:
            if i not in paired and j not in paired.values():
                paired[i] = j
        return [(slots[i], models[paired[i]]) for i in sorted(paired)]

    def _pair_bases(
        found: list[tuple[str, str, SwapSlot]], checkpoints: list[SwapModel]
    ) -> list[tuple[SwapSlot, SwapModel]]:
        """Each base slot of *found* with the set checkpoint it takes, in graph order.

        Paired by filename, closest pair first, so a high-noise file is
        replaced by the high-noise one of a Wan 2.2 pair, a set holding only
        the low-noise expert replaces the low-noise file, and a graph loading
        one expert takes the set's matching one. A slot left over keeps its
        file.
        """
        bases = [slot for _c, _w, slot in found if slot.kind in BASE_MODEL_KINDS]
        return _closest_pairs(bases, checkpoints)

    def _clip_type(node: dict) -> str | None:
        """A CLIP loader node's ``type``, or None for any other node."""
        class_type = node.get("class_type", "")
        value = (node.get("inputs") or {}).get("type")
        if (
            isinstance(value, str)
            and "CLIPVision" not in class_type
            and any(
                f.startswith("clip_name") for f in model_filename_fields(class_type)
            )
        ):
            return value
        return None

    def _loads(slot: SwapSlot, model: SwapModel) -> bool:
        """Whether *slot* already loads *model*'s file.

        Only when its name is that shelf row (``SwapSlot.model``); a name the
        shelf cannot pin to one row is compared whole, so a generic
        ``diffusion_pytorch_model.safetensors`` in another folder is swapped
        rather than read as the same file.
        """
        if slot.model is not None:
            return slot.model.id == model.id
        return (
            slot.filename.replace("\\", "/").casefold()
            == model.filename.replace("\\", "/").casefold()
        )

    def _same_layout(slot: SwapSlot, model: SwapModel) -> bool:
        """Whether *model* has the layout of the file *slot* loads, when known."""
        family = slot.model.family if slot.model else None
        return bool(family) and model.family == family

    def _set_swaps(
        found: list[tuple[str, str, SwapSlot]],
        bases: list[tuple[SwapSlot, SwapModel]],
        members: list[SwapModel],
        picks: dict[str, int] | None = None,
    ) -> tuple[dict[str, str], dict[str, SwapModel]]:
        """The set's checkpoints, and which graph file each of its files replaces.

        Each base slot takes the checkpoint :func:`_pair_bases` paired it with
        (*bases*). The VAE and the text-encoder slots are each paired with the
        set's files of that kind in four passes, a file taken once:

        1. the owner's own pairing (*picks*, slot filename -> model id);
        2. a slot that already loads one of the set's files keeps it
           (:func:`_loads`), so a set holding the very files the workflow
           loads changes none of them;
        3. a slot takes the set's file of its own layout (``family``);
        4. when the set holds exactly as many files of the kind as the graph
           has slots, every slot left takes one: the set's files go with the
           set's checkpoint, so a Krea 2 text encoder replaces a Z-Image one
           whatever their layouts.

        Where a pass leaves a choice (two files of one layout, or the fill of
        pass 4) it pairs by filename, closest pair first
        (:func:`_closest_pairs`), never in order: a ``video_vae`` slot takes
        the set's ``video_vae`` wherever the set lists it. A slot the set has
        nothing for keeps its file, and so does one no pass fills; a pick
        naming a file the graph has no slot of that kind for is ignored. Two slots of a kind are two different
        files (the slot list merges loaders naming one file), so one set file
        is never written over both.

        Returns:
            ``(swaps, filled)``: the graph's filename -> the set's, for the
            files that change, and every slot's filename -> the set's model
            it now loads, changed or not.
        """
        picks = picks or {}
        paired = {slot.filename: new for slot, new in bases}
        chosen: dict[str, SwapModel] = {
            slot.filename: paired[slot.filename]
            for _c, _w, slot in found
            if slot.kind in BASE_MODEL_KINDS and slot.filename in paired
        }
        for kind, file_kind in (("vae", FILE_VAE), ("clip", FILE_TEXT_ENCODER)):
            files = [m for m in members if m.file_kind == file_kind]
            slots = [slot for _c, _w, slot in found if slot.kind == kind]
            taken: set[int] = set()

            def pair(fits: Callable[[SwapSlot, SwapModel], bool] | None) -> None:
                pairs = _closest_pairs(
                    [slot for slot in slots if slot.filename not in chosen],
                    [m for m in files if m.id not in taken],
                    fits,
                )
                for slot, model in pairs:
                    taken.add(model.id)
                    chosen[slot.filename] = model

            pair(lambda slot, model: picks.get(slot.filename) == model.id)
            pair(_loads)
            pair(_same_layout)
            if len(files) == len(slots):
                pair(None)
        ignored = {
            filename: model_id
            for filename, model_id in picks.items()
            if filename not in chosen or chosen[filename].id != model_id
        }
        if ignored:
            # A pick for a file the graph has no VAE or text-encoder slot for,
            # or of a model of another kind: the plan answers without it.
            logger.warning(
                "Ignored set-clone picks that fit no loader of their kind: %s",
                ignored,
            )
        swaps = {
            slot.filename: chosen[slot.filename].filename
            for _c, _w, slot in found
            if slot.filename in chosen and not _loads(slot, chosen[slot.filename])
        }
        return swaps, chosen

    def _wont_load(
        found, unswapped: list[dict], object_info: dict | None
    ) -> str | None:
        """Why a set's clone would be refused, in words, or None.

        The base loader's refusal names the loader that would read the file,
        as far as ComfyUI says one does.
        """
        if not unswapped:
            return None
        first = unswapped[0]
        base = next(
            (slot for _c, _w, slot in found if slot.kind in BASE_MODEL_KINDS), None
        )
        if base is not None and first["was"] == base.filename and object_info:
            for cls, widget in (
                ("CheckpointLoaderSimple", "ckpt_name"),
                ("UNETLoader", "unet_name"),
                ("UnetLoaderGGUF", "unet_name"),
            ):
                options = listed_options(object_info, cls, widget)
                if options and listed_as(first["now"], options):
                    return f"Needs a {cls}"
        name = os.path.basename(first["now"].replace("\\", "/"))
        if first["reason"] == "several_on_comfyui":
            return f"ComfyUI has two files named {name}"
        return f"ComfyUI does not list {name}"

    @router.post(
        "/workflows/{workflow_id}/set-clone-plans",
        summary="What cloning a workflow onto each workflow set would write",
        description=(
            "For each set asked (its shelf model ids), the files the clone "
            "would take from it, each model loader before and after (its node "
            "class too, when a file of another type needs another loader, and "
            "the node pack that loader comes from), whether the set's "
            "checkpoint has the workflow's base model so its LoRAs are kept, "
            "and, when the clone would be refused, why. Nothing is written."
        ),
        response_model=SetClonePlans,
        responses={
            404: {"description": "This machine has no such workflow."},
            409: {"description": "There is no graph to clone."},
        },
    )
    def plan_set_clones(request: Request, workflow_id: str, body: SetClonePlansRequest):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        _workflow, card = _require_base(hub, workflow_id)
        # Once per request, for every set: the dialog asks on open.
        object_info, error = _read_object_info(_comfyui_url(_user(request)))
        graph = _card_source(card, object_info=object_info).graph
        models = _swap_models(hub)
        index = recipe_asset_index(hub)
        found = _swap_slots(graph, models, index)
        base = next(
            (slot for _c, _w, slot in found if slot.kind in BASE_MODEL_KINDS), None
        )
        old_base = (
            _replaced_base_model(
                graph, models, index, normalized_filename(base.filename)
            )
            if base is not None
            else None
        )
        if object_info is None:
            logger.info(
                "Planning clones of workflow %s onto workflow sets without "
                "ComfyUI (names unchecked, node packs unknown): %s",
                workflow_id,
                error,
            )
        loader_nodes = [
            (node_id, class_type)
            for node_id, class_type in dict.fromkeys(
                (node_id, class_type)
                for node_id, class_type, widget, _value in iter_model_fields_api(graph)
                if slot_kind(widget) in (*BASE_MODEL_KINDS, "vae", "clip")
                and "CLIPVision" not in class_type
            )
        ]
        plans = []
        for ask in body.sets:
            # A model the dialog read that has since left the shelf: planning
            # the rest would present a partial set as this one, so the set is
            # refused on its own, never the whole read.
            gone = [i for i in (*ask.model_ids, *ask.checkpoint_ids) if i not in models]
            # Each once: a repeated id would be one file for two loaders.
            members = [models[i] for i in dict.fromkeys(ask.model_ids) if i in models]
            # Named by the caller, never guessed from the members: a set's
            # checkpoint slot takes a checkpoint or an unclassified diffusion
            # file, and an upscaler beside no checkpoint is no base model.
            set_checkpoints = [
                models[i]
                for i in ask.checkpoint_ids
                if i in models
                and models[i].file_kind in (FILE_CHECKPOINT, FILE_UNKNOWN)
            ]
            bases = _pair_bases(found, set_checkpoints)
            # The one the graph's first base slot takes, which `old_base` is
            # read from; else whichever slot was paired.
            checkpoint = next(
                (new for slot, new in bases if slot is base),
                bases[0][1] if bases else None,
            )
            swaps, filled = _set_swaps(found, bases, members, ask.picks)
            new_base = _base_key(checkpoint.base_model) if checkpoint else None
            keeps = old_base is not None and new_base == old_base
            pending = deepcopy(graph)
            loaders, _swapped, unswapped = _swap_files(pending, swaps, object_info)
            # The clone's own retype, so the diff shows the type it writes.
            retype_text_encoders(
                pending, _swapped_in_families(models, index, graph, swaps), object_info
            )
            rewritten = {str(row["node_id"]): row for row in loaders}
            # The workflow's own reason first: it applies to every set.
            reason = (
                "This workflow loads no checkpoint"
                if base is None
                else "A model of this set is no longer on the shelf"
                if gone
                else "Has no checkpoint"
                if checkpoint is None
                else _wont_load(found, unswapped, object_info)
            )
            if reason:
                fit = "wont_load"
            elif keeps:
                fit = "same_base_model"
            # The same source as `keeps`: the graph's base model as the shelf
            # or its LoRAs know it (`family_of` reads either spelling).
            elif family_of(checkpoint.base_model) and family_of(
                checkpoint.base_model
            ) == family_of(old_base):
                fit = "same_family"
            else:
                fit = "other"
            diffs = []
            for node_id, class_type in loader_nodes:
                fields = model_filename_fields(class_type)
                was_inputs = graph[node_id].get("inputs", {})
                now_node = pending[node_id]
                now_inputs = now_node.get("inputs", {})
                row = rewritten.get(str(node_id))
                diffs.append(
                    LoaderDiff(
                        node_id=str(node_id),
                        kind=next(
                            (
                                slot_kind(f)
                                for f in fields
                                if isinstance(was_inputs.get(f), str)
                            ),
                            "model",
                        ),
                        was_class=class_type,
                        now_class=now_node.get("class_type", class_type),
                        was=[
                            was_inputs[f]
                            for f in fields
                            if isinstance(was_inputs.get(f), str) and was_inputs[f]
                        ],
                        now=[
                            now_inputs[f]
                            for f in model_filename_fields(
                                now_node.get("class_type", class_type)
                            )
                            if isinstance(now_inputs.get(f), str) and now_inputs[f]
                        ],
                        pack=row["pack"] if row else None,
                        installed=row["installed"] if row else None,
                        was_type=_clip_type(graph[node_id]),
                        now_type=_clip_type(now_node),
                    )
                )
            essentials = [
                slot
                for _c, _w, slot in found
                if slot.kind in (*BASE_MODEL_KINDS, "vae", "clip")
            ]
            set_ids = {
                m.id
                for m in (
                    *set_checkpoints,
                    *(
                        m
                        for m in members
                        if m.file_kind in (FILE_VAE, FILE_TEXT_ENCODER)
                    ),
                )
            }
            took = [filled.get(slot.filename) for slot in essentials]
            # Onto another family the encoders must load as the new model:
            # a type the retype could not name would encode for the old one.
            wanted_type = CLIP_TYPE_BY_FAMILY.get(
                family_of(checkpoint.base_model) if checkpoint else ""
            )
            typed = fit != "other" or all(
                diff.now_type == wanted_type
                for diff in diffs
                if diff.now_type is not None
            )
            maps_cleanly = (
                fit != "wont_load"
                and typed
                and len(essentials) == len(set_ids)
                and all(took)
                and {m.id for m in took} == set_ids
            )
            plans.append(
                SetClonePlan(
                    key=ask.key,
                    fit=fit,
                    maps_cleanly=maps_cleanly,
                    takes={
                        slot.filename: filled[slot.filename].id
                        for _c, _w, slot in found
                        if slot.kind in ("vae", "clip") and slot.filename in filled
                    },
                    choices={
                        "vae": [m.id for m in members if m.file_kind == FILE_VAE],
                        "clip": [
                            m.id for m in members if m.file_kind == FILE_TEXT_ENCODER
                        ],
                    },
                    reason=reason,
                    base_model=checkpoint.base_model if checkpoint else None,
                    keeps_loras=keeps,
                    swaps=swaps,
                    unpaired_bases=[
                        slot.filename
                        for _c, _w, slot in found
                        if slot.kind in BASE_MODEL_KINDS
                        and all(slot is not paired for paired, _new in bases)
                    ],
                    loaders=diffs,
                )
            )
        return SetClonePlans(
            base_filename=base.filename if base else None,
            base_model=base.model.base_model if base and base.model else None,
            plans=plans,
        )

    @router.post(
        "/workflows/{workflow_id}/clone-with-models",
        summary="Clone a workflow onto other models",
        description=(
            "Write a copy of this workflow with some of its model files "
            "replaced, named as asked, beside the original. Every loader "
            "naming a replaced file is rewritten. When ComfyUI answers, each "
            "new name is written as ComfyUI lists it and a name it does not "
            "list is left out; when it does not, the names are written "
            "unchecked. The original file is not changed; the clone is a file "
            "of its own, in the same workflow."
        ),
        response_model=ClonedWorkflow,
        status_code=201,
        responses={
            404: {"description": "This machine has no such workflow."},
            409: {"description": "No graph to clone, or a swap could not be made."},
            413: {
                "description": "The graph is past the size a stored workflow may be."
            },
            500: {"description": "The copy could not be written."},
        },
    )
    def clone_with_models(request: Request, workflow_id: str, body: CloneWithModels):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow, card = _require_base(hub, workflow_id)
        # No 503 here: nothing depends on ComfyUI's link
        # types, so an unreachable ComfyUI means "write the names unchecked".
        # A chain to rewire is the exception, below.
        object_info, error = _read_object_info(_comfyui_url(_user(request)))
        source = _card_source(card, object_info=object_info)
        graph = deepcopy(source.graph)
        if object_info is None:
            logger.info(
                "Cloning workflow %s with its model names unchecked, ComfyUI did "
                "not answer: %s",
                workflow_id,
                error,
            )
        chain_changed = False
        if body.loras is not None:
            _require_object_info(
                (object_info, error),
                "what its nodes hand on, so it cannot rewire this clone's LoRAs",
            )
            # Before the loader rewrite: the chain is read against the
            # original's classes, which ComfyUI can type even when the new
            # loader's pack is not installed. A rewrite keeps every node id.
            plan = _chain_plan(
                hub, graph, object_info, body.loras.entries, body.loras.lanes
            )
            chain_changed = bool(plan["changes"])
            if chain_changed:
                try:
                    apply_lora_chain(graph, plan, object_info)
                except LookupError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
        # Read before the swap: the base slot is found by the file it names now.
        families = _swapped_in_families(
            _swap_models(hub), recipe_asset_index(hub), graph, body.swaps
        )
        loaders, swapped, unswapped = _swap_files(graph, body.swaps, object_info)
        if not swapped and not unswapped and not chain_changed:
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
        for row in retype_text_encoders(graph, families, object_info):
            logger.info(
                "Clone of workflow %s loads its text encoder on node %s as %r, "
                "not %r, for the checkpoint it now feeds",
                workflow_id,
                row["node_id"],
                row["now"],
                row["was"],
            )
        # A manual workflow of its own, whatever changed: it never joins the
        # original's automatic workflow, even where only filenames moved.
        name, landed = _store_copy(
            hub,
            download_stem(body.name) or "workflow",
            graph,
            source.bindings,
            "clone",
            workflow,
            card,
        )
        _announce(request, [landed], "imported")
        return ClonedWorkflow(
            name=name,
            workflow_id=landed,
            swapped=swapped,
            unswapped=unswapped,
            loaders=loaders,
            verified=all(entry["verified"] for entry in swapped),
        )

    def _swapped_in_families(
        models: dict[int, SwapModel], index: tuple, graph: dict, swaps: dict[str, str]
    ) -> dict[str, str]:
        """Each base loader *swaps* replaces a file of -> the new file's family.

        Matched as the rewrite matches (``swap_target``). A loader is left out
        when the shelf holds no single row of the new file's name, or that row
        has no known base model.
        """
        by_name = index[0]
        families: dict[str, str] = {}
        for node_id, class_type, widget, value in iter_model_fields_api(graph):
            if slot_kind(widget) not in BASE_MODEL_KINDS:
                continue
            new = swap_target(value, swaps)
            ids = by_name.get(normalized_filename(new), set()) if new else set()
            model = models.get(next(iter(ids))) if len(ids) == 1 else None
            family = family_of(model.base_model) if model else None
            if family:
                families[str(node_id)] = family
        return families

    def _swap_files(
        graph: dict, swaps: dict[str, str], object_info: dict | None
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """The clone's rewrite of *graph*, in place: loader classes, then files.

        The class goes first so each file is checked against the loader that
        will read it. A loader whose pack ComfyUI lacks lists nothing, so its
        file goes in unchecked: a missing pack warns and does not refuse.

        Returns:
            ``(loaders, swapped, unswapped)``: :func:`plan_loader_rewrites`'s
            rows and :func:`apply_filename_swap`'s two lists.
        """
        loaders = plan_loader_rewrites(graph, swaps, object_info)
        apply_loader_rewrites(graph, loaders)
        swapped, unswapped = apply_filename_swap(graph, swaps, object_info)
        return loaders, swapped, unswapped

    def _store_copy(
        hub,
        stem: str,
        graph: dict,
        bindings: list | None,
        origin: str,
        workflow,
        card,
    ) -> tuple[str, str]:
        """Store one graph as a new manual workflow made from *workflow*.

        Returns ``(its name, its id)``, or raises the 500 that says why not.
        *bindings* are the source's ``pixlstash_bindings``, which the resolved
        graph has lost (``Source.bindings``). They go back in, or a copy of a
        workflow that opted out of a picture input would opt back in on its
        first run. The copy remembers where it came from, by id and by the
        name the source had then.
        """
        if bindings is not None:
            graph = {**graph, BINDINGS_KEY: bindings}
        name = stem or "workflow"
        # The source's name as the grid shows it now, generated or typed.
        shown = _display_names(
            read_grid(hub, server.vault, manual_models=_manual_models).figures
        ).get(workflow.workflow_id)
        try:
            return name, store_manual_workflow(
                hub,
                name,
                graph,
                origin,
                from_workflow_id=workflow.workflow_id,
                from_name=shown or workflow.name or _display_name(card),
            )
        except WorkflowFileTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (NotAWorkflowError, RecursionError, sqlite3.Error) as exc:
            logger.error("A workflow copy named %r could not be stored: %s", stem, exc)
            raise HTTPException(
                status_code=500,
                detail="PixlStash could not store the workflow copy.",
            ) from exc

    def _store_over(
        hub,
        workflow,
        graph: dict,
        bindings: list | None,
        source: str,
        version: int | None,
        *,
        original: dict,
    ) -> None:
        """Store one graph over *workflow*'s own, or raise the 500 that says why not.

        :func:`_store_copy`'s twin for a gesture the owner asked to land on
        the workflow itself: its next version. *bindings* go back in for the
        same reason, on *original* too, which is the graph the edit was made
        on and becomes version 1 of an automatic workflow saved over for the
        first time. *version* is the version that graph was read at (``None``
        for an automatic workflow never saved over); another one since is a
        409, not an overwrite.
        """
        if bindings is not None:
            graph = {**graph, BINDINGS_KEY: bindings}
            original = {**original, BINDINGS_KEY: bindings}
        try:
            store_over_workflow(
                hub,
                workflow.workflow_id,
                graph,
                source,
                expected_version=version,
                original=original,
            )
        except WorkflowChanged as exc:
            logger.info("An overwrite was refused: %s.", exc)
            raise HTTPException(
                status_code=409,
                detail=(
                    "This workflow got a newer version while it was being "
                    "edited. Open Edit LoRAs again to edit that one."
                ),
            ) from exc
        except WorkflowFileTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (NotAWorkflowError, RecursionError, LookupError, sqlite3.Error) as exc:
            logger.error(
                "An edited graph could not be stored over workflow %s: %s",
                workflow.workflow_id,
                exc,
            )
            raise HTTPException(
                status_code=500,
                detail="PixlStash could not store the edited workflow.",
            ) from exc

    @router.post(
        "/recipes/{recipe_id}/extract-workflow",
        summary="Make a saved recipe a manual workflow of its own",
        description=(
            "Store the graph this saved recipe runs - its workflow's graph "
            "with the recipe applied, exactly as Run would build it, seeds "
            "and picture inputs left for a run to fill - as a new manual "
            "workflow named after the recipe, remembering the recipe and "
            "workflow it came from. ComfyUI is not asked. A recipe whose "
            "workflow is gone is built on the graph it was saved from."
        ),
        response_model=ExtractedWorkflow,
        status_code=201,
        responses={
            404: {"description": "No such saved recipe."},
            409: {"description": "The recipe has no graph left to build on."},
            413: {
                "description": "The graph is past the size a stored workflow may be."
            },
        },
    )
    def extract_workflow(request: Request, recipe_id: int):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        saved = saved_recipe_service.read_recipe(server.vault, recipe_id)
        if saved is None:
            raise HTTPException(status_code=404, detail="Unknown saved recipe.")
        # The run's own planner, so there is one way a recipe becomes a
        # graph. `allow_unchecked`: no ComfyUI is asked, and that is consent
        # to nothing here, since nothing is submitted.
        plan = _plan(
            request,
            RunRequest(saved_recipe_id=recipe_id, count=1, allow_unchecked=True),
            extract=True,
        )
        if not plan.submittable:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This recipe has no graph left to build a workflow on: "
                    + ", ".join(
                        reason["code"]
                        for group in plan.groups
                        for reason in group.reasons
                    )
                ),
            )
        graph = plan.submittable[0][0]
        name = (saved.name or "").strip() or "Extracted workflow"
        try:
            workflow_id = store_manual_workflow(
                hub,
                name,
                graph,
                "recipe",
                from_workflow_id=saved.workflow_id,
                from_name=saved.name or None,
            )
        except WorkflowFileTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (NotAWorkflowError, RecursionError, sqlite3.Error) as exc:
            logger.error("Saved recipe %s could not be extracted: %s", recipe_id, exc)
            raise HTTPException(
                status_code=500, detail="PixlStash could not store the workflow."
            ) from exc
        _announce(request, [workflow_id], "imported")
        return ExtractedWorkflow(workflow_id=workflow_id, name=name)

    @router.delete(
        "/workflows/{workflow_id}",
        summary="Delete a manual workflow",
        description=(
            "Delete one manual workflow: its document is written back to the "
            "watched workflows folder and sent to the system trash from there, "
            "so restoring it from the trash imports it again; a pull from "
            "ComfyUI does not bring it back. Its pictures stay, on the "
            "automatic workflow their graph is in, and its saved recipes stay, "
            "unfiled. An automatic workflow is not a record and cannot be "
            "deleted: hide it instead."
        ),
        response_model=WorkflowDeleted,
        responses={
            404: {"description": "This machine has no such workflow."},
            409: {"description": "An automatic workflow, which is hidden instead."},
            500: {"description": "The document could not be moved to the trash."},
        },
    )
    def delete_workflow(request: Request, workflow_id: str):
        server.auth.ensure_secure_when_required(request)
        hub = _hub()
        workflow = _require_workflow(hub, workflow_id)
        if not workflow.workflow_id.startswith(MANUAL_PREFIX):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This workflow is automatic — the library knows it from its "
                    "pictures. Hide it instead."
                ),
            )
        row = hub.fetchone(
            "SELECT document FROM workflow_document WHERE workflow_id = ?",
            (workflow_id,),
        )
        if row is None:
            # Deleted by another request since it was found.
            raise HTTPException(status_code=404, detail="Unknown workflow.")
        name = workflow.name or "workflow"
        owned = {
            origin: remote_path
            for origin, remote_path in hub.fetchall(
                "SELECT origin, remote_path FROM workflow_origin "
                "WHERE workflow_name = ? AND origin IN (?, ?) AND dismissed = 0",
                (workflow_id, INBOX_ORIGIN, FILE_ORIGIN),
            )
        }
        # Only what THIS workflow owns is swept from the inbox: an inbox file
        # of identical content can be another live workflow's.
        sweep = INBOX_ORIGIN in owned
        adopted = owned.get(FILE_ORIGIN)
        try:
            if adopted is not None and user_workflow_exists(adopted):
                # A user-folder file data step 7 made this workflow of: the
                # file itself goes to the trash (through the inbox, as a
                # delete always did), or it would still list and re-adopt.
                trash_user_workflow(hub, adopted, sweep=sweep)
                with workflow_inbox.INBOX_LOCK:
                    delete_manual_workflow(hub, workflow_id)
            else:
                with workflow_inbox.INBOX_LOCK:
                    # The trash copy first, the rows once it is there: a
                    # failed trash never loses the workflow. Under the lock a
                    # pull checks its dismissals under, so it cannot land
                    # between.
                    workflow_inbox.trash_workflow(
                        workflow_inbox.workflow_inbox_dir(),
                        f"{download_stem(name) or 'workflow'}.json",
                        json.loads(row["document"]),
                        sweep=sweep,
                    )
                    delete_manual_workflow(hub, workflow_id)
        except (
            OSError,
            ValueError,
            RecursionError,
            TrashPermissionError,
            sqlite3.Error,
        ) as exc:
            # sqlite3.Error: the trash copy may be made and the rows kept.
            logger.warning("Failed to delete workflow %s: %s", workflow_id, exc)
            raise HTTPException(
                status_code=500, detail="Failed to delete the workflow."
            ) from exc
        _announce(request, [workflow_id], "changed")
        return WorkflowDeleted(deleted=name, workflow_id=workflow_id)

    return router

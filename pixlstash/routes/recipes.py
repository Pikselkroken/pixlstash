"""Saved recipes: the looks the owner keeps, and what each one has made.

A **saved recipe** is the user's Recipe - prompt, LoRAs with strengths,
overrides - and not the hub's ``workflow_recipe`` row, which new code calls a
*variant*. The rows are vault rows because they are authored and must travel
with a snapshot (``db_models/saved_recipe.py``).

**A recipe belongs to one workflow** (#1623): ``GET /recipes?workflow_id=…``
answers with that workflow's recipes, and credit counts its pictures, resolved
through the hub (``variants_in_workflow``). The card a recipe was saved from
(``workflow_key``) is internal storage and never on the wire.

**Every route here is ``OWNER_ONLY``, and that is a decision.** A recipe holds
the owner's prompt and names the models they run; the credit beside it counts
pictures across the whole workflow, which is the whole-library disclosure class
§16 exists for. Declared in ``pixlstash/authz/registry.py``, never inline.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field, StringConstraints, field_validator

from pixlstash.hub.workflow_card_reads import (
    card_index,
    find_workflow,
    variants_in_workflow,
    workflow_index,
)
from pixlstash.hub.workflows import shelf_model_names
from pixlstash.routes.comfyui import _picture_workflow_key
from pixlstash.routes.workflows import RunModel
from pixlstash.services.workflow_library_service import read_variant_picture_counts
from pixlstash.services.workflow_export import download_name
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import normalized_filename
from pixlstash.services import saved_recipe_service
from pixlstash.services.workflow_events import announce_changed_workflows
from pixlstash.utils.workflow_ids import WORKFLOW_ID_PATTERN

logger = get_logger(__name__)

# Long enough for a prompt somebody actually wrote, short enough that the column
# is not a place to park a file. The frontend's Save dialog offers nothing near
# it; the ceiling is here so a hand-made request cannot.
MAX_PROMPT_LENGTH = 20000
MAX_NAME_LENGTH = 200
MAX_LORAS = 64
# Every free-form field a caller can write has a ceiling, the overrides map
# included: it is a parameter form's answers, and the largest real one is a few
# hundred bytes. Measured on the serialised form, because it is nesting rather
# than any one value that would make it big.
MAX_OVERRIDES_LENGTH = 20000
# ComfyUI draws seeds up to 2**64 - 1, which is 20 digits.
MAX_SEED_LENGTH = 64
# One tab's worth of recipes, and the same ceiling the verdict batch in
# ``routes/dedup.py`` uses. Unbounded, the whole list goes into one ``IN`` and a
# long enough one exhausts SQLite's host parameters — a database limit surfacing
# as a 500, which is the class the unknown-source-picture check above closes.
MAX_REORDER_IDS = 500
# How many workflows one selection may ask about at once. The Workflows grid
# selects with shift and ctrl, so this is a gesture's worth of workflows, not a
# library's; each costs a resolution and every one's variants go into one
# ``IN`` on the picture table.
MAX_UNION_KEYS = 100
# The models one recipe may pin: one per loader, and no real graph has more.
MAX_MODELS = 64
# ``auto:<core and families digest>`` or ``manual:<uuid hex>``. Declared on the ITEM:
# `max_length` on a `list[str]` bounds the list, not each id.
WorkflowId = Annotated[str, StringConstraints(pattern=WORKFLOW_ID_PATTERN)]


def _bounded_overrides(value: Optional[dict]) -> Optional[dict]:
    """Refuse an overrides map too large to be a form's answers."""
    if value is not None and len(json.dumps(value)) > MAX_OVERRIDES_LENGTH:
        raise ValueError(
            f"overrides is longer than {MAX_OVERRIDES_LENGTH} characters serialised."
        )
    return value


class RecipeLora(BaseModel):
    """One LoRA a recipe loads.

    ``sha256`` is what finds the file again after a rename and may be absent
    for one that has not been hashed yet; ``strength`` is the look, and is
    deliberately not part of credit because no picture row stores one.
    """

    filename: str
    sha256: Optional[str] = None
    strength: float = 1.0


class SavedRecipePayload(BaseModel):
    """What ``POST /recipes`` takes. Every field but the workflow has a default.

    ``models`` pins models by loader address over the workflow's default
    recipe, validated as a run's are (``RunModel``); absent pins nothing.
    """

    workflow_id: WorkflowId
    models: Optional[list[RunModel]] = Field(None, max_length=MAX_MODELS)
    name: str = Field("", max_length=MAX_NAME_LENGTH)
    prompt: str = Field("", max_length=MAX_PROMPT_LENGTH)
    negative: Optional[str] = Field(None, max_length=MAX_PROMPT_LENGTH)
    loras: list[RecipeLora] = Field(default_factory=list, max_length=MAX_LORAS)
    overrides: dict[str, Any] = Field(default_factory=dict)
    seed: Optional[str] = Field(None, max_length=MAX_SEED_LENGTH)
    keep_seed: bool = False
    source_picture_id: Optional[int] = None

    _check_overrides = field_validator("overrides")(_bounded_overrides)


class SavedRecipeEdit(BaseModel):
    """What ``PATCH /recipes/{id}`` takes: the fields actually sent, and no more.

    The workflow is absent on purpose - a recipe does not move between
    workflows - and so is ``position``, which is ``PUT /recipes/order``'s job.
    """

    name: Optional[str] = Field(None, max_length=MAX_NAME_LENGTH)
    prompt: Optional[str] = Field(None, max_length=MAX_PROMPT_LENGTH)
    negative: Optional[str] = Field(None, max_length=MAX_PROMPT_LENGTH)
    loras: Optional[list[RecipeLora]] = Field(None, max_length=MAX_LORAS)
    overrides: Optional[dict[str, Any]] = None
    seed: Optional[str] = Field(None, max_length=MAX_SEED_LENGTH)
    keep_seed: Optional[bool] = None
    source_picture_id: Optional[int] = None

    _check_overrides = field_validator("overrides")(_bounded_overrides)


class SavedRecipeOut(BaseModel):
    """One saved recipe as the API returns it."""

    id: int
    name: str
    position: int
    workflow_id: Optional[str] = Field(
        None,
        description=(
            "The workflow it runs on; null for a recipe the conversion has "
            "not reached yet."
        ),
    )
    models: Optional[list[dict]] = Field(
        None,
        description=(
            "`[{address, filename|sha256}]` pinned over the default recipe; "
            "null pins nothing."
        ),
    )
    prompt: str
    negative: Optional[str] = None
    loras: list[dict] = Field(default_factory=list)
    overrides: dict = Field(default_factory=dict)
    seed: Optional[str] = None
    keep_seed: bool = False
    source_picture_id: Optional[int] = None
    created_at: Optional[str] = None
    pictures: int = Field(
        0,
        description=(
            "Kept pictures of this recipe's workflow whose prompt and LoRA names "
            "are the recipe's. Computed on read; 0 on a write's own response."
        ),
    )
    extractable: Optional[bool] = Field(
        None,
        description=(
            "The unfiled listing only: whether Extract workflow has a graph to "
            "build on. False when the card it was saved from is gone too, as "
            "with a deleted manual workflow. Null in every other listing."
        ),
    )


class UsedLook(BaseModel):
    """One look this workflow's pictures were made with, saved or not.

    Not a saved recipe: it has no id, no name and no place in the tab's order,
    because nothing was authored. ``loras`` are file names with no strength -
    a picture row stores none - and ``cover_picture_id`` is the newest picture
    of the group, which is where the Save dialog reads the strengths back from.
    """

    prompt: str = ""
    loras: list[dict] = Field(default_factory=list)
    pictures: int = 0
    cover_picture_id: Optional[int] = None
    saved: bool = Field(
        False,
        description="A saved recipe of this workflow keeps this look.",
    )


class RecipeOrder(BaseModel):
    """The complete ordered list of the recipes one tab is showing."""

    recipe_ids: list[int] = Field(default_factory=list, max_length=MAX_REORDER_IDS)


class RecipeOrderResult(BaseModel):
    """The order as it was written, so a caller can confirm what landed."""

    recipe_ids: list[int]


class RecipeDeleted(BaseModel):
    """Which recipe went."""

    deleted: int


class RecipeExport(BaseModel):
    """``GET /recipes/{id}/export``: the recipe whole, and what it gives away.

    Nothing is withheld, which is the difference from
    ``GET /workflows/{workflow_id}/export``: a recipe IS the prompt and the LoRA names,
    and one with those taken out would make nothing. ``shares`` is what the
    dialog lists so the owner agrees to it knowing what it says.
    """

    filename: str
    recipe: dict
    shares: list[str] = Field(default_factory=list)


def _shares(recipe: dict, on_the_shelf: set[str]) -> list[str]:
    """Plainly what the exported file tells whoever opens it.

    The LoRA line names the files, because the file itself names them and an
    owner deciding whether to send it is owed the same list it is agreeing to.
    The last line is the guard implementation plan §5.7 asks for on **both**
    exports: a model this machine no longer holds is one whose name may have
    been forgotten on purpose, and a recipe carries it in plain text.
    """
    shares: list[str] = []
    if recipe.get("name"):
        shares.append(f"the name you gave it, {recipe['name']!r}")
    if recipe.get("prompt"):
        shares.append("the prompt you wrote")
    if recipe.get("negative"):
        shares.append("the negative prompt")
    names = [
        str(lora.get("filename"))
        for lora in recipe.get("loras") or []
        if isinstance(lora, dict) and lora.get("filename")
    ]
    if names:
        shares.append(f"{len(names)} LoRA file name(s): {', '.join(sorted(names))}")
    if recipe.get("overrides"):
        shares.append(f"{len(recipe['overrides'])} parameter setting(s)")
    if recipe.get("seed") is not None:
        shares.append("the seed it keeps")
    if recipe.get("created_at"):
        shares.append("when you saved it")
    # Judged against the shelf directly rather than through
    # `unvouched_model_values`: that one ends in an extension test, which is
    # right for a graph widget (where a value may be an enum token rather than
    # a filename) and wrong here, where the field IS a model name — a recipe
    # saved with "ada" rather than "ada.safetensors" would otherwise be
    # exported in plain text with nothing said about it.
    forgotten = sorted(
        {name for name in names if normalized_filename(name) not in on_the_shelf}
    )
    if forgotten:
        shares.append(
            "a model name this machine no longer holds, which you may have "
            f"asked PixlStash to forget: {', '.join(forgotten)}"
        )
    return shares


def create_router(server) -> APIRouter:
    """Create the saved-recipes router.

    Args:
        server: The Server instance, for ``vault`` (the recipes) and ``hub``
            (which variants a recipe's workflow covers).

    Returns:
        The configured router.
    """
    router = APIRouter(tags=["recipes"])

    def _announce(request: Request, workflow_id: Optional[str]) -> None:
        """Say the workflow's saved recipes changed, so an open tab re-reads them.

        The workflow's own `saved_recipe_count` moves with them, and the
        one-off clause reads it (a workflow carrying a saved recipe is never
        folded away), so the Workflows grid is stale until it looks again.
        """
        announce_changed_workflows(
            server,
            [workflow_id] if workflow_id else [],
            "recipes",
            origin_client_id=getattr(request.state, "origin_client_id", None),
        )

    def _variants(hub, workflow_ids: list[str]) -> list[str]:
        """Every variant the named workflows hold; an unknown id adds none."""
        found: set[str] = set()
        for workflow_id in workflow_ids:
            found.update(variants_in_workflow(hub, workflow_id))
        return sorted(found)

    def _hub():
        hub = getattr(server, "hub", None)
        if hub is None:
            # Without a hub there are no workflows, so there is nothing to
            # resolve and no credit to count. A configuration state, not a
            # fault, and the same answer the Workflows reads give.
            raise HTTPException(
                status_code=503,
                detail="No hub is attached, so this machine has no workflow library.",
            )
        return hub

    @router.get(
        "/recipes",
        summary="List saved recipes",
        description=(
            "The owner's saved recipes, each with how many kept pictures it "
            "accounts for. Given a workflow id, that workflow's recipes; "
            "given none, every recipe in the library. `unfiled` lists instead "
            "the recipes whose workflow is gone or not decided yet, with no "
            "credit: what the Workflows view offers to extract or delete, "
            "each saying whether it can be extracted (`extractable`)."
        ),
        response_model=list[SavedRecipeOut],
        responses={400: {"description": "`unfiled` together with a workflow id."}},
    )
    def list_recipes(
        request: Request,
        workflow_id: list[WorkflowId] = Query(
            default_factory=list,
            max_length=MAX_UNION_KEYS,
            description=(
                "Show the recipes of this workflow. Repeat it for a selection "
                "of several; the answer is the union."
            ),
        ),
        unfiled: bool = Query(
            False,
            description=(
                "Only the recipes on no workflow this machine holds: a NULL "
                "workflow, or one deleted or moved since. No credit."
            ),
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        workflow_ids = list(dict.fromkeys(workflow_id))
        if unfiled:
            if workflow_ids:
                raise HTTPException(
                    status_code=400,
                    detail="unfiled lists recipes on no workflow; name none.",
                )
            hub = _hub()
            known = {entry.workflow_id for entry in workflow_index(hub)}
            unfiled_recipes = [
                recipe
                for recipe in saved_recipe_service.read_recipes(server.vault)
                if recipe["workflow_id"] not in known
            ]
            if not unfiled_recipes:
                return []
            # Extraction builds on the card the recipe was saved from (the
            # run planner's fallback once the workflow is gone), so a recipe
            # whose card is gone as well - a deleted manual workflow's, whose
            # only document went with it - has nothing to extract (#1687).
            cards = {card.workflow_key for card in card_index(hub)}
            keys = saved_recipe_service.read_workflow_keys(server.vault)
            for recipe in unfiled_recipes:
                recipe["extractable"] = keys.get(recipe["id"]) in cards
            return unfiled_recipes
        if not workflow_ids:
            # Every recipe in the library, with no credit: crediting them would
            # mean resolving every workflow, a query per workflow for a number
            # this listing is not the place for. The tab, which is what shows
            # credit, always names its workflow.
            return saved_recipe_service.read_recipes(server.vault)

        hub = _hub()
        recipes = saved_recipe_service.read_recipes(server.vault, workflow_ids)
        if not recipes:
            return []
        groups = saved_recipe_service.read_credit_groups(
            server.vault, _variants(hub, workflow_ids)
        )
        credit = saved_recipe_service.credit_by_recipe(recipes, groups)
        for recipe in recipes:
            recipe["pictures"] = credit.get(recipe["id"], 0)
        return recipes

    @router.post(
        "/recipes",
        summary="Save a recipe",
        description=(
            "Keep a look: prompt, LoRAs, overrides and pinned models, on one workflow."
        ),
        response_model=SavedRecipeOut,
        status_code=201,
        responses={404: {"description": "Unknown workflow."}},
    )
    def create_recipe(request: Request, payload: SavedRecipePayload):
        server.auth.ensure_secure_when_required(request)
        workflow = find_workflow(
            _hub(),
            payload.workflow_id,
            read_variant_picture_counts(server.vault),
        )
        if workflow is None or workflow.base_card is None:
            raise HTTPException(status_code=404, detail="Unknown workflow.")
        fields = payload.model_dump()
        fields["loras"] = [lora.model_dump() for lora in payload.loras]
        fields["models"] = (
            None
            if payload.models is None
            else [model.model_dump(exclude_none=True) for model in payload.models]
        )
        # The NOT NULL card column, internal since #1623, and the graph the
        # recipe runs on: the source picture's own card when it is in this
        # workflow, so a look saved off the plain graph runs plain rather than
        # on the base graph's stages. Else the base card.
        source_card = (
            _picture_workflow_key(server, payload.source_picture_id)
            if payload.source_picture_id is not None
            else None
        )
        fields["workflow_key"] = (
            source_card if source_card in workflow.cards else workflow.base_card
        )
        try:
            recipe = saved_recipe_service.create_recipe(server.vault, fields)
        except saved_recipe_service.UnknownSourcePicture as exc:
            # A bad id in the body, not a fault: answered as a refusal rather
            # than left to the vault's foreign key, which would be a 500.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _announce(request, payload.workflow_id)
        return recipe

    # Declared before the ``{recipe_id}`` routes below. Nothing collides today —
    # there is no ``PUT /recipes/{recipe_id}`` — but FastAPI matches in
    # declaration order, so a literal path that sits behind a templated sibling
    # is a shadowing bug waiting for the next verb somebody adds.
    @router.put(
        "/recipes/order",
        summary="Reorder saved recipes",
        description=(
            "Set the order of the recipes listed, by a complete ordered id "
            "list. Refused whole if an id is unknown, so a half-applied order "
            "is never left behind."
        ),
        response_model=RecipeOrderResult,
        responses={404: {"description": "One of the recipes does not exist."}},
    )
    def reorder_recipes(request: Request, payload: RecipeOrder = Body(...)):
        server.auth.ensure_secure_when_required(request)
        recipe_ids = payload.recipe_ids
        if len(set(recipe_ids)) != len(recipe_ids):
            raise HTTPException(status_code=400, detail="recipe_ids must be unique")
        ordered = saved_recipe_service.reorder_recipes(server.vault, recipe_ids)
        if ordered is None:
            raise HTTPException(status_code=404, detail="No such recipe.")
        # No id: a reorder is one tab's list and the ids are recipes, not
        # workflows. The event still says "look again", which is all it promises.
        _announce(request, None)
        return {"recipe_ids": ordered}

    @router.get(
        "/recipes/used",
        summary="Looks this workflow's pictures were made with",
        description=(
            "Every distinct prompt-and-LoRAs combination the kept pictures of "
            "this workflow carry, with how many pictures each accounts for; a "
            "look a saved recipe already keeps says so in `saved`. A library "
            "that has never saved a recipe still has these, so the Recipes tab "
            "has something to show and something to save from. Name several "
            "workflows to get the union."
        ),
        response_model=list[UsedLook],
    )
    def list_used_looks(
        request: Request,
        workflow_id: list[WorkflowId] = Query(
            default_factory=list,
            max_length=MAX_UNION_KEYS,
            description=(
                "A workflow to read. Repeat it for a selection of several; the "
                "answer is the union, counted once per look."
            ),
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        workflow_ids = list(dict.fromkeys(workflow_id))
        if not workflow_ids:
            return []
        hub = _hub()
        recipes = saved_recipe_service.read_recipes(server.vault, workflow_ids)
        groups = saved_recipe_service.read_credit_groups(
            server.vault, _variants(hub, workflow_ids)
        )
        return saved_recipe_service.used_looks(groups, recipes)

    @router.patch(
        "/recipes/{recipe_id}",
        summary="Edit a saved recipe",
        description=(
            "Write the fields the request carries; the rest stand. A null name "
            "or prompt clears it to empty, which is what those columns hold."
        ),
        response_model=SavedRecipeOut,
        responses={404: {"description": "No such recipe."}},
    )
    def edit_recipe(request: Request, recipe_id: int, payload: SavedRecipeEdit):
        server.auth.ensure_secure_when_required(request)
        changes = payload.model_dump(exclude_unset=True)
        if "loras" in changes and payload.loras is not None:
            changes["loras"] = [lora.model_dump() for lora in payload.loras]
        try:
            recipe = saved_recipe_service.update_recipe(
                server.vault, recipe_id, changes
            )
        except saved_recipe_service.UnknownSourcePicture as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if recipe is None:
            raise HTTPException(status_code=404, detail="No such recipe.")
        _announce(request, recipe.get("workflow_id"))
        return recipe

    @router.get(
        "/recipes/{recipe_id}/export",
        summary="Export a saved recipe",
        description=(
            "This recipe as a file: the prompt, the LoRAs with their "
            "strengths, the settings and the seed, exactly as it was saved. "
            "It shares everything, and `shares` says so line by line — export "
            "the workflow instead to give away the graph and nothing else."
        ),
        response_model=RecipeExport,
        responses={404: {"description": "No such recipe."}},
    )
    def export_recipe(request: Request, recipe_id: int):
        server.auth.ensure_secure_when_required(request)
        row = saved_recipe_service.read_recipe(server.vault, recipe_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No such recipe.")
        recipe = saved_recipe_service.serialize(row)
        # The row's own id, its place in the tab and the picture it was saved
        # from are this library's bookkeeping and mean nothing anywhere else.
        for local in ("id", "position", "source_picture_id", "pictures"):
            recipe.pop(local, None)
        shares = _shares(recipe, shelf_model_names(_hub()))
        logger.info(
            "Saved recipe %s was exported; it shares %d thing(s).",
            recipe_id,
            len(shares),
        )
        return RecipeExport(
            filename=download_name(recipe.get("name")),
            recipe=recipe,
            shares=shares,
        )

    @router.delete(
        "/recipes/{recipe_id}",
        summary="Delete a saved recipe",
        description="Forget one recipe. The pictures it made are untouched.",
        response_model=RecipeDeleted,
        responses={404: {"description": "No such recipe."}},
    )
    def delete_recipe(request: Request, recipe_id: int):
        server.auth.ensure_secure_when_required(request)
        if not saved_recipe_service.delete_recipe(server.vault, recipe_id):
            raise HTTPException(status_code=404, detail="No such recipe.")
        # The row is gone, so the workflow it was on cannot be named. Reading it
        # first, only to put it in an event that says "look again" anyway,
        # would be a query bought for nothing.
        _announce(request, None)
        return {"deleted": recipe_id}

    return router

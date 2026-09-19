"""Saved recipes: the looks the owner keeps, and what each one has made.

A **saved recipe** is the user's Recipe - prompt, LoRAs with strengths,
overrides - and not the hub's ``workflow_recipe`` row, which new code calls a
*variant*. The rows are vault rows because they are authored and must travel
with a snapshot (``db_models/saved_recipe.py``).

**A recipe belongs to one workflow and runs on its whole stack** (decision
D10). ``GET /recipes?workflow_key=…`` therefore answers with every member's
recipes, resolved through the hub, and an Unstack leaves each recipe with the
workflow it was saved from because that is the only thing the row names.

**Every route here is ``OWNER_ONLY``, and that is a decision.** A recipe holds
the owner's prompt and names the models they run; the credit beside it counts
pictures across the whole stack, which is the whole-library disclosure class
§16 exists for. Declared in ``pixlstash/authz/registry.py``, never inline.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from pixlstash.hub.workflow_cards import effective_stack_keys, variant_hashes_for_keys
from pixlstash.pixl_logging import get_logger
from pixlstash.services import saved_recipe_service

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
    """What ``POST /recipes`` takes. Every field but the key has a default."""

    workflow_key: str = Field(min_length=1, max_length=200)
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

    ``workflow_key`` is absent on purpose - a recipe does not move between
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
    workflow_key: str
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
            "Kept pictures of this recipe's stack whose prompt and LoRA names "
            "are the recipe's. Computed on read; 0 on a write's own response."
        ),
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


def create_router(server) -> APIRouter:
    """Create the saved-recipes router.

    Args:
        server: The Server instance, for ``vault`` (the recipes) and ``hub``
            (which workflows a recipe's stack covers).

    Returns:
        The configured router.
    """
    router = APIRouter(tags=["recipes"])

    def _hub():
        hub = getattr(server, "hub", None)
        if hub is None:
            # Without a hub there are no workflow cards, so there is no stack to
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
            "accounts for. Given a workflow key, the recipes of every workflow "
            "in that one's stack; given none, every recipe in the library."
        ),
        response_model=list[SavedRecipeOut],
    )
    def list_recipes(
        request: Request,
        workflow_key: Optional[str] = Query(
            None,
            max_length=200,
            description="Show the recipes of this workflow's whole stack.",
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        if not workflow_key:
            # Every recipe in the library, with no credit: crediting them would
            # mean resolving a stack per workflow, which is a query per card for
            # a number this listing is not the place for. The tab, which is what
            # shows credit, always names its workflow.
            return saved_recipe_service.read_recipes(server.vault)

        hub = _hub()
        keys = effective_stack_keys(hub, workflow_key)
        recipes = saved_recipe_service.read_recipes(server.vault, keys)
        if not recipes:
            return []
        groups = saved_recipe_service.read_credit_groups(
            server.vault, variant_hashes_for_keys(hub, keys)
        )
        credit = saved_recipe_service.credit_by_recipe(recipes, groups)
        for recipe in recipes:
            recipe["pictures"] = credit.get(recipe["id"], 0)
        return recipes

    @router.post(
        "/recipes",
        summary="Save a recipe",
        description="Keep a look: prompt, LoRAs and overrides, on one workflow.",
        response_model=SavedRecipeOut,
        status_code=201,
    )
    def create_recipe(request: Request, payload: SavedRecipePayload):
        server.auth.ensure_secure_when_required(request)
        fields = payload.model_dump()
        fields["loras"] = [lora.model_dump() for lora in payload.loras]
        try:
            return saved_recipe_service.create_recipe(server.vault, fields)
        except saved_recipe_service.UnknownSourcePicture as exc:
            # A bad id in the body, not a fault: answered as a refusal rather
            # than left to the vault's foreign key, which would be a 500.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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
        return {"recipe_ids": ordered}

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
        return recipe

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
        return {"deleted": recipe_id}

    return router

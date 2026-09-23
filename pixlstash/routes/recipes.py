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
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field, StringConstraints, field_validator

from pixlstash.hub.workflow_cards import effective_stack_keys, variant_hashes_for_keys
from pixlstash.hub.workflows import shelf_model_names
from pixlstash.services.workflow_export import download_name
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import normalized_filename
from pixlstash.services import saved_recipe_service
from pixlstash.services.workflow_events import announce_changed_workflows

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
# selects with shift and ctrl, so this is a gesture's worth of cards, not a
# library's; each key costs a stack resolution and every stack's variants go
# into one ``IN`` on the picture table.
MAX_UNION_KEYS = 100
# A workflow key is a 64-character digest. Declared on the ITEM: `max_length`
# on a `list[str]` bounds the list, so a ceiling written there would leave
# every individual key unbounded - which is exactly what happened when this
# parameter stopped being a single string.
WorkflowKey = Annotated[str, StringConstraints(max_length=200)]


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


class UsedLook(BaseModel):
    """One look this stack's pictures were made with, saved or not.

    Not a saved recipe: it has no id, no name and no place in the tab's order,
    because nothing was authored. ``loras`` are file names with no strength -
    a picture row stores none - and ``cover_picture_id`` is the newest picture
    of the group, which is where the Save dialog reads the strengths back from.
    """

    prompt: str = ""
    loras: list[dict] = Field(default_factory=list)
    pictures: int = 0
    cover_picture_id: Optional[int] = None
    bookmarked: bool = Field(
        False,
        description="A saved recipe of this stack keeps this look.",
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
    ``GET /workflows/{key}/export``: a recipe IS the prompt and the LoRA names,
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
            (which workflows a recipe's stack covers).

    Returns:
        The configured router.
    """
    router = APIRouter(tags=["recipes"])

    def _announce(request: Request, workflow_key: Optional[str]) -> None:
        """Say the card's saved recipes changed, so an open tab re-reads them.

        The card's own `saved_recipe_count` moves with them, and the one-off
        clause reads it (a card carrying a saved recipe is never folded away),
        so the Workflows grid is stale until it looks again.
        """
        announce_changed_workflows(
            server,
            [workflow_key] if workflow_key else [],
            "recipes",
            origin_client_id=getattr(request.state, "origin_client_id", None),
        )

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
        workflow_key: list[WorkflowKey] = Query(
            default_factory=list,
            max_length=MAX_UNION_KEYS,
            description=(
                "Show the recipes of this workflow's whole stack. Repeat it "
                "for a selection of several; the answer is the union."
            ),
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        workflow_keys = [key for key in workflow_key if key]
        if not workflow_keys:
            # Every recipe in the library, with no credit: crediting them would
            # mean resolving a stack per workflow, which is a query per card for
            # a number this listing is not the place for. The tab, which is what
            # shows credit, always names its workflow.
            return saved_recipe_service.read_recipes(server.vault)

        hub = _hub()
        keys: list[str] = []
        for key in workflow_keys:
            for member in effective_stack_keys(hub, key):
                if member not in keys:
                    keys.append(member)
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
            recipe = saved_recipe_service.create_recipe(server.vault, fields)
        except saved_recipe_service.UnknownSourcePicture as exc:
            # A bad id in the body, not a fault: answered as a refusal rather
            # than left to the vault's foreign key, which would be a 500.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _announce(request, payload.workflow_key)
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
        # No key: a reorder is one tab's list and the ids are recipes, not
        # cards. The event still says "look again", which is all it promises.
        _announce(request, None)
        return {"recipe_ids": ordered}

    @router.get(
        "/recipes/used",
        summary="Looks this workflow's pictures were made with",
        description=(
            "Every distinct prompt-and-LoRAs combination the kept pictures of "
            "this workflow's stack carry, with how many pictures each accounts "
            "for; a look a saved recipe already keeps says so in `bookmarked`. A library "
            "that has never saved a recipe still has these, so the Recipes tab "
            "has something to show and something to save from. Name several "
            "workflows to get the union across all of their stacks."
        ),
        response_model=list[UsedLook],
    )
    def list_used_looks(
        request: Request,
        workflow_key: list[WorkflowKey] = Query(
            default_factory=list,
            max_length=MAX_UNION_KEYS,
            description=(
                "A workflow whose stack to read. Repeat it for a selection of "
                "several; the answer is the union, counted once per look."
            ),
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        keys = [key for key in workflow_key if key]
        if not keys:
            return []
        hub = _hub()
        # The union of every named workflow's stack. Deduplicated for the size
        # of the query and not for the answer: two members of one stack resolve
        # to the same keys, and both reads end in an ``IN``, which already
        # counts a row once however many times its key was listed. Keeping the
        # list short is what this is for.
        stack_keys: list[str] = []
        for key in keys:
            for member in effective_stack_keys(hub, key):
                if member not in stack_keys:
                    stack_keys.append(member)
        recipes = saved_recipe_service.read_recipes(server.vault, stack_keys)
        groups = saved_recipe_service.read_credit_groups(
            server.vault, variant_hashes_for_keys(hub, stack_keys)
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
        _announce(request, recipe.get("workflow_key"))
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
        # The row is gone, so the card it was on cannot be named. Reading it
        # first, only to put it in an event that says "look again" anyway,
        # would be a query bought for nothing.
        _announce(request, None)
        return {"deleted": recipe_id}

    return router

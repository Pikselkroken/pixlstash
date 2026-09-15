"""The Workflows view's reads: the library list, one row's variants, its graph.

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

**Nothing here mutates.** Naming a workflow and running one are later steps
(§F3, §F5), and forgetting ghosts is a privacy purge that lives with the
retention setting in ``routes/config.py``; this module is the view's read side
and it is deliberately the whole of it.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

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
from pixlstash.services.workflow_library_service import (
    read_library,
    read_recipe_activity,
    read_topology_picture_ids,
)

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

    return router

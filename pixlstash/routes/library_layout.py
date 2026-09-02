"""The library layout, the offered "Move to match", and the migration.

One rule holds the first two together: **a picture moves only when its folder
stops being true.** Choosing a layout reorganises nothing, because every path
already in the library is what the assignments were read from. Drift - a folder
that is still true but is not what the owner would pick today - is *offered*
here and never taken automatically. That is v1.11 Phase 4b, and the automatic
half has no route at all: it is ``LayoutMoveTask``, woken by the
assignment-change stamp in ``database.py``.

``/server-config/layout/migration`` (Phase 4c) is the deliberate exception and
is **not** that rule. Under the rule a flat path parses against nothing, can
never be false, and never moves; the migration is the owner asking for
something else - *make it all match, now* - so it is previewed, consented to,
and reversible in one undo. See ``services/layout_migration_service.py``.
"""

import re
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.event_types import EventType
from pixlstash.services.layout_migration_service import (
    MIGRATION_BATCH,
    new_batch_id,
    preview_migration,
    run_migration_pass,
)
from pixlstash.services.layout_move_service import (
    move_to_match,
    picture_exists,
    picture_layout,
)
from pixlstash.services.library_settings_service import get_layout, set_layout
from pixlstash.services.operation_log_service import request_context
from pixlstash.utils.library_layout import (
    DEFAULT_LAYOUT,
    folder_name,
    format_layout,
    parse_layout,
)

#: Most ids one "Move to match" request may carry - the same number as
#: ``ROTATE_MAX_IDS``, and for the same two reasons. Every id is a file
#: operation on the owner's own disk, and the whole request runs in one
#: transaction on the single DB writer thread, so a batch large enough to be
#: convenient is also large enough to stall every other request behind it. It is
#: also one undo unit, and an undo covering thousands of files is not something
#: a person can hold in their head. The background engine batches at the same
#: order (``layout_move_service.BATCH_SIZE``) and simply takes more passes.
MOVE_TO_MATCH_MAX_IDS = 200


class LayoutResponse(BaseModel):
    status: str = "success"
    layout: Optional[str] = Field(
        description="The library root's layout, or null when it has none."
    )
    layout_unfiled: str = Field(
        description="The folder a picture with nothing to file it by goes to."
    )
    default_layout: str = Field(
        description="What a new library starts on: `project/person,set`."
    )


class LayoutPatch(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"layout": "project/person,set"}}
    )

    layout: Optional[str] = Field(
        default=None,
        description=(
            "Segments separated by `/`, a segment's alternatives by `,`, first "
            "match wins, and a segment nothing fills is skipped rather than "
            "left as an empty folder. Facets: `project`, `person`, `set`, "
            "`tag`. `null` turns the layout off."
        ),
    )
    layout_unfiled: Optional[str] = Field(
        default=None,
        description=(
            "One safe path component for the unfiled folder; null means "
            "`Unassigned`. It is never the library root - the root is where an "
            "unmigrated flat library lives, and those files must never move."
        ),
    )


class PictureLayoutResponse(BaseModel):
    """What `GET /pictures/{id}/layout` answers.

    Declared rather than returned as a bare dict so the Scalar reference renders
    a body instead of `null` - `tests/test_openapi_response_schemas.py` guards
    that for every 2xx JSON response, and it is the reason this model exists.

    Every field but `status` is nullable, and all three go null together for a
    picture that is not in a root with a layout. That is a 200, not a 404: the
    picture exists and there is simply nothing to say about its folder.
    """

    status: str = "success"
    layout: Optional[str] = Field(
        default=None,
        description=(
            "The layout of the root this picture is in, as `project/person,set`, "
            "or null when it is not in a root that has one."
        ),
    )
    current_folder: Optional[str] = Field(
        default=None,
        description=(
            'The folder the picture is in, relative to its root. `""` is the '
            "root itself; null when the picture is not in a laid-out root."
        ),
    )
    suggested_folder: Optional[str] = Field(
        default=None,
        description=(
            "The **Move to match** offer, or null when there is nothing to "
            "offer - no layout, an off-layout folder of the owner's own, or a "
            "picture already where the layout would put it. An offer is never "
            "a correction: the folder it is in has not stopped being true."
        ),
    )


#: The shape a migration's batch id has to have. A migration mints its own
#: (``new_batch_id``) and the client echoes it on every following pass, so the
#: whole run is one undo unit.
#:
#: **This checks the shape, not the provenance** - it cannot tell an id this
#: server minted from a well-formed one a client composed, and it does not try
#: to. What it is for is what ``OriginClientMiddleware`` validates the
#: ``X-Operation-Batch-Id`` header for: ``batch_id`` decides what one undo
#: reverses, so it stays bounded, safe, and inside this feature's own
#: namespace rather than being free text that could join a migration's passes
#: to some other gesture's undo unit. A caller who composes one can only
#: regroup its own migrations, and reaching this route at all needs a local
#: owner.
_MIGRATION_BATCH_ID_RE = re.compile(r"^srv-layout-migration-[0-9a-f]{16}$")


class MigrationSample(BaseModel):
    """One before/after pair, both relative to the library root."""

    picture_id: int
    from_path: str = Field(alias="from")
    to_path: str = Field(alias="to")

    model_config = ConfigDict(populate_by_name=True)


class MigrationFolder(BaseModel):
    """One folder of the library as the chosen layout would draw it.

    A row is here when anything about it is non-zero, so a folder that only
    ever holds subfolders is absent rather than listed empty. The library root
    itself is never a row: it is the thing every `path` is relative to.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "path": "Harbour Nights/Nova",
                "name": "Nova",
                "depth": 1,
                "have": 0,
                "arriving": 34,
                "leaving": 0,
                "is_new": True,
            }
        }
    )

    path: str = Field(
        description=(
            "The folder, relative to the library root and `/`-joined - never "
            "absolute, because an absolute path says where the owner keeps "
            "their pictures. `Harbour Nights/Nova`."
        )
    )
    name: str = Field(
        description="The last component of `path`, to show: `Nova`.",
    )
    depth: int = Field(
        description=(
            "How far below the library root this sits: `0` for a top-level "
            "folder, `1` for one nested inside it. The root has no row of its "
            "own, so a depth of `0` is the first level the owner sees."
        )
    )
    have: int = Field(
        description="How many pictures sit in this folder today, before any move."
    )
    arriving: int = Field(
        description="How many pictures the migration would move **into** it."
    )
    leaving: int = Field(
        description=(
            "How many it would move **out** of it. A folder can be both: one "
            "picture arrives as another leaves."
        )
    )
    is_new: bool = Field(
        description=(
            "True when the folder is not on disk yet and the migration would "
            "create it. A folder left empty by the move is kept, never deleted, "
            "so the opposite never happens."
        )
    )


class MigrationPreviewResponse(BaseModel):
    status: str = "success"
    layout: Optional[str] = Field(
        default=None, description="The layout this would move the library onto."
    )
    picture_count: int = Field(description="How many pictures would move.")
    folder_count: int = Field(description="How many folders they would move into.")
    samples: list[MigrationSample] = Field(default_factory=list)
    collision_count: int = Field(
        description=(
            "How many of them render onto a path something else already has, "
            "and are therefore suffixed `-2`, `-3`... The file already sitting "
            "there is never renamed and never overwritten."
        )
    )
    collisions: list[MigrationSample] = Field(default_factory=list)
    cross_volume_count: int = Field(
        description=(
            "How many sit on a different filesystem from where the layout "
            "would put them - a mount point or a bind mount inside the "
            "library. Those **cannot be moved**: the destination is claimed "
            "with `os.link` and then `os.replace`, and both refuse to cross a "
            "device. They are refused in the plan rather than attempted, so "
            "they also appear in `skipped_counts` as "
            "`destination_other_volume`, and they stay exactly where they are."
        )
    )
    skipped_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Pictures the planner refuses, **counted by reason** — not the "
            "per-picture list the `POST` answers with, because a preview over "
            "a whole library would otherwise be a listing of it."
        ),
    )
    tree: list[MigrationFolder] = Field(
        default_factory=list,
        description=(
            "The library as this layout would draw it, one row per folder, so "
            "the shape can be seen before it is agreed to. **Every folder**, "
            "not a sample: a library filed by date has hundreds of folders "
            "leaving, and each one is a row the owner may need to check. "
            "**Ordered by path**, so a parent listed here comes immediately "
            "before its children and the tree can be drawn by indenting on "
            "`depth`. A parent can be missing from it: a folder that only ever "
            "holds subfolders has nothing to count and is not a row."
        ),
    )


class MigrationRunRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"after_id": 0, "batch_id": None, "sweep_unfiled": False}
        }
    )

    after_id: int = Field(
        default=0,
        ge=0,
        description=(
            "Resume cursor: only pictures with a higher id are examined. Send "
            "`0` to start, then the `next_after_id` of the previous pass."
        ),
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "The `batch_id` the first pass returned. Omit it on the first pass "
            "and echo it on every one after, so the whole migration is one "
            "undo. Compose one and it is refused: the value has to be in "
            "this feature's own `srv-layout-migration-` namespace, so a "
            "migration's passes can never join another gesture's undo unit."
        ),
    )
    sweep_unfiled: bool = Field(
        default=False,
        description=(
            "Also move the pictures the layout cannot place - nothing files "
            "them - into the unfiled folder (`layout_unfiled`). Off, they stay "
            "wherever they are. Send the same value on every pass of one "
            "migration, and the value the preview was read with."
        ),
    )


class MigrationRunResponse(BaseModel):
    status: str = "success"
    batch_id: str
    moved_count: int
    moved_picture_ids: list[int]
    examined: int
    next_after_id: int
    done: bool = Field(
        description="True when the last picture in the library has been examined."
    )
    skipped: list[dict] = Field(default_factory=list)
    operation_id: Optional[int] = None


class MoveToMatchRequest(BaseModel):
    picture_ids: list[int] = Field(description="The pictures to move.")


class MoveToMatchResponse(BaseModel):
    status: str = "success"
    moved_count: int
    moved_picture_ids: list[int]
    skipped: list[dict] = Field(default_factory=list)
    operation_id: Optional[int] = None


def _response(layout: Optional[str], unfiled: Optional[str]) -> LayoutResponse:
    return LayoutResponse(
        layout=layout,
        layout_unfiled=unfiled or DEFAULT_LAYOUT.unfiled,
        default_layout=format_layout(DEFAULT_LAYOUT),
    )


def create_router(server) -> APIRouter:
    """The library-level layout settings. Included as its own router."""
    router = APIRouter()

    @router.get(
        "/server-config/layout",
        summary="Get the library's folder layout",
        description=(
            "Returns how this library's own picture root is laid out. `null` "
            "means it has none, which is every existing library: without a "
            "layout PixlStash places nothing and moves nothing, whatever "
            "changes about the pictures.\n\n"
            "A reference folder carries its own layout, on "
            "`PATCH /reference-folders/{folder_id}`."
        ),
        response_model=LayoutResponse,
    )
    def read_layout(request: Request):
        return _response(*get_layout(server.vault.db))

    @router.patch(
        "/server-config/layout",
        summary="Set the library's folder layout",
        description=(
            "**Choosing a layout moves no files.** Every path already in the "
            "library is where its assignments came from, so every path is "
            "already true, and a path the layout cannot read can never become "
            "false. An existing flat library therefore needs no migration and "
            "keeps working exactly as it did.\n\n"
            "What the layout decides from here on is where a *new* picture is "
            "written, and where a picture goes when the folder it is in stops "
            "describing it - removing the project its folder is named after, "
            "or swapping one for another. Adding a second project or a second "
            "person moves nothing.\n\n"
            "A malformed layout is refused with 400 rather than stored: a "
            "layout that could not be read would silently behave as no layout "
            "at all."
        ),
        response_model=LayoutResponse,
        responses={400: {"description": "The layout is not readable."}},
    )
    def patch_layout(request: Request, body: LayoutPatch = Body(...)):
        # A PATCH, not a PUT. A field the caller did not send keeps its stored
        # value: sending only ``layout_unfiled`` must rename the unfiled folder,
        # not silently turn the layout off, and a client that reads-modifies-
        # writes the whole object still gets what it asked for either way.
        current_layout, current_unfiled = get_layout(server.vault.db)
        layout = (
            (body.layout or None)
            if "layout" in body.model_fields_set
            else current_layout
        )
        unfiled = (
            (body.layout_unfiled or None)
            if "layout_unfiled" in body.model_fields_set
            else current_unfiled
        )
        # Validated even when there is no layout to parse: ``parse_layout``
        # short-circuits on empty text and would never reach ``Layout``'s own
        # check, so an unfiled name that could escape the library root would be
        # stored and only refused later, on read, as if the owner had asked for
        # no layout at all.
        if unfiled is not None and unfiled != folder_name(unfiled):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"layout_unfiled must be a single safe path component, got "
                    f"{unfiled!r} (try {folder_name(unfiled)!r})"
                ),
            )
        try:
            parse_layout(layout, unfiled or DEFAULT_LAYOUT.unfiled)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_layout(server.vault.db, layout, unfiled)
        return _response(*get_layout(server.vault.db))

    @router.get(
        "/server-config/layout/migration",
        summary="What moving the whole library onto its layout would do",
        description=(
            "**Counts it, and moves nothing.** v1.11 Phase 4c: the one "
            "operation in this release that deliberately moves everything, and "
            "it is offered - when a layout is set or changed - never taken.\n\n"
            "This is *not* the move-when-false rule and must not be described "
            "as one. Under that rule a flat path parses against nothing, can "
            "never be false, and never moves, which is why an existing library "
            "needs no migration. This is the owner asking for something else: "
            "make it all match, now.\n\n"
            "Three things the count is for. `collision_count` is how many "
            "pictures render onto a path something already occupies and are "
            "therefore suffixed `-2`, `-3`... - the file already there is never "
            "renamed and never overwritten. `cross_volume_count` is how many "
            "sit across a mount point inside the library and therefore "
            "**cannot be moved at all**, because the destination claim refuses "
            "to cross a device. `skipped_counts` is every refusal by reason, "
            "that one included.\n\n"
            "`tree` is the same answer drawn rather than counted: one row per "
            "folder, with what it holds now, what would arrive, what would "
            "leave, and whether it exists yet. Path-ordered so it can be drawn "
            "by indenting on `depth`.\n\n"
            "A picture the layout cannot place, because nothing files it, is in "
            "none of those counts and does not move unless `sweep_unfiled` is "
            "true, which puts every one of them in the unfiled folder "
            "(`layout_unfiled`). Everything else lands exactly where the layout "
            "says, folders of the owner's own included: the automatic rule "
            "leaves those alone, the migration flattens them. Two files of one "
            "name meeting in one folder are suffixed, never overwritten."
        ),
        response_model=MigrationPreviewResponse,
    )
    def preview_layout_migration(
        request: Request,
        sweep_unfiled: bool = Query(
            default=False,
            description=(
                "Count the pictures nothing files as moving into the unfiled "
                "folder too. Read with the same value the `POST` will be sent."
            ),
        ),
    ):
        return MigrationPreviewResponse(
            **preview_migration(server.vault, sweep_unfiled=sweep_unfiled)
        )

    @router.post(
        "/server-config/layout/migration",
        summary="Move the library onto its layout, one pass",
        description=(
            f"Examines up to {MIGRATION_BATCH} pictures and moves the ones the "
            "layout would put somewhere else. Call it again with the "
            "`next_after_id` and the `batch_id` it returned until `done` is "
            "true; that is the progress bar, and it is also what makes the run "
            "resumable - a pass that fails leaves the tree half-moved and "
            "wholly consistent, and re-running finishes it, because a picture "
            "already where the layout wants it plans no move.\n\n"
            "**Every pass of one migration is one undo.** Each records its own "
            "`pictures.layout.move` operation, all stamped with the same "
            "`batch_id`, and a batch is a single undo unit - so one undo puts "
            "every file back at the path it had.\n\n"
            "A folder left empty by the move is kept, never deleted."
        ),
        response_model=MigrationRunResponse,
        responses={400: {"description": "batch_id is not one this route minted."}},
    )
    def run_layout_migration(request: Request, body: MigrationRunRequest = Body(...)):
        batch_id = body.batch_id
        if batch_id is not None and not _MIGRATION_BATCH_ID_RE.match(batch_id):
            # The id decides which operations one undo reverses, so it has to
            # stay inside this feature's namespace. A shape check, not a
            # provenance one - see _MIGRATION_BATCH_ID_RE.
            raise HTTPException(
                status_code=400,
                detail=(
                    "batch_id must be the value a previous pass returned, or "
                    "omitted to start a new migration"
                ),
            )
        if batch_id is None:
            batch_id = new_batch_id()
        context = request_context(request)
        # The gesture is this migration, not whatever the client was already
        # grouping: overriding the header is what keeps every pass in one undo.
        context["batch_id"] = batch_id
        result = run_migration_pass(
            server.vault,
            after_id=body.after_id,
            sweep_unfiled=body.sweep_unfiled,
            **context,
        )
        if result["moved_picture_ids"]:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": result["moved_picture_ids"],
                    "change_kind": "updated",
                    "fields": ["file_path", "pixels"],
                    "source": "ui",
                    "origin_client_id": getattr(
                        request.state, "origin_client_id", None
                    ),
                },
            )
        return MigrationRunResponse(
            batch_id=batch_id,
            moved_count=len(result["moved_picture_ids"]),
            moved_picture_ids=result["moved_picture_ids"],
            examined=result["examined"],
            next_after_id=result["next_after_id"],
            done=result["done"],
            skipped=result["skipped"],
            operation_id=result["operation_id"],
        )

    return router


def register_picture_routes(router: APIRouter, server) -> None:
    """Add the picture-scoped layout routes to the pictures router.

    They live on the **pictures** router rather than on this module's own, and
    that is a routing fact rather than a preference: ``_crud`` registers the
    ``/pictures/{id}/{field}`` field-allowlist catch-all, and anything matching
    that shape must be registered ahead of it or the catch-all answers first -
    with a 400 naming a field nobody asked for. ``_anomaly`` and
    ``_character_likeness`` are here for the same reason, and
    ``routes/pictures/__init__.py`` says so at the call site.
    """

    @router.get(
        "/pictures/{id}/layout",
        summary="Where this picture is, and where the layout would put it",
        description=(
            "`suggested_folder` is the **Move to match** offer and is null "
            "whenever there is nothing to offer: the root has no layout, the "
            "picture is not in a laid-out root, its folder is one of the "
            "owner's own (a permanent override the layout will not touch), or "
            "it is already where the layout would put it.\n\n"
            "An offer is never a correction. A picture filed under one project "
            "that has become mostly another's job is still filed truthfully; "
            "the tree is not wrong, it is only not always what the owner would "
            "have picked."
        ),
        response_model=PictureLayoutResponse,
    )
    def get_picture_layout(id: int, request: Request):
        entry = picture_layout(server.vault, id)
        if entry is None:
            # Either the picture is not in a laid-out root or it does not exist.
            # Told apart here so a missing picture is a 404 and a picture with
            # no layout is an honest "nothing to say".
            if not picture_exists(server.vault, id):
                raise HTTPException(status_code=404, detail="Picture not found")
            return PictureLayoutResponse()
        return PictureLayoutResponse(
            layout=entry["layout"],
            current_folder=entry["current_folder"],
            suggested_folder=entry["suggested_folder"],
        )

    @router.post(
        "/pictures/layout/move-to-match",
        summary="Move pictures to where the layout would put them",
        description=(
            "The owner taking the offer. Every picture whose folder is already "
            "what the layout would pick, or is one of the owner's own, is "
            "reported in `skipped` and left exactly where it is.\n\n"
            "Recorded as a single `pictures.layout.move` operation, so the "
            "whole request is **one** undo and one undo puts every file back. "
            "A folder left empty by the move is kept, never deleted."
        ),
        response_model=MoveToMatchResponse,
        responses={400: {"description": "picture_ids is empty or not integers."}},
    )
    def move_pictures_to_match(request: Request, body: MoveToMatchRequest = Body(...)):
        if not body.picture_ids:
            raise HTTPException(
                status_code=400, detail="picture_ids must be a non-empty list"
            )
        if len(body.picture_ids) > MOVE_TO_MATCH_MAX_IDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"picture_ids exceeds the maximum of {MOVE_TO_MATCH_MAX_IDS} "
                    "ids per request"
                ),
            )
        moved, skipped, operation_id = move_to_match(
            server.vault, body.picture_ids, **request_context(request)
        )
        if moved:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": moved,
                    "change_kind": "updated",
                    # A moved file changes the thumbnail URL, which is derived
                    # from the path and does not come back from the metadata
                    # endpoint - the marker a rotate raises, for the same reason.
                    "fields": ["file_path", "pixels"],
                    "source": "ui",
                    "origin_client_id": getattr(
                        request.state, "origin_client_id", None
                    ),
                },
            )
        return MoveToMatchResponse(
            moved_count=len(moved),
            moved_picture_ids=moved,
            skipped=[
                {"picture_id": picture_id, "reason": reason}
                for picture_id, reason in skipped
            ],
            operation_id=operation_id,
        )

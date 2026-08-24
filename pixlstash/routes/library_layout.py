"""The library layout, and the offered "Move to match" (v1.11 Phase 4b).

Three routes and one rule between them: **a picture moves only when its folder
stops being true.** Choosing a layout reorganises nothing, because every path
already in the library is what the assignments were read from. Drift — a folder
that is still true but is not what the owner would pick today — is *offered*
here and never taken automatically.

The automatic half has no route at all: it is ``LayoutMoveTask``, woken by the
assignment-change stamp in ``database.py``.
"""

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.event_types import EventType
from pixlstash.services.layout_move_service import (
    move_to_match,
    picture_exists,
    picture_layout,
)
from pixlstash.services.library_settings_service import get_layout, set_layout
from pixlstash.services.operation_log_service import request_context
from pixlstash.utils.library_layout import DEFAULT_LAYOUT, format_layout, parse_layout

#: Most ids one "Move to match" request may carry. The same order as the rotate
#: cap and for the same reason: every id is a file rename on the owner's disk,
#: and one request is one undo.
MOVE_TO_MATCH_MAX_IDS = 5000


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
            "`_Inbox`. It is never the library root — the root is where an "
            "unmigrated flat library lives, and those files must never move."
        ),
    )


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
            "describing it — removing the project its folder is named after, "
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
        try:
            parse_layout(body.layout, body.layout_unfiled or DEFAULT_LAYOUT.unfiled)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_layout(server.vault.db, body.layout or None, body.layout_unfiled or None)
        return _response(*get_layout(server.vault.db))

    return router


def register_picture_routes(router: APIRouter, server) -> None:
    """Add the picture-scoped layout routes to the pictures router.

    They live on the **pictures** router rather than on this module's own, and
    that is a routing fact rather than a preference: ``_crud`` registers the
    ``/pictures/{id}/{field}`` field-allowlist catch-all, and anything matching
    that shape must be registered ahead of it or the catch-all answers first —
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
    )
    def get_picture_layout(id: int, request: Request):
        entry = picture_layout(server.vault, id)
        if entry is None:
            # Either the picture is not in a laid-out root or it does not exist.
            # Told apart here so a missing picture is a 404 and a picture with
            # no layout is an honest "nothing to say".
            if not picture_exists(server.vault, id):
                raise HTTPException(status_code=404, detail="Picture not found")
            return {
                "status": "success",
                "layout": None,
                "current_folder": None,
                "suggested_folder": None,
            }
        return {"status": "success", **entry}

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
                    # endpoint — the marker a rotate raises, for the same reason.
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

"""HTTP routes for the moves-made-outside-PixlStash reconciliation queue.

v1.11 Phase 5 (``docs/plans/v1.11.0-existing-library.md`` §4). The queue itself
and its classification live in ``pixlstash.services.move_reconciliation_service``;
this module is the schema and the owner-only gate.
"""

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, Field

from pixlstash.event_types import EventType
from pixlstash.services import move_reconciliation_service, operation_log_service


class MoveFacetChangeModel(BaseModel):
    """One membership a reconciliation would add or remove."""

    facet: str = Field(description="'project', 'set' or 'person'.")
    name: str = Field(description="The entity's name.")


class PendingMoveModel(BaseModel):
    """One file move made outside PixlStash, awaiting reconciliation."""

    review_id: int
    picture_id: int
    old_path: str = Field(description="Where the file was.")
    new_path: str = Field(description="Where the scan found it.")
    removals: list[MoveFacetChangeModel] = Field(
        default_factory=list, description="What leaving old_path's folder implies."
    )
    additions: list[MoveFacetChangeModel] = Field(
        default_factory=list, description="What arriving at new_path's folder implies."
    )
    current: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Populated only in the ambiguous bucket: the picture's current "
            "names for each facet a removal is ambiguous about, e.g. "
            "{'project': ['2024 Shoots', 'Client · Nordvik']} - why leaving "
            "one folder does not say which."
        ),
    )


class PendingMovesResponse(BaseModel):
    """The reconciliation queue, in the three outcomes the release plan names."""

    unambiguous: list[PendingMoveModel] = Field(
        description="One membership swap each; safe to apply as a batch."
    )
    ambiguous: list[PendingMoveModel] = Field(
        description="The picture has more than one of the thing it left; resolved one at a time."
    )
    off_layout: list[PendingMoveModel] = Field(
        description="The new folder names nothing the layout knows; already followed, nothing to decide."
    )


class MoveReviewIdsRequest(BaseModel):
    review_ids: list[int] = Field(
        description="review_id values from GET /moves/pending."
    )


class ApplyMovesResponse(BaseModel):
    applied_picture_ids: list[int] = Field(
        description="Pictures whose assignments actually changed."
    )
    skipped_review_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Rows that had a change to make but it could not be applied - most "
            "commonly a set or person name that is no longer unique - and were "
            "cleared from the queue anyway, since they were explicitly acted on."
        ),
    )


class DismissMovesResponse(BaseModel):
    dismissed_review_ids: list[int] = Field(
        description="Rows cleared from the queue without changing anything."
    )


def create_router(server) -> APIRouter:
    """Return the moves-reconciliation router bound to *server*."""
    router = APIRouter()

    @router.get(
        "/moves/pending",
        summary="Moves made outside PixlStash, awaiting reconciliation",
        description=(
            "Every row is classified live against current assignments and the "
            "current layout - there is no cache to invalidate, and a row that "
            "no longer implies anything is quietly cleared on read rather than "
            "returned."
        ),
        response_model=PendingMovesResponse,
    )
    def get_pending_moves():
        return move_reconciliation_service.pending_moves(server.vault)

    @router.post(
        "/moves/apply",
        summary="Apply the given pending moves",
        description=(
            "Reconciliation is recomputed fresh at apply time, never trusted "
            "from an earlier GET. Pass every currently-unambiguous review_id to "
            "apply that whole bucket in one undoable batch, or a single "
            "ambiguous review_id to resolve it ('Only <project> now')."
        ),
        response_model=ApplyMovesResponse,
    )
    def apply_pending_moves(
        request: Request, payload: MoveReviewIdsRequest = Body(...)
    ):
        result = move_reconciliation_service.apply_reviews(
            server.vault,
            payload.review_ids,
            **operation_log_service.request_context(request),
        )
        applied_ids = result.get("applied_picture_ids") or []
        if applied_ids:
            server.vault.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": applied_ids,
                    "origin_client_id": getattr(
                        request.state, "origin_client_id", None
                    ),
                    "change_kind": "updated",
                },
            )
        return result

    @router.post(
        "/moves/dismiss",
        summary="Drop the given pending moves without changing anything",
        description=(
            "'Keep both' on one ambiguous row, or 'Leave everything as it was' "
            "on the whole strip. The files stay exactly where the owner put "
            "them either way - dismissing only clears the review queue."
        ),
        response_model=DismissMovesResponse,
    )
    def dismiss_pending_moves(payload: MoveReviewIdsRequest = Body(...)):
        return move_reconciliation_service.dismiss_reviews(
            server.vault, payload.review_ids
        )

    return router

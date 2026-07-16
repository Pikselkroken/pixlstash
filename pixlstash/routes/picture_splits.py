"""HTTP routes for the train/eval split leakage guard (Wave B).

See :mod:`pixlstash.services.picture_split_service` for the component-aware
assignment algorithm and the write-path/read-path conflict guards. This is a
vault-wide curation surface (like ``/tag_health`` and ``/reviews``): there is
no single resolvable ``picture_id`` scope for a bulk assignment/conflict-list
route, so per the design doc these routes follow the exact same owner-only
pattern already established by ``tag_health.py``'s ``_reject_scoped_tokens``
and ``reviews.py``'s repeated ``fetch_scope_allowed_picture_ids(...) is not
None`` checks, copied verbatim rather than inventing a new mechanism.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.pixl_logging import get_logger
from pixlstash.services import picture_split_service
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids

logger = get_logger(__name__)


class AssignSplitsResponse(BaseModel):
    """Result of a (re)assignment sweep."""

    model_config = ConfigDict(extra="allow")

    assigned: int
    conflicted: int


class ConflictRowResponse(BaseModel):
    """One conflicted ``picture_split`` row."""

    model_config = ConfigDict(extra="allow")

    picture_id: int
    split: str
    component_key: int
    assigned_at: Optional[str] = None
    conflict_detail: Optional[str] = None


class ConflictsResponse(BaseModel):
    """Paginated conflict queue (``SELECT * FROM picture_split WHERE conflict``)."""

    model_config = ConfigDict(extra="allow")

    total: int
    rows: list[ConflictRowResponse] = []


class ResolveConflictRequest(BaseModel):
    """Human resolution of a conflicted near-dup component."""

    split: str  # TRAIN | EVAL | NEITHER


class ResolveConflictResponse(BaseModel):
    """Every picture in the resolved component, and the split they now share."""

    model_config = ConfigDict(extra="allow")

    picture_ids: list[int]
    split: str


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _reject_scoped_tokens(request: Request) -> None:
        # Vault-wide split assignment/conflict data cannot be narrowed to a
        # share token's scope without leaking which pictures exist outside
        # it — owner/full tokens only. Same mechanism as tag_health.py and
        # reviews.py (see module docstring).
        if fetch_scope_allowed_picture_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")

    @router.post(
        "/picture_splits/assign",
        summary="(Re)assign train/eval splits",
        description=(
            "Component-aware TRAIN/EVAL assignment for pictures lacking a "
            "split (or every picture, on the first call). Corroborated "
            "near-duplicates are unioned into one component and always "
            "assigned together — see the module docs for the corroboration "
            "rule and the 80/20-within-picture-set stratification. Returns "
            "how many rows were newly assigned a definitive split vs. newly "
            "flagged as conflicting (fail-closed, never auto-resolved)."
        ),
        response_model=AssignSplitsResponse,
    )
    def assign_splits(request: Request):
        _reject_scoped_tokens(request)
        return picture_split_service.assign_splits(server.vault)

    @router.get(
        "/picture_splits/conflicts",
        summary="List conflicted split assignments",
        description=(
            "Paginated rows with ``conflict=true`` — pictures whose "
            "corroborated near-dup component currently disagrees on "
            "TRAIN/EVAL/NEITHER, pending human resolution. This IS the "
            "conflict queue; there is no separate table."
        ),
        response_model=ConflictsResponse,
    )
    def list_conflicts(request: Request, limit: int = 100, offset: int = 0):
        _reject_scoped_tokens(request)
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        return picture_split_service.list_conflicts(
            server.vault, limit=limit, offset=offset
        )

    @router.post(
        "/picture_splits/{picture_id}/resolve",
        summary="Resolve a conflicted split assignment",
        description=(
            "Human resolution: assigns the given picture's ENTIRE "
            "corroborated near-dup component (every picture sharing its "
            "``component_key``, not just a pair) to the given TRAIN/EVAL/"
            "NEITHER split and clears ``conflict``. Resolving only part of a "
            "component would immediately re-trigger the write-path guard, "
            "so the component is the unit of resolution."
        ),
        response_model=ResolveConflictResponse,
    )
    def resolve_conflict(
        picture_id: int, payload: ResolveConflictRequest, request: Request
    ):
        _reject_scoped_tokens(request)
        try:
            return picture_split_service.resolve_conflict(
                server.vault, picture_id, payload.split
            )
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"No split row for picture {picture_id}"
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router

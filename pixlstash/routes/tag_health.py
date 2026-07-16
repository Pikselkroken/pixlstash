"""HTTP routes for the tag health board (per-tag aggregate signal cache).

The board is the review workflow's landing view: a ranked table of tags with
cheap SQL signals (est. wrong/missing, verification coverage, boundary mass,
overturn rate, model-disputes-human, duplicate mismatches). Rows come from the
``tag_health`` cache table; ``POST /tag_health/rebuild`` recomputes it in the
background with tags-processed/total progress.

See :mod:`pixlstash.services.tag_health_service` for signal definitions.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_health_service
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids

logger = get_logger(__name__)


class TagHealthRowResponse(BaseModel):
    """Cached health signals for one tag."""

    model_config = ConfigDict(extra="allow")

    tag: str
    est_wrong: int = 0
    est_missing: int = 0
    mismatch: int = 0
    verified_pct: float = 0.0
    boundary_pct: float = 0.0
    overturn_rate: Optional[float] = None
    model_disputes: int = 0
    has_model: bool = False
    # Latest reviewed_at over the tag's suggestions; null = never reviewed.
    last_reviewed_at: Optional[str] = None
    computed_at: Optional[str] = None


class TagHealthResponse(BaseModel):
    """The board payload: cached rows + rebuild state."""

    model_config = ConfigDict(extra="allow")

    rows: list[TagHealthRowResponse] = []
    building: bool = False
    progress: float = 0.0
    computed_at: Optional[str] = None


class TagHealthRebuildResponse(BaseModel):
    """Rebuild kick-off acknowledgement."""

    model_config = ConfigDict(extra="allow")

    building: bool
    progress: float = 0.0


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _reject_scoped_tokens(request: Request) -> None:
        # Vault-wide aggregates cannot be narrowed to a share token's scope
        # without leaking counts about pictures outside it — owner/full only.
        if fetch_scope_allowed_picture_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")

    @router.get(
        "/tag_health",
        summary="Tag health board rows",
        description=(
            "Returns the cached per-tag health signals plus the rebuild state "
            "(``building`` and tags-processed progress in [0, 1]). "
            "``computed_at`` is null until the first rebuild."
        ),
        response_model=TagHealthResponse,
    )
    def get_tag_health(request: Request):
        _reject_scoped_tokens(request)
        return tag_health_service.list_tag_health(server.vault)

    @router.post(
        "/tag_health/rebuild",
        summary="Rebuild the tag health cache",
        description=(
            "Kicks a background recompute of every tag's signals on the shared "
            "task runner. Idempotent while a rebuild is running. Poll GET "
            "/tag_health for progress."
        ),
        response_model=TagHealthRebuildResponse,
    )
    def rebuild_tag_health(request: Request):
        _reject_scoped_tokens(request)
        return tag_health_service.start_rebuild(server.vault)

    return router

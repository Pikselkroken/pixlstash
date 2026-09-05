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
from pydantic import BaseModel, ConfigDict, Field

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
    est_wrong_adj: Optional[float] = Field(
        default=None,
        description=(
            "est_wrong discounted by the tag's measured precision from the latest "
            "ingested TaggerRun report (falls back to DEFAULT_TAG_PRECISION when no "
            "report covers the tag), so an unreliable tag doesn't dominate the "
            "board's 'estimated fixes' ranking. Null on a row that predates this "
            "field, until the next rebuild."
        ),
    )
    est_missing_adj: Optional[float] = Field(
        default=None,
        description="est_missing, same precision discount as est_wrong_adj.",
    )
    mismatch: int = 0
    verified_pct: float = 0.0
    boundary_pct: float = 0.0
    overturn_rate: Optional[float] = None
    model_disputes: int = 0
    has_model: bool = False
    ground_truth: Optional[int] = Field(
        default=None,
        description=(
            "How many pictures (not tag rows) carry this tag - the tag's confirmed "
            "examples. Counts DISTINCT non-deleted pictures holding any literal tag "
            "in this tag's DEFAULT_TAG_MERGES equivalence class, so a merge alias "
            "('extra digit') counts toward its parent ('malformed hand') and a "
            "picture holding both counts once. Restricted to the requested scope on "
            "a scoped response. Zero means a review of this tag has no ground truth "
            "to vote against: the scan falls back to model confidence alone, so "
            "ground_truth == 0 together with est_missing == 0 proves a review would "
            "yield no items. **Null** on a cached row that predates this field, "
            "until the next rebuild - null means 'not measured', which is NOT the "
            "same as 0 and must never be treated as proof of zero yield. A scoped "
            "response is always computed live and so is never null."
        ),
    )
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
    stale: bool = Field(
        default=False,
        description=(
            "True when a new picture, a new tagger run, or a reviewed tag "
            "suggestion has landed since the cache's computed_at - the rows "
            "are still the last-rebuilt values, but a rebuild is due. "
            "Top-level, not per-row: the cache is vault-wide and one rebuild "
            "covers every row. Always False for a scoped response (computed "
            "live, never cached)."
        ),
    )
    # True when the rows were computed live for a project/set/character scope
    # (never cached); the rebuild state fields then describe nothing.
    scoped: bool = False


class TagHealthRebuildResponse(BaseModel):
    """Rebuild kick-off acknowledgement."""

    model_config = ConfigDict(extra="allow")

    building: bool
    progress: float = 0.0


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _reject_scoped_tokens(request: Request) -> None:
        # Vault-wide aggregates cannot be narrowed to a share token's scope
        # without leaking counts about pictures outside it - owner/full only.
        if fetch_scope_allowed_picture_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")

    @router.get(
        "/tag_health",
        summary="Tag health board rows",
        description=(
            "Returns the cached per-tag health signals plus the rebuild state "
            "(``building`` and tags-processed progress in [0, 1]). "
            "``computed_at`` is null until the first rebuild. ``stale=true`` "
            "means new pictures, tagger runs, or reviewed tag suggestions "
            "have landed since ``computed_at`` - a background finder rebuilds "
            "automatically within a few minutes of that, or "
            "``POST /tag_health/rebuild`` forces it immediately. When any of "
            "``project_id`` / ``set_id`` / ``character_id`` is given, the rows "
            "are instead computed live for that scope (``scoped=true`` in the "
            "response) - the same project/set/character semantics as review "
            "creation, including ``character_id=UNASSIGNED``."
        ),
        response_model=TagHealthResponse,
    )
    def get_tag_health(
        request: Request,
        project_id: Optional[int] = None,
        set_id: Optional[int] = None,
        character_id: Optional[str] = None,
    ):
        _reject_scoped_tokens(request)
        if project_id is not None or set_id is not None or character_id:
            return tag_health_service.list_tag_health_scoped(
                server.vault,
                project_id=project_id,
                set_id=set_id,
                character_id=character_id,
            )
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

"""HTTP routes for frozen per-tag evaluation slices (Wave C).

See :mod:`pixlstash.services.tag_eval_slice_service` for the freeze mechanics
and the tiered AP/F1 metric procedure. This is a vault-wide curation surface
(like ``/tag_health``, ``/reviews``, ``/picture_splits``): there is no single
resolvable ``picture_id`` scope for a freeze/history/detail route, so per the
design doc these routes follow the same owner-only pattern already
established there (``fetch_scope_allowed_picture_ids(...) is not None`` ->
403), copied verbatim rather than inventing a new mechanism.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_eval_slice_service
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids

logger = get_logger(__name__)


class FreezeEvalSliceRequest(BaseModel):
    """Which tag to freeze."""

    tag: str = Field(description="The literal tag to freeze an eval slice for.")


class FreezeEvalSliceResponse(BaseModel):
    """Result of a freeze attempt — may not have created a slice."""

    model_config = ConfigDict(extra="allow")

    created: bool = Field(
        description=(
            "False when the candidate set's positive count didn't clear the "
            "MIN_EVAL_N_POS floor (see 'reason'); no ACTIVE slice was created "
            "in that case."
        )
    )
    slice_id: Optional[int] = Field(
        default=None, description="The new ACTIVE slice's id, when created."
    )
    tag: str
    n_pos: int = Field(
        description="Verified POS count in the (post-exclusion) candidate set."
    )
    n_total: int = Field(
        description="Total candidate count (POS + NEG) after exclusion."
    )
    excluded_conflict_ids: list[int] = Field(
        default=[],
        description=(
            "Candidate picture ids excluded because has_train_side_conflict "
            "flagged a corroborated near-dup on the TRAIN side."
        ),
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Null when created. Otherwise 'no_candidates' (no human-labeled "
            "EVAL-side predictions for this tag) or 'insufficient_positives' "
            "(n_pos below the floor)."
        ),
    )


class EvalSliceSummaryResponse(BaseModel):
    """One freeze event in a tag's history."""

    model_config = ConfigDict(extra="allow")

    id: int
    tag: str
    status: str = Field(description="ACTIVE | SUPERSEDED")
    created_at: Optional[str] = None
    n_total: int
    n_pos: int


class EvalSliceItemResponse(BaseModel):
    """One frozen (picture, ground-truth label) pair."""

    model_config = ConfigDict(extra="allow")

    picture_id: int
    label_state: str = Field(description="POS | NEG, snapshotted at freeze time.")
    frozen_at: Optional[str] = None


class EvalSlicePictureIdsResponse(BaseModel):
    """Paginated picture ids for a tag's current ACTIVE eval slice.

    The entire id-discovery surface a downstream consumer (e.g. pixltagger)
    needs (design doc §6): no label payload travels here -- feed these ids
    into the existing, unmodified ``POST /pictures/tags/bulk_fetch`` to get
    current human-corrected tags for them.
    """

    model_config = ConfigDict(extra="allow")

    tag: str
    eval_slice_id: int
    picture_ids: list[int] = []
    total: int = Field(description="Total items in the ACTIVE slice, unpaginated.")
    limit: int
    offset: int


class EvalSliceDetailResponse(BaseModel):
    """A slice's frozen items plus its computed metrics for one model version."""

    model_config = ConfigDict(extra="allow")

    id: int
    tag: str
    status: str
    created_at: Optional[str] = None
    model_version: Optional[str] = Field(
        description="The generation whose live predictions were joined for scoring."
    )
    items: list[EvalSliceItemResponse] = []
    eval_precision: Optional[float] = None
    eval_recall: Optional[float] = None
    eval_f1: Optional[float] = None
    eval_ap: Optional[float] = None
    eval_ap_ci_low: Optional[float] = None
    eval_ap_ci_high: Optional[float] = None
    eval_n: int = 0
    eval_n_pos: int = 0
    eval_metric_kind: str = Field(description="AP | F1 | insufficient_data | none")
    eval_threshold_source: str = Field(
        description=(
            "calibrated | carried_forward | rederived_disjoint_val | "
            "uncalibrated_fallback | none"
        )
    )


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _reject_scoped_tokens(request: Request) -> None:
        # Vault-wide eval-slice data cannot be narrowed to a share token's
        # scope without leaking membership of a curated set outside it —
        # owner/full tokens only. Same mechanism as tag_health.py,
        # reviews.py, and picture_splits.py (see module docstring).
        if fetch_scope_allowed_picture_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")

    @router.post(
        "/tag_eval_slices",
        summary="Freeze a tag's current EVAL-side verified labels",
        description=(
            "Snapshots every picture with a human-labeled TagPrediction for "
            "this tag whose train/eval split is EVAL into a new ACTIVE "
            "TagEvalSlice, superseding the tag's prior ACTIVE slice (if any). "
            "Candidates flagged by the Wave B near-dup leakage guard "
            "(has_train_side_conflict) are excluded first. If the surviving "
            "positive count is below the MIN_EVAL_N_POS floor, no slice is "
            "created — 'created' is false and 'reason' explains why."
        ),
        response_model=FreezeEvalSliceResponse,
    )
    def freeze_eval_slice(payload: FreezeEvalSliceRequest, request: Request):
        _reject_scoped_tokens(request)
        return tag_eval_slice_service.freeze_eval_slice(server.vault, payload.tag)

    @router.get(
        "/tag_eval_slices",
        summary="Freeze history for a tag",
        description="Every freeze event for the given tag, most recent first.",
        response_model=list[EvalSliceSummaryResponse],
    )
    def list_eval_slices(request: Request, tag: str):
        _reject_scoped_tokens(request)
        return tag_eval_slice_service.list_eval_slices(server.vault, tag)

    @router.get(
        "/tag_eval_slices/{id}",
        summary="A slice's frozen items + computed metrics",
        description=(
            "Returns the slice's frozen (picture, label) items plus metrics "
            "computed by joining live TagPrediction.confidence for the given "
            "model_version (default: the vault's current tagger version) "
            "against the frozen label_state — see "
            "pixlstash.services.tag_eval_slice_service for the tiered "
            "AP/F1 procedure. allow_uncalibrated_f1 opts into the tier-5 "
            "fixed-0.5-threshold F1 fallback when no calibrated or "
            "rederived threshold is available; default is off, in which "
            "case an uncalibrated tag reports Average Precision instead."
        ),
        response_model=EvalSliceDetailResponse,
    )
    def get_eval_slice(
        id: int,
        request: Request,
        model_version: Optional[str] = None,
        allow_uncalibrated_f1: bool = False,
    ):
        _reject_scoped_tokens(request)
        result = tag_eval_slice_service.get_eval_slice(
            server.vault,
            id,
            model_version=model_version,
            allow_uncalibrated_f1=allow_uncalibrated_f1,
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"No eval slice with id {id}")
        return {
            **result,
            "created_at": result["created_at"].isoformat()
            if result["created_at"]
            else None,
            "items": [
                {
                    **item,
                    "frozen_at": item["frozen_at"].isoformat()
                    if item["frozen_at"]
                    else None,
                }
                for item in result["items"]
            ],
        }

    @router.get(
        "/tag_eval_slices/{tag}/picture_ids",
        summary="Picture ids for a tag's ACTIVE eval slice",
        description=(
            "Returns just the ACTIVE slice's picture_id list, paginated -- "
            "no label payload, no new artifact shape. This is the entire "
            "id-discovery mechanism a downstream consumer (e.g. pixltagger) "
            "needs: feed the returned ids into the existing, unmodified "
            "POST /pictures/tags/bulk_fetch to get current human-corrected "
            "tags for them. 404 when no ACTIVE slice exists for the tag, "
            "matching GET /tag_eval_slices/{id}'s convention for an "
            "unresolvable slice rather than a silently empty list."
        ),
        response_model=EvalSlicePictureIdsResponse,
        responses={404: {"description": "No ACTIVE eval slice for this tag."}},
    )
    def get_active_slice_picture_ids(
        tag: str, request: Request, limit: int = 500, offset: int = 0
    ):
        _reject_scoped_tokens(request)
        limit = max(1, min(limit, 2000))
        offset = max(0, offset)
        result = tag_eval_slice_service.list_active_slice_picture_ids(
            server.vault, tag, limit=limit, offset=offset
        )
        if result is None:
            raise HTTPException(
                status_code=404, detail=f"No ACTIVE eval slice for tag {tag!r}"
            )
        return result

    return router

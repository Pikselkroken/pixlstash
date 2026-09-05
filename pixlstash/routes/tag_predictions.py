from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service, tag_prediction_service
from pixlstash.utils.service.anomaly_thresholds import (
    load_label_thresholds,
    load_raw_label_thresholds,
)
from pixlstash.utils.service.caption_utils import sync_picture_sidecar

logger = get_logger(__name__)


class ResetTagsRequest(BaseModel):
    """Request body for reset_tags and reset_description endpoints."""

    model: str | None = None


class BulkResetRequest(BaseModel):
    """Request body for the multi-picture reset_tags / reset_description endpoints."""

    picture_ids: list[int]
    model: str | None = None


class BulkResetResponse(BaseModel):
    """How many pictures a bulk reset queued."""

    status: str
    count: int


class TagPredictionItemResponse(BaseModel):
    """A single stored tag prediction."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    tag: str
    confidence: Optional[float] = None
    model_version: Optional[str] = None
    status: Optional[str] = None
    predicted_at: Optional[str] = None


class TagPredictionsMetaResponse(BaseModel):
    """Tagger metadata returned alongside predictions when requested."""

    model_config = ConfigDict(extra="allow")

    acceptance_threshold: Optional[float] = None
    label_thresholds: dict[str, float] = {}


class TagPredictionsResponse(BaseModel):
    """Predictions plus tagger metadata (returned when include_meta=True)."""

    model_config = ConfigDict(extra="allow")

    tag_predictions: list[TagPredictionItemResponse] = []
    meta: TagPredictionsMetaResponse


class ConfirmTagPredictionResponse(BaseModel):
    """Result of confirming a tag prediction."""

    model_config = ConfigDict(extra="allow")

    status: str
    tag: str


class RejectTagPredictionResponse(BaseModel):
    """Result of rejecting a tag prediction."""

    model_config = ConfigDict(extra="allow")

    status: str
    tag: str


class DeleteTagPredictionsResponse(BaseModel):
    """Result of deleting a picture's tag predictions."""

    model_config = ConfigDict(extra="allow")

    status: str
    count: int


class ResetStatusResponse(BaseModel):
    """Result of resetting a picture's tags or description."""

    model_config = ConfigDict(extra="allow")

    status: str


class LabelThresholdResponse(BaseModel):
    """Base and effective threshold for a single tagger label."""

    model_config = ConfigDict(extra="allow")

    label: str
    base_threshold: float
    effective_threshold: float


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/pictures/{id}/tag_predictions",
        summary="Get tag predictions for a picture",
        description=(
            "Returns all stored tag predictions for the given picture, ordered by "
            "confidence descending.  Use the ``status`` query param to filter by "
            "``PENDING``, ``CONFIRMED``, or ``REJECTED``."
        ),
        response_model=list[TagPredictionItemResponse] | TagPredictionsResponse,
    )
    def get_tag_predictions(
        request: Request,
        id: int,
        status: str | None = None,
        include_meta: bool = False,
    ):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        predictions = tag_prediction_service.get_predictions(
            server.vault, pic_id, status
        )
        payload = [
            {
                "id": p.id,
                "tag": p.tag,
                "confidence": p.confidence,
                "model_version": p.model_version,
                "status": p.status,
                "predicted_at": p.predicted_at.isoformat() if p.predicted_at else None,
            }
            for p in predictions
        ]
        if not include_meta:
            return payload
        meta_path = server.vault.get_pixlstash_tagger_meta_path()
        offset = server.vault.get_pixlstash_tagger_threshold_offset()
        return {
            "tag_predictions": payload,
            "meta": {
                "acceptance_threshold": server.vault.get_pixlstash_acceptance_threshold(),
                "label_thresholds": load_label_thresholds(meta_path, offset),
            },
        }

    @router.post(
        "/pictures/{id}/tag_predictions/{tag}/confirm",
        summary="Confirm a tag prediction",
        description=(
            "Marks the prediction as CONFIRMED and ensures a corresponding row "
            "exists in the Tag table.  Emits a CHANGED_PICTURES event and records "
            "an undoable ``pictures.tags.confirm`` operation, so undo reverses "
            "both the Tag row and the human-label ledger entry.  423 when a "
            "locked picture set freezes the picture (nothing is recorded)."
        ),
        response_model=ConfirmTagPredictionResponse,
    )
    def confirm_tag_prediction(id: int, tag: str, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def _confirm(session):
            tag_prediction_service.confirm_tag_prediction_in_session(
                session,
                pic_id,
                tag,
                server.vault.interactive_rescore_registry,
                origin_client_id,
                commit=False,
            )

        # Recorded so Ctrl+Z can revert it (§21.2). The op captures BOTH the Tag
        # row this creates and the prediction row's ledger, because undoing one
        # without the other would put the tag back while the tagger still read it
        # as adjudicated. Recorded regardless of which principal called: a scoped
        # token may reach this route, but /operations* is OWNER_ONLY, so only the
        # owner can ever see or undo the row (precedent N2, v1.9 authz sign-off).
        try:
            _result, _operation = operation_log_service.run_recorded_metadata_task(
                server.vault,
                _confirm,
                op_type=operation_log_service.OP_TAGS_CONFIRM,
                picture_ids=[pic_id],
                summary=f"Confirmed tag '{tag}'",
                **operation_log_service.request_context(request),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Prediction not found")
        server.handle_vault_event(
            EventType.CHANGED_PICTURES,
            {"picture_ids": [pic_id], "origin_client_id": origin_client_id},
        )
        sync_picture_sidecar(server, pic_id)
        return {"status": "confirmed", "tag": tag}

    @router.post(
        "/pictures/{id}/tag_predictions/{tag}/reject",
        summary="Reject a tag prediction",
        description=(
            "Marks the prediction as REJECTED, writing a durable human NEG label "
            "(a synthetic ``manual`` prediction row is created when the tag was "
            "hand-added and the tagger never predicted it).  Does not modify the "
            "Tag table.  Records an undoable ``pictures.tags.reject`` operation "
            "summarised as *Removed tag 'x'* - that is what the user did; the "
            "ledger is the mechanism.  423 when a locked picture set freezes the "
            "picture (nothing is recorded)."
        ),
        response_model=RejectTagPredictionResponse,
    )
    def reject_tag_prediction(id: int, tag: str, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def _reject(session):
            tag_prediction_service.reject_tag_prediction_in_session(
                session,
                pic_id,
                tag,
                server.vault.interactive_rescore_registry,
                origin_client_id,
                commit=False,
            )

        # Recorded so Ctrl+Z can revert it (§21.2). "Removed tag" is what the user
        # did; the NEG ledger row is the mechanism, and it is captured with the
        # tags facet so an undo reverses both - a restored tag the tagger still
        # treats as rejected is a half-undo, which is worse than none.
        operation_log_service.run_recorded_metadata_task(
            server.vault,
            _reject,
            op_type=operation_log_service.OP_TAGS_REJECT,
            picture_ids=[pic_id],
            summary=f"Removed tag '{tag}'",
            **operation_log_service.request_context(request),
        )
        server.handle_vault_event(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return {"status": "rejected", "tag": tag}

    @router.post(
        "/pictures/{id}/tag_predictions/delete",
        summary="Delete tag predictions for a picture",
        description=(
            "Deletes all TagPrediction rows for the picture except those with "
            "model_version='manual' (user-rejected tags), so the background tagger "
            "treats it as never seen and rebuilds predictions from scratch."
        ),
        response_model=DeleteTagPredictionsResponse,
    )
    def delete_tag_predictions(id: int, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        count = tag_prediction_service.delete_tag_predictions(
            server.vault, pic_id, origin_client_id=origin_client_id
        )
        server.handle_vault_event(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return {"status": "deleted", "count": count}

    @router.post(
        "/pictures/{id}/reset_tags",
        summary="Reset tags and predictions for a picture",
        description=(
            "Atomically deletes all non-manual TagPrediction rows and all Tag rows "
            "for the picture, then restores the pending-retag sentinel.  This is the "
            "single-round-trip equivalent of calling tag_predictions/delete followed "
            "by DELETE tags - it avoids the intermediate state where predictions are "
            "gone but tags still exist, which otherwise tricks the background "
            "MissingTagFinder into running a wasted inference pass."
        ),
        response_model=ResetStatusResponse,
    )
    def reset_picture_tags(
        id: int, request: Request, payload: ResetTagsRequest | None = None
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        model = payload.model if payload else None
        tag_prediction_service.reset_picture_tags(
            server.vault, pic_id, engine_name=model, origin_client_id=origin_client_id
        )
        server.vault.notify(
            EventType.CHANGED_TAGS,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        server.vault.retag_picture_interactive(pic_id, engine_name=model)
        return {"status": "reset"}

    @router.post(
        "/pictures/{id}/reset_description",
        summary="Reset description for a picture",
        description=(
            "Clears the picture's description field and queues a new description "
            "inference pass.  Pass a 'model' field in the request body to override "
            "which description plugin to use for this specific picture."
        ),
        response_model=ResetStatusResponse,
    )
    def reset_picture_description(
        id: int, request: Request, payload: ResetTagsRequest | None = None
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        model = payload.model if payload else None
        found = server.vault.reset_description_interactive(
            pic_id, engine_name=model, origin_client_id=origin_client_id
        )
        if not found:
            raise HTTPException(status_code=404, detail="Picture not found")
        return {"status": "reset"}

    @router.post(
        "/pictures/reset_tags",
        summary="Reset tags for many pictures",
        description=(
            "The multi-picture form of /pictures/{id}/reset_tags: one transaction "
            "drops every listed picture's non-manual predictions and tags and "
            "restores the pending-retag sentinel. The background tagger then "
            "processes them in batches; no per-picture task is queued."
        ),
        response_model=BulkResetResponse,
    )
    def reset_pictures_tags(request: Request, payload: BulkResetRequest):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        ids = sorted({int(pid) for pid in payload.picture_ids if int(pid) > 0})
        if not ids:
            return {"status": "reset", "count": 0}
        reset_ids = tag_prediction_service.reset_pictures_tags(
            server.vault,
            ids,
            engine_name=payload.model,
            origin_client_id=origin_client_id,
        )
        if reset_ids:
            server.vault.notify(
                EventType.CHANGED_TAGS,
                {
                    "picture_ids": reset_ids,
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
        return {"status": "reset", "count": len(reset_ids)}

    @router.post(
        "/pictures/reset_description",
        summary="Reset descriptions for many pictures",
        description=(
            "The multi-picture form of /pictures/{id}/reset_description: one "
            "transaction marks every listed picture for re-captioning, and the "
            "background captioner processes them in batches. Pass 'model' to "
            "pick the description plugin."
        ),
        response_model=BulkResetResponse,
    )
    def reset_pictures_descriptions(request: Request, payload: BulkResetRequest):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        ids = [int(pid) for pid in payload.picture_ids if int(pid) > 0]
        count = server.vault.reset_descriptions(
            ids, engine_name=payload.model, origin_client_id=origin_client_id
        )
        return {"status": "reset", "count": count}

    @router.get(
        "/tagger/label-thresholds",
        summary="Get per-label thresholds for the PixlStash tagger",
        description=(
            "Returns each label's base threshold and the effective threshold after "
            "applying an offset. When the ``offset`` query parameter is omitted the "
            "saved user offset is used; pass one to preview an unsaved value. "
            "Results are sorted alphabetically."
        ),
        response_model=list[LabelThresholdResponse],
    )
    def get_label_thresholds(
        offset: Optional[float] = Query(None, ge=-0.5, le=0.5),
    ):
        if offset is None:
            offset = server.vault.get_pixlstash_tagger_threshold_offset()
        meta_path = server.vault.get_pixlstash_tagger_meta_path()
        raw = load_raw_label_thresholds(meta_path)
        sorted_labels = sorted(raw.items())
        return [
            {
                "label": label,
                "base_threshold": round(base, 4),
                "effective_threshold": round(max(0.01, base + offset), 4),
            }
            for label, base in sorted_labels
        ]

    return router

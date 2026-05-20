from fastapi import APIRouter, HTTPException

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_prediction_service
from pixlstash.utils.service.caption_utils import sync_picture_sidecar

logger = get_logger(__name__)


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
    )
    def get_tag_predictions(
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
                "label_thresholds": tag_prediction_service.load_label_thresholds(
                    meta_path, offset
                ),
            },
        }

    @router.post(
        "/pictures/{id}/tag_predictions/{tag}/confirm",
        summary="Confirm a tag prediction",
        description=(
            "Marks the prediction as CONFIRMED and ensures a corresponding row "
            "exists in the Tag table.  Emits a CHANGED_PICTURES event."
        ),
    )
    def confirm_tag_prediction(id: int, tag: str):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        try:
            tag_prediction_service.confirm_tag_prediction(server.vault, pic_id, tag)
        except KeyError:
            raise HTTPException(status_code=404, detail="Prediction not found")
        server._handle_vault_event(
            EventType.CHANGED_PICTURES,
            {"picture_ids": [pic_id]},
        )
        sync_picture_sidecar(server, pic_id)
        return {"status": "confirmed", "tag": tag}

    @router.post(
        "/pictures/{id}/tag_predictions/{tag}/reject",
        summary="Reject a tag prediction",
        description="Marks the prediction as REJECTED.  Does not modify the Tag table.",
    )
    def reject_tag_prediction(id: int, tag: str):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        tag_prediction_service.reject_tag_prediction(server.vault, pic_id, tag)
        server._handle_vault_event(
            EventType.CHANGED_PICTURES,
            {"picture_ids": [pic_id]},
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
    )
    def delete_tag_predictions(id: int):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        count = tag_prediction_service.delete_tag_predictions(server.vault, pic_id)
        server._handle_vault_event(
            EventType.CHANGED_PICTURES,
            {"picture_ids": [pic_id]},
        )
        return {"status": "deleted", "count": count}

    @router.post(
        "/pictures/{id}/reset_tags",
        summary="Reset tags and predictions for a picture",
        description=(
            "Atomically deletes all non-manual TagPrediction rows and all Tag rows "
            "for the picture, then restores the empty-tag sentinel.  This is the "
            "single-round-trip equivalent of calling tag_predictions/delete followed "
            "by DELETE tags — it avoids the intermediate state where predictions are "
            "gone but tags still exist, which otherwise tricks the background "
            "MissingTagFinder into running a wasted inference pass."
        ),
    )
    def reset_picture_tags(id: int):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        tag_prediction_service.reset_picture_tags(server.vault, pic_id)
        server.vault.notify(EventType.CHANGED_TAGS, [pic_id])
        server.vault.retag_picture_interactive(pic_id)
        return {"status": "reset"}

    @router.get(
        "/tagger/label-thresholds",
        summary="Get per-label thresholds for the PixlStash tagger",
        description=(
            "Returns each label's base threshold and the effective threshold after "
            "applying the current user offset. Results are sorted alphabetically."
        ),
    )
    def get_label_thresholds():
        offset = server.vault.get_pixlstash_tagger_threshold_offset()
        meta_path = server.vault.get_pixlstash_tagger_meta_path()
        raw = tag_prediction_service.load_raw_label_thresholds(meta_path)
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

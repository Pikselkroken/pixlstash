from fastapi import APIRouter, HTTPException
from sqlmodel import Session, select

from pixlstash.db_models import Tag
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.event_types import EventType
from pixlstash.picture_tagger import CUSTOM_TAGGER_THRESHOLD_FULL
from pixlstash.pixl_logging import get_logger

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

        def _fetch(session: Session):
            q = select(TagPrediction).where(TagPrediction.picture_id == pic_id)
            if status:
                q = q.where(TagPrediction.status == status.upper())
            q = q.order_by(TagPrediction.confidence.desc())
            return session.exec(q).all()

        predictions = server.vault.db.run_immediate_read_task(_fetch)
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
        return {
            "tag_predictions": payload,
            "meta": {
                "acceptance_threshold": float(CUSTOM_TAGGER_THRESHOLD_FULL),
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

        def _confirm(session: Session):
            prediction = session.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == pic_id,
                    TagPrediction.tag == tag,
                )
            ).first()
            if prediction is None:
                raise HTTPException(status_code=404, detail="Prediction not found")
            prediction.status = "CONFIRMED"

            # Ensure the Tag row exists
            existing_tag = session.exec(
                select(Tag).where(Tag.picture_id == pic_id, Tag.tag == tag)
            ).first()
            if existing_tag is None:
                session.add(Tag(picture_id=pic_id, tag=tag))

            session.commit()

        server.vault.db.run_task(_confirm)
        server._handle_vault_event(
            EventType.CHANGED_PICTURES,
            {"picture_ids": [pic_id]},
        )
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

        def _reject(session: Session):
            prediction = session.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == pic_id,
                    TagPrediction.tag == tag,
                )
            ).first()
            if prediction is None:
                raise HTTPException(status_code=404, detail="Prediction not found")
            prediction.status = "REJECTED"
            session.commit()

        server.vault.db.run_task(_reject)
        return {"status": "rejected", "tag": tag}

    return router

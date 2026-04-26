import json

from fastapi import APIRouter, HTTPException
from sqlmodel import Session, delete, or_, select

from pixlstash.db_models import Tag
from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.event_types import EventType
from pixlstash.picture_tagger import (
    CUSTOM_TAGGER_META_PATH,
    CUSTOM_TAGGER_DEFAULT_THRESHOLD,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.caption_utils import sanitise_tag
from pixlstash.utils.service.caption_utils import sync_picture_sidecar
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)

logger = get_logger(__name__)


def _load_label_thresholds(bias: float = 0.0) -> dict[str, float]:
    """Load per-label acceptance thresholds from the custom tagger meta JSON.

    Keys are naturalized to match the values stored in TagPrediction.tag.
    The bias is the user-configured offset added to each label's base threshold.
    Returns an empty dict if the file is missing or lacks label_thresholds.
    """
    try:
        with open(CUSTOM_TAGGER_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        raw = meta.get("label_thresholds", {})
        if not raw:
            return {}
        return {
            sanitise_tag(k) or k: max(0.01, float(v) + bias) for k, v in raw.items()
        }
    except Exception:
        return {}


def _load_raw_label_thresholds() -> dict[str, float]:
    """Load per-label thresholds from meta JSON without any offset applied."""
    try:
        with open(CUSTOM_TAGGER_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        raw = meta.get("label_thresholds", {})
        return {sanitise_tag(k) or k: float(v) for k, v in raw.items()}
    except Exception:
        return {}


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
        bias = getattr(server.vault, "_custom_tagger_threshold_offset", None) or 0.0
        return {
            "tag_predictions": payload,
            "meta": {
                "acceptance_threshold": max(
                    0.01, float(CUSTOM_TAGGER_DEFAULT_THRESHOLD) + bias
                ),
                "label_thresholds": _load_label_thresholds(bias),
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

            session.flush()
            recompute_anomaly_tag_uncertainty(session, pic_id)
            session.commit()

        server.vault.db.run_task(_confirm)
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

        def _reject(session: Session):
            prediction = session.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == pic_id,
                    TagPrediction.tag == tag,
                )
            ).first()
            if prediction is None:
                # Tag was added manually — create a synthetic REJECTED prediction
                # so it persists through fetches.
                session.add(
                    TagPrediction(
                        picture_id=pic_id,
                        tag=tag,
                        confidence=1.0,
                        model_version="manual",
                        status="REJECTED",
                    )
                )
            else:
                prediction.status = "REJECTED"
            recompute_anomaly_tag_uncertainty(session, pic_id)
            session.commit()

        server.vault.db.run_task(_reject)
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

        def _delete(session: Session):
            # Use a direct bulk DELETE statement — avoid loading ORM objects,
            # which triggers the Picture.tag_predictions cascade="all, delete-orphan"
            # relationship and can corrupt rows across unrelated pictures.
            # Explicitly include NULL model_version (SQL != does not match NULLs).
            stmt = (
                delete(TagPrediction)
                .where(TagPrediction.picture_id == pic_id)
                .where(
                    or_(
                        TagPrediction.model_version != "manual",
                        TagPrediction.model_version.is_(None),
                    )
                )
            )
            result = session.exec(stmt)
            session.commit()
            return result.rowcount

        count = server.vault.db.run_task(_delete)
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
            "MissingTagPredictionFinder into running a wasted inference pass."
        ),
    )
    def reset_picture_tags(id: int):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def _reset(session: Session):
            # Delete non-manual predictions first (same logic as tag_predictions/delete)
            session.exec(
                delete(TagPrediction)
                .where(TagPrediction.picture_id == pic_id)
                .where(
                    or_(
                        TagPrediction.model_version != "manual",
                        TagPrediction.model_version.is_(None),
                    )
                )
            )
            # Clear all tags and restore the empty sentinel
            session.exec(delete(Tag).where(Tag.picture_id == pic_id))
            session.add(Tag(tag=TAG_EMPTY_SENTINEL, picture_id=pic_id))
            session.commit()

        server.vault.db.run_task(_reset)
        server.vault.notify(EventType.CHANGED_TAGS, [pic_id])
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
        offset = getattr(server.vault, "_custom_tagger_threshold_offset", None) or 0.0
        raw = _load_raw_label_thresholds()
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

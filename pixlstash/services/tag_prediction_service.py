"""Service layer for tag prediction operations.

Extracted from pixlstash/routes/tag_predictions.py to keep route handlers thin.
Provides vault-level functions so route handlers need not call vault.db directly.
"""

import json
from typing import TYPE_CHECKING

from sqlmodel import Session, delete, or_, select

from pixlstash.db_models import Tag
from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.caption_utils import sanitise_tag
from pixlstash.utils.service.tag_prediction_utils import recompute_anomaly_tag_uncertainty

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


def load_label_thresholds(meta_path: str | None, bias: float = 0.0) -> dict[str, float]:
    """Load per-label acceptance thresholds from the PixlStash tagger meta JSON.

    Keys are naturalized to match the values stored in TagPrediction.tag.
    The bias is the user-configured offset added to each label's base threshold.
    Returns an empty dict if the file is missing or lacks label_thresholds.

    Args:
        meta_path: Path to the tagger meta JSON file, or None.
        bias: Offset to add to each label's base threshold.

    Returns:
        Dict mapping sanitised tag name → effective threshold.
    """
    if not meta_path:
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        raw = meta.get("label_thresholds", {})
        if not raw:
            return {}
        return {
            sanitise_tag(k) or k: max(0.01, float(v) + bias) for k, v in raw.items()
        }
    except Exception:
        return {}


def load_raw_label_thresholds(meta_path: str | None) -> dict[str, float]:
    """Load per-label thresholds from meta JSON without any offset applied.

    Args:
        meta_path: Path to the tagger meta JSON file, or None.

    Returns:
        Dict mapping sanitised tag name → base threshold.
    """
    if not meta_path:
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        raw = meta.get("label_thresholds", {})
        return {sanitise_tag(k) or k: float(v) for k, v in raw.items()}
    except Exception:
        return {}


def get_predictions(
    vault: "Vault", pic_id: int, status: str | None = None
) -> list[TagPrediction]:
    """Return tag predictions for a picture, ordered by confidence descending.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID to fetch predictions for.
        status: Optional status filter (``PENDING``, ``CONFIRMED``, ``REJECTED``).

    Returns:
        List of TagPrediction instances.
    """

    def _fetch(session: Session) -> list[TagPrediction]:
        q = select(TagPrediction).where(TagPrediction.picture_id == pic_id)
        if status:
            q = q.where(TagPrediction.status == status.upper())
        q = q.order_by(TagPrediction.confidence.desc())
        return list(session.exec(q).all())

    return vault.db.run_immediate_read_task(_fetch)


def confirm_tag_prediction(vault: "Vault", pic_id: int, tag: str) -> None:
    """Mark a prediction as CONFIRMED and ensure the Tag row exists.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID owning the prediction.
        tag: Tag value to confirm.

    Raises:
        KeyError: If no prediction with the given tag exists for the picture.
    """

    def _confirm(session: Session) -> None:
        prediction = session.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == pic_id,
                TagPrediction.tag == tag,
            )
        ).first()
        if prediction is None:
            raise KeyError(f"Prediction not found: picture_id={pic_id} tag={tag!r}")
        prediction.status = "CONFIRMED"

        existing_tag = session.exec(
            select(Tag).where(Tag.picture_id == pic_id, Tag.tag == tag)
        ).first()
        if existing_tag is None:
            session.add(Tag(picture_id=pic_id, tag=tag))

        session.flush()
        recompute_anomaly_tag_uncertainty(session, pic_id)
        session.commit()

    vault.db.run_task(_confirm)


def reject_tag_prediction(vault: "Vault", pic_id: int, tag: str) -> None:
    """Mark a prediction as REJECTED (or create a synthetic REJECTED row).

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID owning the prediction.
        tag: Tag value to reject.
    """

    def _reject(session: Session) -> None:
        prediction = session.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == pic_id,
                TagPrediction.tag == tag,
            )
        ).first()
        if prediction is None:
            # Tag was added manually — create a synthetic REJECTED prediction so it
            # persists through fetches.
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

    vault.db.run_task(_reject)


def delete_tag_predictions(vault: "Vault", pic_id: int) -> int:
    """Delete all non-manual TagPrediction rows for the picture.

    Uses a direct bulk DELETE to avoid ORM cascade side-effects.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID whose predictions are to be deleted.

    Returns:
        Number of rows deleted.
    """

    def _delete(session: Session) -> int:
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

    return vault.db.run_task(_delete)


def reset_picture_tags(vault: "Vault", pic_id: int) -> None:
    """Atomically delete all non-manual predictions and all tags, then restore the sentinel.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID to reset.
    """

    def _reset(session: Session) -> None:
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
        session.exec(delete(Tag).where(Tag.picture_id == pic_id))
        session.add(Tag(tag=TAG_EMPTY_SENTINEL, picture_id=pic_id))
        session.commit()

    vault.db.run_task(_reset)

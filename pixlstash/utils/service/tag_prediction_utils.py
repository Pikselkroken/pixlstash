from sqlmodel import Session, select

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.tag import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    TAG_EMPTY_SENTINEL,
    Tag,
)
from pixlstash.db_models.tag_prediction import TagPrediction

_PENALISED_TAG_SET = {t.strip().lower() for t in DEFAULT_SMART_SCORE_PENALIZED_TAGS}


def recompute_anomaly_tag_uncertainty(session: Session, picture_id: int) -> None:
    """Recompute and persist ``anomaly_tag_uncertainty`` on a Picture.

    The score is computed dynamically from the model's raw confidence scores and
    the *current* ``Tag`` rows — it does not rely on ``TagPrediction.status`` so
    it stays correct whether tags were changed via the prediction workflow or
    edited directly.

    For each anomaly tag the model has scored:

    - Tag currently applied: score = ``1 - confidence``
      (human says the defect is present; the model was less than fully confident)
    - Tag not currently applied: score = ``confidence``
      (model says the defect is probably there; human hasn't accepted it)

    A higher value means a stronger disagreement between the model score and the
    current human label on at least one anomaly tag.

    Call this **after** any ``session.flush()`` that modifies ``Tag`` rows, and
    **before** ``session.commit()``, so the read sees the latest state.
    """
    predictions = session.exec(
        select(TagPrediction.tag, TagPrediction.confidence).where(
            TagPrediction.picture_id == picture_id
        )
    ).all()

    applied_tags = {
        row
        for row in session.exec(
            select(Tag.tag).where(
                Tag.picture_id == picture_id,
                Tag.tag.is_not(None),
                Tag.tag != TAG_EMPTY_SENTINEL,
            )
        ).all()
        if row is not None
    }

    scores: list[float] = []
    for tag, confidence in predictions:
        if tag is None or tag.strip().lower() not in _PENALISED_TAG_SET:
            continue
        if tag in applied_tags:
            scores.append(1.0 - float(confidence))
        else:
            scores.append(float(confidence))

    anomaly_uncertainty = max(scores) if scores else 0.0

    pic = session.get(Picture, picture_id)
    if pic is not None:
        pic.anomaly_tag_uncertainty = anomaly_uncertainty

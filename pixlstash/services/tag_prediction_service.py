"""Service layer for tag prediction operations.

Extracted from pixlstash/routes/tag_predictions.py to keep route handlers thin.
Provides vault-level functions so route handlers need not call vault.db directly.
"""

from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlmodel import Session, select

from pixlstash.db_models import Picture, Tag
from pixlstash.db_models.tag import make_tag_sentinel
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import enforce_pictures_not_locked
from pixlstash.utils.sql_chunking import chunked
from pixlstash.utils.service.label_ledger import (
    NEG,
    POS,
    not_human_labeled,
    record_human_label,
)
from pixlstash.utils.service.smart_score_invalidation import (
    InteractiveRescoreRegistry,
    invalidate_on_anomaly_change,
)
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


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


def confirm_tag_prediction_in_session(
    session: Session,
    pic_id: int,
    tag: str,
    registry: "InteractiveRescoreRegistry | None" = None,
    origin_client_id: str | None = None,
    *,
    commit: bool = True,
) -> None:
    """Mark a prediction as CONFIRMED and ensure the Tag row exists.

    Session-level so the route can hand it to
    :func:`~pixlstash.services.operation_log_service.run_recorded_metadata_task`,
    which snapshots the picture's facets on either side of this call inside the
    same queued DB task (§21.2). The lock guard runs *here*, before anything is
    written, so a refused confirm records no operation.

    Args:
        session: Active DB session.
        pic_id: Picture ID owning the prediction.
        tag: Tag value to confirm.
        registry: The vault's interactive rescore registry (see :func:`confirm_tag_prediction`).
        origin_client_id: The originating tab's ``X-Client-Id``.
        commit: Commit before returning. The operation-log wrapper passes
            ``False`` so it owns the mutation and receipt transaction.

    Raises:
        KeyError: If no prediction with the given tag exists for the picture.
    """
    # Confirming promotes a prediction to a Tag and writes a human POS - label
    # data frozen when the picture is in a locked set.
    enforce_pictures_not_locked(session, [pic_id], "confirm a tag on a locked picture")
    prediction = session.exec(
        select(TagPrediction).where(
            TagPrediction.picture_id == pic_id,
            TagPrediction.tag == tag,
        )
    ).first()
    if prediction is None:
        raise KeyError(f"Prediction not found: picture_id={pic_id} tag={tag!r}")

    # Confirming an anomaly tag folds its probability to 1.0 in the scorer's
    # inputs, so the cached smart score must be dropped for recompute.
    with invalidate_on_anomaly_change(
        session,
        [pic_id],
        context="confirm tag prediction",
        registry=registry,
        origin_client_id=origin_client_id,
    ):
        # Record the human acceptance, snapshotting the tagger version/confidence the
        # reviewer agreed with (frozen in label_model_version/label_confidence).
        record_human_label(session, pic_id, tag, POS)

        existing_tag = session.exec(
            select(Tag).where(Tag.picture_id == pic_id, Tag.tag == tag)
        ).first()
        if existing_tag is None:
            session.add(Tag(picture_id=pic_id, tag=tag))

        session.flush()
        recompute_anomaly_tag_uncertainty(session, pic_id)
    if commit:
        session.commit()
    else:
        session.flush()


def confirm_tag_prediction(
    vault: "Vault", pic_id: int, tag: str, origin_client_id: str | None = None
) -> None:
    """Mark a prediction as CONFIRMED and ensure the Tag row exists.

    The vault-level wrapper, for callers that are not recording an operation. The
    HTTP route records one, so it calls :func:`confirm_tag_prediction_in_session`
    through the operation log instead.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID owning the prediction.
        tag: Tag value to confirm.
        origin_client_id: The originating tab's ``X-Client-Id``. When confirming an
            anomaly tag moves the scorer's inputs, the invalidated id is recorded in the
            vault's interactive rescore registry so the background recompute emits an
            immediate, origin-stamped ``smart_score`` grid refresh for that card - the
            user's primary "confirm-driven" workflow, where the visible score must drop in
            place rather than routing to the deferred "view changed" pill.

    Raises:
        KeyError: If no prediction with the given tag exists for the picture.
    """
    vault.db.run_task(
        confirm_tag_prediction_in_session,
        pic_id,
        tag,
        vault.interactive_rescore_registry,
        origin_client_id,
    )


def reject_tag_prediction_in_session(
    session: Session,
    pic_id: int,
    tag: str,
    registry: "InteractiveRescoreRegistry | None" = None,
    origin_client_id: str | None = None,
    *,
    commit: bool = True,
) -> None:
    """Mark a prediction as REJECTED (or create a synthetic REJECTED row).

    Session-level for the same reason as
    :func:`confirm_tag_prediction_in_session`: the route wraps it in a recorded
    operation so the rejection is undoable, and the lock guard runs here so a
    refused reject records nothing.

    Args:
        session: Active DB session.
        pic_id: Picture ID owning the prediction.
        tag: Tag value to reject.
        registry: The vault's interactive rescore registry (see :func:`reject_tag_prediction`).
        origin_client_id: The originating tab's ``X-Client-Id``.
        commit: Commit before returning. The operation-log wrapper passes
            ``False`` so it owns the mutation and receipt transaction.
    """
    # Rejecting writes a human NEG onto the picture - label data frozen when
    # the picture is in a locked set.
    enforce_pictures_not_locked(session, [pic_id], "reject a tag on a locked picture")
    # Rejecting an anomaly tag folds its probability to 0.0 in the scorer's
    # inputs, so the cached smart score must be dropped for recompute.
    with invalidate_on_anomaly_change(
        session,
        [pic_id],
        context="reject tag prediction",
        registry=registry,
        origin_client_id=origin_client_id,
    ):
        # Record the human rejection as durable NEG supervision (snapshotting the
        # tagger version/confidence overruled). Creates a synthetic 'manual' row if
        # the tag was added manually, so the reject persists through fetches.
        record_human_label(session, pic_id, tag, NEG)
        session.flush()
        recompute_anomaly_tag_uncertainty(session, pic_id)
    if commit:
        session.commit()
    else:
        session.flush()


def reject_tag_prediction(
    vault: "Vault", pic_id: int, tag: str, origin_client_id: str | None = None
) -> None:
    """Mark a prediction as REJECTED (or create a synthetic REJECTED row).

    The vault-level wrapper, for callers that are not recording an operation. The
    HTTP route records one, so it calls :func:`reject_tag_prediction_in_session`
    through the operation log instead.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID owning the prediction.
        tag: Tag value to reject.
        origin_client_id: The originating tab's ``X-Client-Id``. When rejecting an anomaly
            tag moves the scorer's inputs, the invalidated id is recorded in the vault's
            interactive rescore registry so the background recompute emits an immediate,
            origin-stamped ``smart_score`` grid refresh for that card instead of the
            deferred bulk-drain path.
    """
    vault.db.run_task(
        reject_tag_prediction_in_session,
        pic_id,
        tag,
        vault.interactive_rescore_registry,
        origin_client_id,
    )


def delete_tag_predictions(
    vault: "Vault", pic_id: int, origin_client_id: str | None = None
) -> int:
    """Delete all non-manual TagPrediction rows for the picture.

    Uses a direct bulk DELETE to avoid ORM cascade side-effects.

    Dropping the model's anomaly prediction rows removes their probabilities from the
    scorer's inputs (:func:`pixlstash.scoring.smart_score.fetch_anomaly_confidences` reads
    ``TagPrediction`` rows in the anomaly vocabulary), so the cached ``Picture.smart_score``
    goes stale and must be NULLed for the background ``SmartScoreTask`` to recompute it.
    Wrapping the delete in :func:`invalidate_on_anomaly_change` mirrors the sibling
    :func:`reset_picture_tags` path - without it, deleting a ``malformed nipples`` /
    ``watermark`` prediction leaves the stored score frozen with the old penalty baked in.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID whose predictions are to be deleted.
        origin_client_id: The originating tab's ``X-Client-Id``. When the delete moves the
            anomaly signature, the invalidated id is recorded in the vault's interactive
            rescore registry so the background recompute emits an immediate, origin-stamped
            grid refresh for that card instead of waiting for the whole backfill to drain.

    Returns:
        Number of rows deleted.
    """

    def _delete(session: Session) -> int:
        with invalidate_on_anomaly_change(
            session,
            [pic_id],
            context="delete tag predictions",
            registry=vault.interactive_rescore_registry,
            origin_client_id=origin_client_id,
        ):
            stmt = (
                delete(TagPrediction)
                .where(TagPrediction.picture_id == pic_id)
                .where(not_human_labeled())
            )
            result = session.exec(stmt)
            rowcount = result.rowcount
        session.commit()
        return rowcount

    return vault.db.run_task(_delete)


def reset_picture_tags(
    vault: "Vault",
    pic_id: int,
    engine_name: str | None = None,
    origin_client_id: str | None = None,
) -> None:
    """Single-picture form of :func:`reset_pictures_tags`."""
    reset_pictures_tags(
        vault, [pic_id], engine_name=engine_name, origin_client_id=origin_client_id
    )


def reset_pictures_tags(
    vault: "Vault",
    pic_ids: list[int],
    engine_name: str | None = None,
    origin_client_id: str | None = None,
) -> list[int]:
    """Atomically delete all non-manual predictions and all tags, then restore the sentinel.

    One transaction for the whole list: a bulk retag used to be one request
    and one urgent single-picture task per picture (#1162). Ids that name no
    picture are skipped rather than failing the batch on the sentinel's
    foreign key.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_ids: Picture IDs to reset.
        engine_name: Optional engine/plugin name to embed in the sentinel so the
            background tagger uses that specific engine for this picture.  Pass
            ``None`` to use the default ``active_tag_plugin`` setting.
        origin_client_id: The originating tab's ``X-Client-Id``. When dropping the
            picture's prediction rows moves the scorer's inputs, the invalidated id is
            recorded in the vault's interactive rescore registry so the background
            recompute emits an immediate, origin-stamped ``smart_score`` grid refresh for
            that card instead of the deferred bulk-drain path.
    """

    ids = sorted({int(pid) for pid in pic_ids})
    if not ids:
        return []
    sentinel = make_tag_sentinel(engine_name)

    def _reset(session: Session) -> list[int]:
        reset_ids: list[int] = []
        # Chunked: a whole-library selection can exceed SQLite's parameter cap.
        for chunk in chunked(ids):
            # Reset deletes ALL confirmed Tag rows and drops a retag sentinel -
            # a destructive rewrite of frozen label data. Refuse on a locked
            # picture.
            enforce_pictures_not_locked(
                session, chunk, "reset tags on a locked picture"
            )
            present = list(
                session.exec(select(Picture.id).where(Picture.id.in_(chunk))).all()
            )
            if not present:
                continue
            # Dropping the model's prediction rows removes their anomaly
            # probabilities from the scorer's inputs, so the cached smart score
            # goes stale.
            with invalidate_on_anomaly_change(
                session,
                present,
                context="reset picture tags",
                registry=vault.interactive_rescore_registry,
                origin_client_id=origin_client_id,
            ):
                session.exec(
                    delete(TagPrediction)
                    .where(TagPrediction.picture_id.in_(present))
                    .where(not_human_labeled())
                )
                session.exec(delete(Tag).where(Tag.picture_id.in_(present)))
                for pic_id in present:
                    session.add(Tag(tag=sentinel, picture_id=pic_id))
            reset_ids.extend(present)
        session.commit()
        return reset_ids

    return vault.db.run_task(_reset)

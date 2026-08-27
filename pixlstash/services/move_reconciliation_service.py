"""Reconciling moves made outside PixlStash (v1.11 Phase 5).

The mirror of ``layout_move_service``: that engine moves a file when an
assignment change makes its folder stop being true; this reads a file the
owner already moved in their file manager and decides whether an assignment
should change to match. See ``docs/plans/v1.11.0-existing-library.md`` §4
Phase 5 and ``docs/backend_architecture.md`` §26 "The move journal".

``ReferenceFolderScanTask`` writes an :class:`ExternalMoveReview` row for
every move it attributes to the owner (``record_pending_reviews``). Nothing
else happens automatically - every read here classifies each row LIVE against
the picture's current facets and the root's current layout
(:func:`pixlstash.utils.library_layout.reconcile_move`), never against a
snapshot taken when the row was written, so a picture whose memberships
changed since the move is judged on what is true now.

Applying a review only ever changes the three memberships this reconciles -
project, set and person. **Tag is deliberately not reconciled**: the default
layout never places by tag, Phase 4c (custom layouts) has not shipped, and a
tag-typed folder is therefore unreachable through the product today. A future
layout that uses ``Facet.TAG`` will still classify correctly (the pure
function does not know the applier's limits) but that one change is skipped
and logged rather than guessed at.
ponytail: Facet.TAG membership mutation, add when Phase 4c ships a layout
builder that can actually select it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Iterable, Optional

from sqlmodel import Session, select

from pixlstash.db_models.character import Character
from pixlstash.db_models.external_move_review import ExternalMoveReview
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_move import RETENTION_S
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
from pixlstash.db_models.project import Project
from pixlstash.pixl_logging import get_logger
from pixlstash.services import layout_move_service, operation_log_service
from pixlstash.utils.library_layout import (
    Facet,
    MoveOutcome,
    ReconciledMove,
    reconcile_move,
)
from pixlstash.utils.sql_chunking import chunked

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)

#: The operation type the frontend keys its undo affordance off.
OP_EXTERNAL_MOVE_RECONCILE = "pictures.external_move.reconcile"


# ---------------------------------------------------------------------------
# Recording - called from ReferenceFolderScanTask
# ---------------------------------------------------------------------------


def record_pending_reviews(
    session: Session, moves: Iterable[tuple[int, str, str]]
) -> list[int]:
    """Queue *moves* for reconciliation. ``(picture_id, old_path, new_path)`` each.

    Caller's job to have already decided the root has a layout - every move
    the reference-folder scan follows lands here regardless, but only a
    laid-out root has any assignment for a folder to contradict, so recording
    for the rest would grow the queue for nothing ever readable off it.

    Does not commit; the caller's transaction (the same one that already
    updated ``Picture.file_path`` for these moves) covers it.
    """
    picture_ids: list[int] = []
    for picture_id, old_path, new_path in moves:
        session.add(
            ExternalMoveReview(
                picture_id=int(picture_id), old_path=old_path, new_path=new_path
            )
        )
        picture_ids.append(int(picture_id))
    return picture_ids


# ---------------------------------------------------------------------------
# Classification - live, every read
# ---------------------------------------------------------------------------


def _reconciliation_context(session: Session, picture_ids: Iterable[int]) -> dict:
    """Return ``{picture_id: (root, vocabulary, facets)}`` for *picture_ids*.

    A picture is absent from the result when it no longer exists (deleted
    between the move and the review) or its reference folder no longer has a
    layout - both mean there is nothing left to classify.
    """
    ids = sorted({int(p) for p in picture_ids})
    if not ids:
        return {}
    # Chunked: reorganising a folder queues hundreds of reviews at once
    # (release plan §4 Phase 5), and this is the one query surface Phase 5
    # adds that can see the WHOLE queue in one call rather than layout_move_
    # service's own BATCH_SIZE=200-bounded caller. See sql_chunking.py.
    pictures: dict = {}
    for chunk in chunked(ids):
        for pic in session.exec(select(Picture).where(Picture.id.in_(chunk))).all():
            if pic.id is not None:
                pictures[int(pic.id)] = pic
    if not pictures:
        return {}
    # image_root is the library's own root, irrelevant here: every review row
    # is written for a reference-folder move (record_pending_reviews's only
    # caller), never the library's own root.
    roots = layout_move_service.layout_roots(session, None)
    vocabulary = (
        layout_move_service.library_vocabulary(
            session, [r.layout for r in roots.values()]
        )
        if roots
        else {}
    )
    facets_by_id: dict = {}
    for chunk in chunked(sorted(pictures.keys())):
        facets_by_id.update(layout_move_service.picture_facets(session, chunk))
    context: dict = {}
    for pic_id, picture in pictures.items():
        root = roots.get(picture.reference_folder_id)
        if root is None:
            continue
        context[pic_id] = (root, vocabulary, facets_by_id.get(pic_id, {}))
    return context


def _classify(
    session: Session, reviews: list[ExternalMoveReview]
) -> dict[int, tuple[Optional[ReconciledMove], dict]]:
    """Return ``{review.id: (reconciled_or_None, current_facets)}``.

    ``reconciled`` is ``None`` when the picture or its layout is gone - the
    caller's cue to drop the row rather than show or act on it.
    """
    context = _reconciliation_context(session, (r.picture_id for r in reviews))
    result: dict = {}
    for review in reviews:
        entry = context.get(review.picture_id)
        if entry is None:
            result[review.id] = (None, {})
            continue
        root, vocabulary, facets = entry
        old_folder = layout_move_service.relative_folder(review.old_path, root)
        new_folder = layout_move_service.relative_folder(review.new_path, root)
        reconciled = reconcile_move(
            old_folder, new_folder, facets, root.layout, vocabulary
        )
        result[review.id] = (reconciled, facets)
    return result


def _serialize_pairs(pairs: tuple[tuple[Facet, str], ...]) -> list[dict]:
    return [{"facet": facet.value, "name": name} for facet, name in pairs]


def pending_summary_in_session(session: Session) -> dict:
    """Return the reconciliation queue, bucketed and ready for the review screen.

    Every row is reclassified against current state on every call - there is
    no cache to invalidate. Rows that no longer reconcile to anything
    (``MoveOutcome.NONE``, or the picture/layout is gone) are deleted here:
    that is not a side effect the caller has to know about, it is what "the
    queue holds exactly what is still pending" means.

    An ``off_layout`` row carries no decision - there is nothing for a reader
    to act on, ever - so unlike ``unambiguous``/``ambiguous`` it does not wait
    for a human. It is shown for :data:`RETENTION_S` (the same window the
    move journal keeps a claimed row for) and then pruned on its own, the same
    way a ``picture_move`` row ages out: kept long enough to be seen at least
    once, not kept forever just because nobody had a reason to clear it.
    """
    reviews = session.exec(select(ExternalMoveReview)).all()
    buckets: dict = {"unambiguous": [], "ambiguous": [], "off_layout": []}
    if not reviews:
        return buckets

    classified = _classify(session, reviews)
    off_layout_cutoff = datetime.utcnow() - timedelta(seconds=RETENTION_S)
    stale: list[ExternalMoveReview] = []
    for review in reviews:
        reconciled, facets = classified[review.id]
        if reconciled is None or reconciled.outcome == MoveOutcome.NONE:
            stale.append(review)
            continue
        if (
            reconciled.outcome == MoveOutcome.OFF_LAYOUT
            and review.detected_at < off_layout_cutoff
        ):
            stale.append(review)
            continue
        item = {
            "review_id": review.id,
            "picture_id": review.picture_id,
            "old_path": review.old_path,
            "new_path": review.new_path,
            "removals": _serialize_pairs(reconciled.removals),
            "additions": _serialize_pairs(reconciled.additions),
        }
        if reconciled.outcome == MoveOutcome.OFF_LAYOUT:
            buckets["off_layout"].append(item)
            continue
        if reconciled.outcome == MoveOutcome.AMBIGUOUS:
            item["current"] = {
                facet.value: list(facets.get(facet) or ())
                for facet, _ in reconciled.removals
            }
            buckets["ambiguous"].append(item)
            continue
        buckets["unambiguous"].append(item)

    if stale:
        for review in stale:
            session.delete(review)
        session.flush()
        session.commit()

    return buckets


def pending_moves(vault: "Vault") -> dict:
    """Vault wrapper for :func:`pending_summary_in_session`."""
    return vault.db.run_task(pending_summary_in_session)


# ---------------------------------------------------------------------------
# Applying - one membership write per facet, reused from the picture routes'
# own mutation shape (routes/pictures/_crud.py: set_project_for_pictures)
# ---------------------------------------------------------------------------


def _resolve_entity_id(session: Session, facet: Facet, name: str) -> Optional[int]:
    """Return the one entity *name* names for *facet*, or ``None``.

    Project names are unique; set and character names are not
    (``docs/backend_architecture.md`` §26, "Renaming an entity renames its
    folder"). A name matching more than one row is exactly the ambiguity that
    section declines to resolve for a rename, and the same refusal is correct
    here: guessing which one the folder meant risks reassigning the wrong
    entity's pictures, and leaving the change unapplied risks nothing.
    """
    model = {
        Facet.PROJECT: Project,
        Facet.SET: PictureSet,
        Facet.PERSON: Character,
    }.get(facet)
    if model is None:
        return None
    rows = session.exec(select(model.id).where(model.name == name)).all()
    if len(rows) != 1:
        if len(rows) > 1:
            logger.warning(
                "External-move reconciliation: %r names %d %s rows; the name "
                "is not unique, so this change is skipped.",
                name,
                len(rows),
                facet.value,
            )
        return None
    return int(rows[0])


def _remove_project(session: Session, picture: Picture, project_id: int) -> bool:
    changed = False
    for member in session.exec(
        select(PictureProjectMember).where(
            PictureProjectMember.picture_id == picture.id,
            PictureProjectMember.project_id == project_id,
        )
    ).all():
        session.delete(member)
        changed = True
    if picture.project_id == project_id:
        # Mirrors routes/pictures/_crud.py's own "remove" branch: fall back to
        # another membership rather than leaving a stale primary behind.
        fallback = session.exec(
            select(PictureProjectMember.project_id)
            .where(
                PictureProjectMember.picture_id == picture.id,
                PictureProjectMember.project_id != project_id,
            )
            .order_by(PictureProjectMember.project_id.asc())
        ).first()
        picture.project_id = int(fallback) if fallback is not None else None
        session.add(picture)
        changed = True
    return changed


def _add_project(session: Session, picture: Picture, project_id: int) -> bool:
    changed = False
    member = session.exec(
        select(PictureProjectMember).where(
            PictureProjectMember.picture_id == picture.id,
            PictureProjectMember.project_id == project_id,
        )
    ).first()
    if member is None:
        session.add(PictureProjectMember(picture_id=picture.id, project_id=project_id))
        changed = True
    if picture.project_id != project_id:
        picture.project_id = project_id
        session.add(picture)
        changed = True
    return changed


def _remove_set(session: Session, picture: Picture, set_id: int) -> bool:
    member = session.exec(
        select(PictureSetMember).where(
            PictureSetMember.picture_id == picture.id,
            PictureSetMember.set_id == set_id,
        )
    ).first()
    if member is None:
        return False
    session.delete(member)
    return True


def _add_set(session: Session, picture: Picture, set_id: int) -> bool:
    member = session.exec(
        select(PictureSetMember).where(
            PictureSetMember.picture_id == picture.id,
            PictureSetMember.set_id == set_id,
        )
    ).first()
    if member is not None:
        return False
    session.add(PictureSetMember(picture_id=picture.id, set_id=set_id))
    return True


def _remove_person(session: Session, picture: Picture, character_id: int) -> bool:
    changed = False
    for face in session.exec(
        select(Face).where(
            Face.picture_id == picture.id, Face.character_id == character_id
        )
    ).all():
        face.character_id = None
        session.add(face)
        changed = True
    if picture.pending_character_id == character_id:
        picture.pending_character_id = None
        session.add(picture)
        changed = True
    return changed


def _add_person(session: Session, picture: Picture, character_id: int) -> bool:
    """Assign *character_id* to the picture's largest UNASSIGNED real face.

    Mirrors the no-reference-faces fallback ``POST /characters/{id}/faces``
    already uses (``routes/characters_faces.py``): rank by area, no likeness
    comparison. A folder move is not the manual assignment UI, so the cheaper
    fallback is the right amount of machinery, not a shortcut.

    **Never reassigns a face that already names someone.** An addition is
    supposed to be the safe half of a reconciliation - it cannot make any
    existing folder untrue (``library_layout.reconcile_move``'s whole
    argument for treating it as automatically unambiguous) - and that
    guarantee only holds if it never costs another person their face. A group
    shot with Sara's face largest and Mira's smallest, moved into a folder
    that adds Mira, must gain Mira without losing Sara; stealing the largest
    face regardless of whose it already is would do exactly that, silently,
    inside the bulk "Apply all" action.
    ponytail: likeness-ranked selection among the unassigned faces, add if the
    largest-unassigned-face fallback turns out to pick the wrong person often
    enough to matter.
    """
    faces = Face.find(session, picture_id=picture.id)
    if faces:
        unassigned = [f for f in faces if f.character_id is None]
        if not unassigned:
            # Every real face already names someone. Nothing here is spare to
            # give to a new person, and an addition must not take one that is
            # already spoken for.
            return False
        best = max(unassigned, key=lambda f: (f.width or 0) * (f.height or 0))
        best.character_id = character_id
        session.add(best)
        return True
    any_face_id = session.exec(
        select(Face.id).where(Face.picture_id == picture.id).limit(1)
    ).first()
    if any_face_id is not None:
        # Extraction ran and found no real face in this picture at all.
        return False
    if picture.pending_character_id == character_id:
        return False
    # Extraction has not run yet: defer, the same way a drop-to-person import
    # and POST /characters/{id}/faces already do (Picture.pending_character_id
    # docstring; vault._process_pending_character_assignments consumes it).
    picture.pending_character_id = character_id
    session.add(picture)
    return True


_REMOVERS = {
    Facet.PROJECT: _remove_project,
    Facet.SET: _remove_set,
    Facet.PERSON: _remove_person,
}
_ADDERS = {Facet.PROJECT: _add_project, Facet.SET: _add_set, Facet.PERSON: _add_person}


def _apply_one(
    session: Session,
    picture: Picture,
    removals: tuple[tuple[Facet, str], ...],
    additions: tuple[tuple[Facet, str], ...],
) -> bool:
    changed = False
    for facet, name in removals:
        remover = _REMOVERS.get(facet)
        if remover is None:
            continue
        entity_id = _resolve_entity_id(session, facet, name)
        if entity_id is not None:
            changed = remover(session, picture, entity_id) or changed
    for facet, name in additions:
        adder = _ADDERS.get(facet)
        if adder is None:
            continue
        entity_id = _resolve_entity_id(session, facet, name)
        if entity_id is not None:
            changed = adder(session, picture, entity_id) or changed
    return changed


def _resolve_review_picture_ids(session: Session, review_ids: list[int]) -> list[int]:
    ids: list[int] = []
    for chunk in chunked(review_ids):
        rows = session.exec(
            select(ExternalMoveReview.picture_id).where(
                ExternalMoveReview.id.in_(chunk)
            )
        ).all()
        ids.extend(int(pid) for pid in rows)
    return ids


def _apply_or_dismiss(
    session: Session, review_ids: list[int], apply_changes: bool
) -> tuple[list[int], list[int]]:
    """The recorded work: reconcile fresh, optionally apply, always clear the rows.

    Returns:
        ``(changed_picture_ids, skipped_review_ids)``. A review lands in
        ``skipped_review_ids`` when it genuinely had a removal or addition to
        make - not the harmless ``NONE``/``OFF_LAYOUT`` case, which has
        nothing to apply and is never "skipped" - but every one of those
        changes was refused, most likely by :func:`_resolve_entity_id`
        declining a non-unique ``Character``/``PictureSet`` name. The row is
        cleared from the queue either way, because it was explicitly acted on
        by id; the caller reports the skip rather than letting an apply that
        silently did nothing look identical to one that worked.
    """
    reviews: list[ExternalMoveReview] = []
    for chunk in chunked(review_ids):
        reviews.extend(
            session.exec(
                select(ExternalMoveReview).where(ExternalMoveReview.id.in_(chunk))
            ).all()
        )
    changed_ids: list[int] = []
    skipped_ids: list[int] = []
    if apply_changes and reviews:
        classified = _classify(session, reviews)
        pictures: dict = {}
        for chunk in chunked(sorted({r.picture_id for r in reviews})):
            for pic in session.exec(select(Picture).where(Picture.id.in_(chunk))).all():
                if pic.id is not None:
                    pictures[int(pic.id)] = pic
        for review in reviews:
            reconciled, _facets = classified[review.id]
            if reconciled is None or not (reconciled.removals or reconciled.additions):
                continue
            picture = pictures.get(review.picture_id)
            if picture is None or picture.deleted:
                skipped_ids.append(review.id)
                continue
            if _apply_one(session, picture, reconciled.removals, reconciled.additions):
                changed_ids.append(int(picture.id))
            else:
                skipped_ids.append(review.id)
    for review in reviews:
        session.delete(review)
    if changed_ids or reviews:
        session.flush()
    return sorted(set(changed_ids)), skipped_ids


def apply_reviews(vault: "Vault", review_ids: list[int], **request_context) -> dict:
    """Apply the given pending reviews and clear them from the queue.

    Reconciliation is recomputed fresh inside the same DB-queue slot as the
    mutation, never trusted from an earlier GET - a picture whose memberships
    changed in between is applied against what is true now.
    """
    ids = sorted({int(r) for r in review_ids if r is not None})
    if not ids:
        return {"applied_picture_ids": [], "skipped_review_ids": []}

    def work(session: Session) -> tuple[list[int], list[int]]:
        return _apply_or_dismiss(session, ids, apply_changes=True)

    (changed_ids, skipped_ids), _operation = (
        operation_log_service.run_recorded_metadata_task(
            vault,
            work,
            op_type=OP_EXTERNAL_MOVE_RECONCILE,
            picture_ids=[],
            resolve_picture_ids=lambda session: _resolve_review_picture_ids(
                session, ids
            ),
            summary=f"Reconciled {len(ids)} move(s) made outside PixlStash",
            **request_context,
        )
    )
    if skipped_ids:
        logger.warning(
            "External-move reconciliation: %d review(s) were cleared from the "
            "queue without applying - an entity name resolved to none or more "
            "than one row: %s",
            len(skipped_ids),
            skipped_ids,
        )
    return {"applied_picture_ids": changed_ids, "skipped_review_ids": skipped_ids}


def dismiss_reviews_in_session(session: Session, review_ids: list[int]) -> list[int]:
    """Drop the given pending reviews without changing any assignment."""
    ids = sorted({int(r) for r in review_ids if r is not None})
    if not ids:
        return []
    _apply_or_dismiss(session, ids, apply_changes=False)
    session.commit()
    return ids


def dismiss_reviews(vault: "Vault", review_ids: list[int]) -> dict:
    """Vault wrapper for :func:`dismiss_reviews_in_session`."""
    dismissed = vault.db.run_task(dismiss_reviews_in_session, review_ids)
    return {"dismissed_review_ids": dismissed or []}

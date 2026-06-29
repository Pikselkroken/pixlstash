"""Service layer for the tag-suggestion review queue (dataset refinement).

A :class:`TagSuggestion` is a *suspected* label fix produced by a finder (near-neighbor
disagreement, model mining, propagation). This module is the human half of the loop:
list the ranked suspects, and apply or dismiss them. Applying a suggestion writes
through to the ``Tag`` table — the system of record — so the fix lands in the data the
tagger retrains on:

  * accept a ``remove`` suggestion → delete the wrongly-applied ``Tag`` row.
  * accept an ``add`` suggestion    → ensure the missing ``Tag`` row exists.
  * dismiss either direction         → leave the labels untouched.

Removing a tag is durable on its own: the background ``MissingTagFinder`` only re-tags
pictures still carrying the pending-retag sentinel, so an already-tagged picture is not
re-scanned just because a tag was removed. No synthetic ``REJECTED`` prediction needed.

Mirrors the vault-task conventions in :mod:`pixlstash.services.tag_prediction_service`.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import Picture, Tag
from pixlstash.db_models.tag import is_tag_sentinel
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.filter_helpers import (
    fetch_tag_review_scope_picture_ids,
)
from pixlstash.utils.service.label_ledger import (
    NEG,
    POS,
    clear_human_label,
    record_human_label,
)
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


def resolve_filter_picture_ids(
    vault: "Vault",
    *,
    project_id: int | None = None,
    set_id: int | None = None,
    character_id: str | None = None,
) -> set[int] | None:
    """Resolve the review-scope filter params to an intersection of suspect picture ids.

    Thin wrapper around :func:`fetch_tag_review_scope_picture_ids` that runs it on a
    read session. Keeps the route's DB access in the service layer (the route stays a
    pure caller). Returns ``None`` when no dimension is provided; otherwise the
    intersection of the provided dimensions (possibly an empty set).

    Args:
        vault: Application vault, used for DB task dispatch.
        project_id: Optional project id filter.
        set_id: Optional picture-set id filter.
        character_id: Optional character id (numeric string, or ``"UNASSIGNED"``).
    """
    if project_id is None and set_id is None and not character_id:
        return None

    def _fetch(session: Session) -> set[int] | None:
        return fetch_tag_review_scope_picture_ids(
            session,
            project_id=project_id,
            set_id=set_id,
            character_id=character_id,
        )

    return vault.db.run_immediate_read_task(_fetch)


def list_suggestions(
    vault: "Vault",
    tag: str | None = None,
    direction: str | None = None,
    status: str = "PENDING",
    limit: int = 100,
    offset: int = 0,
    picture_ids: set[int] | None = None,
    source: str | None = None,
) -> list[TagSuggestion]:
    """Return ranked suggestions, highest score first (review-soonest first).

    Args:
        vault: Application vault, used for DB task dispatch.
        tag: Optional exact tag filter (the queue is reviewed one tag at a time).
        direction: Optional ``"add"`` / ``"remove"`` filter.
        status: Status filter (default ``PENDING``); pass ``""`` for all statuses.
        limit: Max rows to return.
        offset: Rows to skip (paging).
        picture_ids: Optional set of in-scope suspect picture ids; when not
            ``None`` only suggestions whose ``picture_id`` is in the set are
            returned (the suspect, never the twin). An empty set yields no rows.
            When ``None`` the picture scope is unrestricted (today's behaviour).
        source: Optional exact ``source`` filter (e.g. ``"near_neighbor"`` or
            ``"impossible_tag"``), so the two review tabs stay isolated.

    Returns:
        List of TagSuggestion instances ordered by score descending, then twin_sim.
    """

    def _fetch(session: Session) -> list[TagSuggestion]:
        q = select(TagSuggestion)
        if status:
            q = q.where(TagSuggestion.status == status.upper())
        if tag:
            q = q.where(TagSuggestion.tag == tag)
        if direction:
            q = q.where(TagSuggestion.direction == direction)
        if source:
            q = q.where(TagSuggestion.source == source)
        if picture_ids is not None:
            q = q.where(TagSuggestion.picture_id.in_(picture_ids))
        q = (
            q.order_by(
                TagSuggestion.score.desc(),
                TagSuggestion.twin_sim.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(session.exec(q).all())

    return vault.db.run_immediate_read_task(_fetch)


def get_picture_exts(vault: "Vault", ids: list[int | None]) -> dict[int, str]:
    """Map picture ids to their file extension (lowercased, no dot).

    The review view renders full-resolution images via ``/pictures/{id}.{ext}``
    (the 384px square-cropped thumbnail is useless for judging fine anomalies like
    malformed hands), so it needs each suspect's and twin's extension.

    Args:
        vault: Application vault, used for DB task dispatch.
        ids: Picture ids (Nones and duplicates are ignored).

    Returns:
        Dict of picture id → extension (e.g. ``"png"``); empty string if unknown.
    """
    wanted = sorted({i for i in ids if i is not None})
    if not wanted:
        return {}

    def _fetch(session: Session) -> dict[int, str]:
        rows = session.exec(
            select(Picture.id, Picture.format, Picture.file_path).where(
                Picture.id.in_(wanted)
            )
        ).all()
        out: dict[int, str] = {}
        for pid, fmt, file_path in rows:
            # The /pictures/{id}.{ext} route requires ext == picture.format.lower()
            # (e.g. a "JPEG"-format file must be requested as .jpeg, not .jpg), so
            # derive from format; fall back to the filename only if format is unset.
            ext = (fmt or "").lower()
            if not ext and file_path:
                base = file_path.rsplit("/", 1)[-1]
                if "." in base:
                    ext = base.rsplit(".", 1)[-1].lower()
            out[pid] = ext
        return out

    return vault.db.run_immediate_read_task(_fetch)


def get_tagger_confidences(
    vault: "Vault", pairs: list[tuple[int, str]]
) -> dict[tuple[int, str], float]:
    """Map ``(picture_id, tag)`` → the tagger's most recent raw confidence for that tag.

    The near-neighbor signal is model-independent by design; surfacing the tagger's own
    confidence gives the reviewer a complementary signal. A label the tagger is *also*
    unsure about is a stronger remove candidate; one it is confident about is a closer call.

    Args:
        vault: Application vault, used for DB task dispatch.
        pairs: ``(picture_id, tag)`` pairs to look up.

    Returns:
        Dict of ``(picture_id, tag)`` → confidence in ``[0, 1]`` (only pairs with a
        prediction on file appear).
    """
    pids = sorted({p for (p, _t) in pairs if p is not None})
    tagset = sorted({t for (_p, t) in pairs if t})
    if not pids or not tagset:
        return {}

    def _fetch(session: Session) -> dict[tuple[int, str], float]:
        rows = session.exec(
            select(
                TagPrediction.picture_id,
                TagPrediction.tag,
                TagPrediction.confidence,
                TagPrediction.predicted_at,
            ).where(
                TagPrediction.picture_id.in_(pids),
                TagPrediction.tag.in_(tagset),
            )
        ).all()
        best: dict[tuple[int, str], tuple[float, object]] = {}
        for pid, tag, conf, predicted_at in rows:
            key = (pid, tag)
            prev = best.get(key)
            # Prefer the most recent prediction (newest model wins).
            if prev is None or (
                predicted_at is not None and (prev[1] is None or predicted_at > prev[1])
            ):
                best[key] = (conf, predicted_at)
        return {k: v[0] for k, v in best.items()}

    return vault.db.run_immediate_read_task(_fetch)


def summary_by_tag(
    vault: "Vault",
    status: str = "PENDING",
    picture_ids: set[int] | None = None,
    source: str | None = None,
) -> list[dict]:
    """Return per-tag counts of suggestions, for the queue's tag picker and progress.

    Args:
        vault: Application vault, used for DB task dispatch.
        status: Status to count (default ``PENDING``).
        picture_ids: Optional set of in-scope suspect picture ids; when not
            ``None`` only suggestions whose ``picture_id`` is in the set are
            counted (the suspect, never the twin). An empty set yields no counts.
            When ``None`` the picture scope is unrestricted (today's behaviour).
        source: Optional exact ``source`` filter (e.g. ``"near_neighbor"`` or
            ``"impossible_tag"``), so the two review tabs stay isolated.

    Returns:
        List of ``{"tag", "add", "remove", "total"}`` dicts, busiest tag first.
    """

    def _fetch(session: Session) -> list[dict]:
        q = (
            select(
                TagSuggestion.tag,
                TagSuggestion.direction,
                func.count().label("n"),
            )
            .where(TagSuggestion.status == status.upper())
            .group_by(TagSuggestion.tag, TagSuggestion.direction)
        )
        if source:
            q = q.where(TagSuggestion.source == source)
        if picture_ids is not None:
            q = q.where(TagSuggestion.picture_id.in_(picture_ids))
        rows = session.exec(q).all()
        by_tag: dict[str, dict] = {}
        for tag, direction, n in rows:
            entry = by_tag.setdefault(
                tag, {"tag": tag, "add": 0, "remove": 0, "total": 0}
            )
            if direction in ("add", "remove"):
                entry[direction] += n
            entry["total"] += n
        return sorted(by_tag.values(), key=lambda e: e["total"], reverse=True)

    return vault.db.run_immediate_read_task(_fetch)


def _apply_writeback(session: Session, suggestion: TagSuggestion) -> None:
    """Write a suggestion's label change through to the Tag table (idempotent).

    ``remove`` deletes the tag if present; ``add`` creates it if absent (clearing the
    pending-retag sentinel, as the manual add path does). Routes through :func:`_set_tag`
    so accepting a suggestion records the human POS/NEG decision in the label ledger.
    """
    if suggestion.direction == "add":
        present = True
    elif suggestion.direction == "remove":
        present = False
    else:
        raise ValueError(f"Unknown suggestion direction: {suggestion.direction!r}")
    _set_tag(session, suggestion.picture_id, suggestion.tag, present)


def accept_suggestion(vault: "Vault", suggestion_id: int) -> dict:
    """Apply a suggestion's fix to the labels and mark it ACCEPTED.

    Idempotent: the underlying Tag mutation is ensure/delete, so re-accepting is safe.
    A suggestion that was already DISMISSED is still applied and flipped to ACCEPTED.

    Args:
        vault: Application vault, used for DB task dispatch.
        suggestion_id: Primary key of the TagSuggestion to accept.

    Returns:
        ``{"picture_id", "tag", "direction"}`` describing the applied change.

    Raises:
        KeyError: If no suggestion with that id exists.
    """

    def _accept(session: Session) -> dict:
        suggestion = session.get(TagSuggestion, suggestion_id)
        if suggestion is None:
            raise KeyError(f"TagSuggestion not found: id={suggestion_id}")
        _apply_writeback(session, suggestion)
        suggestion.status = "ACCEPTED"
        suggestion.reviewed_at = datetime.utcnow()
        result = {
            "picture_id": suggestion.picture_id,
            "tag": suggestion.tag,
            "direction": suggestion.direction,
        }
        session.commit()
        return result

    return vault.db.run_task(_accept)


def _reverse_review(session: Session, suggestion: TagSuggestion) -> None:
    """Undo a reviewed suggestion's label change and set it back to PENDING (no commit).

    ACCEPTED touched the suspect; TWIN_FIXED touched the twin; SWAPPED touched both;
    DISMISSED touched only the ledger. Each path also clears the human ledger entry the
    forward review wrote, so reopening is fully symmetric (no orphan POS/NEG left behind).
    """
    tag_value = suggestion.tag
    if suggestion.status == "ACCEPTED":
        pic_id = suggestion.picture_id
        existing = session.exec(
            select(Tag).where(Tag.picture_id == pic_id, Tag.tag == tag_value)
        ).first()
        if suggestion.direction == "remove" and existing is None:
            session.add(Tag(picture_id=pic_id, tag=tag_value))
        elif suggestion.direction == "add" and existing is not None:
            session.delete(existing)
        clear_human_label(session, pic_id, tag_value)
        session.flush()
        recompute_anomaly_tag_uncertainty(session, pic_id)
    elif suggestion.status == "TWIN_FIXED" and suggestion.twin_picture_id:
        twin_id = suggestion.twin_picture_id
        existing = session.exec(
            select(Tag).where(Tag.picture_id == twin_id, Tag.tag == tag_value)
        ).first()
        if suggestion.direction == "remove" and existing is not None:
            session.delete(existing)
        elif suggestion.direction == "add" and existing is None:
            session.add(Tag(picture_id=twin_id, tag=tag_value))
        clear_human_label(session, twin_id, tag_value)
        session.flush()
        recompute_anomaly_tag_uncertainty(session, twin_id)
    elif suggestion.status == "SWAPPED" and suggestion.twin_picture_id:
        # Reverse the swap: restore the originally-tagged image and re-clear the other.
        # record=False so undoing doesn't write inverse labels; clear both ledgers.
        tagged_id = (
            suggestion.picture_id
            if suggestion.direction == "remove"
            else suggestion.twin_picture_id
        )
        untagged_id = (
            suggestion.twin_picture_id
            if suggestion.direction == "remove"
            else suggestion.picture_id
        )
        _set_tag(session, tagged_id, tag_value, True, record=False)
        _set_tag(session, untagged_id, tag_value, False, record=False)
        clear_human_label(session, tagged_id, tag_value)
        clear_human_label(session, untagged_id, tag_value)
    elif suggestion.status == "DISMISSED":
        # Dismiss recorded a NEG (add) / POS (remove) on the suspect; clear it.
        clear_human_label(session, suggestion.picture_id, tag_value)
    suggestion.status = "PENDING"
    suggestion.reviewed_at = None


def reopen_suggestion(vault: "Vault", suggestion_id: int) -> dict:
    """Reopen a reviewed suggestion (undo): set it back to PENDING and reverse any
    label change. See :func:`_reverse_review`.

    Raises:
        KeyError: If no suggestion with that id exists.
    """

    def _reopen(session: Session) -> dict:
        suggestion = session.get(TagSuggestion, suggestion_id)
        if suggestion is None:
            raise KeyError(f"TagSuggestion not found: id={suggestion_id}")
        _reverse_review(session, suggestion)
        result = {
            "picture_id": suggestion.picture_id,
            "twin_picture_id": suggestion.twin_picture_id,
            "tag": suggestion.tag,
            "direction": suggestion.direction,
        }
        session.commit()
        return result

    return vault.db.run_task(_reopen)


def _confidence_map(session: Session, pids: list[int], tag: str) -> dict[int, float]:
    """Latest tagger confidence per picture id for one tag."""
    if not pids:
        return {}
    best: dict[int, tuple[float, object]] = {}
    for pid, c, at in session.exec(
        select(
            TagPrediction.picture_id,
            TagPrediction.confidence,
            TagPrediction.predicted_at,
        ).where(TagPrediction.picture_id.in_(pids), TagPrediction.tag == tag)
    ).all():
        prev = best.get(pid)
        if prev is None or (at is not None and (prev[1] is None or at > prev[1])):
            best[pid] = (c, at)
    return {pid: v[0] for pid, v in best.items()}


def _decision(
    left_conf: float | None, right_conf: float | None
) -> tuple[str | None, float]:
    """Place a pair in one of four corners from the tagger's per-image confidence — the
    near-neighbour link only *selected* the pair, it doesn't vote. ``left`` is the flagged
    image, ``right`` its untagged twin.

    Returns ``(corner, confidence)`` with corner ∈ {both, neither, leftonly, rightonly};
    confidence is how decisive the *weaker* of the two per-image calls is (both must be
    confident). ``(None, 0)`` if either confidence is missing.
    """
    if left_conf is None or right_conf is None:
        return None, 0.0
    left_has = left_conf >= 0.5
    right_has = right_conf >= 0.5
    if left_has and right_has:
        corner = "both"
    elif not left_has and not right_has:
        corner = "neither"
    elif left_has:
        corner = "leftonly"
    else:
        corner = "rightonly"
    confidence = min(max(left_conf, 1 - left_conf), max(right_conf, 1 - right_conf))
    return corner, confidence


def _neighbor_corner(direction: str) -> str | None:
    """The corner the model-independent near-neighbour scan implicitly proposes.

    The scan flags one direction per suspect; in left/right terms (left = the
    currently-tagged image, right = its untagged twin) that maps to a single corner:

      * ``add``    – the untagged suspect should gain the tag its twin already has → ``both``
      * ``remove`` – the tagged suspect should lose the tag its twin lacks         → ``neither``

    Bulk auto-resolve only fires when the tagger lands in this same corner, i.e. the two
    independent signals agree on the fix.
    """
    if direction == "add":
        return "both"
    if direction == "remove":
        return "neither"
    return None


def _set_tag(
    session: Session,
    picture_id: int,
    tag_value: str,
    present: bool,
    *,
    record: bool = True,
) -> None:
    """Force a picture's tag to ``present`` (add, clearing the sentinel, or delete).

    The single chokepoint for suggestion-driven Tag mutations. When ``record`` (the
    default for a forward human decision), also writes the human POS/NEG to the label
    ledger — ``present`` ⇒ POS, absent ⇒ NEG — so accepting/resolving a suggestion is
    durable supervision. Undo paths pass ``record=False`` and clear the ledger instead.
    """
    existing = session.exec(
        select(Tag).where(Tag.picture_id == picture_id, Tag.tag == tag_value)
    ).first()
    if present and existing is None:
        for t in session.exec(select(Tag).where(Tag.picture_id == picture_id)).all():
            if is_tag_sentinel(t.tag):
                session.delete(t)
        session.add(Tag(picture_id=picture_id, tag=tag_value))
    elif not present and existing is not None:
        session.delete(existing)
    if record:
        record_human_label(session, picture_id, tag_value, POS if present else NEG)
    session.flush()
    recompute_anomaly_tag_uncertainty(session, picture_id)


def _resolve(session: Session, suggestion: TagSuggestion, corner: str) -> None:
    """Apply a corner decision (left = flagged image, right = its twin): ``both`` tags the
    right too; ``neither`` clears the left; ``leftonly`` leaves both (labels already right);
    ``rightonly`` swaps. Records a status :func:`_reverse_review` can undo."""
    d = suggestion.direction
    tagged_id = suggestion.picture_id if d == "remove" else suggestion.twin_picture_id
    untagged_id = suggestion.twin_picture_id if d == "remove" else suggestion.picture_id
    if corner == "leftonly":
        suggestion.status = "DISMISSED"
    elif corner == "both":
        _set_tag(session, untagged_id, suggestion.tag, True)
        suggestion.status = "TWIN_FIXED" if d == "remove" else "ACCEPTED"
    elif corner == "neither":
        _set_tag(session, tagged_id, suggestion.tag, False)
        suggestion.status = "ACCEPTED" if d == "remove" else "TWIN_FIXED"
    elif corner == "rightonly":
        _set_tag(session, tagged_id, suggestion.tag, False)
        _set_tag(session, untagged_id, suggestion.tag, True)
        suggestion.status = "SWAPPED"
    suggestion.reviewed_at = datetime.utcnow()


def bulk_accept(
    vault: "Vault",
    tag: str,
    min_combined: float,
    direction: str | None = None,
    dry_run: bool = False,
    picture_ids: set[int] | None = None,
) -> dict:
    """Auto-resolve every PENDING pair for ``tag`` where two *independent* signals agree.

    A suggestion is applied only when the model-independent near-neighbour vote and the
    tagger point at the *same* fix and both clear ``min_combined``:

    * the near-neighbour scan proposes one corner per direction
      (``add`` → tag both, ``remove`` → tag neither) with strength ``score``;
    * the tagger, from the two images' per-image confidence, must land in that *same*
      corner (see :func:`_decision`) with margin ≥ ``min_combined``;
    * the neighbour ``score`` must also be ≥ ``min_combined``.

    Anything the two signals disagree on — the tagger overruling its near-twin, swaps,
    weakly-agreeing neighbours — is left for the human to hand-review. The queue exists
    *because* the model is noisy, so a confident tagger alone is never enough to auto-edit
    a label. ``dry_run`` applies nothing and instead returns a ``sample`` of the *least
    tagger-confident* would-be resolutions (the riskiest of the agreed set, to
    spot-check). Returns ``{"count", "sample", "accepted_ids", "picture_ids"}``.

    ``picture_ids`` optionally restricts the set of suspects considered (by
    ``TagSuggestion.picture_id``, never the twin): when not ``None`` only suspects
    in the set are counted or resolved, so the dry-run count and the apply both
    respect the same scope and out-of-scope suggestions are never bulk-resolved.
    An empty set resolves nothing; ``None`` is today's unrestricted behaviour.
    """

    def _bulk(session: Session) -> dict:
        q = select(TagSuggestion).where(
            TagSuggestion.status == "PENDING", TagSuggestion.tag == tag
        )
        if direction:
            q = q.where(TagSuggestion.direction == direction)
        if picture_ids is not None:
            q = q.where(TagSuggestion.picture_id.in_(picture_ids))
        rows = list(session.exec(q).all())

        ids: set[int] = set()
        for r in rows:
            ids.add(r.picture_id)
            if r.twin_picture_id is not None:
                ids.add(r.twin_picture_id)
        conf_map = _confidence_map(session, sorted(ids), tag)

        chosen: list[tuple[TagSuggestion, str, float]] = []
        for r in rows:
            tagged_id = r.picture_id if r.direction == "remove" else r.twin_picture_id
            untagged_id = r.twin_picture_id if r.direction == "remove" else r.picture_id
            corner, confidence = _decision(
                conf_map.get(tagged_id), conf_map.get(untagged_id)
            )
            # Blend: only auto-resolve when the two independent signals agree. The
            # near-neighbour scan proposes one corner per direction (add → "both",
            # remove → "neither"); the tagger must land in that SAME corner, and both
            # the neighbour vote (``score``) and the tagger margin (``confidence``) must
            # clear the threshold. Mismatches — the tagger disagreeing with its near-twin,
            # swaps, weakly-agreeing neighbours, or a twin with no prediction to compare —
            # fall through to human review. (Constraining the corner this way also means
            # bulk only ever applies the scan's own proposed change, never its inverse.)
            if corner is None or corner != _neighbor_corner(r.direction):
                continue
            if confidence < min_combined:
                continue
            if r.score is None or r.score < min_combined:
                continue
            chosen.append((r, corner, confidence))

        if dry_run:
            marginal = sorted(chosen, key=lambda c: c[2])[:12]
            sample = [
                {
                    "id": s.id,
                    "picture_id": s.picture_id,
                    "twin_picture_id": s.twin_picture_id,
                    "tag": s.tag,
                    "direction": s.direction,
                    "corner": corner,
                    "confidence": round(confidence, 4),
                    "tagger_confidence": conf_map.get(s.picture_id),
                    "twin_tagger_confidence": conf_map.get(s.twin_picture_id),
                }
                for (s, corner, confidence) in marginal
            ]
            return {
                "count": len(chosen),
                "sample": sample,
                "accepted_ids": [],
                "picture_ids": [],
            }

        accepted_ids: list[int] = []
        pic_ids: set[int] = set()
        for r, corner, _conf in chosen:
            _resolve(session, r, corner)
            accepted_ids.append(r.id)
            pic_ids.add(r.picture_id)
            if r.twin_picture_id is not None:
                pic_ids.add(r.twin_picture_id)
        session.commit()
        return {
            "count": len(accepted_ids),
            "sample": [],
            "accepted_ids": accepted_ids,
            "picture_ids": sorted(pic_ids),
        }

    return vault.db.run_task(_bulk)


def bulk_reopen(vault: "Vault", ids: list[int]) -> dict:
    """Reopen many suggestions at once (batch undo of a bulk-accept). See _reverse_review."""

    def _bulk(session: Session) -> dict:
        pic_ids: set[int] = set()
        count = 0
        for sid in ids:
            suggestion = session.get(TagSuggestion, sid)
            if suggestion is None:
                continue
            _reverse_review(session, suggestion)
            pic_ids.add(suggestion.picture_id)
            if suggestion.twin_picture_id:
                pic_ids.add(suggestion.twin_picture_id)
            count += 1
        session.commit()
        return {"count": count, "picture_ids": sorted(pic_ids)}

    return vault.db.run_task(_bulk)


def fix_twin_suggestion(vault: "Vault", suggestion_id: int) -> dict:
    """Resolve the disagreement in the *twin's* favour: the suspect's label is correct,
    so flip the twin to match it (and leave the suspect untouched).

      * remove suggestion → the untagged twin actually has the tag, so ADD it to the twin.
      * add    suggestion → the tagged twin actually lacks the tag, so REMOVE it from it.

    Marks the suggestion ``TWIN_FIXED`` so :func:`reopen_suggestion` can reverse the twin
    change on undo.

    Args:
        vault: Application vault, used for DB task dispatch.
        suggestion_id: Primary key of the TagSuggestion.

    Returns:
        ``{"picture_id", "twin_picture_id", "tag", "direction"}``.

    Raises:
        KeyError: If no suggestion with that id exists.
        ValueError: If the suggestion has no twin to fix.
    """

    def _fix(session: Session) -> dict:
        suggestion = session.get(TagSuggestion, suggestion_id)
        if suggestion is None:
            raise KeyError(f"TagSuggestion not found: id={suggestion_id}")
        twin_id = suggestion.twin_picture_id
        if twin_id is None:
            raise ValueError("suggestion has no twin to fix")
        # The twin's label is what the human is deciding here: a remove-suggestion means
        # the untagged twin actually has the tag (POS); an add-suggestion means the
        # tagged twin actually lacks it (NEG). _set_tag records that on the twin.
        _set_tag(session, twin_id, suggestion.tag, suggestion.direction == "remove")
        suggestion.status = "TWIN_FIXED"
        suggestion.reviewed_at = datetime.utcnow()
        result = {
            "picture_id": suggestion.picture_id,
            "twin_picture_id": twin_id,
            "tag": suggestion.tag,
            "direction": suggestion.direction,
        }
        session.commit()
        return result

    return vault.db.run_task(_fix)


def swap_suggestion(vault: "Vault", suggestion_id: int) -> dict:
    """Both labels are wrong, in opposite directions: the tagged image is actually clean
    and the untagged twin actually has the tag. Untag the one that has it and tag the one
    that doesn't, marking the suggestion ``SWAPPED`` (undoable via reopen).

    Raises:
        KeyError: If no suggestion with that id exists.
        ValueError: If the suggestion has no twin to swap with.
    """

    def _swap(session: Session) -> dict:
        suggestion = session.get(TagSuggestion, suggestion_id)
        if suggestion is None:
            raise KeyError(f"TagSuggestion not found: id={suggestion_id}")
        if suggestion.twin_picture_id is None:
            raise ValueError("suggestion has no twin to swap")
        tagged_id = (
            suggestion.picture_id
            if suggestion.direction == "remove"
            else suggestion.twin_picture_id
        )
        untagged_id = (
            suggestion.twin_picture_id
            if suggestion.direction == "remove"
            else suggestion.picture_id
        )
        _set_tag(session, tagged_id, suggestion.tag, False)
        _set_tag(session, untagged_id, suggestion.tag, True)
        suggestion.status = "SWAPPED"
        suggestion.reviewed_at = datetime.utcnow()
        result = {
            "picture_id": suggestion.picture_id,
            "twin_picture_id": suggestion.twin_picture_id,
            "tag": suggestion.tag,
            "direction": suggestion.direction,
        }
        session.commit()
        return result

    return vault.db.run_task(_swap)


def dismiss_suggestion(vault: "Vault", suggestion_id: int) -> dict:
    """Mark a suggestion DISMISSED — the human rejected the *suggestion*, which is itself
    a label decision: it affirms the current label is right.

    Dismissing is not "skip": an ``add`` suggestion says "this tag is missing", so
    dismissing it asserts the tag is correctly absent → human NEG; dismissing a
    ``remove`` suggestion asserts the tag correctly belongs → human POS. Recording these
    captures exactly the reviewed-negatives that an absent Tag row would otherwise lose.
    The labels (Tag rows) are left untouched; only the ledger is written.

    Args:
        vault: Application vault, used for DB task dispatch.
        suggestion_id: Primary key of the TagSuggestion to dismiss.

    Returns:
        ``{"picture_id", "tag", "direction"}`` describing the dismissed suggestion.

    Raises:
        KeyError: If no suggestion with that id exists.
    """

    def _dismiss(session: Session) -> dict:
        suggestion = session.get(TagSuggestion, suggestion_id)
        if suggestion is None:
            raise KeyError(f"TagSuggestion not found: id={suggestion_id}")
        record_human_label(
            session,
            suggestion.picture_id,
            suggestion.tag,
            NEG if suggestion.direction == "add" else POS,
        )
        suggestion.status = "DISMISSED"
        suggestion.reviewed_at = datetime.utcnow()
        result = {
            "picture_id": suggestion.picture_id,
            "tag": suggestion.tag,
            "direction": suggestion.direction,
        }
        session.commit()
        return result

    return vault.db.run_task(_dismiss)

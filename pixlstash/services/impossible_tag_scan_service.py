"""On-demand impossible-tag scan — (re)build the cleanup queue for wrong person-tags.

A sibling of :mod:`pixlstash.services.tag_scan_service`, but a different signal: it flags
person-tags that are *impossible* on a picture with no detectable face. No-face only
narrows the candidates; the strongest available evidence then decides which tags to flag
and under which named signal (see :func:`pixlstash.utils.service.person_tags.plan_strips`):

  * ``impossible:no_humans`` — the picture is also tagged ``no humans`` / ``scenery`` →
    strip every person-tag (strongest evidence).
  * ``impossible:object``    — the caption describes a non-person object → strip every
    person-tag (caption-miss risk, lowest score).
  * ``impossible:no_face``   — a face-requiring tag with no detected face → strip just
    the face-requiring tags, keeping hair/body.

Each flagged tag becomes a ``direction="remove"`` :class:`TagSuggestion` with its signal's
``source`` and ``twin_picture_id=None``. The grid's "Impossible tags" filters select
pictures by these sources; clearing accepts the suggestions through the same
accept → delete-tag + human-NEG-ledger path as the rest of the review system. Re-running
rebuilds the PENDING impossible queue and never resurrects a reviewed row.
"""

from datetime import datetime

from sqlalchemy import exists, func
from sqlmodel import Session, delete, select

from pixlstash.db_models import Face, Picture, Project, Tag
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.person_tags import (
    IMPOSSIBLE_SOURCES,
    OBJECT_META_TAGS,
    PERSON_TAGS,
    plan_strips,
)

logger = get_logger(__name__)

# Candidate requirement: carries a person-tag. Per-candidate we also load the object
# meta-tags (evidence for the no_humans signal), so the loader queries the union.
_PERSON_TAGS_LOWER = sorted(PERSON_TAGS)
_LOAD_TAGS_LOWER = sorted(PERSON_TAGS | OBJECT_META_TAGS)

# SQLite caps bound variables per statement (~999). Candidate id lists are small (no-face
# ∩ person-tagged), but chunk the per-picture tag load to stay safe.
_ID_CHUNK = 900


def _chunks(seq: list, size: int = _ID_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def load_impossible_candidates(
    session: Session, *, project_id: int | None = None
) -> list[tuple[int, str | None, list[str]]]:
    """Load (picture_id, description, tags) for every impossible-tag candidate.

    A candidate is a non-deleted picture that has **no real detected face** (its only
    ``Face`` rows are sentinels, ``face_index == -1``) and carries at least one
    person-tag. ``tags`` includes the picture's person-tags *and* any object meta-tags
    (the evidence the classifier needs). Scoped to ``project_id`` when given.
    """
    no_real_face = ~exists().where(
        (Face.picture_id == Picture.id) & (Face.face_index != -1)
    )
    has_person_tag = exists().where(
        (Tag.picture_id == Picture.id) & (func.lower(Tag.tag).in_(_PERSON_TAGS_LOWER))
    )
    q = select(Picture.id, Picture.description).where(
        Picture.deleted.is_(False), no_real_face, has_person_tag
    )
    if project_id is not None:
        q = q.where(Picture.project_id == project_id)
    desc_rows = session.exec(q).all()
    if not desc_rows:
        return []

    cand_ids = [pid for pid, _desc in desc_rows]
    desc_by_pic = {pid: desc for pid, desc in desc_rows}

    tags_by_pic: dict[int, list[str]] = {pid: [] for pid in cand_ids}
    for chunk in _chunks(cand_ids):
        rows = session.exec(
            select(Tag.picture_id, Tag.tag).where(
                Tag.picture_id.in_(chunk),
                func.lower(Tag.tag).in_(_LOAD_TAGS_LOWER),
            )
        ).all()
        for pic_id, tag in rows:
            tags_by_pic[pic_id].append(tag)

    return [(pid, desc_by_pic[pid], tags_by_pic[pid]) for pid in cand_ids]


def _reason(plan: dict, description: str | None, tag: str) -> str:
    """Human-readable why-this-was-flagged, shown to the reviewer."""
    verdict = plan["verdict"]
    if verdict == "no_humans":
        return f'tagged "no humans" with no face — person-tag "{tag}" is suspect'
    if verdict == "object":
        snippet = (description or "").strip()
        if len(snippet) > 70:
            snippet = snippet[:69] + "…"
        return f'no face; caption {snippet!r} describes an object — "{tag}" suspect'
    return f'no face detected; "{tag}" requires a face'


def scan_impossible_tags(session: Session, *, project: str | None = None) -> dict:
    """Scan for impossible person-tags and rebuild the PENDING impossible queue.

    Args:
        session: Open database session supplied by the caller.
        project: Optional project name to scope to; ``None`` (the default) scans the
            whole vault. Person-tags live across the general library, not in the
            ``PixlTagger`` anomaly-training project (which carries no person-tags), so
            this defaults to whole-vault. Unknown names fall back to the whole vault.

    Returns:
        ``{"candidates", "no_humans", "object", "no_face", "pictures_flagged",
        "flagged", "scanned"}`` (the three middle keys count pictures per signal).
    """
    pid = None
    if project:
        pid = session.exec(select(Project.id).where(Project.name == project)).first()
    candidates = load_impossible_candidates(session, project_id=pid)

    # Classify each candidate (pure Python, no session). One signal per picture.
    plans: list[tuple[int, str | None, dict]] = []
    counts = {"no_humans": 0, "object": 0, "no_face": 0, "none": 0}
    for pid, description, tags in candidates:
        plan = plan_strips(description, tags)
        counts[plan["verdict"]] = counts.get(plan["verdict"], 0) + 1
        if plan["flag"]:
            plans.append((pid, description, plan))

    # Full rebuild of the PENDING impossible queue across all signals; reviewed rows
    # are left in place and block re-inserting the same (picture, tag) — so a tag the
    # user already cleared/kept never resurrects, even if its signal changed.
    session.exec(
        delete(TagSuggestion).where(
            TagSuggestion.status == "PENDING",
            TagSuggestion.source.in_(IMPOSSIBLE_SOURCES),
        )
    )
    reviewed = set(
        session.exec(
            select(TagSuggestion.picture_id, TagSuggestion.tag).where(
                TagSuggestion.source.in_(IMPOSSIBLE_SOURCES)
            )
        ).all()
    )
    now = datetime.utcnow()
    flagged = 0
    for pid, description, plan in plans:
        for tag in sorted(plan["flag"]):
            if (pid, tag) in reviewed:
                continue
            session.add(
                TagSuggestion(
                    picture_id=pid,
                    tag=tag,
                    direction="remove",
                    source=plan["source"],
                    score=plan["score"],
                    reason=_reason(plan, description, tag),
                    twin_picture_id=None,
                    twin_sim=None,
                    status="PENDING",
                    created_at=now,
                )
            )
            flagged += 1
    session.commit()

    result = {
        "candidates": len(candidates),
        "no_humans": counts["no_humans"],
        "object": counts["object"],
        "no_face": counts["no_face"],
        "pictures_flagged": len(plans),
        "flagged": flagged,
        "scanned": len(candidates),
    }
    logger.info(
        "Impossible-tag scan: %d candidates (no_humans %d / object %d / no_face %d), "
        "%d tags flagged across %d pictures",
        result["candidates"],
        result["no_humans"],
        result["object"],
        result["no_face"],
        result["flagged"],
        result["pictures_flagged"],
    )
    return result

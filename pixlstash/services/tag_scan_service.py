"""On-demand near-neighbour tag scan — (re)build the review queue for one tag.

The in-app equivalent of ``scripts/near_neighbor_label_disagreement.py``: it reuses the
shared :func:`pixlstash.utils.near_neighbor.knn_disagreement` kernel so the CLI and the UI
can't drift, is merge-aware via :data:`DEFAULT_TAG_MERGES`, and rebuilds the tag's PENDING
suggestions (reviewed rows are preserved). Runs synchronously — fast enough for an
interactive click on a typical vault.
"""

from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlmodel import Session, delete, select

from pixlstash.db_models import Picture, Project, Tag
from pixlstash.db_models.tag import DEFAULT_TAG_MERGES
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.near_neighbor import (
    EMBEDDING_BYTES,
    EMBEDDING_DIM,
    dedupe_by_pair,
    knn_disagreement,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

SOURCE = "near_neighbor"


def scan_tag(
    vault: "Vault",
    tag: str,
    *,
    project: str | None = "PixlTagger",
    k: int = 12,
    add_threshold: float = 0.55,
    remove_threshold: float = 0.45,
    min_twin_sim: float = 0.85,
) -> dict:
    """Scan one tag for near-neighbour label disagreements and rebuild its PENDING queue.

    Args:
        vault: Application vault, used for DB task dispatch.
        tag: The tag to scan, e.g. ``"malformed hand"``.
        project: Scope to this project name (default ``"PixlTagger"``); ``None`` = whole
            vault. Unknown names fall back to the whole vault.
        k, add_threshold, remove_threshold, min_twin_sim: scan knobs (CLI defaults).

    Returns:
        ``{"tag", "count", "added", "removed", "scanned"}``.
    """
    # Child tags that PixlTagger merges into this one count as "has the tag" for voting
    # and the "missing" direction (but not for "remove" — see has_literal vs has_concept).
    equiv = {tag} | {
        child for child, parent in DEFAULT_TAG_MERGES.items() if parent == tag
    }

    def _load(session: Session):
        pid = None
        if project:
            pid = session.exec(
                select(Project.id).where(Project.name == project)
            ).first()
        q = select(Picture.id, Picture.image_embedding).where(
            Picture.image_embedding.is_not(None), Picture.deleted.is_(False)
        )
        if pid is not None:
            q = q.where(Picture.project_id == pid)
        emb_rows = session.exec(q).all()
        literal = set(session.exec(select(Tag.picture_id).where(Tag.tag == tag)).all())
        concept = set(
            session.exec(select(Tag.picture_id).where(Tag.tag.in_(sorted(equiv)))).all()
        )
        return emb_rows, literal, concept

    emb_rows, literal, concept = vault.db.run_immediate_read_task(_load)

    ids: list[int] = []
    blobs: list[bytes] = []
    for pic_id, blob in emb_rows:
        if blob is None or len(blob) != EMBEDDING_BYTES:
            continue
        ids.append(pic_id)
        blobs.append(blob)

    empty = {"tag": tag, "count": 0, "added": 0, "removed": 0, "scanned": len(ids)}
    if len(ids) < 2:
        return empty

    emb = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(
        len(ids), EMBEDDING_DIM
    )
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb = (emb / norms).astype(np.float32)

    has_literal = np.array([pid in literal for pid in ids], dtype=bool)
    has_concept = np.array([pid in concept for pid in ids], dtype=bool)
    pos_frac, twin_idx, twin_sim = knn_disagreement(emb, has_concept, k)

    suspects: list[dict] = []
    for i in range(len(ids)):
        # ADD eligibility uses the merged concept; REMOVE uses the literal tag.
        if not has_concept[i] and pos_frac[i] >= add_threshold:
            direction, score = "add", float(pos_frac[i])
        elif has_literal[i] and pos_frac[i] <= remove_threshold:
            direction, score = "remove", float(1.0 - pos_frac[i])
        else:
            continue
        if twin_sim[i] < min_twin_sim:
            continue
        ti = int(twin_idx[i])
        suspects.append(
            {
                "picture_id": int(ids[i]),
                "direction": direction,
                "score": round(score, 4),
                "twin_picture_id": int(ids[ti]) if ti >= 0 else None,
                "twin_sim": round(float(twin_sim[i]), 4),
                "pos_frac": round(float(pos_frac[i]), 4),
            }
        )

    # A mutually-disagreeing pair yields both a remove and an add suspect that are the
    # same review — keep one per pair so the queue doesn't show it twice.
    suspects = dedupe_by_pair(suspects)

    def _write(session: Session) -> None:
        # Rebuild this tag's PENDING suggestions from this source; reviewed rows are
        # left in place (and block re-inserting the same suspect, so they don't resurrect).
        session.exec(
            delete(TagSuggestion).where(
                TagSuggestion.status == "PENDING",
                TagSuggestion.source == SOURCE,
                TagSuggestion.tag == tag,
            )
        )
        reviewed_pids = set(
            session.exec(
                select(TagSuggestion.picture_id).where(
                    TagSuggestion.tag == tag, TagSuggestion.source == SOURCE
                )
            ).all()
        )
        now = datetime.utcnow()
        for r in suspects:
            if r["picture_id"] in reviewed_pids:
                continue
            reason = (
                f"near-twin {r['twin_picture_id']} (sim {r['twin_sim']:.3f}) disagrees; "
                f"{r['pos_frac']:.0%} of nearest neighbours have the tag"
            )
            session.add(
                TagSuggestion(
                    picture_id=r["picture_id"],
                    tag=tag,
                    direction=r["direction"],
                    source=SOURCE,
                    score=r["score"],
                    reason=reason,
                    twin_picture_id=r["twin_picture_id"],
                    twin_sim=r["twin_sim"],
                    status="PENDING",
                    created_at=now,
                )
            )
        session.commit()

    vault.db.run_task(_write)

    added = sum(1 for r in suspects if r["direction"] == "add")
    removed = sum(1 for r in suspects if r["direction"] == "remove")
    return {
        "tag": tag,
        "count": len(suspects),
        "added": added,
        "removed": removed,
        "scanned": len(ids),
    }

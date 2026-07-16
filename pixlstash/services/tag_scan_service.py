"""On-demand near-neighbour tag scan — find one tag's suspects and append them.

The in-app equivalent of ``scripts/near_neighbor_label_disagreement.py``: it reuses the
shared :func:`pixlstash.utils.near_neighbor.knn_disagreement_with_neighbors` kernel so
the CLI and the UI can't drift, and is merge-aware via :data:`DEFAULT_TAG_MERGES`.

The write path is **diff-insert, never delete-and-rebuild**: a scan only inserts
suspects that don't already have a row for (tag, source), and never deletes or
resurrects rows. When a ``review_id`` is given the scan writes into that review
session (see :class:`pixlstash.db_models.review.Review`):

* new suspects are inserted with ``review_id`` and their neighbourhood evidence
  captured into ``TagSuggestion.neighbors``;
* still-undecided rows from the legacy queue or a closed review are adopted into
  the review (they were never decided, so this resurrects nothing);
* rows already **decided** in an earlier review are skipped and counted as
  ``prev_reviewed`` — unless ``include_reviewed=True``, which re-parents them
  into the new review with ``status`` back to ``PENDING`` (the row is kept, so
  UNIQUE(picture_id, tag, source) and the audit trail both survive);
* rows already belonging to *this* review are never touched — a refresh cannot
  resurrect the review's own decided rows.

Suppression of previously-reviewed suspects is therefore **per-review** (the
explicit ``include_reviewed`` toggle), not the old permanent ``reviewed_pids``
skip. Runs synchronously — fast enough for an interactive click on a typical
vault.

Suggestion *kind* ("pair" for true versions of one shot vs "binary") is derived
at read time (see :func:`pixlstash.services.review_service.derive_kind`) from
the pictures' ``stack_id`` and dhash — not stored — so legacy rows and
re-parented rows get it uniformly.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlmodel import Session, select

from pixlstash.db_models import Picture, Project, Tag
from pixlstash.db_models.tag import DEFAULT_TAG_MERGES
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.near_neighbor import (
    EMBEDDING_BYTES,
    EMBEDDING_DIM,
    dedupe_by_pair,
    hamming_distance,
    knn_disagreement_with_neighbors,
    nearest_opposite_by_hamming,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

SOURCE = "near_neighbor"

# Default dhash Hamming threshold for both the displayed-twin override at scan
# time and the read-time "pair" kind derivation (same shot, altered copy).
DEFAULT_MAX_TWIN_HAMMING = 8


def scan_tag(
    vault: "Vault",
    tag: str,
    *,
    project: str | None = "PixlTagger",
    picture_ids: set[int] | None = None,
    k: int = 12,
    add_threshold: float = 0.55,
    remove_threshold: float = 0.45,
    min_twin_sim: float = 0.85,
    max_twin_hamming: int = DEFAULT_MAX_TWIN_HAMMING,
    review_id: int | None = None,
    include_reviewed: bool = False,
) -> dict:
    """Scan one tag for near-neighbour label disagreements and append its suspects.

    Args:
        vault: Application vault, used for DB task dispatch.
        tag: The tag to scan, e.g. ``"malformed hand"``.
        project: Scope to this project name (default ``"PixlTagger"``); ``None`` = whole
            vault. Unknown names fall back to the whole vault. Ignored when
            ``picture_ids`` is provided (the review path resolves scope itself).
        picture_ids: Optional explicit scope — only these picture ids are scanned.
            An empty set scans nothing. ``None`` = no explicit scope (use ``project``).
        k, add_threshold, remove_threshold, min_twin_sim: scan knobs (CLI defaults).
            ``min_twin_sim`` gates eligibility on the CLIP twin's similarity and is
            unaffected by the perceptual-hash twin override below.
        max_twin_hamming: max 64-bit dhash Hamming distance for the *displayed* twin
            override. When an eligible suspect has an opposite-labelled perceptual
            near-duplicate within this many bits (~<=8 ≈ near-identical), that
            near-duplicate is shown as the twin instead of the CLIP-nearest one. This
            changes only which comparison is displayed, never which pictures are flagged.
        review_id: When set, write the suspects into this review session (see the
            module docstring for the diff-insert / re-parent semantics).
        include_reviewed: Only meaningful with ``review_id``: re-parent suspects
            already decided in earlier reviews into this one (status back to PENDING).

    Returns:
        ``{"tag", "count", "added", "removed", "scanned", "new", "prev_reviewed"}``
        where ``count``/``added``/``removed`` describe the suspects the scan
        *detected*, ``new`` is how many rows this call actually added to the
        queue/review (inserted + adopted + re-included), and ``prev_reviewed``
        is how many detected suspects were already decided in earlier reviews.
    """
    # Child tags that PixlTagger merges into this one count as "has the tag" for voting
    # and the "missing" direction (but not for "remove" — see has_literal vs has_concept).
    equiv = {tag} | {
        child for child, parent in DEFAULT_TAG_MERGES.items() if parent == tag
    }

    def _load(session: Session):
        pid = None
        if project and picture_ids is None:
            pid = session.exec(
                select(Project.id).where(Project.name == project)
            ).first()
        q = select(Picture.id, Picture.image_embedding, Picture.perceptual_hash).where(
            Picture.image_embedding.is_not(None), Picture.deleted.is_(False)
        )
        if picture_ids is not None:
            q = q.where(Picture.id.in_(picture_ids))
        elif pid is not None:
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
    phash_values: list[int] = []
    phash_valid: list[bool] = []
    for pic_id, blob, phash in emb_rows:
        if blob is None or len(blob) != EMBEDDING_BYTES:
            continue
        ids.append(pic_id)
        blobs.append(blob)
        # dhash is stored as a 16-char lowercase hex string (8x8 = 64 bits). Parse to an
        # int; mark missing/malformed values invalid rather than raising.
        value = 0
        valid = False
        if phash:
            try:
                value = int(phash, 16)
                valid = True
            except (ValueError, TypeError):
                logger.warning(
                    "scan_tag: unparseable perceptual_hash %r for picture %s; "
                    "excluding from near-duplicate twin selection",
                    phash,
                    pic_id,
                )
        phash_values.append(value)
        phash_valid.append(valid)

    empty = {
        "tag": tag,
        "count": 0,
        "added": 0,
        "removed": 0,
        "scanned": len(ids),
        "new": 0,
        "prev_reviewed": 0,
    }
    if len(ids) < 2:
        return empty

    # uint64 so the full 64-bit dhash range round-trips for the XOR/popcount Hamming.
    phash_ints = np.array(phash_values, dtype=np.uint64)
    valid_mask = np.array(phash_valid, dtype=bool)

    emb = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(
        len(ids), EMBEDDING_DIM
    )
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb = (emb / norms).astype(np.float32)

    has_literal = np.array([pid in literal for pid in ids], dtype=bool)
    has_concept = np.array([pid in concept for pid in ids], dtype=bool)
    pos_frac, twin_idx, twin_sim, neighbor_idx = knn_disagreement_with_neighbors(
        emb, has_concept, k
    )

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
        # Eligibility above is unchanged. Below, only the *displayed* twin may switch: if
        # this suspect has an opposite-labelled perceptual near-duplicate (an altered copy
        # of itself), show that as the twin instead of the CLIP-nearest one.
        ti = int(twin_idx[i])
        display_twin_id = int(ids[ti]) if ti >= 0 else None
        display_twin_sim = round(float(twin_sim[i]), 4)
        reason = (
            f"near-twin {display_twin_id} (sim {display_twin_sim:.3f}) disagrees; "
            f"{float(pos_frac[i]):.0%} of nearest neighbours have the tag"
        )

        j = nearest_opposite_by_hamming(
            phash_ints, valid_mask, has_concept, i, max_twin_hamming, twin_sim
        )
        if j >= 0 and j != ti:
            d = hamming_distance(int(phash_values[i]), int(phash_values[j]))
            display_twin_id = int(ids[j])
            # Recompute similarity for the actually-shown twin so the stored value
            # describes it, not the (now discarded) CLIP-nearest twin.
            display_twin_sim = round(float(emb[i] @ emb[j]), 4)
            reason = (
                f"near-duplicate twin {display_twin_id} (dhash hamming {d}); "
                f"{float(pos_frac[i]):.0%} of nearest neighbours have the tag"
            )

        # The neighbourhood evidence the vote used, most-similar first, with each
        # neighbour's merged-concept "has the tag" flag — frozen at scan time.
        neighbors = [
            {"picture_id": int(ids[m]), "has": bool(has_concept[m])}
            for m in neighbor_idx[i]
            if m >= 0
        ]

        suspects.append(
            {
                "picture_id": int(ids[i]),
                "direction": direction,
                "score": round(score, 4),
                "twin_picture_id": display_twin_id,
                "twin_sim": display_twin_sim,
                "pos_frac": round(float(pos_frac[i]), 4),
                "reason": reason,
                "neighbors": neighbors,
            }
        )

    # A mutually-disagreeing pair yields both a remove and an add suspect that are the
    # same review — keep one per pair so the queue doesn't show it twice.
    suspects = dedupe_by_pair(suspects)

    def _write(session: Session) -> dict:
        # Diff-insert against ALL existing rows for (tag, source): the unique
        # constraint is on (picture_id, tag, source), so a suspect with any prior
        # row is updated-or-skipped, never re-inserted. Nothing is ever deleted.
        existing = {
            row.picture_id: row
            for row in session.exec(
                select(TagSuggestion).where(
                    TagSuggestion.tag == tag, TagSuggestion.source == SOURCE
                )
            ).all()
        }
        now = datetime.utcnow()
        new_count = 0
        prev_reviewed = 0

        def _refresh_scan_fields(row: TagSuggestion, r: dict) -> None:
            row.direction = r["direction"]
            row.score = r["score"]
            row.reason = r["reason"]
            row.twin_picture_id = r["twin_picture_id"]
            row.twin_sim = r["twin_sim"]
            row.neighbors = json.dumps(r["neighbors"])

        for r in suspects:
            row = existing.get(r["picture_id"])
            if row is None:
                session.add(
                    TagSuggestion(
                        picture_id=r["picture_id"],
                        tag=tag,
                        direction=r["direction"],
                        source=SOURCE,
                        score=r["score"],
                        reason=r["reason"],
                        twin_picture_id=r["twin_picture_id"],
                        twin_sim=r["twin_sim"],
                        status="PENDING",
                        created_at=now,
                        review_id=review_id,
                        neighbors=json.dumps(r["neighbors"]),
                    )
                )
                new_count += 1
                continue
            if review_id is not None and row.review_id == review_id:
                # Already part of this review — pending or decided. Never touch
                # it: a refresh must not resurrect this review's own decisions.
                continue
            if row.status == "PENDING":
                # Undecided row from the legacy global queue or a closed review:
                # adopt it into this review with fresh scan evidence. Nobody
                # decided it, so this resurrects nothing.
                if review_id is not None:
                    row.review_id = review_id
                    _refresh_scan_fields(row, r)
                    new_count += 1
                continue
            # Decided in an earlier review (or the legacy queue).
            prev_reviewed += 1
            if include_reviewed and review_id is not None:
                # Explicit re-surfacing: re-parent the decided row into this
                # review and reopen it. The row (and its history in the ledger)
                # is kept — UNIQUE(picture_id, tag, source) stays intact.
                row.review_id = review_id
                row.status = "PENDING"
                row.reviewed_at = None
                _refresh_scan_fields(row, r)
                new_count += 1
        session.commit()
        return {"new": new_count, "prev_reviewed": prev_reviewed}

    write_stats = vault.db.run_task(_write)

    added = sum(1 for r in suspects if r["direction"] == "add")
    removed = sum(1 for r in suspects if r["direction"] == "remove")
    return {
        "tag": tag,
        "count": len(suspects),
        "added": added,
        "removed": removed,
        "scanned": len(ids),
        "new": write_stats["new"],
        "prev_reviewed": write_stats["prev_reviewed"],
    }
